"""Supabase-backed client token store with safe fallback/error handling.

Expected Supabase table: public.client_licenses
Columns supported by this app:
  id bigint identity primary key
  name text not null
  token text unique not null
  active boolean default true
  created_at timestamptz default now()

This module also tries CLIENT_TOKENS_TABLE, client_tokens, and tokens so older
projects do not break if the table was named differently.
"""

import json
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
import streamlit as st

DATA_DIR = Path("data")
LOCAL_TOKEN_FILE = DATA_DIR / "client_tokens_local_fallback.json"
DEFAULT_TABLES = ["client_licenses", "client_tokens", "tokens"]


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
            or st.secrets.get("SUPABASE_ANON_KEY")
            or ""
        )
    except Exception:
        return ""


def _using_supabase() -> bool:
    return bool(_supabase_url() and _supabase_key())


def _headers(prefer: Optional[str] = None) -> dict:
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


def _candidate_tables() -> list[str]:
    configured = ""
    try:
        configured = str(st.secrets.get("CLIENT_TOKENS_TABLE", "")).strip()
    except Exception:
        pass
    tables = []
    if configured:
        tables.append(configured)
    for table in DEFAULT_TABLES:
        if table not in tables:
            tables.append(table)
    return tables


def _table_url(table: str) -> str:
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


def _normalize(row: dict) -> dict:
    client_name = (
        row.get("client_name")
        or row.get("name")
        or row.get("client")
        or row.get("owner_name")
        or "Client"
    )
    token = row.get("token") or row.get("client_token") or row.get("license_token") or ""
    return {
        "id": row.get("id"),
        "client_name": str(client_name),
        "name": str(client_name),
        "token": str(token).strip(),
        "active": bool(row.get("active", True)),
        "created_at": row.get("created_at") or row.get("created") or "",
    }


def _safe_json(response):
    try:
        return response.json()
    except Exception:
        return None


def _find_working_table() -> str | None:
    if not _using_supabase():
        _set_last_error("Supabase is not configured, so tokens are using local fallback only.")
        return None

    for table in _candidate_tables():
        try:
            response = requests.get(
                _table_url(table),
                headers=_headers(),
                params={"select": "*", "limit": "1"},
                timeout=20,
            )
            if response.status_code < 400:
                _set_last_error(None)
                return table
            if response.status_code == 404:
                continue
            raise RuntimeError(f"{response.status_code} {response.text}")
        except Exception as exc:
            _set_last_error(f"Supabase token table check failed: {exc}")
            return None

    _set_last_error(
        "Supabase token table not found. Create public.client_licenses using the SQL file included with this fix."
    )
    return None


# ── Local fallback only prevents crashes. Supabase remains the source of truth.
def _ensure_local() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if not LOCAL_TOKEN_FILE.exists():
        LOCAL_TOKEN_FILE.write_text("[]", encoding="utf-8")


def _load_local() -> list[dict]:
    _ensure_local()
    try:
        rows = json.loads(LOCAL_TOKEN_FILE.read_text(encoding="utf-8"))
        return [_normalize(row) for row in rows if isinstance(row, dict)]
    except Exception:
        LOCAL_TOKEN_FILE.write_text("[]", encoding="utf-8")
        return []


def _save_local(tokens: list[dict]) -> None:
    _ensure_local()
    LOCAL_TOKEN_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def generate_short_token(length: int = 5) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def load_tokens() -> list[dict]:
    table = _find_working_table()
    if not table:
        return _load_local()

    response = requests.get(
        _table_url(table),
        headers=_headers(),
        params={"select": "*", "order": "created_at.desc"},
        timeout=30,
    )
    if response.status_code >= 400:
        _set_last_error(f"Supabase token load failed: {response.status_code} {response.text}")
        return _load_local()

    payload = _safe_json(response)
    if not isinstance(payload, list):
        _set_last_error(f"Unexpected Supabase token response: {payload}")
        return _load_local()

    tokens = [_normalize(row) for row in payload if isinstance(row, dict)]
    _save_local(tokens)
    _set_last_error(None)
    return tokens


def create_token(client_name: str) -> dict:
    client_name = (client_name or "").strip()
    if not client_name:
        raise RuntimeError("Client name is required")

    existing = {item.get("token") for item in load_tokens()}
    token = generate_short_token()
    while token in existing:
        token = generate_short_token()

    token_data = {
        "client_name": client_name,
        "name": client_name,
        "token": token,
        "active": True,
        "created_at": _now_iso(),
    }

    table = _find_working_table()
    if not table:
        tokens = _load_local()
        tokens.append(token_data)
        _save_local(tokens)
        return token_data

    # Use the schema from the SQL file: name/token/active/created_at.
    row = {
        "name": client_name,
        "token": token,
        "active": True,
        "created_at": token_data["created_at"],
    }
    response = requests.post(
        _table_url(table),
        headers=_headers("return=representation"),
        json=row,
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase token create failed: {response.status_code} {response.text}")

    payload = _safe_json(response)
    if isinstance(payload, list) and payload:
        token_data = _normalize(payload[0])
    _set_last_error(None)
    return token_data


def cancel_token(token: str) -> None:
    token = str(token or "").strip()
    if not token:
        return

    table = _find_working_table()
    if not table:
        tokens = _load_local()
        for item in tokens:
            if item.get("token") == token:
                item["active"] = False
        _save_local(tokens)
        return

    response = requests.patch(
        _table_url(table),
        headers=_headers(),
        params={"token": f"eq.{token}"},
        json={"active": False},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase token cancel failed: {response.status_code} {response.text}")
    _set_last_error(None)


def cancel_all_tokens() -> None:
    table = _find_working_table()
    if not table:
        tokens = _load_local()
        for item in tokens:
            item["active"] = False
        _save_local(tokens)
        return

    response = requests.patch(
        _table_url(table),
        headers=_headers(),
        params={"active": "eq.true"},
        json={"active": False},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase cancel all tokens failed: {response.status_code} {response.text}")
    _set_last_error(None)


def validate_client_token(token: str) -> dict | None:
    token = str(token or "").strip()
    if not token:
        return None

    table = _find_working_table()
    if not table:
        for item in _load_local():
            if item.get("token") == token and item.get("active") is True:
                return item
        return None

    response = requests.get(
        _table_url(table),
        headers=_headers(),
        params={"token": f"eq.{token}", "active": "eq.true", "select": "*", "limit": "1"},
        timeout=30,
    )
    if response.status_code >= 400:
        _set_last_error(f"Supabase token validate failed: {response.status_code} {response.text}")
        return None

    rows = _safe_json(response)
    if isinstance(rows, list) and rows:
        return _normalize(rows[0])
    return None
