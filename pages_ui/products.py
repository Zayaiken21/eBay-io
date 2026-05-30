import streamlit as st


def render_products() -> None:
    st.markdown(
        """
        <section class="app-hero">
            <h1>Products</h1>
            <p>Import, monitor, and manage eBay listings.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    st.info("Product tools will go here.")