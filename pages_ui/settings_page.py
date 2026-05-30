import streamlit as st

from config.ceo_settings import CEO_SETTINGS
from core.ebay_account_store import (
    get_connected_ebay_label,
    get_latest_ebay_account,
    save_ebay_account,
)
from core.ebay_oauth import build_ebay_oauth_url, handle_oauth_callback
from core.token_store import (
    cancel_all_tokens,
    cancel_token,
    create_token,
    load_tokens,
)


def _get_owner_name() -> str:
    return st.session_state.get("client_name") or st.session_state.get("username") or "CEO"


def _get_role() -> str:
    return st.session_state.get("role") or "CLIENT"


def process_ebay_oauth_callback_if_present() -> None:
    query_params = st.query_params

    if "code" not in query_params or "state" not in query_params:
        return

    code = query_params.get("code")
    state = query_params.get("state")

    if isinstance(code, list):
        code = code[0]
    if isinstance(state, list):
        state = state[0]

    try:
        result = handle_oauth_callback(code=code, state=state)
        oauth_state = result["state"]

        save_ebay_account(
            owner_name=oauth_state["owner_name"],
            role=oauth_state["role"],
            environment=oauth_state["environment"],
            marketplace_id=oauth_state.get("marketplace_id", "EBAY_US"),
            token_data=result["token_data"],
            profile=result.get("profile", {}),
        )

        st.session_state["ebay_oauth_success"] = True
        st.query_params.clear()
        st.rerun()

    except Exception as exc:
        st.error(f"eBay OAuth connection failed: {exc}")


def render_settings() -> None:
    process_ebay_oauth_callback_if_present()

    st.markdown(
        """
        <section class="app-hero">
            <h1>Settings</h1>
            <p>Manage secure eBay access and account controls.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    if st.session_state.pop("ebay_oauth_success", False):
        st.success("eBay account connected and saved successfully.")

    if st.session_state.role == "CEO":
        render_ceo_settings()
    else:
        render_client_settings()


def render_ebay_connection(environment_options: list[str]) -> None:
    owner_name = _get_owner_name()
    role = _get_role()

    st.subheader("Connect eBay Account")

    saved = get_latest_ebay_account(owner_name)
    if saved:
        st.success(f"Connected: {get_connected_ebay_label(owner_name)}")

        details = []
        if saved.get("ebay_username"):
            details.append(f"Username: {saved['ebay_username']}")
        if saved.get("store_name"):
            details.append(f"Store: {saved['store_name']}")
        if saved.get("ebay_user_id"):
            details.append(f"User ID: {saved['ebay_user_id']}")
        if details:
            st.caption(" | ".join(details))
    else:
        st.info("No eBay account connected yet.")

    marketplace_id = st.selectbox("Marketplace", ["EBAY_US"], index=0, key="ebay_marketplace_id")

    if len(environment_options) > 1:
        environment = st.radio(
            "Environment",
            environment_options,
            horizontal=True,
            key="ebay_oauth_environment",
        )
    else:
        environment = environment_options[0]
        st.caption("Clients connect to Production only.")

    oauth_url = build_ebay_oauth_url(
        owner_name=owner_name,
        role=role,
        environment=environment,
        marketplace_id=marketplace_id,
    )

    st.link_button(
        f"Connect eBay {environment.title()} Account",
        oauth_url,
        use_container_width=True,
    )

    st.caption("You will leave this app, sign into eBay, approve access, and return automatically.")


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
