
import streamlit as st

from config.ceo_settings import CEO_SETTINGS
from core.ebay_oauth import build_ebay_oauth_url, handle_oauth_callback
from core.ebay_account_store import (
    disconnect_ebay_account,
    get_connected_ebay_label,
    get_latest_ebay_account,
    save_ebay_account,
)
from core.token_store import (
    create_token,
    load_tokens,
    cancel_token,
    cancel_all_tokens,
)


def _current_owner_name() -> str:
    return (
        st.session_state.get("client_name")
        or st.session_state.get("owner_name")
        or st.session_state.get("username")
        or "default"
    )


def process_ebay_oauth_callback_if_present() -> bool:
    """
    Must be safe to call before the app login gate. eBay redirects to the root
    Streamlit app with ?code=...&state=..., so the owner/environment must come
    from the signed OAuth state, not from st.session_state.
    """
    params = st.query_params
    if "code" not in params or "state" not in params:
        return False

    try:
        result = handle_oauth_callback(
            code=params["code"],
            state=params["state"],
        )

        save_ebay_account(
            owner_name=result.get("owner_name", "default"),
            role=result.get("role", "CLIENT"),
            environment=result.get("environment", "production"),
            marketplace_id=result.get("marketplace_id", "EBAY_US"),
            token_data=result.get("token_data", {}),
            ebay_user=result.get("ebay_user", {}),
        )

        st.session_state["active_page"] = result.get("return_page", "Settings")
        st.session_state["ebay_oauth_success"] = True
        st.query_params.clear()
        return True

    except Exception as exc:
        st.session_state["ebay_oauth_error"] = str(exc)
        st.query_params.clear()
        return False


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

    if st.session_state.pop("ebay_oauth_success", False):
        st.success("eBay account connected successfully.")

    oauth_error = st.session_state.pop("ebay_oauth_error", None)
    if oauth_error:
        st.error(f"eBay OAuth callback error: {oauth_error}")

    if st.session_state.role == "CEO":
        render_ceo_settings()
    else:
        render_client_settings()


def render_ebay_connection(environment_options: list[str]) -> None:
    owner_name = _current_owner_name()
    role = st.session_state.get("role") or "CLIENT"

    st.subheader("Connect eBay Account")

    environment = st.selectbox("Environment", environment_options, key="ebay_environment")
    marketplace_id = st.selectbox("Marketplace", ["EBAY_US"], index=0, key="ebay_marketplace")

    saved = get_latest_ebay_account(owner_name, environment)
    if saved:
        st.success(f"Connected: {get_connected_ebay_label(owner_name, environment)}")
        st.caption("This account will be used for live eBay API calls across the app.")

        cols = st.columns([1, 1])
        with cols[0]:
            if st.button("Disconnect eBay Account", use_container_width=True, type="secondary"):
                try:
                    disconnect_ebay_account(owner_name, environment)
                    st.session_state.pop("orders_live_cache", None)
                    st.session_state.pop("orders_last_sync", None)
                    st.success("eBay account disconnected. You can connect a new account now.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Disconnect failed: {exc}")

        with cols[1]:
            oauth_url = build_ebay_oauth_url(
                owner_name=owner_name,
                role=role,
                environment=environment,
                marketplace_id=marketplace_id,
                return_page="Settings",
            )
            st.link_button("Reconnect / Replace Account", oauth_url, use_container_width=True)
        return

    st.info("No eBay account connected yet.")
    st.caption("You will leave this app, sign into eBay, approve access, and return automatically.")

    oauth_url = build_ebay_oauth_url(
        owner_name=owner_name,
        role=role,
        environment=environment,
        marketplace_id=marketplace_id,
        return_page="Settings",
    )
    st.link_button("Connect eBay Account", oauth_url, use_container_width=True, type="primary")


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
