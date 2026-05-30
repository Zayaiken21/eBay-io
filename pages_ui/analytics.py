import streamlit as st


def render_analytics() -> None:
    st.markdown(
        """
        <section class="app-hero">
            <h1>Analytics</h1>
            <p>Track revenue, profit, listing health, and automation performance.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    st.info("Analytics tools will go here.")