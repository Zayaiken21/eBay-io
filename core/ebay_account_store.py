import json
import sqlite3
from pathlib import Path
from typing import Any

import streamlit as st
from cryptography.fernet import Fernet

DATA_DIR = Path("data")
DB_FILE = DATA_DIR / "ebay_accounts.db"


def get_cipher() -> Fernet:
    key = st.secrets.get("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("Missing ENCRYPTION_KEY in .streamlit/secrets.toml")
    return Fernet(key.encode("utf-8"))


def init_ebay_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ebay_accounts (
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
            row[1] for row in conn.execute("PRAGMA table_info(ebay_accounts)").fetchall()
        }

        migrations = {
            "encrypted_access_token": "ALTER TABLE ebay_accounts ADD COLUMN encrypted_access_token TEXT",
            "encrypted_refresh_token": "ALTER TABLE ebay_accounts ADD COLUMN encrypted_refresh_token TEXT",
            "access_token_expires_in": "ALTER TABLE ebay_accounts ADD COLUMN access_token_expires_in INTEGER",
            "refresh_token_expires_in": "ALTER TABLE ebay_accounts ADD COLUMN refresh_token_expires_in INTEGER",
            "ebay_user_id": "ALTER TABLE ebay_accounts ADD COLUMN ebay_user_id TEXT",
            "ebay_username": "ALTER TABLE ebay_accounts ADD COLUMN ebay_username TEXT",
            "store_name": "ALTER TABLE ebay_accounts ADD COLUMN store_name TEXT",
            "profile_json": "ALTER TABLE ebay_accounts ADD COLUMN profile_json TEXT",
            "updated_at": "ALTER TABLE ebay_accounts ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        }

        for column, sql in migrations.items():
            if column not in existing_columns:
                conn.execute(sql)


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
    )


def _extract_store_name(profile: dict[str, Any]) -> str | None:
    return (
        profile.get("storeName")
        or profile.get("store_name")
        or profile.get("store", {}).get("name")
    )


def save_ebay_account(
    owner_name: str,
    role: str,
    environment: str,
    marketplace_id: str = "EBAY_US",
    user_access_token: str | None = None,
    token_data: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> None:
    """
    Saves either a modern OAuth connection or, for backward compatibility,
    a manually provided user_access_token.
    """
    init_ebay_db()

    token_data = token_data or {}
    profile = profile or {}

    access_token = token_data.get("access_token") or user_access_token
    refresh_token = token_data.get("refresh_token")

    ebay_username = _extract_username(profile)
    ebay_user_id = _extract_user_id(profile)
    store_name = _extract_store_name(profile)

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO ebay_accounts (
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
                owner_name,
                role,
                environment,
                marketplace_id,
                _encrypt_optional(access_token),
                _encrypt_optional(refresh_token),
                token_data.get("expires_in"),
                token_data.get("refresh_token_expires_in"),
                ebay_user_id,
                ebay_username,
                store_name,
                json.dumps(profile),
            ),
        )


def get_latest_ebay_account(owner_name: str) -> dict[str, Any] | None:
    init_ebay_db()

    with sqlite3.connect(DB_FILE) as conn:
        row = conn.execute(
            """
            SELECT
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
                created_at,
                updated_at
            FROM ebay_accounts
            WHERE owner_name = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (owner_name,),
        ).fetchone()

    if not row:
        return None

    profile = {}
    if row[11]:
        try:
            profile = json.loads(row[11])
        except json.JSONDecodeError:
            profile = {}

    return {
        "owner_name": row[0],
        "role": row[1],
        "environment": row[2],
        "marketplace_id": row[3],
        "access_token": _decrypt_optional(row[4]),
        "refresh_token": _decrypt_optional(row[5]),
        "access_token_expires_in": row[6],
        "refresh_token_expires_in": row[7],
        "ebay_user_id": row[8],
        "ebay_username": row[9],
        "store_name": row[10],
        "profile": profile,
        "created_at": row[12],
        "updated_at": row[13],
    }


def get_connected_ebay_label(owner_name: str) -> str:
    saved = get_latest_ebay_account(owner_name)
    if not saved:
        return "No eBay account connected"

    name = saved.get("store_name") or saved.get("ebay_username") or saved.get("ebay_user_id")
    if not name:
        name = "Connected eBay account"

    return f"{name} · {saved.get('environment', 'production')} · {saved.get('marketplace_id', 'EBAY_US')}"


def has_connected_ebay_account(owner_name: str) -> bool:
    return get_latest_ebay_account(owner_name) is not None


# Compatibility aliases in case other files use these names.
def save_connected_ebay_account(*args, **kwargs):
    return save_ebay_account(*args, **kwargs)


def get_latest_connected_ebay_account(*args, **kwargs):
    return get_latest_ebay_account(*args, **kwargs)

def get_connected_ebay_label(owner_name: str, role: str | None = None) -> str:
    account = get_latest_ebay_account(owner_name)

    if not account:
        return "No eBay account connected"

    username = (
        account.get("ebay_username")
        or account.get("store_name")
        or account.get("ebay_user_id")
        or "Connected eBay account"
    )

    environment = account.get("environment", "production")

    return f"{username} ({environment})"