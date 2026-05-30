import streamlit as st

from core.session import logout


def render_top_menu(app_name: str, pages: list[str]) -> None:
    st.markdown(
        f"""
        <div class="top-nav">
            <div class="top-nav-brand">{app_name}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    cols = st.columns(len(pages) + 1)

    for index, page in enumerate(pages):
        with cols[index]:
            is_active = st.session_state.active_page == page

            if st.button(
                page,
                key=f"nav_{page}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.session_state.active_page = page
                st.rerun()

    with cols[-1]:
        if st.button("Logout", use_container_width=True):
            logout()