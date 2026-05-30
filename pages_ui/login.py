import streamlit as st

from core.auth import login_ceo, login_client


def render_login() -> None:
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
        with st.form("client_login_form"):
            st.subheader("Client Access")
            token = st.text_input("Client token", type="password")
            submitted = st.form_submit_button("Sign in as Client", use_container_width=True)

            if submitted:
                client = login_client(token)

                if client:
                    st.session_state.authenticated = True
                    st.session_state.role = "CLIENT"
                    st.session_state.client_name = client["client_name"]
                    st.session_state.active_page = "Dashboard"
                    st.rerun()
                else:
                    st.error("Invalid or inactive client token.")

    with tab_ceo:
        with st.form("ceo_login_form"):
            st.subheader("CEO Access")
            password = st.text_input("CEO password", type="password")
            submitted = st.form_submit_button("Sign in as CEO", use_container_width=True)

            if submitted:
                if login_ceo(password):
                    st.session_state.authenticated = True
                    st.session_state.role = "CEO"
                    st.session_state.client_name = "CEO"
                    st.session_state.active_page = "Dashboard"
                    st.rerun()
                else:
                    st.error("Invalid CEO password.")