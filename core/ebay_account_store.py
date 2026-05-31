import json
from datetime import datetime, timezone
from typing import Any

import requests
import streamlit as st
from cryptography.fernet import Fernet

from core.ebay_oauth import get_ebay_config, refresh_access_token


def _supabase_url() -> str:
    url = st.secrets.get("SUPABASE_URL")
    if not url:
        raise RuntimeError("Missing SUPABASE_URL in Streamlit secrets")
    return url.rstrip("/")


def _supabase_key() -> str:
    key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError("Missing SUPABASE_SERVICE_ROLE_KEY in Streamlit secrets")
    return key


def _headers() -> dict[str, str]:
    key = _supabase_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def get_cipher() -> Fernet:
    key = st.secrets.get("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("Missing ENCRYPTION_KEY in Streamlit secrets")
    return Fernet(key.encode())


def _encrypt(value: str | None) -> str | None:
    if not value:
        return None
    return get_cipher().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str | None) -> str | None:
    if not value:
        return None
    return get_cipher().decrypt(value.encode("utf-8")).decode("utf-8")


def _extract_ebay_user_fields(ebay_user: dict[str, Any]) -> dict[str, str | None]:
    username = (
        ebay_user.get("username")
        or ebay_user.get("userName")
        or ebay_user.get("user_id")
        or ebay_user.get("userId")
    )
    user_id = ebay_user.get("userId") or ebay_user.get("user_id") or ebay_user.get("id")
    store_name = (
        ebay_user.get("storeName")
        or ebay_user.get("store_name")
        or ebay_user.get("businessName")
        or ebay_user.get("companyName")
    )
    return {
        "ebay_user_id": user_id,
        "ebay_username": username,
        "store_name": store_name,
    }


def save_ebay_account(
    *,
    owner_name: str,
    role: str,
    environment: str,
    marketplace_id: str = "EBAY_US",
    token_data: dict[str, Any],
    ebay_user: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    One active saved eBay account per owner. We delete old rows for the owner,
    then insert the newest OAuth token set.
    """
    ebay_user = ebay_user or {}
    fields = _extract_ebay_user_fields(ebay_user)

    delete_ebay_account(owner_name)

    payload = {
        "owner_name": owner_name,
        "role": role,
        "environment": environment,
        "marketplace_id": marketplace_id or "EBAY_US",
        "encrypted_access_token": _encrypt(token_data.get("access_token")),
        "encrypted_refresh_token": _encrypt(token_data.get("refresh_token")),
        "access_token_expires_in": token_data.get("expires_in"),
        "refresh_token_expires_in": token_data.get("refresh_token_expires_in"),
        "ebay_user_id": fields["ebay_user_id"],
        "ebay_username": fields["ebay_username"],
        "store_name": fields["store_name"],
        "profile_json": json.dumps(ebay_user),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    url = f"{_supabase_url()}/rest/v1/ebay_accounts"
    response = requests.post(url, headers=_headers(), json=payload, timeout=30)

    if response.status_code not in (200, 201):
        raise RuntimeError(f"Supabase save failed: {response.status_code} {response.text}")

    data = response.json()
    return data[0] if isinstance(data, list) and data else payload


def get_latest_ebay_account(owner_name: str) -> dict[str, Any] | None:
    url = (
        f"{_supabase_url()}/rest/v1/ebay_accounts"
        f"?owner_name=eq.{requests.utils.quote(owner_name)}"
        "&order=updated_at.desc"
        "&limit=1"
    )
    response = requests.get(url, headers=_headers(), timeout=30)

    if response.status_code != 200:
        raise RuntimeError(f"Supabase load failed: {response.status_code} {response.text}")

    data = response.json()
    if not data:
        return None

    return data[0]


def delete_ebay_account(owner_name: str) -> None:
    url = (
        f"{_supabase_url()}/rest/v1/ebay_accounts"
        f"?owner_name=eq.{requests.utils.quote(owner_name)}"
    )
    response = requests.delete(url, headers=_headers(), timeout=30)

    if response.status_code not in (200, 202, 204):
        raise RuntimeError(f"Supabase delete failed: {response.status_code} {response.text}")


def get_connected_ebay_label(owner_name: str) -> str:
    account = get_latest_ebay_account(owner_name)
    if not account:
        return "No eBay account connected"

    label = (
        account.get("store_name")
        or account.get("ebay_username")
        or account.get("ebay_user_id")
        or "Connected eBay account"
    )
    return f"{label} ({account.get('environment', 'production')})"


def get_ebay_api_context(owner_name: str) -> dict[str, Any]:
    account = get_latest_ebay_account(owner_name)
    if not account:
        raise RuntimeError("No eBay account connected")

    access_token = _decrypt(account.get("encrypted_access_token"))
    refresh_token = _decrypt(account.get("encrypted_refresh_token"))

    if not refresh_token:
        raise RuntimeError("Saved eBay account is missing refresh token. Disconnect and reconnect eBay.")

    # Refresh every time for reliability. Later you can add expires_at caching.
    refreshed = refresh_access_token(refresh_token, account["environment"])
    if refreshed.get("access_token"):
        access_token = refreshed["access_token"]
        token_data = {
            "access_token": access_token,
            "refresh_token": refreshed.get("refresh_token") or refresh_token,
            "expires_in": refreshed.get("expires_in"),
            "refresh_token_expires_in": account.get("refresh_token_expires_in"),
        }
        save_ebay_account(
            owner_name=account["owner_name"],
            role=account["role"],
            environment=account["environment"],
            marketplace_id=account.get("marketplace_id", "EBAY_US"),
            token_data=token_data,
            ebay_user=json.loads(account.get("profile_json") or "{}"),
        )

    config = get_ebay_config(account["environment"])

    return {
        "owner_name": account["owner_name"],
        "role": account["role"],
        "environment": account["environment"],
        "marketplace_id": account.get("marketplace_id", "EBAY_US"),
        "api_base": config["api_base"],
        "access_token": access_token,
    }


def call_ebay_api(
    owner_name: str,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> requests.Response:
    ctx = get_ebay_api_context(owner_name)

    headers = {
        "Authorization": f"Bearer {ctx['access_token']}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": ctx["marketplace_id"],
    }
    if extra_headers:
        headers.update(extra_headers)

    url = ctx["api_base"] + path

    response = requests.request(
        method.upper(),
        url,
        headers=headers,
        params=params,
        json=json_body,
        timeout=30,
    )
    return response
