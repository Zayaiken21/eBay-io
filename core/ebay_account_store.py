"""
ebay_account_store.py — Local per-user eBay OAuth token storage. No Supabase required.

Every signed-in user gets their own connected eBay account using owner_name:
- client_name for client users
- "ceo" for CEO

Data file: data/ebay_accounts_local.json
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import requests
import streamlit as st

from core.ebay_oauth import get_ebay_config, refresh_access_token

DATA_DIR = Path("data")
ACCOUNT_FILE = DATA_DIR / "ebay_accounts_local.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normal_owner(owner_name: str | None) -> str:
    owner_name = (owner_name or "").strip()
    return owner_name or "default"


def _ensure_file() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if not ACCOUNT_FILE.exists():
        ACCOUNT_FILE.write_text("{}", encoding="utf-8")


def _load_all() -> dict[str, Any]:
    _ensure_file()
    try:
        data = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        ACCOUNT_FILE.write_text("{}", encoding="utf-8")
        return {}


def _save_all(data: dict[str, Any]) -> None:
    _ensure_file()
    ACCOUNT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _profile_text(ebay_user: dict[str, Any] | None) -> str:
    try:
        return json.dumps(ebay_user or {}, separators=(",", ":"), default=str)
    except Exception:
        return "{}"


def _extract_user_fields(ebay_user: dict[str, Any] | None) -> tuple[str | None, str | None, str | None]:
    ebay_user = ebay_user or {}
    ebay_user_id = ebay_user.get("userId") or ebay_user.get("user_id") or ebay_user.get("accountId")
    ebay_username = ebay_user.get("username") or ebay_user.get("userName") or ebay_user.get("name")
    store_name = None
    if isinstance(ebay_user.get("storefront"), dict):
        store_name = ebay_user["storefront"].get("storeName") or ebay_user["storefront"].get("name")
    store_name = store_name or ebay_user.get("storeName")
    return ebay_user_id, ebay_username, store_name


def delete_ebay_account(owner_name: str, environment: str | None = None) -> None:
    owner = _normal_owner(owner_name)
    env = (environment or "").lower()
    data = _load_all()
    if owner not in data:
        return
    if not env:
        data.pop(owner, None)
    else:
        accounts = [a for a in data.get(owner, []) if (a.get("environment", "production").lower() != env)]
        if accounts:
            data[owner] = accounts
        else:
            data.pop(owner, None)
    _save_all(data)


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
    owner = _normal_owner(owner_name)
    env = (environment or "production").lower()
    token_data = token_data or {}

    access_token = token_data.get("access_token") or user_access_token
    refresh_token = token_data.get("refresh_token")
    if not access_token and not refresh_token:
        raise RuntimeError("No eBay OAuth tokens were returned to save")

    ebay_user_id, ebay_username, store_name = _extract_user_fields(ebay_user)
    now = _now_iso()
    data = _load_all()
    data.setdefault(owner, [])
    data[owner] = [a for a in data[owner] if a.get("environment", "production").lower() != env]
    data[owner].append({
        "owner_name": owner,
        "role": role or "CLIENT",
        "environment": env,
        "marketplace_id": marketplace_id or "EBAY_US",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_token_expires_in": token_data.get("expires_in"),
        "refresh_token_expires_in": token_data.get("refresh_token_expires_in"),
        "ebay_user_id": ebay_user_id,
        "ebay_username": ebay_username,
        "store_name": store_name,
        "profile_json": _profile_text(ebay_user),
        "created_at": now,
        "updated_at": now,
    })
    _save_all(data)


def get_latest_ebay_account(owner_name: str, environment: str | None = None) -> dict[str, Any] | None:
    owner = _normal_owner(owner_name)
    env = (environment or "").lower()
    data = _load_all()
    owners_to_try = [owner]
    if owner != "default":
        owners_to_try.append("default")

    for current_owner in owners_to_try:
        accounts = data.get(current_owner, [])
        if env:
            accounts = [a for a in accounts if a.get("environment", "production").lower() == env]
        accounts.sort(key=lambda a: a.get("updated_at", ""), reverse=True)
        if accounts:
            return dict(accounts[0])
    return None


def get_connected_ebay_label(owner_name: str, environment: str | None = None) -> str:
    account = get_latest_ebay_account(owner_name, environment)
    if not account:
        return "No eBay account connected"
    label = account.get("store_name") or account.get("ebay_username") or account.get("ebay_user_id") or "Connected eBay account"
    return f"{label} ({account.get('environment', 'production')})"


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
        "label": account.get("store_name") or account.get("ebay_username") or account.get("ebay_user_id") or "Connected eBay account",
    }


def _update_access_token(account: dict[str, Any], token_data: dict[str, Any]) -> str:
    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("eBay refresh did not return an access token")
    owner = _normal_owner(account.get("owner_name"))
    env = account.get("environment", "production").lower()
    data = _load_all()
    for item in data.get(owner, []):
        if item.get("environment", "production").lower() == env:
            item["access_token"] = access_token
            item["access_token_expires_in"] = token_data.get("expires_in")
            item["updated_at"] = _now_iso()
            break
    _save_all(data)
    return access_token


def get_valid_ebay_access_token(owner_name: str, environment: str | None = None) -> tuple[str, dict[str, Any]]:
    account = get_latest_ebay_account(owner_name, environment)
    if not account:
        raise RuntimeError("No connected eBay account found. Please connect eBay in Settings.")

    refresh_token = account.get("refresh_token")
    if refresh_token:
        try:
            token_data = refresh_access_token(refresh_token, account.get("environment", "production"))
            access_token = _update_access_token(account, token_data)
            account["access_token"] = access_token
            config = get_ebay_config(account.get("environment", "production"))
            account["api_base"] = config["api_base"]
            return access_token, account
        except Exception:
            # Fall back to saved access token if refresh temporarily fails.
            pass

    access_token = account.get("access_token")
    if access_token:
        config = get_ebay_config(account.get("environment", "production"))
        account["api_base"] = config["api_base"]
        return access_token, account

    raise RuntimeError("Connected eBay account has no usable access token. Disconnect and reconnect eBay.")


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
        "Accept-Language": "en-US",
        "Content-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": account.get("marketplace_id", "EBAY_US"),
    }
    if headers:
        request_headers.update(headers)
    return requests.request(method.upper(), url, headers=request_headers, params=params, json=json_body, timeout=45)
