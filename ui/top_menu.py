from html import escape
from urllib.parse import quote

import streamlit as st

from core.session import logout


def _query_value(key: str) -> str | None:
    value = st.query_params.get(key)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def render_top_menu(app_name: str, pages: list[str]) -> None:
    """
    Renders a real horizontal HTML top menu instead of Streamlit columns.

    This keeps the mobile menu looking like the laptop menu:
    brand on top, all page choices in one horizontal row underneath.
    """
    if _query_value("logout") == "1":
        st.query_params.clear()
        logout()

    requested_page = _query_value("page")

    if requested_page in pages and st.session_state.active_page != requested_page:
        st.session_state.active_page = requested_page

    nav_links = []

    for page in pages:
        is_active = st.session_state.active_page == page
        active_class = " nav-pill-active" if is_active else ""
        href = f"?page={quote(page)}"

        nav_links.append(
            f'<a class="nav-pill{active_class}" href="{href}">{escape(page)}</a>'
        )

    nav_links.append('<a class="nav-pill" href="?logout=1">Logout</a>')

    st.markdown(
        f"""
        <nav class="top-nav">
            <div class="top-nav-brand">{escape(app_name)}</div>
            <div class="nav-button-row">
                {''.join(nav_links)}
            </div>
        </nav>
        """,
        unsafe_allow_html=True,
    )
