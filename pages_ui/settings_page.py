import streamlit as st

from config.ceo_settings import CEO_SETTINGS
from core.ebay_oauth import build_ebay_oauth_url, handle_oauth_callback
from core.ebay_account_store import (
    disconnect_ebay_account,
    get_connected_ebay_label,
    get_latest_ebay_account,
    save_ebay_account,
)
from core.token_store import create_token, load_tokens, cancel_token, cancel_all_tokens
try:
    from core.token_store import get_last_error as get_token_store_error
except Exception:
    def get_token_store_error():
        return None


def _role_lower() -> str:
    return str(st.session_state.get("role") or "").lower()


def _current_owner_name() -> str:
    if _role_lower() == "ceo":
        return "ceo"
    return (
        st.session_state.get("client_name")
        or st.session_state.get("owner_name")
        or st.session_state.get("username")
        or "default"
    )


def process_ebay_oauth_callback_if_present() -> bool:
    params = st.query_params
    if "code" not in params or "state" not in params:
        return False

    try:
        result = handle_oauth_callback(code=params["code"], state=params["state"])
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

    if _role_lower() == "ceo":
        render_ceo_settings()
    else:
        render_client_settings()


def render_ebay_connection(environment_options: list[str]) -> None:
    owner_name = _current_owner_name()
    role = st.session_state.get("role") or "CLIENT"

    st.subheader("Connect eBay Account")
    st.caption(f"Connected eBay data is isolated to owner: `{owner_name}`")

    environment = st.selectbox("Environment", environment_options, key="ebay_environment")
    marketplace_id = st.selectbox("Marketplace", ["EBAY_US"], index=0, key="ebay_marketplace")

    saved = get_latest_ebay_account(owner_name, environment)
    if saved:
        st.success(f"Connected: {get_connected_ebay_label(owner_name, environment)}")
        st.caption("This account will be used only for this signed-in user's live eBay API calls.")

        cols = st.columns([1, 1])
        with cols[0]:
            if st.button("Disconnect eBay Account", use_container_width=True, type="secondary"):
                try:
                    disconnect_ebay_account(owner_name, environment)
                    st.session_state.pop("orders_live_cache", None)
                    st.session_state.pop("orders_last_sync", None)
                    st.session_state.pop("store_data", None)
                    st.success("eBay account disconnected and removed from Supabase.")
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
    st.subheader("Client Account Generator")
    st.caption("New clients receive a short access code. First login uses the code as Username with a blank Password, then they create username/password.")

    with st.form("client_token_form", clear_on_submit=True):
        client_name = st.text_input("Client name")
        submitted = st.form_submit_button("Generate Client Access Code", use_container_width=True)
        if submitted:
            if not client_name.strip():
                st.error("Enter a client name first.")
            else:
                try:
                    token_data = create_token(client_name)
                    st.success("Client access code created in Supabase.")
                    st.code(token_data["token"])
                    st.caption("Give this code to the client. They use it one time as their Username with a blank Password.")
                except Exception as exc:
                    st.error(f"Token create failed: {exc}")

    st.divider()
    token_store_error = get_token_store_error()
    if token_store_error:
        st.warning(token_store_error)

    try:
        tokens = load_tokens()
    except Exception as exc:
        st.error(f"Could not load client accounts: {exc}")
        tokens = []

    active_tokens = [token for token in tokens if token.get("active") is True]
    st.subheader("Active Client Accounts")

    if not active_tokens:
        st.info("No active client accounts yet.")
        return

    max_per_page = CEO_SETTINGS.get("max_tokens_per_page", 5)
    total_pages = max(1, (len(active_tokens) + max_per_page - 1) // max_per_page)
    if st.session_state.get("token_page", 0) > total_pages - 1:
        st.session_state.token_page = total_pages - 1
    if "token_page" not in st.session_state:
        st.session_state.token_page = 0

    start = st.session_state.token_page * max_per_page
    page_tokens = active_tokens[start:start + max_per_page]

    for item in page_tokens:
        col1, col2, col3, col4, col5 = st.columns([2, 2, 2, 2, 1])
        with col1:
            st.write(item.get("client_name", "Client"))
        with col2:
            st.caption("Access Code")
            st.code(item.get("token", ""))
        with col3:
            st.caption("Username")
            st.write(item.get("username") or item.get("token"))
        with col4:
            st.caption("Password")
            st.write("✅ Set" if item.get("password_set") else "🟡 First login pending")
        with col5:
            if st.button("Delete", key=f"cancel_{item['token']}"):
                try:
                    cancel_token(item["token"])
                    st.success("Client and their saved eBay account were removed from Supabase.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Delete failed: {exc}")

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
    if st.button("Delete All Client Accounts", use_container_width=True):
        try:
            cancel_all_tokens()
            st.warning("All client accounts and their saved eBay accounts were removed from Supabase.")
            st.rerun()
        except Exception as exc:
            st.error(f"Delete all failed: {exc}")


def render_client_settings() -> None:
    render_ebay_connection(["production"])
