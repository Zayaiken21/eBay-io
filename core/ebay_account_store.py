import json
import sqlite3
from pathlib import Path
from typing import Any

import requests
import streamlit as st
from cryptography.fernet import Fernet


DATA_DIR = Path("data")
DB_FILE = DATA_DIR / "ebay_accounts.db"
TABLE_NAME = "ebay_accounts"


def _secret(name: str, default: str | None = None) -> str | None:
    value = st.secrets.get(name, default)
    if value is None:
        return None
    return str(value)


def _use_supabase() -> bool:
    return bool(_secret("SUPABASE_URL") and _secret("SUPABASE_SERVICE_ROLE_KEY"))


def get_cipher() -> Fernet:
    key = _secret("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("Missing ENCRYPTION_KEY in Streamlit secrets.")
    return Fernet(key.encode("utf-8"))


def _encrypt_optional(value: str | None) -> str | None:
    if not value:
        return None
    return get_cipher().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt_optional(value: str | None) -> str | None:
    if not value:
        return None
    return get_cipher().decrypt(value.encode("utf-8")).decode("utf-8")


def _extract_username(profile: dict[str, Any]) -> str | None:
    return (
        profile.get("username")
        or profile.get("userName")
        or profile.get("user_name")
        or profile.get("account", {}).get("username")
    )


def _extract_user_id(profile: dict[str, Any]) -> str | None:
    return (
        profile.get("userId")
        or profile.get("user_id")
        or profile.get("legacyUserId")
        or profile.get("account", {}).get("userId")
        or profile.get("account", {}).get("legacyUserId")
    )


def _extract_store_name(profile: dict[str, Any]) -> str | None:
    return (
        profile.get("storeName")
        or profile.get("store_name")
        or profile.get("store", {}).get("name")
        or profile.get("account", {}).get("storeName")
    )


def _supabase_headers() -> dict[str, str]:
    key = _secret("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError("Missing SUPABASE_SERVICE_ROLE_KEY in Streamlit secrets.")

    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation",
    }


def _supabase_url(path: str) -> str:
    base = (_secret("SUPABASE_URL") or "").rstrip("/")
    if not base:
        raise RuntimeError("Missing SUPABASE_URL in Streamlit secrets.")
    return f"{base}/rest/v1/{path.lstrip('/')}"


def init_ebay_db() -> None:
    if _use_supabase():
        return

    DATA_DIR.mkdir(exist_ok=True)

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_name TEXT NOT NULL,
                role TEXT NOT NULL,
                environment TEXT NOT NULL,
                marketplace_id TEXT NOT NULL,
                encrypted_access_token TEXT,
                encrypted_refresh_token TEXT,
                access_token_expires_in INTEGER,
                refresh_token_expires_in INTEGER,
                ebay_user_id TEXT,
                ebay_username TEXT,
                store_name TEXT,
                profile_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        existing_columns = {
            row[1] for row in conn.execute(f"PRAGMA table_info({TABLE_NAME})").fetchall()
        }

        migrations = {
            "encrypted_access_token": f"ALTER TABLE {TABLE_NAME} ADD COLUMN encrypted_access_token TEXT",
            "encrypted_refresh_token": f"ALTER TABLE {TABLE_NAME} ADD COLUMN encrypted_refresh_token TEXT",
            "access_token_expires_in": f"ALTER TABLE {TABLE_NAME} ADD COLUMN access_token_expires_in INTEGER",
            "refresh_token_expires_in": f"ALTER TABLE {TABLE_NAME} ADD COLUMN refresh_token_expires_in INTEGER",
            "ebay_user_id": f"ALTER TABLE {TABLE_NAME} ADD COLUMN ebay_user_id TEXT",
            "ebay_username": f"ALTER TABLE {TABLE_NAME} ADD COLUMN ebay_username TEXT",
            "store_name": f"ALTER TABLE {TABLE_NAME} ADD COLUMN store_name TEXT",
            "profile_json": f"ALTER TABLE {TABLE_NAME} ADD COLUMN profile_json TEXT",
            "updated_at": f"ALTER TABLE {TABLE_NAME} ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        }

        for column, sql in migrations.items():
            if column not in existing_columns:
                conn.execute(sql)


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    profile_json = record.get("profile_json")
    profile: dict[str, Any] = {}

    if isinstance(profile_json, str) and profile_json:
        try:
            profile = json.loads(profile_json)
        except json.JSONDecodeError:
            profile = {}
    elif isinstance(profile_json, dict):
        profile = profile_json

    normalized = dict(record)
    normalized["profile"] = profile
    normalized["access_token"] = _decrypt_optional(record.get("encrypted_access_token"))
    normalized["refresh_token"] = _decrypt_optional(record.get("encrypted_refresh_token"))

    # Do not leak encrypted columns to UI callers.
    normalized.pop("encrypted_access_token", None)
    normalized.pop("encrypted_refresh_token", None)
    normalized.pop("profile_json", None)

    return normalized


def save_ebay_account(
    owner_name: str,
    role: str,
    environment: str,
    marketplace_id: str = "EBAY_US",
    user_access_token: str | None = None,
    token_data: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    ebay_user: dict[str, Any] | None = None,
) -> None:
    """
    Save an eBay OAuth connection.

    token_data should be the response from eBay's OAuth token endpoint.
    user_access_token is kept only for backwards compatibility and should not be used by the UI.
    """
    init_ebay_db()

    token_data = token_data or {}
    profile = profile or ebay_user or {}

    access_token = token_data.get("access_token") or user_access_token
    refresh_token = token_data.get("refresh_token")

    record = {
        "owner_name": owner_name or "Unknown",
        "role": (role or "CLIENT").upper(),
        "environment": (environment or "production").lower(),
        "marketplace_id": marketplace_id or "EBAY_US",
        "encrypted_access_token": _encrypt_optional(access_token),
        "encrypted_refresh_token": _encrypt_optional(refresh_token),
        "access_token_expires_in": token_data.get("expires_in"),
        "refresh_token_expires_in": token_data.get("refresh_token_expires_in"),
        "ebay_user_id": _extract_user_id(profile),
        "ebay_username": _extract_username(profile),
        "store_name": _extract_store_name(profile),
        "profile_json": json.dumps(profile),
    }

    if _use_supabase():
        response = requests.post(
            _supabase_url(TABLE_NAME),
            headers=_supabase_headers(),
            json=record,
            timeout=30,
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(f"Supabase save failed: {response.status_code} {response.text}")
        return

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            f"""
            INSERT INTO {TABLE_NAME} (
                owner_name,
                role,
                environment,
                marketplace_id,
                encrypted_access_token,
                encrypted_refresh_token,
                access_token_expires_in,
                refresh_token_expires_in,
                ebay_user_id,
                ebay_username,
                store_name,
                profile_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                record["owner_name"],
                record["role"],
                record["environment"],
                record["marketplace_id"],
                record["encrypted_access_token"],
                record["encrypted_refresh_token"],
                record["access_token_expires_in"],
                record["refresh_token_expires_in"],
                record["ebay_user_id"],
                record["ebay_username"],
                record["store_name"],
                record["profile_json"],
            ),
        )


def get_latest_ebay_account(owner_name: str) -> dict[str, Any] | None:
    init_ebay_db()

    if _use_supabase():
        response = requests.get(
            _supabase_url(
                f"{TABLE_NAME}?owner_name=eq.{requests.utils.quote(owner_name or 'Unknown')}"
                "&order=created_at.desc&limit=1"
            ),
            headers=_supabase_headers(),
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Supabase lookup failed: {response.status_code} {response.text}")

        rows = response.json()
        if not rows:
            return None
        return _normalize_record(rows[0])

    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"""
            SELECT *
            FROM {TABLE_NAME}
            WHERE owner_name = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (owner_name or "Unknown",),
        ).fetchone()

    if not row:
        return None

    return _normalize_record(dict(row))


def get_connected_ebay_label(owner_name: str, role: str | None = None) -> str:
    account = get_latest_ebay_account(owner_name)

    if not account:
        return "No eBay account connected"

    username = (
        account.get("store_name")
        or account.get("ebay_username")
        or account.get("ebay_user_id")
        or "Connected eBay account"
    )
    environment = account.get("environment", "production")
    marketplace_id = account.get("marketplace_id", "EBAY_US")

    return f"{username} ({environment}, {marketplace_id})"


def has_connected_ebay_account(owner_name: str) -> bool:
    return get_latest_ebay_account(owner_name) is not None


# Compatibility aliases for any older imports in the project.
def save_connected_ebay_account(*args, **kwargs):
    return save_ebay_account(*args, **kwargs)


def get_latest_connected_ebay_account(*args, **kwargs):
    return get_latest_ebay_account(*args, **kwargs)
