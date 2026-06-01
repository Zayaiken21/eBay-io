"""
draft_store.py — Persistent draft storage using a local JSON file.
Drafts are saved to drafts.json in the working directory.
"""

import json
import os
import uuid
from datetime import datetime
from typing import Optional

DRAFTS_FILE = "drafts.json"


def _load_all() -> dict:
    if not os.path.exists(DRAFTS_FILE):
        return {}
    try:
        with open(DRAFTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_all(data: dict) -> None:
    with open(DRAFTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_draft(product: dict, draft_id: Optional[str] = None) -> str:
    """
    Save or update a draft. Returns the draft_id.
    If draft_id is None, creates a new draft with a generated ID.
    """
    all_drafts = _load_all()

    if draft_id is None:
        draft_id = str(uuid.uuid4())[:8]

    product = dict(product)  # shallow copy
    product["draft_id"] = draft_id
    product["updated_at"] = datetime.now().isoformat()

    if draft_id not in all_drafts:
        product["created_at"] = product["updated_at"]
    else:
        product["created_at"] = all_drafts[draft_id].get("created_at", product["updated_at"])

    all_drafts[draft_id] = product
    _save_all(all_drafts)
    return draft_id


def load_draft(draft_id: str) -> Optional[dict]:
    """Load a single draft by ID. Returns None if not found."""
    return _load_all().get(draft_id)


def list_drafts() -> list[dict]:
    """Return all drafts sorted by updated_at descending."""
    all_drafts = _load_all()
    drafts = list(all_drafts.values())
    drafts.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
    return drafts


def delete_draft(draft_id: str) -> bool:
    """Delete a draft. Returns True if deleted, False if not found."""
    all_drafts = _load_all()
    if draft_id in all_drafts:
        del all_drafts[draft_id]
        _save_all(all_drafts)
        return True
    return False


def duplicate_draft(draft_id: str) -> Optional[str]:
    """Duplicate a draft and return the new ID."""
    original = load_draft(draft_id)
    if not original:
        return None
    copy = dict(original)
    copy["title"] = f"{copy.get('title', 'Untitled')} (Copy)"
    copy.pop("draft_id", None)
    return save_draft(copy)
