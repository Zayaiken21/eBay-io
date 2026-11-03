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

    # Tell the next run to wipe the remembered-session token, or the browser
    # would hand it straight back on load and sign the user in again. A flag
    # rather than a direct call: st.rerun() below discards anything rendered
    # in this run, so clearing the token here would never reach the browser.
    st.session_state["_forget_session"] = True

    st.rerun()