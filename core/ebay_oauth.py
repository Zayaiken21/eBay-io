import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any

import requests
import streamlit as st


SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/sell.marketing",
]


def get_ebay_config(environment: str) -> dict[str, str]:
    environment = (environment or "production").lower()

    if environment == "production":
        return {
            "client_id": st.secrets["EBAY_PROD_CLIENT_ID"],
            "client_secret": st.secrets["EBAY_PROD_CLIENT_SECRET"],
            "ru_name": st.secrets["EBAY_PROD_RU_NAME"],
            "auth_url": "https://auth.ebay.com/oauth2/authorize",
            "token_url": "https://api.ebay.com/identity/v1/oauth2/token",
            "api_base": "https://api.ebay.com",
        }

    return {
        "client_id": st.secrets["EBAY_SANDBOX_CLIENT_ID"],
        "client_secret": st.secrets["EBAY_SANDBOX_CLIENT_SECRET"],
        "ru_name": st.secrets["EBAY_SANDBOX_RU_NAME"],
        "auth_url": "https://auth.sandbox.ebay.com/oauth2/authorize",
        "token_url": "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
        "api_base": "https://api.sandbox.ebay.com",
    }


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))


def _sign_state(payload: dict[str, Any]) -> str:
    secret = st.secrets["OAUTH_STATE_SECRET"].encode("utf-8")
    raw_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    raw_b64 = _b64url_encode(raw_json.encode("utf-8"))
    sig = hmac.new(secret, raw_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw_b64}.{sig}"


def verify_state(state: str) -> dict[str, Any]:
    try:
        raw_b64, sig = state.split(".", 1)
    except ValueError as exc:
        raise ValueError("Invalid OAuth state format") from exc

    secret = st.secrets["OAUTH_STATE_SECRET"].encode("utf-8")
    expected = hmac.new(secret, raw_b64.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(sig, expected):
        raise ValueError("Invalid OAuth state signature")

    payload = json.loads(_b64url_decode(raw_b64).decode("utf-8"))

    if int(time.time()) - int(payload.get("iat", 0)) > 1800:
        raise ValueError("OAuth state expired. Start the eBay connection again.")

    for required in ("owner_name", "role", "environment"):
        if not payload.get(required):
            raise ValueError(f"OAuth state missing {required}")

    return payload


def build_ebay_oauth_url(
    *,
    owner_name: str,
    role: str,
    environment: str,
    marketplace_id: str = "EBAY_US",
) -> str:
    environment = (environment or "production").lower()
    if role != "CEO":
        environment = "production"

    config = get_ebay_config(environment)

    state = _sign_state(
        {
            "owner_name": owner_name or "default",
            "role": role or "CLIENT",
            "environment": environment,
            "marketplace_id": marketplace_id or "EBAY_US",
            "iat": int(time.time()),
        }
    )

    params = {
        "client_id": config["client_id"],
        "redirect_uri": config["ru_name"],
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "prompt": "login",
    }

    return config["auth_url"] + "?" + urllib.parse.urlencode(params)


def exchange_code_for_tokens(code: str, environment: str) -> dict[str, Any]:
    config = get_ebay_config(environment)
    credentials = f"{config['client_id']}:{config['client_secret']}"
    encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded_credentials}",
    }

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config["ru_name"],
    }

    response = requests.post(config["token_url"], headers=headers, data=data, timeout=30)

    if response.status_code != 200:
        raise RuntimeError(f"eBay token exchange failed: {response.status_code} {response.text}")

    return response.json()


def refresh_access_token(refresh_token: str, environment: str) -> dict[str, Any]:
    config = get_ebay_config(environment)
    credentials = f"{config['client_id']}:{config['client_secret']}"
    encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded_credentials}",
    }

    # Do NOT send scope during refresh.
    # eBay refresh tokens can only be refreshed for scopes already granted.
    # Sending a broader/different scope list causes invalid_scope even when login worked.
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    response = requests.post(config["token_url"], headers=headers, data=data, timeout=30)

    if response.status_code != 200:
        raise RuntimeError(f"eBay token refresh failed: {response.status_code} {response.text}")

    return response.json()


def get_ebay_user_profile(access_token: str, environment: str) -> dict[str, Any]:
    config = get_ebay_config(environment)
    headers = {"Authorization": f"Bearer {access_token}"}

    # Best-effort profile endpoint. If eBay returns a scope/product-specific
    # error, OAuth still saves; username/store may just be blank.
    url = f"{config['api_base']}/commerce/identity/v1/user/"
    response = requests.get(url, headers=headers, timeout=30)

    if response.status_code == 200:
        return response.json()

    return {
        "profile_error_status": response.status_code,
        "profile_error": response.text,
    }


def handle_oauth_callback(*, code: str, state: str) -> dict[str, Any]:
    state_payload = verify_state(state)
    token_data = exchange_code_for_tokens(code, state_payload["environment"])

    ebay_user = {}
    access_token = token_data.get("access_token")
    if access_token:
        ebay_user = get_ebay_user_profile(access_token, state_payload["environment"])

    return {
        "state": state_payload,
        "token_data": token_data,
        "ebay_user": ebay_user,
    }
