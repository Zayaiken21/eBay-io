
import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any

import requests
import streamlit as st


DEFAULT_SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/sell.marketing",
]


def get_oauth_scopes() -> list[str]:
    """
    Keep sign-in stable by default. Do not include client-credentials-only or
    unapproved scopes here. You can override from Streamlit secrets later:

    EBAY_OAUTH_SCOPES = "scope1 scope2 scope3"
    """
    raw = st.secrets.get("EBAY_OAUTH_SCOPES", "")
    if raw:
        return [scope.strip() for scope in raw.replace("\n", " ").split(" ") if scope.strip()]
    return DEFAULT_SCOPES


SCOPES = DEFAULT_SCOPES


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


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("utf-8"))


def _sign_state(payload: dict[str, Any]) -> str:
    secret = st.secrets["OAUTH_STATE_SECRET"].encode("utf-8")
    raw_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    raw_b64 = _b64url_encode(raw_json)
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

    # 30 minutes gives users enough time to complete eBay login on mobile/2FA.
    if int(time.time()) - int(payload.get("iat", 0)) > 1800:
        raise ValueError("OAuth state expired. Please start the eBay connection again.")

    return payload


def build_ebay_oauth_url(
    *,
    owner_name: str,
    role: str,
    environment: str,
    marketplace_id: str = "EBAY_US",
    return_page: str = "Settings",
) -> str:
    config = get_ebay_config(environment)

    state = _sign_state(
        {
            "owner_name": owner_name or "default",
            "role": role or "CLIENT",
            "environment": environment or "production",
            "marketplace_id": marketplace_id or "EBAY_US",
            "return_page": return_page or "Settings",
            "iat": int(time.time()),
        }
    )

    params = {
        "client_id": config["client_id"],
        "redirect_uri": config["ru_name"],
        "response_type": "code",
        "scope": " ".join(get_oauth_scopes()),
        "state": state,
        "prompt": "login",
    }

    return config["auth_url"] + "?" + urllib.parse.urlencode(params)


def _basic_auth_header(environment: str) -> dict[str, str]:
    config = get_ebay_config(environment)
    credentials = f"{config['client_id']}:{config['client_secret']}"
    encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    return {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded_credentials}",
    }


def exchange_code_for_tokens(code: str, environment: str) -> dict[str, Any]:
    config = get_ebay_config(environment)

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config["ru_name"],
    }

    response = requests.post(
        config["token_url"],
        headers=_basic_auth_header(environment),
        data=data,
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(f"eBay token exchange failed: {response.status_code} {response.text}")

    return response.json()


def refresh_access_token(refresh_token: str, environment: str) -> dict[str, Any]:
    """
    Important: Do NOT send scope during refresh. eBay can reject refresh requests
    with invalid_scope if scopes differ from the original grant.
    """
    config = get_ebay_config(environment)

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    response = requests.post(
        config["token_url"],
        headers=_basic_auth_header(environment),
        data=data,
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(f"eBay token refresh failed: {response.status_code} {response.text}")

    return response.json()


def get_ebay_user_profile(access_token: str, environment: str) -> dict[str, Any]:
    config = get_ebay_config(environment)
    response = requests.get(
        f"{config['api_base']}/commerce/identity/v1/user/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        timeout=30,
    )

    if response.status_code >= 400:
        return {}

    try:
        return response.json()
    except Exception:
        return {}


def handle_oauth_callback(code: str, state: str) -> dict[str, Any]:
    payload = verify_state(state)
    environment = payload.get("environment", "production")
    token_data = exchange_code_for_tokens(code, environment)

    profile = {}
    access_token = token_data.get("access_token")
    if access_token:
        profile = get_ebay_user_profile(access_token, environment)

    return {
        "owner_name": payload.get("owner_name", "default"),
        "role": payload.get("role", "CLIENT"),
        "environment": environment,
        "marketplace_id": payload.get("marketplace_id", "EBAY_US"),
        "return_page": payload.get("return_page", "Settings"),
        "token_data": token_data,
        "ebay_user": profile,
    }
