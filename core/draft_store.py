"""
draft_store.py — Per-user local draft storage. No Supabase required.

Every signed-in user is isolated by owner_name from Streamlit session:
- client_name for client logins
- "ceo" for CEO login
- "default" only as a last fallback

Data file: data/product_drafts_local.json
"""

import json
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import streamlit as st

DATA_DIR = Path("data")
DRAFT_FILE = DATA_DIR / "product_drafts_local.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner_name() -> str:
    client_name = st.session_state.get("client_name") or ""
    role = st.session_state.get("role") or ""
    if client_name:
        return client_name.strip()
    if role == "ceo":
        return "ceo"
    return "default"


def _ensure_file() -> None:
    DATA_DIR.mkdir(exist_ok=True)
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
    DRAFT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def get_last_error() -> Optional[str]:
    return None


def _paged(items: list, total: int, page: int, page_size: int) -> dict:
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    return {"items": items, "total": total, "page": page, "page_size": page_size, "total_pages": total_pages}


def save_draft(product: dict, draft_id: Optional[str] = None) -> str:
    owner = _owner_name()
    data = _load_all()
    data.setdefault(owner, {})

    if draft_id is None:
        draft_id = str(uuid.uuid4())[:8]

    now = _now_iso()
    existing = data[owner].get(draft_id, {})
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
    return _load_all().get(owner, {}).get(draft_id)


def list_drafts(page: int = 1, page_size: int = 20) -> dict:
    owner = _owner_name()
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 20))

    items = list(_load_all().get(owner, {}).values())
    items.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
    total = len(items)
    start = (page - 1) * page_size
    return _paged(items[start:start + page_size], total, page, page_size)


def delete_draft(draft_id: str) -> bool:
    owner = _owner_name()
    data = _load_all()
    if draft_id in data.get(owner, {}):
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
    return save_draft(copy)
