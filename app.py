
import streamlit as st

from config.ceo_settings import CEO_SETTINGS
from core.session import init_session
from ui.styles import load_css
from ui.top_menu import render_top_menu

from pages_ui.login import render_login
from pages_ui.dashboard import render_dashboard
from pages_ui.products import render_products
from pages_ui.analytics import render_analytics
from pages_ui.settings_page import render_settings, process_ebay_oauth_callback_if_present
from pages_ui.ai_blogger import render_ai_blogger
from pages_ui.orders import render_orders
from pages_ui.catalog_pro import render_catalog_pro


st.set_page_config(
    page_title=CEO_SETTINGS["platform_name"],
    page_icon="🟦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

load_css()
init_session()

# eBay redirects back to the app root. Process/save this before the login gate,
# because the returned browser session may not have the same Streamlit state.
process_ebay_oauth_callback_if_present()

PAGES = {
    "Dashboard": render_dashboard,
    "Products": render_products,
    "Analytics": render_analytics,
    "Settings": render_settings,
    "AiBlogger": render_ai_blogger,
    "Orders": render_orders,
    "Catalog-Pro": render_catalog_pro,
}

if not st.session_state.authenticated:
    render_login()
else:
    if st.session_state.active_page not in PAGES:
        st.session_state.active_page = "Dashboard"

    render_top_menu(
        app_name=CEO_SETTINGS["platform_name"],
        pages=list(PAGES.keys()),
    )

    PAGES[st.session_state.active_page]()
