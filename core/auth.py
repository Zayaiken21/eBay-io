import streamlit as st

from core.token_store import (
    validate_client_login,
    validate_client_token,
    set_client_profile,
    reset_client_profile_with_token,
)


def login_ceo(password: str) -> bool:
    saved_password = st.secrets.get("CEO_PASSWORD", "")
    return bool(password) and password == saved_password


def login_client(username_or_token: str, password: str = "") -> dict | None:
    """
    Backward-compatible client login.

    - First login: use the original token as the username and leave password blank.
      The returned payload will include requires_profile_setup=True.
    - Normal login: username + password.
    - Old callers that still pass only token continue to work for first-time tokens.
    """
    if not username_or_token:
        return None

    return validate_client_login(username_or_token.strip(), password or "")


# Convenience exports used by the login page.
def finish_client_profile_setup(token: str, username: str, password: str) -> dict:
    return set_client_profile(token, username, password)


def reset_client_login_with_token(token: str, username: str | None = None, password: str | None = None) -> dict:
    return reset_client_profile_with_token(token, username, password)
