import streamlit as st


def render_ai_blogger() -> None:
    st.markdown(
        """
        <section class="app-hero">
            <h1>AiBlogger</h1>
            <p>Create SEO product blogs, descriptions, and marketplace content.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    with st.form("ai_blogger_form"):
        topic = st.text_input("Blog topic or product keyword")
        submitted = st.form_submit_button("Generate Draft", use_container_width=True)

        if submitted:
            st.info(f"AI blog workflow will start here for: {topic}")