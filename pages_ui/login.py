import streamlit as st

from core.auth import (
    finish_client_profile_setup,
    login_ceo,
    login_client,
    reset_client_login_with_token,
)


def _rerun():
    st.rerun()


def _clear_client_flows():
    for key in (
        "pending_profile_token",
        "pending_profile_user",
        "profile_setup_required",
        "reset_password_mode",
    ):
        st.session_state.pop(key, None)


def _finish_client_login(user: dict) -> None:
    st.session_state.authenticated = True
    st.session_state.role = "client"
    st.session_state.client_name = user.get("client_name") or user.get("name") or user.get("username")
    st.session_state.username = user.get("username")
    st.session_state.owner_name = st.session_state.client_name
    st.session_state.active_page = "Dashboard"
    _clear_client_flows()
    _rerun()


def _render_first_time_setup() -> None:
    pending_token = st.session_state.get("pending_profile_token", "")
    pending_user = st.session_state.get("pending_profile_user") or {}
    client_name = pending_user.get("client_name") or pending_user.get("name") or "Client"

    st.markdown(
        """
        <section class="app-hero">
            <h1>Finish Account Setup</h1>
            <p>Create the username and password you will use after your first token login.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )
    st.info(f"Welcome, **{client_name}**. Your access code worked. Now create your permanent login.")

    with st.form("first_time_profile_form", clear_on_submit=False):
        username = st.text_input("Choose Username", placeholder="letters, numbers, dots, underscores, hyphens")
        password = st.text_input("Create Password", type="password", placeholder="At least 6 characters")
        confirm = st.text_input("Confirm Password", type="password")
        submitted = st.form_submit_button("Save & Open Dashboard", use_container_width=True)

        if submitted:
            if not pending_token:
                st.error("Session expired. Sign in again with your access code.")
            elif not username or not password or not confirm:
                st.error("Fill in username, password, and confirmation.")
            elif password != confirm:
                st.error("Passwords do not match.")
            else:
                try:
                    user = finish_client_profile_setup(pending_token, username, password)
                    _finish_client_login(user)
                except Exception as exc:
                    st.error(str(exc))

    if st.button("← Back to Login", use_container_width=True):
        _clear_client_flows()
        _rerun()


def _render_reset_login() -> None:
    st.markdown(
        """
        <section class="app-hero">
            <h1>Reset Client Login</h1>
            <p>Use your original access code to reset your username or password.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    with st.form("reset_client_login_form", clear_on_submit=False):
        token = st.text_input("Original Access Code", placeholder="Your 5-character token")
        new_username = st.text_input("New Username", placeholder="Optional")
        new_password = st.text_input("New Password", type="password", placeholder="Optional")
        confirm = st.text_input("Confirm New Password", type="password", placeholder="Required if changing password")
        c1, c2 = st.columns(2)
        with c1:
            submitted = st.form_submit_button("Save Reset", use_container_width=True)
        with c2:
            cancel = st.form_submit_button("← Back", use_container_width=True)

        if cancel:
            _clear_client_flows()
            _rerun()

        if submitted:
            wants_username = bool(new_username.strip())
            wants_password = bool(new_password or confirm)
            if not token.strip():
                st.error("Enter your original access code.")
            elif not wants_username and not wants_password:
                st.error("Enter a new username, password, or both.")
            elif wants_password and new_password != confirm:
                st.error("Passwords do not match.")
            else:
                try:
                    reset_client_login_with_token(
                        token,
                        new_username.strip() if wants_username else None,
                        new_password if wants_password else None,
                    )
                    st.success("Login details updated. You can now sign in with your username and password.")
                    _clear_client_flows()
                except Exception as exc:
                    st.error(str(exc))


def render_login() -> None:
    if st.session_state.get("profile_setup_required"):
        _render_first_time_setup()
        return

    st.markdown(
        """
        <section class="app-hero">
            <h1>Welcome to Ebay io</h1>
            <p>Secure access for CEOs and clients.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    tab_client, tab_ceo = st.tabs(["Client Login", "CEO Login"])

    with tab_client:
        if st.session_state.get("reset_password_mode"):
            _render_reset_login()
        else:
            with st.form("client_login_form", clear_on_submit=False):
                st.subheader("Client Access")
                username = st.text_input(
                    "Username",
                    placeholder="First time? Enter your access code here",
                    help="First-time users: enter the access code as username and leave password blank.",
                )
                password = st.text_input("Password", type="password", placeholder="Leave blank for first-time token login")
                st.caption("First time? Use your access code as the username and leave password blank.")
                c1, c2 = st.columns(2)
                with c1:
                    submitted = st.form_submit_button("Login", use_container_width=True)
                with c2:
                    reset = st.form_submit_button("Reset Login", use_container_width=True)

                if reset:
                    st.session_state.reset_password_mode = True
                    _rerun()

                if submitted:
                    client = login_client(username, password)
                    if client and (client.get("requires_profile_setup") or client.get("requires_password_setup")):
                        st.session_state.pending_profile_token = str(client.get("token") or username).strip().upper()
                        st.session_state.pending_profile_user = dict(client)
                        st.session_state.profile_setup_required = True
                        _rerun()
                    elif client and client.get("password_required"):
                        st.error("Password required for this username.")
                    elif client and client.get("login_ok", True):
                        _finish_client_login(client)
                    else:
                        st.error("Invalid username, password, or inactive account.")

    with tab_ceo:
        with st.form("ceo_login_form"):
            st.subheader("CEO Access")
            password = st.text_input("CEO password", type="password")
            submitted = st.form_submit_button("Sign in as CEO", use_container_width=True)

            if submitted:
                if login_ceo(password):
                    st.session_state.authenticated = True
                    st.session_state.role = "ceo"
                    st.session_state.client_name = "CEO"
                    st.session_state.owner_name = "ceo"
                    st.session_state.active_page = "Dashboard"
                    st.rerun()
                else:
                    st.error("Invalid CEO password.")
