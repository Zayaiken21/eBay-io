"""
draft_store.py — Per-user draft storage backed by Supabase.

Why Supabase instead of a local JSON file:
  - Streamlit Cloud's filesystem is ephemeral and NOT shared across replicas.
    A local drafts.json means every user (and every container restart) sees
    different/empty data, and everyone who DOES share a replica sees each
    other's drafts. Supabase fixes both problems at once.
  - Drafts are scoped by owner_name, exactly like ebay_accounts, so each
    logged-in user (client_name from session.py, or "ceo") only ever sees
    their own drafts.

Table expected in Supabase — `product_drafts`:
    id              bigint, primary key, identity
    draft_id        text, unique
    owner_name      text
    data            jsonb        -- the full product dict
    status          text
    created_at      timestamptz
    updated_at      timestamptz

No row-count cap is enforced anywhere in this module — Postgres/Supabase
has no practical limit on the number of rows per owner.
"""

import json
import uuid
import requests
import streamlit as st
from datetime import datetime, timezone
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _supabase_url() -> str:
    return str(st.secrets.get("SUPABASE_URL", "")).rstrip("/")

def _supabase_key() -> str:
    return str(
        st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")
        or st.secrets.get("SUPABASE_SECRET_KEY")
        or ""
    )

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
    """
    Resolve the current logged-in user — matches session.py exactly.
    session.py sets client_name (client login) or role (ceo login).
    """
    client_name = st.session_state.get("client_name") or ""
    role        = st.session_state.get("role") or ""
    if client_name:
        return client_name.strip()
    if role == "ceo":
        return "ceo"
    return "default"


# ── Local-file fallback (only used if Supabase isn't configured) ─────────
# Kept ONLY for local dev convenience when secrets.toml has no Supabase
# credentials yet. Per-user isolation is NOT guaranteed in this mode.
import os
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
    with open(_LOCAL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Public API ─────────────────────────────────────────────────────────

def save_draft(product: dict, draft_id: Optional[str] = None) -> str:
    """Create or update a draft, scoped to the current logged-in owner."""
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
        all_drafts = _local_load_all()
        all_drafts.setdefault(owner, {})
        product["created_at"] = all_drafts[owner].get(draft_id, {}).get("created_at", now)
        all_drafts[owner][draft_id] = product
        _local_save_all(all_drafts)
        return draft_id

    # Check if a row already exists for this draft_id + owner
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
            _table_url(),
            headers=_headers(),
            params={"draft_id": f"eq.{draft_id}", "owner_name": f"eq.{owner}"},
            data=json.dumps(row),
            timeout=30,
        )
    else:
        row["created_at"] = now
        resp = requests.post(
            _table_url(),
            headers=_headers("return=representation"),
            data=json.dumps(row),
            timeout=30,
        )

    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase draft save failed: {resp.status_code} {resp.text}")

    return draft_id


def _get_row(draft_id: str, owner: str) -> Optional[dict]:
    resp = requests.get(
        _table_url(),
        headers=_headers(),
        params={"draft_id": f"eq.{draft_id}", "owner_name": f"eq.{owner}", "select": "id", "limit": "1"},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase lookup failed: {resp.status_code} {resp.text}")
    rows = resp.json()
    return rows[0] if rows else None


def load_draft(draft_id: str) -> Optional[dict]:
    """Load a single draft — only returns it if it belongs to the current owner."""
    owner = _owner_name()

    if not _using_supabase():
        return _local_load_all().get(owner, {}).get(draft_id)

    resp = requests.get(
        _table_url(),
        headers=_headers(),
        params={"draft_id": f"eq.{draft_id}", "owner_name": f"eq.{owner}", "select": "data", "limit": "1"},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase load failed: {resp.status_code} {resp.text}")
    rows = resp.json()
    return rows[0]["data"] if rows else None


def list_drafts(page: int = 1, page_size: int = 20) -> dict:
    """
    Returns drafts belonging ONLY to the current logged-in owner, paginated.

    Returns:
        {"items": [product, ...], "total": int, "page": int, "page_size": int, "total_pages": int}

    No cap on total drafts — page_size only controls how many are shown per page.
    """
    owner  = _owner_name()
    offset = (page - 1) * page_size

    if not _using_supabase():
        all_for_owner = list(_local_load_all().get(owner, {}).values())
        all_for_owner.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
        total = len(all_for_owner)
        items = all_for_owner[offset: offset + page_size]
        return _paged(items, total, page, page_size)

    # Get total count first
    count_resp = requests.get(
        _table_url(),
        headers={**_headers(), "Prefer": "count=exact"},
        params={"owner_name": f"eq.{owner}", "select": "id", "limit": "1"},
        timeout=30,
    )
    total = 0
    if count_resp.status_code < 400:
        content_range = count_resp.headers.get("content-range", "")
        if "/" in content_range:
            try:
                total = int(content_range.split("/")[-1])
            except ValueError:
                total = 0

    resp = requests.get(
        _table_url(),
        headers=_headers(),
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
        raise RuntimeError(f"Supabase list failed: {resp.status_code} {resp.text}")

    items = [row["data"] for row in resp.json()]
    return _paged(items, total, page, page_size)


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

    resp = requests.delete(
        _table_url(),
        headers=_headers(),
        params={"draft_id": f"eq.{draft_id}", "owner_name": f"eq.{owner}"},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase delete failed: {resp.status_code} {resp.text}")
    return True


def duplicate_draft(draft_id: str) -> Optional[str]:
    original = load_draft(draft_id)
    if not original:
        return None
    copy = dict(original)
    copy["title"] = f"{copy.get('title','Untitled')} (Copy)"
    copy.pop("draft_id", None)
    return save_draft(copy)


def count_drafts() -> int:
    """Total draft count for current owner — no cap, just informational."""
    result = list_drafts(page=1, page_size=1)
    return result["total"]
