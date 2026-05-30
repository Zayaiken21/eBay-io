import streamlit as st

from core.token_store import validate_client_token


def login_ceo(password: str) -> bool:
    saved_password = st.secrets.get("CEO_PASSWORD", "")
    return bool(password) and password == saved_password


def login_client(token: str) -> dict | None:
    if not token:
        return None

    return validate_client_token(token.strip())