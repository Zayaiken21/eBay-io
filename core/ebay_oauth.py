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


def _get_secret(name: str) -> str:
    value = st.secrets.get(name)
    if not value:
        raise RuntimeError(f"Missing {name} in Streamlit secrets.")
    return str(value)


def get_ebay_config(environment: str) -> dict[str, str]:
    env = (environment or "production").lower().strip()

    if env == "sandbox":
        return {
            "environment": "sandbox",
            "client_id": _get_secret("EBAY_SANDBOX_CLIENT_ID"),
            "client_secret": _get_secret("EBAY_SANDBOX_CLIENT_SECRET"),
            "ru_name": _get_secret("EBAY_SANDBOX_RU_NAME"),
            "auth_url": "https://auth.sandbox.ebay.com/oauth2/authorize",
            "token_url": "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
            "api_base": "https://api.sandbox.ebay.com",
        }

    return {
        "environment": "production",
        "client_id": _get_secret("EBAY_PROD_CLIENT_ID"),
        "client_secret": _get_secret("EBAY_PROD_CLIENT_SECRET"),
        "ru_name": _get_secret("EBAY_PROD_RU_NAME"),
        "auth_url": "https://auth.ebay.com/oauth2/authorize",
        "token_url": "https://api.ebay.com/identity/v1/oauth2/token",
        "api_base": "https://api.ebay.com",
    }


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))


def _sign_state(payload: dict[str, Any]) -> str:
    secret = _get_secret("OAUTH_STATE_SECRET").encode("utf-8")
    raw_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    raw_b64 = _b64url_encode(raw_json)
    signature = hmac.new(secret, raw_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw_b64}.{signature}"


def verify_state(state: str) -> dict[str, Any]:
    if not state:
        raise ValueError("Missing OAuth state.")

    try:
        raw_b64, signature = state.split(".", 1)
    except ValueError as exc:
        raise ValueError("Invalid OAuth state format.") from exc

    secret = _get_secret("OAUTH_STATE_SECRET").encode("utf-8")
    expected = hmac.new(secret, raw_b64.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid OAuth state signature.")

    payload = json.loads(_b64url_decode(raw_b64).decode("utf-8"))
    issued_at = int(payload.get("iat", 0))
    if int(time.time()) - issued_at > 60 * 30:
        raise ValueError("OAuth state expired. Please click Connect eBay again.")

    return payload


def build_ebay_oauth_url(
    *,
    owner_name: str,
    role: str,
    environment: str,
    marketplace_id: str = "EBAY_US",
) -> str:
    normalized_role = (role or "CLIENT").upper()
    env = (environment or "production").lower().strip()

    # Clients are production-only. CEO/admin may choose sandbox for testing.
    if normalized_role != "CEO":
        env = "production"

    config = get_ebay_config(env)

    state = _sign_state(
        {
            "owner_name": owner_name or "Unknown",
            "role": normalized_role,
            "environment": env,
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

    response = requests.post(
        config["token_url"],
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_credentials}",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config["ru_name"],
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(f"eBay token exchange failed: {response.status_code} {response.text}")

    return response.json()


def refresh_access_token(refresh_token: str, environment: str) -> dict[str, Any]:
    config = get_ebay_config(environment)
    credentials = f"{config['client_id']}:{config['client_secret']}"
    encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

    response = requests.post(
        config["token_url"],
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_credentials}",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(SCOPES),
        },
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

    if response.status_code != 200:
        # Do not fail the whole OAuth save if profile lookup is unavailable.
        return {
            "lookup_failed": True,
            "status_code": response.status_code,
            "response": response.text,
        }

    return response.json()


def handle_oauth_callback(code: str, state: str) -> dict[str, Any]:
    state_payload = verify_state(state)
    environment = state_payload["environment"]

    token_data = exchange_code_for_tokens(code, environment)
    access_token = token_data.get("access_token")

    profile: dict[str, Any] = {}
    if access_token:
        profile = get_ebay_user_profile(access_token, environment)

    return {
        "state": state_payload,
        "token_data": token_data,
        "profile": profile,
    }
