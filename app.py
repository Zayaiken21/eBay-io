
import os
import shutil
import sys

# Self-heal stale bytecode. __pycache__ directories were committed to this
# repo for a long time; removing them from git does not delete copies already
# sitting in a running container, and Python will load a stale .pyc over
# newer source. That produced tracebacks pointing at docstring lines and
# "fixes" that appeared not to deploy. Clearing them on boot makes the next
# run compile from source. Harmless once they are gone.
if os.environ.get("EBAYIO_PYCACHE_SWEPT") != "1":
    _root = os.path.dirname(os.path.abspath(__file__))
    for _dirpath, _dirnames, _ in os.walk(_root):
        if ".git" in _dirpath:
            continue
        for _d in list(_dirnames):
            if _d == "__pycache__":
                shutil.rmtree(os.path.join(_dirpath, _d), ignore_errors=True)
    os.environ["EBAYIO_PYCACHE_SWEPT"] = "1"
sys.dont_write_bytecode = True

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

# Streamlit session state does not survive a reload or a dropped websocket, so
# a signed-in user was landing back on the login screen whenever they
# refreshed or backgrounded the app. Restore from the signed token the browser
# is holding, before the gate below decides what to render.
from core.remember import restore_session, sync_browser  # noqa: E402

restore_session()

PAGES = {
    "Dashboard": render_dashboard,
    "Products": render_products,
    "Analytics": render_analytics,
    "Settings": render_settings,
    "AiBlogger": render_ai_blogger,
    "Orders": render_orders,
    "Catalog-Pro": render_catalog_pro,
}

# Runs on every script execution, in both branches: signed in it refreshes the
# token's expiry (so the five minutes is idle time, not session length), signed
# out it offers any stored token to the server.
sync_browser()

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
