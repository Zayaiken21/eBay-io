"""
draft_store.py — Per-user draft storage backed by Supabase.

Hardened against:
  - Missing/misconfigured Supabase credentials
  - Table not yet created
  - PostgREST returning an error dict instead of a list
  - Missing or malformed Content-Range headers
  - Any other unexpected response shape

On ANY Supabase failure, this module degrades to the local-file fallback
instead of crashing the page with an unhandled TypeError. A warning is
shown via _last_error so the UI can surface "drafts are running in local
mode" instead of a blank crash screen.

Table expected in Supabase — `product_drafts`:
    id              bigint, primary key, identity
    draft_id        text, unique
    owner_name      text
    data            jsonb
    status          text
    created_at      timestamptz
    updated_at      timestamptz
"""

import json
import os
import uuid
import requests
import streamlit as st
from datetime import datetime, timezone
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _supabase_url() -> str:
    try:
        return str(st.secrets.get("SUPABASE_URL", "")).rstrip("/")
    except Exception:
        return ""

def _supabase_key() -> str:
    try:
        return str(
            st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")
            or st.secrets.get("SUPABASE_SECRET_KEY")
            or ""
        )
    except Exception:
        return ""

def _using_supabase() -> bool:
    return bool(_supabase_url() and _supabase_key())

def _headers(prefer: Optional[str] = None) -> dict:
    key = _supabase_key()
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    return h

def _table_url() -> str:
    return f"{_supabase_url()}/rest/v1/product_drafts"

def _owner_name() -> str:
    client_name = st.session_state.get("client_name") or ""
    role        = st.session_state.get("role") or ""
    if client_name:
        return client_name.strip()
    if role == "ceo":
        return "ceo"
    return "default"

def _is_list_response(payload) -> bool:
    return isinstance(payload, list)

def _safe_json(resp):
    """Never throws — returns None on any parse failure."""
    try:
        return resp.json()
    except Exception:
        return None

def _set_last_error(msg: Optional[str]):
    st.session_state["_draft_store_error"] = msg

def get_last_error() -> Optional[str]:
    """UI can call this to show a banner like 'Drafts running in local-only mode'."""
    return st.session_state.get("_draft_store_error")


# ── Local-file fallback ───────────────────────────────────────────────────
# Used automatically whenever Supabase is unreachable or misconfigured.
# NOTE: in this mode, drafts are NOT guaranteed isolated across Streamlit
# Cloud replicas — it exists purely so the app never hard-crashes.
_LOCAL_FILE = "drafts_local_fallback.json"

def _local_load_all() -> dict:
    if not os.path.exists(_LOCAL_FILE):
        return {}
    try:
        with open(_LOCAL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _local_save_all(data: dict) -> None:
    try:
        with open(_LOCAL_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def _local_list(owner: str, page: int, page_size: int) -> dict:
    all_for_owner = list(_local_load_all().get(owner, {}).values())
    all_for_owner.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
    total  = len(all_for_owner)
    offset = (page - 1) * page_size
    items  = all_for_owner[offset: offset + page_size]
    return _paged(items, total, page, page_size)


# ── Public API ─────────────────────────────────────────────────────────

def save_draft(product: dict, draft_id: Optional[str] = None) -> str:
    owner = _owner_name()
    if draft_id is None:
        draft_id = str(uuid.uuid4())[:8]

    product = dict(product)
    product["draft_id"]   = draft_id
    product["owner_name"] = owner
    now = _now_iso()
    product["updated_at"] = now
    product.setdefault("created_at", now)

    if not _using_supabase():
        _set_last_error("Supabase not configured — drafts saved locally only (not shared across deployments).")
        all_drafts = _local_load_all()
        all_drafts.setdefault(owner, {})
        product["created_at"] = all_drafts[owner].get(draft_id, {}).get("created_at", now)
        all_drafts[owner][draft_id] = product
        _local_save_all(all_drafts)
        return draft_id

    try:
        existing = _get_row(draft_id, owner)
        row = {
            "draft_id":   draft_id,
            "owner_name": owner,
            "data":       product,
            "status":     product.get("status", "draft"),
            "updated_at": now,
        }

        if existing:
            resp = requests.patch(
                _table_url(), headers=_headers(),
                params={"draft_id": f"eq.{draft_id}", "owner_name": f"eq.{owner}"},
                data=json.dumps(row), timeout=30,
            )
        else:
            row["created_at"] = now
            resp = requests.post(
                _table_url(), headers=_headers("return=representation"),
                data=json.dumps(row), timeout=30,
            )

        if resp.status_code >= 400:
            raise RuntimeError(f"{resp.status_code} {resp.text[:300]}")

        _set_last_error(None)
        return draft_id

    except Exception as e:
        # Never crash on save — fall back to local file so the user's work isn't lost.
        _set_last_error(f"Supabase save failed ({e}) — saved locally instead.")
        all_drafts = _local_load_all()
        all_drafts.setdefault(owner, {})
        all_drafts[owner][draft_id] = product
        _local_save_all(all_drafts)
        return draft_id


def _get_row(draft_id: str, owner: str) -> Optional[dict]:
    resp = requests.get(
        _table_url(), headers=_headers(),
        params={"draft_id": f"eq.{draft_id}", "owner_name": f"eq.{owner}", "select": "id", "limit": "1"},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"{resp.status_code} {resp.text[:300]}")
    rows = _safe_json(resp)
    if not _is_list_response(rows):
        raise RuntimeError(f"Unexpected Supabase response: {rows}")
    return rows[0] if rows else None


def load_draft(draft_id: str) -> Optional[dict]:
    owner = _owner_name()

    if not _using_supabase():
        return _local_load_all().get(owner, {}).get(draft_id)

    try:
        resp = requests.get(
            _table_url(), headers=_headers(),
            params={"draft_id": f"eq.{draft_id}", "owner_name": f"eq.{owner}", "select": "data", "limit": "1"},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"{resp.status_code} {resp.text[:300]}")
        rows = _safe_json(resp)
        if not _is_list_response(rows):
            raise RuntimeError(f"Unexpected Supabase response: {rows}")
        return rows[0]["data"] if rows else None
    except Exception as e:
        _set_last_error(f"Supabase load failed ({e}) — checking local fallback.")
        return _local_load_all().get(owner, {}).get(draft_id)


def list_drafts(page: int = 1, page_size: int = 20) -> dict:
    """
    Returns drafts for the current owner, paginated. Never raises —
    on any Supabase problem this transparently falls back to local storage
    so the page always renders instead of crashing.
    """
    owner = _owner_name()
    page  = max(1, int(page or 1))
    page_size = max(1, int(page_size or 20))
    offset = (page - 1) * page_size

    if not _using_supabase():
        return _local_list(owner, page, page_size)

    try:
        # ── Count ────────────────────────────────────────────────────
        count_resp = requests.get(
            _table_url(),
            headers={**_headers(), "Prefer": "count=exact"},
            params={"owner_name": f"eq.{owner}", "select": "id", "limit": "1"},
            timeout=30,
        )
        total = 0
        if count_resp.status_code < 400:
            content_range = count_resp.headers.get("content-range") or ""
            if isinstance(content_range, str) and "/" in content_range:
                tail = content_range.split("/")[-1]
                if tail.isdigit():
                    total = int(tail)
        elif count_resp.status_code == 404:
            # Table doesn't exist yet — fall back cleanly with a clear message.
            raise RuntimeError(
                "Table 'product_drafts' not found in Supabase. "
                "Create it (see draft_store.py docstring) or drafts will run in local-only mode."
            )
        else:
            raise RuntimeError(f"{count_resp.status_code} {count_resp.text[:300]}")

        # ── Page of results ──────────────────────────────────────────
        resp = requests.get(
            _table_url(), headers=_headers(),
            params={
                "owner_name": f"eq.{owner}",
                "select": "data",
                "order": "updated_at.desc",
                "limit": str(page_size),
                "offset": str(offset),
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"{resp.status_code} {resp.text[:300]}")

        payload = _safe_json(resp)
        if not _is_list_response(payload):
            raise RuntimeError(f"Unexpected Supabase response shape: {str(payload)[:200]}")

        items = [row.get("data", {}) for row in payload if isinstance(row, dict)]
        _set_last_error(None)
        return _paged(items, total, page, page_size)

    except Exception as e:
        _set_last_error(f"Supabase unavailable ({e}) — showing locally saved drafts instead.")
        return _local_list(owner, page, page_size)


def _paged(items: list, total: int, page: int, page_size: int) -> dict:
    total_pages = max(1, (total + page_size - 1) // page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size, "total_pages": total_pages}


def delete_draft(draft_id: str) -> bool:
    owner = _owner_name()

    if not _using_supabase():
        all_drafts = _local_load_all()
        if draft_id in all_drafts.get(owner, {}):
            del all_drafts[owner][draft_id]
            _local_save_all(all_drafts)
            return True
        return False

    try:
        resp = requests.delete(
            _table_url(), headers=_headers(),
            params={"draft_id": f"eq.{draft_id}", "owner_name": f"eq.{owner}"},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"{resp.status_code} {resp.text[:300]}")
        return True
    except Exception as e:
        _set_last_error(f"Supabase delete failed ({e}).")
        all_drafts = _local_load_all()
        if draft_id in all_drafts.get(owner, {}):
            del all_drafts[owner][draft_id]
            _local_save_all(all_drafts)
            return True
        return False


def duplicate_draft(draft_id: str) -> Optional[str]:
    original = load_draft(draft_id)
    if not original:
        return None
    copy = dict(original)
    copy["title"] = f"{copy.get('title','Untitled')} (Copy)"
    copy.pop("draft_id", None)
    return save_draft(copy)


def count_drafts() -> int:
    return list_drafts(page=1, page_size=1)["total"]
