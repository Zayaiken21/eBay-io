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


def _supabase_key() -> str | None:
    # Supports both old Supabase service_role key name and the newer secret key naming.
    return (
        _secret("SUPABASE_SERVICE_ROLE_KEY")
        or _secret("SUPABASE_SECRET_KEY")
        or _secret("SUPABASE_SERVICE_KEY")
    )


def _use_supabase() -> bool:
    return bool(_secret("SUPABASE_URL") and _supabase_key())


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


def _supabase_headers(prefer: str = "return=representation") -> dict[str, str]:
    key = _supabase_key()
    if not key:
        raise RuntimeError("Missing SUPABASE_SERVICE_ROLE_KEY or SUPABASE_SECRET_KEY in Streamlit secrets.")

    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": prefer,
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
                encrypted_user_token TEXT,
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
            "encrypted_user_token": f"ALTER TABLE {TABLE_NAME} ADD COLUMN encrypted_user_token TEXT",
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

    encrypted_access_token = record.get("encrypted_access_token") or record.get("encrypted_user_token")
    encrypted_refresh_token = record.get("encrypted_refresh_token")

    return {
        "id": record.get("id"),
        "owner_name": record.get("owner_name"),
        "role": record.get("role"),
        "environment": record.get("environment"),
        "marketplace_id": record.get("marketplace_id") or "EBAY_US",
        "access_token": _decrypt_optional(encrypted_access_token),
        "refresh_token": _decrypt_optional(encrypted_refresh_token),
        "access_token_expires_in": record.get("access_token_expires_in"),
        "refresh_token_expires_in": record.get("refresh_token_expires_in"),
        "ebay_user_id": record.get("ebay_user_id"),
        "ebay_username": record.get("ebay_username"),
        "store_name": record.get("store_name"),
        "profile": profile,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def disconnect_ebay_account(owner_name: str) -> None:
    """Remove the saved eBay account for this owner. The app supports one connected account at a time."""
    init_ebay_db()

    if _use_supabase():
        response = requests.delete(
            _supabase_url(f"{TABLE_NAME}?owner_name=eq.{owner_name}"),
            headers=_supabase_headers(prefer="return=minimal"),
            timeout=30,
        )
        if response.status_code not in (200, 204):
            raise RuntimeError(f"Supabase disconnect failed: {response.status_code} {response.text}")
        return

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(f"DELETE FROM {TABLE_NAME} WHERE owner_name = ?", (owner_name,))


def save_ebay_account(
    owner_name: str,
    role: str,
    environment: str,
    marketplace_id: str = "EBAY_US",
    token_data: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    ebay_user: dict[str, Any] | None = None,
    user_access_token: str | None = None,
) -> dict[str, Any] | None:
    """
    Save one active eBay OAuth connection per owner.

    Supports the old manual-token argument for compatibility, but production OAuth should pass token_data/profile.
    """
    init_ebay_db()

    token_data = token_data or {}
    profile = profile or ebay_user or {}

    access_token = token_data.get("access_token") or user_access_token
    refresh_token = token_data.get("refresh_token")

    ebay_user_id = _extract_user_id(profile)
    ebay_username = _extract_username(profile)
    store_name = _extract_store_name(profile)

    record = {
        "owner_name": owner_name or "Unknown",
        "role": (role or "CLIENT").upper(),
        "environment": (environment or "production").lower(),
        "marketplace_id": marketplace_id or "EBAY_US",
        "encrypted_access_token": _encrypt_optional(access_token),
        "encrypted_refresh_token": _encrypt_optional(refresh_token),
        "access_token_expires_in": token_data.get("expires_in"),
        "refresh_token_expires_in": token_data.get("refresh_token_expires_in"),
        "ebay_user_id": ebay_user_id,
        "ebay_username": ebay_username,
        "store_name": store_name,
        "profile_json": json.dumps(profile),
    }

    # One connected account per owner. This prevents old rows from being displayed.
    disconnect_ebay_account(record["owner_name"])

    if _use_supabase():
        response = requests.post(
            _supabase_url(TABLE_NAME),
            headers=_supabase_headers(),
            json=record,
            timeout=30,
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(f"Supabase save failed: {response.status_code} {response.text}")
        data = response.json()
        return _normalize_record(data[0]) if data else None

    with sqlite3.connect(DB_FILE) as conn:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE_NAME})").fetchall()}
        sqlite_record = dict(record)
        if "encrypted_user_token" in columns:
            sqlite_record["encrypted_user_token"] = record["encrypted_access_token"]

        insert_columns = [column for column in sqlite_record if column in columns]
        placeholders = ", ".join("?" for _ in insert_columns)
        sql = f"""
            INSERT INTO {TABLE_NAME} ({", ".join(insert_columns)})
            VALUES ({placeholders})
        """
        conn.execute(sql, tuple(sqlite_record[column] for column in insert_columns))

    return get_latest_ebay_account(record["owner_name"])


def get_latest_ebay_account(owner_name: str) -> dict[str, Any] | None:
    init_ebay_db()

    if _use_supabase():
        response = requests.get(
            _supabase_url(
                f"{TABLE_NAME}?owner_name=eq.{owner_name}&select=*&order=id.desc&limit=1"
            ),
            headers=_supabase_headers(),
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Supabase load failed: {response.status_code} {response.text}")
        rows = response.json()
        return _normalize_record(rows[0]) if rows else None

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
            (owner_name,),
        ).fetchone()

    if not row:
        return None

    return _normalize_record(dict(row))


def update_ebay_tokens(
    owner_name: str,
    access_token: str,
    access_token_expires_in: int | None = None,
    refresh_token: str | None = None,
    refresh_token_expires_in: int | None = None,
) -> None:
    saved = get_latest_ebay_account(owner_name)
    if not saved:
        raise RuntimeError("No connected eBay account found to update.")

    encrypted_access_token = _encrypt_optional(access_token)
    encrypted_refresh_token = _encrypt_optional(refresh_token) if refresh_token else None

    if _use_supabase():
        patch: dict[str, Any] = {
            "encrypted_access_token": encrypted_access_token,
            "access_token_expires_in": access_token_expires_in,
            "updated_at": "now()",
        }
        if encrypted_refresh_token:
            patch["encrypted_refresh_token"] = encrypted_refresh_token
        if refresh_token_expires_in:
            patch["refresh_token_expires_in"] = refresh_token_expires_in

        response = requests.patch(
            _supabase_url(f"{TABLE_NAME}?id=eq.{saved['id']}"),
            headers=_supabase_headers(prefer="return=minimal"),
            json=patch,
            timeout=30,
        )
        if response.status_code not in (200, 204):
            raise RuntimeError(f"Supabase token update failed: {response.status_code} {response.text}")
        return

    with sqlite3.connect(DB_FILE) as conn:
        if encrypted_refresh_token:
            conn.execute(
                f"""
                UPDATE {TABLE_NAME}
                SET encrypted_access_token = ?,
                    encrypted_refresh_token = ?,
                    access_token_expires_in = ?,
                    refresh_token_expires_in = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    encrypted_access_token,
                    encrypted_refresh_token,
                    access_token_expires_in,
                    refresh_token_expires_in,
                    saved["id"],
                ),
            )
        else:
            conn.execute(
                f"""
                UPDATE {TABLE_NAME}
                SET encrypted_access_token = ?,
                    access_token_expires_in = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (encrypted_access_token, access_token_expires_in, saved["id"]),
            )


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


def get_ebay_api_context(owner_name: str) -> dict[str, Any]:
    """
    Reusable helper for other app functions.

    Returns a fresh access token and eBay API base URL for the connected owner.
    Call this before making Inventory, Fulfillment, Account, Marketing, etc. API calls.
    """
    from core.ebay_oauth import get_ebay_config, refresh_access_token

    account = get_latest_ebay_account(owner_name)
    if not account:
        raise RuntimeError("No eBay account connected. Connect eBay in Settings first.")

    refresh_token = account.get("refresh_token")
    if refresh_token:
        refreshed = refresh_access_token(refresh_token, account["environment"])
        access_token = refreshed.get("access_token")
        if access_token:
            update_ebay_tokens(
                owner_name=owner_name,
                access_token=access_token,
                access_token_expires_in=refreshed.get("expires_in"),
                refresh_token=refreshed.get("refresh_token"),
                refresh_token_expires_in=refreshed.get("refresh_token_expires_in"),
            )
        else:
            access_token = account.get("access_token")
    else:
        access_token = account.get("access_token")

    if not access_token:
        raise RuntimeError("Connected eBay account has no usable access token. Disconnect and reconnect eBay.")

    config = get_ebay_config(account["environment"])
    return {
        "owner_name": owner_name,
        "environment": account["environment"],
        "marketplace_id": account.get("marketplace_id", "EBAY_US"),
        "api_base": config["api_base"],
        "access_token": access_token,
        "account": account,
    }


def call_ebay_api(
    owner_name: str,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> requests.Response:
    """
    Reusable eBay API caller for your Products, Orders, Catalog-Pro, and future functions.

    Example:
        response = call_ebay_api(owner_name, "GET", "/sell/inventory/v1/inventory_item")
    """
    context = get_ebay_api_context(owner_name)
    url = f"{context['api_base'].rstrip('/')}/{path.lstrip('/')}"

    request_headers = {
        "Authorization": f"Bearer {context['access_token']}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": context["marketplace_id"],
    }
    if headers:
        request_headers.update(headers)

    response = requests.request(
        method=method.upper(),
        url=url,
        headers=request_headers,
        json=json_body,
        params=params,
        timeout=timeout,
    )
    return response


# Compatibility aliases for older imports.
def save_connected_ebay_account(*args, **kwargs):
    return save_ebay_account(*args, **kwargs)


def get_latest_connected_ebay_account(*args, **kwargs):
    return get_latest_ebay_account(*args, **kwargs)
