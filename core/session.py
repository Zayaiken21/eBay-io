import streamlit as st


def init_session() -> None:
    defaults = {
        "authenticated": False,
        "role": None,
        "client_name": None,
        "active_page": "Dashboard",
        "token_page": 0,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def logout() -> None:
    st.session_state.authenticated = False
    st.session_state.role = None
    st.session_state.client_name = None
    st.session_state.active_page = "Dashboard"
    st.rerun()