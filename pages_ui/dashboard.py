import streamlit as st


def render_dashboard() -> None:
    name = st.session_state.get("client_name") or "User"

    st.markdown(
        f"""
        <section class="app-hero">
            <h1>Ebay io Dashboard</h1>
            <p>Welcome back, {name}. Manage listings, orders, automation, and performance.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="dashboard-grid">
            <div class="metric-card">
                <h3>Total Revenue</h3>
                <strong>$12,480</strong>
            </div>
            <div class="metric-card">
                <h3>Active Listings</h3>
                <strong>342</strong>
            </div>
            <div class="metric-card">
                <h3>Orders Today</h3>
                <strong>29</strong>
            </div>
            <div class="metric-card">
                <h3>Estimated Profit</h3>
                <strong>$3,210</strong>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )