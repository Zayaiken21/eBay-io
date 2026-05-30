import streamlit as st
from core.session import logout


def render_top_menu(app_name: str, pages: list[str]) -> None:
    with st.container(key="top_menu_area"):
        st.markdown(
            f"""
            <div class="top-nav">
                <div class="top-nav-brand">{app_name}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        nav_items = pages + ["Logout"]
        cols = st.columns(len(nav_items), gap="small")

        for index, item in enumerate(nav_items):
            with cols[index]:
                if item == "Logout":
                    if st.button("Logout", key="nav_logout", use_container_width=True):
                        logout()
                else:
                    is_active = st.session_state.active_page == item

                    if st.button(
                        item,
                        key=f"nav_{item}",
                        use_container_width=True,
                        type="primary" if is_active else "secondary",
                    ):
                        st.session_state.active_page = item
                        st.rerun()