import json
from datetime import datetime, timezone
from typing import Any

import requests
import streamlit as st
from cryptography.fernet import Fernet

from core.ebay_oauth import get_ebay_config, refresh_access_token


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
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _table_url() -> str:
    return f"{_supabase_url()}/rest/v1/ebay_accounts"


def get_cipher() -> Fernet:
    key = st.secrets.get("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("Missing ENCRYPTION_KEY in Streamlit secrets")
    return Fernet(str(key).encode("utf-8"))


def _encrypt(value: str | None) -> str | None:
    if not value:
        return None
    return get_cipher().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str | None) -> str | None:
    if not value:
        return None
    return get_cipher().decrypt(value.encode("utf-8")).decode("utf-8")


def _profile_text(ebay_user: dict[str, Any] | None) -> str:
    try:
        return json.dumps(ebay_user or {}, separators=(",", ":"), default=str)
    except Exception:
        return "{}"


def _extract_user_fields(ebay_user: dict[str, Any] | None) -> tuple[str | None, str | None, str | None]:
    ebay_user = ebay_user or {}
    ebay_user_id = (
        ebay_user.get("userId")
        or ebay_user.get("user_id")
        or ebay_user.get("accountId")
    )
    ebay_username = (
        ebay_user.get("username")
        or ebay_user.get("userName")
        or ebay_user.get("name")
    )
    store_name = None
    if isinstance(ebay_user.get("storefront"), dict):
        store_name = ebay_user["storefront"].get("storeName") or ebay_user["storefront"].get("name")
    store_name = store_name or ebay_user.get("storeName")
    return ebay_user_id, ebay_username, store_name


def _normal_owner(owner_name: str | None) -> str:
    owner_name = (owner_name or "").strip()
    return owner_name or "default"


def _request(method: str, url: str, **kwargs):
    response = requests.request(method, url, timeout=30, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase request failed: {response.status_code} {response.text}")
    return response


def delete_ebay_account(owner_name: str, environment: str | None = None) -> None:
    """
    Removes saved eBay OAuth tokens so the next connect attempt starts fresh.
    Also removes legacy rows saved under 'default' for the same environment.
    """
    if not _using_supabase():
        return

    owners = {_normal_owner(owner_name), "default"}
    for owner in owners:
        params = {"owner_name": f"eq.{owner}"}
        if environment:
            params["environment"] = f"eq.{environment}"
        response = requests.delete(
            _table_url(),
            headers=_headers(),
            params=params,
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Supabase disconnect failed: {response.status_code} {response.text}")


def disconnect_ebay_account(owner_name: str, environment: str | None = None) -> None:
    delete_ebay_account(owner_name, environment)


def save_ebay_account(
    owner_name: str,
    role: str,
    environment: str,
    marketplace_id: str = "EBAY_US",
    token_data: dict[str, Any] | None = None,
    ebay_user: dict[str, Any] | None = None,
    user_access_token: str | None = None,
    **_: Any,
) -> None:
    """
    One connected eBay account per app user. We delete old rows first so Settings,
    Orders, and future API tools all read the same current account.
    """
    if not _using_supabase():
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in Streamlit secrets")

    owner = _normal_owner(owner_name)
    env = (environment or "production").lower()
    token_data = token_data or {}

    access_token = token_data.get("access_token") or user_access_token
    refresh_token = token_data.get("refresh_token")

    if not access_token and not refresh_token:
        raise RuntimeError("No eBay OAuth tokens were returned to save")

    ebay_user_id, ebay_username, store_name = _extract_user_fields(ebay_user)
    now = _now_iso()

    # Remove stale rows for this user/environment before saving the new connection.
    delete_ebay_account(owner, env)

    row = {
        "owner_name": owner,
        "role": role or "CLIENT",
        "environment": env,
        "marketplace_id": marketplace_id or "EBAY_US",
        "encrypted_access_token": _encrypt(access_token),
        "encrypted_refresh_token": _encrypt(refresh_token),
        "access_token_expires_in": token_data.get("expires_in"),
        "refresh_token_expires_in": token_data.get("refresh_token_expires_in"),
        "ebay_user_id": ebay_user_id,
        "ebay_username": ebay_username,
        "store_name": store_name,
        "profile_json": _profile_text(ebay_user),
        "created_at": now,
        "updated_at": now,
    }

    response = requests.post(
        _table_url(),
        headers=_headers("return=representation"),
        data=json.dumps(row),
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"Supabase save failed: {response.status_code} {response.text}")


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["access_token"] = _decrypt(row.get("encrypted_access_token"))
    row["refresh_token"] = _decrypt(row.get("encrypted_refresh_token"))
    return row


def get_latest_ebay_account(owner_name: str, environment: str | None = None) -> dict[str, Any] | None:
    """Return the eBay account saved for this exact app user only.

    Tenant-isolation rule: never fall back to the shared/legacy "default"
    eBay account for a client user. That fallback is what caused client token
    accounts to see the CEO store listings in Product Manager → My Store.
    """
    if not _using_supabase():
        return None

    owner = _normal_owner(owner_name)
    params = {
        "owner_name": f"eq.{owner}",
        "select": "*",
        "order": "updated_at.desc,id.desc",
        "limit": "1",
    }
    if environment:
        params["environment"] = f"eq.{environment}"

    response = requests.get(_table_url(), headers=_headers(), params=params, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase load failed: {response.status_code} {response.text}")

    rows = response.json()
    if rows:
        row = _decode_row(rows[0])
        row["_resolved_owner"] = owner
        return row

    return None


def get_connected_ebay_label(owner_name: str, environment: str | None = None) -> str:
    account = get_latest_ebay_account(owner_name, environment)
    if not account:
        return "No eBay account connected"

    label = (
        account.get("store_name")
        or account.get("ebay_username")
        or account.get("ebay_user_id")
        or "Connected eBay account"
    )
    env = account.get("environment", "production")
    return f"{label} ({env})"


def get_ebay_api_context(owner_name: str, environment: str | None = None) -> dict[str, Any] | None:
    account = get_latest_ebay_account(owner_name, environment)
    if not account:
        return None

    config = get_ebay_config(account.get("environment", "production"))

    return {
        "owner_name": account.get("owner_name"),
        "role": account.get("role"),
        "environment": account.get("environment", "production"),
        "marketplace_id": account.get("marketplace_id", "EBAY_US"),
        "api_base": config["api_base"],
        "access_token": account.get("access_token"),
        "refresh_token": account.get("refresh_token"),
        "label": (
            account.get("store_name")
            or account.get("ebay_username")
            or account.get("ebay_user_id")
            or "Connected eBay account"
        ),
    }


def _update_access_token(account: dict[str, Any], token_data: dict[str, Any]) -> str:
    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("eBay refresh did not return an access token")

    row_id = account.get("id")
    if not row_id:
        return access_token

    payload = {
        "encrypted_access_token": _encrypt(access_token),
        "access_token_expires_in": token_data.get("expires_in"),
        "updated_at": _now_iso(),
    }

    response = requests.patch(
        _table_url(),
        headers=_headers(),
        params={"id": f"eq.{row_id}"},
        data=json.dumps(payload),
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase token update failed: {response.status_code} {response.text}")

    return access_token


def get_valid_ebay_access_token(owner_name: str, environment: str | None = None) -> tuple[str, dict[str, Any]]:
    account = get_latest_ebay_account(owner_name, environment)
    if not account:
        raise RuntimeError("No connected eBay account found. Please connect eBay in Settings.")

    refresh_token = account.get("refresh_token")
    if not refresh_token:
        access_token = account.get("access_token")
        if access_token:
            return access_token, account
        raise RuntimeError("Connected eBay account is missing a refresh token. Disconnect and reconnect eBay.")

    token_data = refresh_access_token(refresh_token, account.get("environment", "production"))
    access_token = _update_access_token(account, token_data)
    account["access_token"] = access_token
    return access_token, account


def call_ebay_api(
    owner_name: str,
    method: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    environment: str | None = None,
    headers: dict[str, str] | None = None,
):
    access_token, account = get_valid_ebay_access_token(owner_name, environment)
    config = get_ebay_config(account.get("environment", "production"))
    url = endpoint if endpoint.startswith("http") else f"{config['api_base']}{endpoint}"

    request_headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": account.get("marketplace_id", "EBAY_US"),
    }
    if headers:
        request_headers.update(headers)

    return requests.request(
        method.upper(),
        url,
        headers=request_headers,
        params=params,
        json=json_body,
        timeout=45,
    )