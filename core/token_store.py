"""Supabase-backed Ebay IO client license store.

Expected table: public.client_licenses

Flow:
- CEO creates a short token in Settings.
- First-time client logs in with token as Username and blank Password.
- Client creates username/password.
- Client can reset username/password later using the original token.
- Deleting a client in CEO Settings removes the license row and that user's saved eBay accounts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import string
from datetime import datetime, timezone
from typing import Any

import requests
import streamlit as st

TABLE = "client_licenses"


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


def _headers(prefer: str | None = None) -> dict[str, str]:
    key = _supabase_key()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _table_url(table: str = TABLE) -> str:
    return f"{_supabase_url()}/rest/v1/{table}"


def _set_last_error(message: str | None) -> None:
    try:
        st.session_state["_token_store_error"] = message
    except Exception:
        pass


def get_last_error() -> str | None:
    try:
        return st.session_state.get("_token_store_error")
    except Exception:
        return None


def _require_supabase() -> None:
    if not _using_supabase():
        raise RuntimeError("Supabase is not configured. Add SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to Streamlit secrets.")


def _safe_json(response):
    try:
        return response.json()
    except Exception:
        return None


def _normalize(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("name") or row.get("client_name") or "Client").strip()
    token = str(row.get("token") or "").strip().upper()
    username = str(row.get("username") or token).strip()
    return {
        "id": row.get("id"),
        "name": name,
        "client_name": name,
        "token": token,
        "username": username,
        "active": bool(row.get("active", True)),
        "password_set": bool(row.get("password_set", False)),
        "created_at": row.get("created_at") or "",
        "password_updated_at": row.get("password_updated_at"),
        "username_updated_at": row.get("username_updated_at"),
    }


def _hash_password(password: str) -> str:
    password = str(password or "")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return "pbkdf2_sha256$200000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def _verify_password(password: str, stored: str | None) -> bool:
    if not password or not stored:
        return False
    try:
        algo, rounds, salt_b64, digest_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64.encode())
        expected = base64.b64decode(digest_b64.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _validate_username(username: str) -> str:
    username = str(username or "").strip()
    if len(username) < 3:
        raise RuntimeError("Username must be at least 3 characters.")
    if len(username) > 50:
        raise RuntimeError("Username must be 50 characters or less.")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(ch not in allowed for ch in username):
        raise RuntimeError("Username can only use letters, numbers, dots, underscores, and hyphens.")
    return username


def _validate_password(password: str) -> str:
    password = str(password or "")
    if len(password) < 6:
        raise RuntimeError("Password must be at least 6 characters.")
    return password


def _get_rows(params: dict[str, str]) -> list[dict[str, Any]]:
    _require_supabase()
    response = requests.get(_table_url(), headers=_headers(), params=params, timeout=30)
    if response.status_code >= 400:
        _set_last_error(f"Supabase token load failed: {response.status_code} {response.text}")
        raise RuntimeError(f"Supabase token load failed: {response.status_code} {response.text}")
    data = _safe_json(response)
    if not isinstance(data, list):
        raise RuntimeError("Unexpected Supabase response while loading client licenses.")
    _set_last_error(None)
    return data


def _find_by_token(token: str) -> dict[str, Any] | None:
    token = str(token or "").strip().upper()
    if not token:
        return None
    rows = _get_rows({"token": f"eq.{token}", "select": "*", "limit": "1"})
    return rows[0] if rows else None


def _find_by_username(username: str) -> dict[str, Any] | None:
    username = str(username or "").strip()
    if not username:
        return None
    rows = _get_rows({"username": f"eq.{username}", "active": "eq.true", "select": "*", "limit": "1"})
    if rows:
        return rows[0]
    # Case-insensitive fallback without relying on PostgREST ilike on every deploy.
    all_rows = _get_rows({"active": "eq.true", "select": "*"})
    for row in all_rows:
        if str(row.get("username") or "").strip().lower() == username.lower():
            return row
    return None


def generate_short_token(length: int = 5) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def load_tokens() -> list[dict]:
    rows = _get_rows({"select": "*", "order": "created_at.desc"})
    return [_normalize(row) for row in rows]


def create_token(client_name: str) -> dict:
    _require_supabase()
    client_name = (client_name or "").strip()
    if not client_name:
        raise RuntimeError("Client name is required.")

    existing = {item.get("token") for item in load_tokens()}
    token = generate_short_token()
    while token in existing:
        token = generate_short_token()

    now = _now_iso()
    row = {
        "name": client_name,
        "token": token,
        "username": token,
        "active": True,
        "password_set": False,
        "created_at": now,
    }
    response = requests.post(_table_url(), headers=_headers("return=representation"), json=row, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase token create failed: {response.status_code} {response.text}")
    payload = _safe_json(response)
    _set_last_error(None)
    return _normalize(payload[0] if isinstance(payload, list) and payload else row)


def _delete_ebay_accounts_for_owner_names(owner_names: list[str]) -> None:
    owner_names = [str(x or "").strip() for x in owner_names if str(x or "").strip()]
    if not owner_names:
        return
    # This keeps token/license deletion aligned with saved eBay accounts.
    for owner in sorted(set(owner_names), key=str.lower):
        try:
            requests.delete(
                _table_url("ebay_accounts"),
                headers=_headers(),
                params={"owner_name": f"eq.{owner}"},
                timeout=30,
            )
        except Exception:
            pass


def cancel_token(token: str) -> None:
    """Delete a client license and that client's saved eBay accounts from Supabase."""
    _require_supabase()
    row = _find_by_token(token)
    if not row:
        return
    norm = _normalize(row)
    response = requests.delete(_table_url(), headers=_headers(), params={"token": f"eq.{norm['token']}"}, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase token delete failed: {response.status_code} {response.text}")
    _delete_ebay_accounts_for_owner_names([norm.get("client_name"), norm.get("name"), norm.get("username"), norm.get("token")])
    _set_last_error(None)


def cancel_all_tokens() -> None:
    for item in load_tokens():
        cancel_token(item.get("token", ""))


def validate_client_token(token: str) -> dict | None:
    """Old token-only compatibility validation."""
    try:
        row = _find_by_token(token)
    except Exception:
        return None
    if not row or not bool(row.get("active", True)):
        return None
    return _normalize(row)


def validate_token(token: str) -> dict | None:
    return validate_client_token(token)


def validate_client_login(username_or_token: str, password: str = "") -> dict | None:
    username_or_token = str(username_or_token or "").strip()
    password = str(password or "")
    if not username_or_token:
        return None

    # First-time/recovery code path: token as username.
    token_row = _find_by_token(username_or_token)
    if token_row and bool(token_row.get("active", True)):
        norm = _normalize(token_row)
        if not token_row.get("password_set"):
            if password:
                return {**norm, "password_required": False, "login_ok": False}
            return {**norm, "requires_profile_setup": True, "requires_password_setup": True, "login_ok": False}
        # Token can still be used as recovery key, but not as normal passwordless login after setup.
        if not password and username_or_token.upper() == norm["token"]:
            return {**norm, "password_required": True, "login_ok": False}

    row = _find_by_username(username_or_token)
    if not row or not bool(row.get("active", True)):
        return None

    norm = _normalize(row)
    if not row.get("password_set"):
        if not password:
            return {**norm, "requires_profile_setup": True, "requires_password_setup": True, "login_ok": False}
        return None

    if not password:
        return {**norm, "password_required": True, "login_ok": False}

    if not _verify_password(password, row.get("password_hash")):
        return None

    return {**norm, "login_ok": True}


def set_client_profile(token: str, username: str, password: str) -> dict:
    _require_supabase()
    token = str(token or "").strip().upper()
    username = _validate_username(username)
    password = _validate_password(password)

    row = _find_by_token(token)
    if not row or not bool(row.get("active", True)):
        raise RuntimeError("Invalid or inactive access code.")

    existing = _find_by_username(username)
    if existing and str(existing.get("token") or "").upper() != token:
        raise RuntimeError("That username is already taken. Choose another username.")

    now = _now_iso()
    payload = {
        "username": username,
        "password_hash": _hash_password(password),
        "password_set": True,
        "password_updated_at": now,
        "username_updated_at": now,
    }
    response = requests.patch(_table_url(), headers=_headers("return=representation"), params={"token": f"eq.{token}"}, json=payload, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"Could not save client profile: {response.status_code} {response.text}")
    rows = _safe_json(response)
    _set_last_error(None)
    return _normalize(rows[0] if isinstance(rows, list) and rows else {**row, **payload})


def reset_client_profile_with_token(token: str, new_username: str | None = None, new_password: str | None = None) -> dict:
    _require_supabase()
    token = str(token or "").strip().upper()
    row = _find_by_token(token)
    if not row or not bool(row.get("active", True)):
        raise RuntimeError("Invalid or inactive access code.")

    payload: dict[str, Any] = {}
    now = _now_iso()
    if new_username is not None and str(new_username).strip():
        username = _validate_username(new_username)
        existing = _find_by_username(username)
        if existing and str(existing.get("token") or "").upper() != token:
            raise RuntimeError("That username is already taken. Choose another username.")
        payload["username"] = username
        payload["username_updated_at"] = now

    if new_password is not None and str(new_password):
        password = _validate_password(new_password)
        payload["password_hash"] = _hash_password(password)
        payload["password_set"] = True
        payload["password_updated_at"] = now

    if not payload:
        raise RuntimeError("Enter a new username, a new password, or both.")

    response = requests.patch(_table_url(), headers=_headers("return=representation"), params={"token": f"eq.{token}"}, json=payload, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"Could not reset client login: {response.status_code} {response.text}")
    rows = _safe_json(response)
    _set_last_error(None)
    return _normalize(rows[0] if isinstance(rows, list) and rows else {**row, **payload})


def reset_client_password_with_token(token: str, new_password: str) -> dict:
    return reset_client_profile_with_token(token, None, new_password)
