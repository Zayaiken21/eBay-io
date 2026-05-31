import streamlit as st

from config.ceo_settings import CEO_SETTINGS
from core.ebay_account_store import (
    delete_ebay_account,
    delete_ebay_accounts_for_owners,
    get_connected_ebay_label,
    get_latest_ebay_account,
    save_ebay_account,
)
from core.ebay_oauth import build_ebay_oauth_url, handle_oauth_callback
from core.token_store import (
    create_token,
    load_tokens,
    cancel_token,
    cancel_all_tokens,
)


def _current_owner_name() -> str:
    return (
        st.session_state.get("client_name")
        or st.session_state.get("username")
        or st.session_state.get("owner_name")
        or st.session_state.get("role")
        or "default"
    )


def _current_role() -> str:
    return st.session_state.get("role") or "CLIENT"

def _owner_disconnect_candidates(owner_name: str) -> list[str]:
    """All owner keys that may have been used by older/current OAuth saves."""
    candidates = [owner_name, "default"]
    for key in ("client_name", "username", "owner_name", "email", "current_user"):
        value = st.session_state.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())

    role = st.session_state.get("role")
    if isinstance(role, str) and role.strip():
        candidates.append(role.strip())

    seen = set()
    clean = []
    for item in candidates:
        item = (item or "").strip()
        if item and item not in seen:
            seen.add(item)
            clean.append(item)
    return clean




def process_ebay_oauth_callback_if_present():
    """
    Runs on every app load. eBay redirects to the app root with ?code=...&state=...
    after login, often in a new Streamlit session. The signed state contains the
    owner/role/environment, so saving does not depend on session_state.
    """
    params = st.query_params

    if "code" not in params or "state" not in params:
        return None

    try:
        code = params.get("code")
        state = params.get("state")

        result = handle_oauth_callback(code=code, state=state)
        state_payload = result["state"]

        save_ebay_account(
            owner_name=state_payload["owner_name"],
            role=state_payload["role"],
            environment=state_payload["environment"],
            marketplace_id=state_payload.get("marketplace_id", "EBAY_US"),
            token_data=result["token_data"],
            ebay_user=result.get("ebay_user") or {},
        )

        st.session_state["ebay_oauth_success"] = True
        st.session_state["ebay_oauth_owner_name"] = state_payload["owner_name"]
        st.session_state["active_page"] = "Settings"

        st.query_params.clear()
        st.success("eBay account connected successfully.")
        return result

    except Exception as exc:
        st.query_params.clear()
        st.error(f"eBay OAuth callback error: {exc}")
        return None


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

    if st.session_state.get("role") == "CEO":
        render_ceo_settings()
    else:
        render_client_settings()


def render_ebay_connection(environment_options: list[str]) -> None:
    owner_name = _current_owner_name()
    role = _current_role()

    st.subheader("Connect eBay Account")

    saved = get_latest_ebay_account(owner_name)

    if saved:
        st.success(f"Connected: {get_connected_ebay_label(owner_name)}")
        st.caption(
            f"Owner: {saved.get('owner_name')} | "
            f"Environment: {saved.get('environment')} | "
            f"Marketplace: {saved.get('marketplace_id', 'EBAY_US')}"
        )

        if st.button("Disconnect eBay account", type="secondary", use_container_width=True):
            delete_ebay_accounts_for_owners(_owner_disconnect_candidates(owner_name))
            for key in list(st.session_state.keys()):
                if key.startswith("orders_live_") or key.startswith("ebay_oauth_"):
                    del st.session_state[key]
            st.success("Disconnected eBay account and removed saved tokens.")
            st.rerun()

        st.divider()
        st.info("Only one eBay account can be connected at a time. Disconnect first to connect another.")
        return

    st.info("No eBay account connected yet.")

    marketplace_id = st.selectbox("Marketplace", ["EBAY_US"], index=0)

    if role == "CEO":
        environment = st.radio("Environment", environment_options, horizontal=True)
    else:
        environment = "production"
        st.caption("Client accounts connect to production eBay only.")

    oauth_url = build_ebay_oauth_url(
        owner_name=owner_name,
        role=role,
        environment=environment,
        marketplace_id=marketplace_id,
    )

    st.markdown(
        "You will leave this app, sign into eBay, approve access, and return automatically."
    )
    st.link_button(
        f"Connect eBay {environment.title()} Account",
        oauth_url,
        use_container_width=True,
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
