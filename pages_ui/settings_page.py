import streamlit as st

from config.ceo_settings import CEO_SETTINGS
from core.ebay_account_store import (
    call_ebay_api,
    disconnect_ebay_account,
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


def _get_query_value(name: str) -> str | None:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _get_owner_name() -> str:
    return (
        st.session_state.get("client_name")
        or st.session_state.get("username")
        or st.session_state.get("owner_name")
        or "CEO"
    )


def _get_role() -> str:
    return str(st.session_state.get("role") or "CLIENT").upper()


def process_ebay_oauth_callback_if_present() -> None:
    """
    Called by app.py on every run. It only does work when eBay redirects back
    with ?code=...&state=...
    """
    code = _get_query_value("code")
    state = _get_query_value("state")
    error = _get_query_value("error")
    error_description = _get_query_value("error_description")

    if error:
        st.error(f"eBay authorization failed: {error_description or error}")
        st.query_params.clear()
        return

    if not code or not state:
        return

    try:
        result = handle_oauth_callback(code=code, state=state)
        oauth_state = result["state"]

        save_ebay_account(
            owner_name=oauth_state.get("owner_name", "Unknown"),
            role=oauth_state.get("role", "CLIENT"),
            environment=oauth_state.get("environment", "production"),
            marketplace_id=oauth_state.get("marketplace_id", "EBAY_US"),
            token_data=result.get("token_data", {}),
            profile=result.get("profile", {}),
        )

        st.session_state["ebay_oauth_success"] = True
        st.session_state["active_page"] = "Settings"
        st.query_params.clear()
        st.rerun()
    except Exception as exc:
        st.error(f"eBay OAuth callback error: {exc}")


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
        st.success("eBay account connected and saved successfully.")

    if _get_role() == "CEO":
        render_ceo_settings()
    else:
        render_client_settings()


def render_ebay_connection(environment_options: list[str]) -> None:
    owner_name = _get_owner_name()
    role = _get_role()

    st.subheader("Connect eBay Account")

    try:
        saved = get_latest_ebay_account(owner_name)
    except Exception as exc:
        saved = None
        st.error(f"Could not load saved eBay account: {exc}")

    if saved:
        st.success(f"Connected: {get_connected_ebay_label(owner_name)}")

        details = []
        if saved.get("ebay_username"):
            details.append(f"Username: {saved['ebay_username']}")
        if saved.get("store_name"):
            details.append(f"Store: {saved['store_name']}")
        if saved.get("ebay_user_id"):
            details.append(f"User ID: {saved['ebay_user_id']}")
        if saved.get("environment"):
            details.append(f"Environment: {saved['environment']}")
        if saved.get("marketplace_id"):
            details.append(f"Marketplace: {saved['marketplace_id']}")

        if details:
            st.caption(" | ".join(details))

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Disconnect eBay Account", use_container_width=True, type="secondary"):
                disconnect_ebay_account(owner_name)
                st.success("eBay account disconnected.")
                st.rerun()

        with col_b:
            if st.button("Test eBay API Access", use_container_width=True):
                try:
                    response = call_ebay_api(owner_name, "GET", "/commerce/identity/v1/user/")
                    if response.status_code == 200:
                        st.success("eBay API access is working.")
                    else:
                        st.error(f"eBay API test failed: {response.status_code} {response.text}")
                except Exception as exc:
                    st.error(f"eBay API test failed: {exc}")
    else:
        st.info("No eBay account connected yet.")

    marketplace_id = st.selectbox(
        "Marketplace",
        ["EBAY_US"],
        index=0,
        key="ebay_marketplace_id",
    )

    if role == "CEO" and len(environment_options) > 1:
        environment = st.radio(
            "Environment",
            environment_options,
            horizontal=True,
            key="ebay_oauth_environment",
        )
    else:
        environment = "production"
        st.caption("Client accounts connect to Production only.")

    try:
        oauth_url = build_ebay_oauth_url(
            owner_name=owner_name,
            role=role,
            environment=environment,
            marketplace_id=marketplace_id,
        )
        button_label = "Reconnect eBay Account" if saved else f"Connect eBay {environment.title()} Account"
        st.link_button(
            button_label,
            oauth_url,
            use_container_width=True,
        )
    except Exception as exc:
        st.error(f"OAuth setup is incomplete: {exc}")

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
                st.success(f"Token created for {client_name.strip()}")
                st.code(token_data["token"])

    tokens = load_tokens()
    active_tokens = [item for item in tokens if item.get("active", True)]

    if not active_tokens:
        st.info("No active client tokens.")
        return

    if "token_page" not in st.session_state:
        st.session_state.token_page = 0

    per_page = 5
    total_pages = max(1, (len(active_tokens) + per_page - 1) // per_page)

    if st.session_state.token_page >= total_pages:
        st.session_state.token_page = total_pages - 1

    start = st.session_state.token_page * per_page
    end = start + per_page
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
