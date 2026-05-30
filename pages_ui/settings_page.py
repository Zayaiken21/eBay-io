import streamlit as st

from config.ceo_settings import CEO_SETTINGS
from core.ebay_account_store import save_ebay_account, get_latest_ebay_account
from core.token_store import (
    create_token,
    load_tokens,
    cancel_token,
    cancel_all_tokens,
)


def render_settings() -> None:
    st.markdown(
        """
        <section class="app-hero">
            <h1>Settings</h1>
            <p>Manage secure eBay access and account controls.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    if st.session_state.role == "CEO":
        render_ceo_settings()
    else:
        render_client_settings()


def render_ebay_connection(environment_options: list[str]) -> None:
    owner_name = st.session_state.get("client_name") or "Unknown"
    role = st.session_state.get("role") or "CLIENT"

    st.subheader("Connect eBay Account")

    with st.form("ebay_connection_form", clear_on_submit=True):
        environment = st.selectbox("Environment", environment_options)
        marketplace_id = st.selectbox("Marketplace", ["EBAY_US"], index=0)
        user_access_token = st.text_input("Production User Access Token", type="password")

        submitted = st.form_submit_button("Save eBay Connection", use_container_width=True)

        if submitted:
            if not user_access_token.strip():
                st.error("Enter a valid eBay User Access Token.")
                return

            save_ebay_account(
                owner_name=owner_name,
                role=role,
                environment=environment,
                marketplace_id=marketplace_id,
                user_access_token=user_access_token.strip(),
            )

            st.success("eBay account connection saved securely.")

    saved = get_latest_ebay_account(owner_name)

    if saved:
        st.info(
            f"Saved connection found: {saved['environment']} / {saved['marketplace_id']}"
        )


def render_ceo_settings() -> None:
    st.subheader("CEO Controls")

    render_ebay_connection(["production", "sandbox"])

    st.divider()
    st.subheader("Client Token Generator")

    with st.form("client_token_form", clear_on_submit=True):
        client_name = st.text_input("Client name")
        submitted = st.form_submit_button("Generate 5-Character Client Token", use_container_width=True)

        if submitted:
            if not client_name.strip():
                st.error("Enter a client name first.")
            else:
                token_data = create_token(client_name)
                st.success("Client token created.")
                st.code(token_data["token"])

    st.divider()

    tokens = load_tokens()
    active_tokens = [token for token in tokens if token.get("active") is True]

    st.subheader("Active Client Tokens")

    if not active_tokens:
        st.info("No active client tokens yet.")
        return

    max_per_page = CEO_SETTINGS.get("max_tokens_per_page", 5)
    total_pages = max(1, (len(active_tokens) + max_per_page - 1) // max_per_page)

    if st.session_state.token_page > total_pages - 1:
        st.session_state.token_page = total_pages - 1

    start = st.session_state.token_page * max_per_page
    end = start + max_per_page
    page_tokens = active_tokens[start:end]

    for item in page_tokens:
        col1, col2, col3 = st.columns([2, 5, 1])

        with col1:
            st.write(item["client_name"])

        with col2:
            st.code(item["token"])

        with col3:
            if st.button("X", key=f"cancel_{item['token']}"):
                cancel_token(item["token"])
                st.rerun()

    left, middle, right = st.columns([1, 2, 1])

    with left:
        if st.button("← Previous", disabled=st.session_state.token_page == 0):
            st.session_state.token_page -= 1
            st.rerun()

    with middle:
        st.write(f"Page {st.session_state.token_page + 1} of {total_pages}")

    with right:
        if st.button("Next →", disabled=st.session_state.token_page >= total_pages - 1):
            st.session_state.token_page += 1
            st.rerun()

    st.divider()

    if st.button("Cancel All Tokens", use_container_width=True):
        cancel_all_tokens()
        st.warning("All client tokens have been cancelled.")
        st.rerun()


def render_client_settings() -> None:
 
    render_ebay_connection(["production"])