import streamlit as st


def render_orders() -> None:
    st.markdown(
        """
        <section class="app-hero">
            <h1>Orders</h1>
            <p>Track orders, fulfillment, and customer activity.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    st.info("Order tools will go here.")