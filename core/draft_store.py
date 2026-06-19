"""
draft_store.py — Per-user local draft storage for eBay IO.

Supabase is NOT used for product drafts.
Supabase remains for:
- client_licenses / login
- ebay_accounts / connected eBay OAuth accounts

Drafts are stored locally in:
    data/product_drafts_local.json

Isolation rule:
- Prefer a stable license/client token when available.
- Fall back to client_name / username / CEO only when no token exists.
- Each user reads/writes only their own bucket inside the JSON file.

This keeps drafts separated between CEO and clients and avoids storing large
product JSON/images/descriptions in Supabase.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import streamlit as st

DATA_DIR = Path("data")
DRAFT_FILE = DATA_DIR / "product_drafts_local.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_key(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"[^A-Za-z0-9_.@-]+", "_", value)
    return value[:120] or "default"


def _owner_name() -> str:
    """
    Resolve the current app user for local draft isolation.

    New login systems often store the client/user in different session keys.
    This supports the common keys from both eBay IO and Dropz-style auth.
    """
    role = str(st.session_state.get("role") or "").strip().lower()

    # Dropz-style login may store the whole user dict.
    user = st.session_state.get("user") or {}
    if isinstance(user, dict):
        token = user.get("token") or user.get("license_token") or user.get("client_token")
        if token:
            return "token_" + _clean_key(token).upper()

        username = user.get("username")
        if username:
            return "user_" + _clean_key(username).lower()

        name = user.get("name") or user.get("client_name")
        if name:
            return "name_" + _clean_key(name).lower()

    # eBay IO style session keys.
    for key in ("client_token", "license_token", "token"):
        value = st.session_state.get(key)
        if value:
            return "token_" + _clean_key(value).upper()

    for key in ("client_name", "owner_name", "username"):
        value = st.session_state.get(key)
        if value:
            normalized = _clean_key(value)
            if normalized.lower() == "ceo":
                return "ceo"
            return f"{key}_" + normalized.lower()

    if role == "ceo":
        return "ceo"

    return "default"


def _ensure_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DRAFT_FILE.exists():
        DRAFT_FILE.write_text("{}", encoding="utf-8")


def _load_all() -> dict:
    _ensure_file()
    try:
        data = json.loads(DRAFT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        DRAFT_FILE.write_text("{}", encoding="utf-8")
        return {}


def _save_all(data: dict) -> None:
    _ensure_file()
    tmp = DRAFT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(DRAFT_FILE)


def get_last_error() -> Optional[str]:
    return None


def _paged(items: list[dict], total: int, page: int, page_size: int) -> dict:
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 20))
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def save_draft(product: dict, draft_id: Optional[str] = None) -> str:
    owner = _owner_name()
    data = _load_all()
    data.setdefault(owner, {})

    if not draft_id:
        draft_id = str(uuid.uuid4())[:8]

    now = _now_iso()
    existing = data[owner].get(draft_id, {}) if isinstance(data.get(owner), dict) else {}

    product = dict(product or {})
    product["draft_id"] = draft_id
    product["owner_name"] = owner
    product["created_at"] = existing.get("created_at") or product.get("created_at") or now
    product["updated_at"] = now
    product.setdefault("status", "draft")

    data[owner][draft_id] = product
    _save_all(data)
    return draft_id


def load_draft(draft_id: str) -> Optional[dict]:
    owner = _owner_name()
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        return None
    draft = _load_all().get(owner, {}).get(draft_id)
    return dict(draft) if isinstance(draft, dict) else None


def list_drafts(page: int = 1, page_size: int = 20) -> dict:
    owner = _owner_name()
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 20))

    items = list((_load_all().get(owner, {}) or {}).values())
    items = [dict(item) for item in items if isinstance(item, dict)]
    items.sort(key=lambda d: d.get("updated_at", ""), reverse=True)

    total = len(items)
    start = (page - 1) * page_size
    return _paged(items[start:start + page_size], total, page, page_size)


def delete_draft(draft_id: str) -> bool:
    owner = _owner_name()
    draft_id = str(draft_id or "").strip()
    data = _load_all()
    if draft_id and draft_id in data.get(owner, {}):
        del data[owner][draft_id]
        _save_all(data)
        return True
    return False


def duplicate_draft(draft_id: str) -> Optional[str]:
    src = load_draft(draft_id)
    if not src:
        return None

    copy = dict(src)
    copy.pop("draft_id", None)
    copy["title"] = f"{copy.get('title', 'Untitled')} (Copy)"
    copy["status"] = "draft"
    copy.pop("ebay_listing_id", None)
    copy.pop("ebay_listing_url", None)
    copy.pop("_from_ebay_sku", None)
    copy.pop("_edit_via_trading", None)
    return save_draft(copy)
