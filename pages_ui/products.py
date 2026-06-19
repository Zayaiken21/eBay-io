"""
products.py — Pro seller product manager. Per-user, paginated, no item caps.

Auth: owner = st.session_state.client_name (client login) or "ceo" (CEO login),
      matching session.py exactly. Every draft/listing query is scoped to this owner.

Drafts: stored in Supabase via core.draft_store — never a local file, so every
        Streamlit Cloud replica and every redeploy sees the same per-user data.

My Store: pulls LIVE listings directly from eBay's API (not our database) so
          sellers can see and edit what's already posted.

eBay upload: core.ebay_account_store handles token refresh — always fresh,
             never a stale "please sign in" when already connected.
"""

import re
import streamlit as st
import streamlit.components.v1 as components

from ui.scraper         import fetch_product_page
from ui.ai_rewriter     import rewrite_title, rewrite_description, generate_tags, auto_seo_optimize
from ui.ebay_formatter  import generate_ebay_html, generate_ebay_export
from ui.ebay_uploader   import upload_to_ebay, get_seller_policies, get_account_info
from ui.ebay_listings   import fetch_my_listings, fetch_inventory_item
from core.draft_store   import save_draft, load_draft, list_drafts, delete_draft, duplicate_draft

# get_last_error was added to draft_store.py for the Supabase-fallback banner.
# Imported defensively so this page never crashes if an older draft_store.py
# (without this function) is still deployed — it just won't show the banner.
try:
    from core.draft_store import get_last_error
except ImportError:
    def get_last_error():
        return None


# ─── CSS ──────────────────────────────────────────────────────────────────────
CSS = """<style>
*, *::before, *::after { box-sizing: border-box; }

.pro-hero {
    background: linear-gradient(135deg, #0053a0 0%, #002d6b 100%);
    color: #fff; padding: 28px 34px; border-radius: 14px; margin-bottom: 22px;
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 16px; box-shadow: 0 4px 24px rgba(0,83,160,0.18);
}
.hero-left h1  { font-size: 26px; font-weight: 800; margin: 0 0 4px; letter-spacing: -0.4px; }
.hero-left p   { opacity: 0.78; font-size: 13px; margin: 0; }
.hero-stats    { display: flex; gap: 28px; }
.hstat         { text-align: center; }
.hstat-num     { font-size: 30px; font-weight: 900; line-height: 1; }
.hstat-lbl     { font-size: 10px; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 2px; }

.acct-banner {
    display: flex; align-items: center; gap: 10px;
    background: #f0fff4; border: 1px solid #9ae6b4;
    border-radius: 9px; padding: 10px 16px;
    font-size: 13px; color: #276749; font-weight: 600; margin-bottom: 16px;
}
.acct-banner.disconnected { background: #fff8f0; border-color: #fbd38d; color: #744210; }

.import-box {
    background: linear-gradient(135deg, #f0f6ff 0%, #eaf0fe 100%);
    border: 1.5px solid #c3d5f7; border-radius: 12px;
    padding: 26px 28px; margin-bottom: 22px;
}
.import-box h3 { margin: 0 0 5px; color: #1a3a6b; font-size: 16px; font-weight: 800; }
.import-box p  { margin: 0 0 16px; color: #4a5568; font-size: 13px; }
.site-pills    { display: flex; flex-wrap: wrap; gap: 6px; }
.site-pill {
    background: white; border: 1px solid #d1ddf5; border-radius: 20px;
    padding: 3px 11px; font-size: 11px; font-weight: 700; color: #2b5ba8;
}

.draft-row {
    background: white; border: 1px solid #e8edf5; border-radius: 12px;
    padding: 14px 16px; margin-bottom: 9px;
    transition: box-shadow .15s, border-color .15s;
}
.draft-row:hover { box-shadow: 0 3px 16px rgba(0,83,160,.09); border-color: #b8ccf0; }
.draft-title { font-weight: 700; font-size: 14px; color: #1a202c; margin-bottom: 4px; line-height: 1.3; }
.draft-meta  { font-size: 12px; color: #718096; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }

.badge { display:inline-block; padding:2px 9px; border-radius:20px; font-size:11px; font-weight:700; }
.b-draft    { background:#ebf4ff; color:#2b6cb0; }
.b-ready    { background:#f0fff4; color:#276749; }
.b-live     { background:#fffbeb; color:#92400e; border:1px solid #fbd38d; }
.b-exported { background:#fef3c7; color:#92400e; }
.b-ended    { background:#fee2e2; color:#991b1b; }

.sec-hdr {
    font-size: 10px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase;
    color: #4a5568; margin: 26px 0 9px; padding-bottom: 6px;
    border-bottom: 1.5px solid #e8edf5; display: flex; align-items: center; gap: 7px;
}
.sec-hdr-icon { font-size: 13px; }

.seo-row {
    display: flex; align-items: center; gap: 10px;
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
    padding: 10px 14px; margin-bottom: 18px;
}
.seo-track { flex: 1; height: 7px; background: #e2e8f0; border-radius: 4px; overflow: hidden; }
.seo-fill  { height: 100%; border-radius: 4px; transition: width .4s ease; }
.seo-label { font-size: 13px; font-weight: 800; min-width: 100px; }
.seo-lbl-txt { font-size: 12px; font-weight: 700; color: #718096; min-width: 70px; }

.chk-row   { display:flex; align-items:center; gap:8px; font-size:13px; padding:4px 0; }
.chk-ok    { color:#276749; font-size:15px; }
.chk-warn  { color:#d97706; font-size:15px; }

.ef-card {
    background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px;
    padding:12px 14px; margin-bottom:8px;
}
.ef-lbl  { font-size:10px; font-weight:800; color:#718096; text-transform:uppercase; letter-spacing:.08em; margin-bottom:3px; }
.ef-val  { font-size:14px; font-weight:600; color:#1a202c; word-break:break-all; }

.upload-panel {
    background: linear-gradient(135deg,#f0fff4 0%,#e6ffee 100%);
    border: 1.5px solid #9ae6b4; border-radius: 12px; padding: 22px;
}
.upload-panel h4 { color:#276749; margin:0 0 5px; font-size:15px; font-weight:800; }
.upload-panel p  { color:#4a5568; font-size:13px; margin:0 0 14px; }

.no-connect-panel {
    background: #fff8f0; border: 1.5px solid #fbd38d; border-radius: 12px; padding: 22px;
}
.no-connect-panel h4 { color:#92400e; margin:0 0 6px; font-size:15px; font-weight:800; }
.no-connect-panel p  { color:#744210; font-size:13px; margin:0; }

.listing-success {
    background: linear-gradient(135deg,#f0fff4,#e6ffee);
    border: 2px solid #48bb78; border-radius: 12px;
    padding: 22px; text-align: center; margin-top: 16px;
}
.listing-success .ls-id  { font-size: 28px; font-weight: 900; color: #276749; }
.listing-success .ls-sub { font-size: 13px; color: #4a7c59; margin-top: 4px; }

.title-count { font-size: 12px; font-weight: 600; }
.title-ok    { color: #276749; }
.title-warn  { color: #d97706; }
.title-over  { color: #e53e3e; }

/* ── Pagination ── */
.page-info { font-size: 12px; color: #718096; text-align: center; margin: 4px 0; }

/* ── Store listing row ── */
.store-row {
    background: white; border: 1px solid #e8edf5; border-radius: 12px;
    padding: 12px 16px; margin-bottom: 8px;
}
.store-sku { font-size: 11px; color: #a0aec0; font-family: monospace; }

.stTextInput>div>div>input          { border-radius:8px!important; font-size:14px!important; }
.stTextArea>div>div>textarea        { border-radius:8px!important; font-size:14px!important; line-height:1.6!important; }
.stButton>button                    { border-radius:8px!important; font-weight:700!important; font-size:13px!important; }
.stSelectbox>div>div                { border-radius:8px!important; }
.stNumberInput>div>div>input        { border-radius:8px!important; }
div[data-testid="stExpander"]       { border-radius:9px!important; }
div[data-testid="stExpander"] summary { font-weight:600; }
</style>"""


# ─── Session helpers ──────────────────────────────────────────────────────────

def _init():
    for k, v in {
        "prod_tab":        "import",
        "import_result":   None,
        "editing_id":       None,
        "edit_product":     None,
        "export_data":      None,
        "upload_result":    None,
        "policies":         None,
        "drafts_page":      1,
        "store_page":       1,
        "store_data":       None,
        "bot_wall_blocked": False,
        "bot_wall_url":      "",
        "bot_wall_domain":   "",
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v

def _owner() -> str:
    """Resolve owner — matches session.py exactly (client_name or 'ceo')."""
    client_name = st.session_state.get("client_name") or ""
    role        = st.session_state.get("role") or ""
    if client_name:
        return client_name.strip()
    if role == "ceo":
        return "ceo"
    return "default"

def _set_tab(tab: str):
    st.session_state.prod_tab = tab

def _domain_from_url(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


# ─── Hero & account banner ────────────────────────────────────────────────────

def _hero(total_drafts: int):
    role       = st.session_state.get("role") or ""
    owner      = _owner()
    role_label = "CEO" if role == "ceo" else owner
    st.markdown(f"""
    <div class="pro-hero">
      <div class="hero-left">
        <h1>📦 Product Manager</h1>
        <p>Logged in as <strong>{role_label}</strong> · Import · Optimize · Publish</p>
      </div>
      <div class="hero-stats">
        <div class="hstat"><div class="hstat-num">{total_drafts}</div><div class="hstat-lbl">Drafts</div></div>
      </div>
    </div>""", unsafe_allow_html=True)


def _account_banner():
    try:
        info = get_account_info()
    except Exception as e:
        st.markdown(f"""<div class="acct-banner disconnected">
            ⚠️ &nbsp;Could not read eBay account: {e}</div>""", unsafe_allow_html=True)
        return

    if info:
        label   = (info.get("store_name") or info.get("ebay_username")
                   or info.get("ebay_user_id") or "eBay Account")
        env     = info.get("environment", "production")
        env_tag = " · Sandbox" if env != "production" else " · Live"
        st.markdown(f"""<div class="acct-banner">
            ✅ &nbsp;Connected: <strong>{label}</strong>{env_tag}
            &nbsp;·&nbsp; Listings post to this account</div>""", unsafe_allow_html=True)
    else:
        owner = _owner()
        st.markdown(f"""<div class="acct-banner disconnected">
            ⚠️ &nbsp;No eBay account connected for <code>{owner}</code> — go to
            <strong>Settings</strong> to connect your store</div>""", unsafe_allow_html=True)


# ─── Tab navigation ───────────────────────────────────────────────────────────

def _tabs(draft_count: int):
    c1, c2, c3, c4, c5, _ = st.columns([2, 2.3, 2, 2, 2.3, 2])
    for col, key, label in zip(
        [c1, c2, c3, c4, c5],
        ["import", "drafts", "editor", "publish", "store"],
        ["⬇️ Import", f"📝 Drafts ({draft_count})", "✏️ Editor", "🚀 Publish", "🏬 My Store"],
    ):
        with col:
            disabled = (key == "editor" and not st.session_state.edit_product)
            ptype    = "primary" if st.session_state.prod_tab == key else "secondary"
            if st.button(label, key=f"ptab_{key}", type=ptype,
                         use_container_width=True, disabled=disabled):
                _set_tab(key); st.rerun()
    st.markdown("<hr style='margin:10px 0 22px;border-color:#e2e8f0;'>", unsafe_allow_html=True)


# ─── Pagination widget ────────────────────────────────────────────────────────

def _pagination(current_page: int, total_pages: int, key_prefix: str, on_change_key: str) -> int:
    """Renders Prev / page numbers / Next. Returns the (possibly new) page number."""
    if total_pages <= 1:
        return current_page

    new_page = current_page
    # Build a window of page numbers around the current page
    window = 2
    start  = max(1, current_page - window)
    end    = min(total_pages, current_page + window)
    page_nums = list(range(start, end + 1))
    if start > 1: page_nums = [1, "…"] + page_nums
    if end < total_pages: page_nums = page_nums + ["…", total_pages]

    cols = st.columns([1] + [1] * len(page_nums) + [1])
    with cols[0]:
        if st.button("‹ Prev", key=f"{key_prefix}_prev", disabled=current_page <= 1,
                     use_container_width=True):
            new_page = current_page - 1
    for i, pn in enumerate(page_nums):
        with cols[i + 1]:
            if pn == "…":
                st.markdown("<div style='text-align:center;color:#a0aec0;padding-top:6px;'>…</div>",
                            unsafe_allow_html=True)
            else:
                is_current = (pn == current_page)
                if st.button(str(pn), key=f"{key_prefix}_p{pn}",
                             type="primary" if is_current else "secondary",
                             use_container_width=True):
                    new_page = pn
    with cols[-1]:
        if st.button("Next ›", key=f"{key_prefix}_next", disabled=current_page >= total_pages,
                     use_container_width=True):
            new_page = current_page + 1

    if new_page != current_page:
        st.session_state[on_change_key] = new_page
        st.rerun()

    return new_page


# ─── IMPORT TAB ───────────────────────────────────────────────────────────────

def _tab_import():
    st.markdown("""
    <div class="import-box">
      <h3>🔗 Import Product from Any Store</h3>
      <p>Paste any product URL — automatically extracts title, images, price, description, features, and specs,
         then automatically applies SEO optimization. No extra steps needed.</p>
      <div class="site-pills">
        <span class="site-pill">🛒 Amazon</span><span class="site-pill">🏪 eBay</span>
        <span class="site-pill">🔵 Walmart</span><span class="site-pill">🟠 AliExpress</span>
        <span class="site-pill">🛍️ Temu</span><span class="site-pill">🟢 Etsy</span>
        <span class="site-pill">🎯 Target</span><span class="site-pill">💛 Best Buy</span>
        <span class="site-pill">🏠 Home Depot</span><span class="site-pill">+ Any Shopify Store</span>
      </div>
    </div>""", unsafe_allow_html=True)

    url = st.text_input("URL", placeholder="https://www.amazon.com/dp/... or any product page URL",
                        label_visibility="collapsed", key="import_url_field")

    go = st.button("⬇️ Import & Auto-Optimize", type="primary", use_container_width=False)

    if go and url.strip():
        _run_import(url.strip())
    elif go:
        st.warning("Paste a product URL above first.")

    if st.session_state.import_result:
        _show_import_result(st.session_state.import_result)

    if st.session_state.get("bot_wall_blocked"):
        _show_manual_entry_form()


def _run_import(url: str):
    """
    Imports a product AND automatically applies SEO optimization —
    no button click required. This always runs; there is no manual-only path
    UNLESS the source site blocks the request with a bot-check page, in which
    case we route straight to a polished manual entry form instead of
    confusingly extracting garbage from the challenge page.
    """
    bar = st.progress(0, text="Starting...")
    bar.progress(15, "📡 Connecting to store...")
    result = fetch_product_page(url)
    bar.progress(70, "🔍 Parsing product data...")

    if not result["success"]:
        bar.empty()
        if result.get("bot_wall"):
            st.error(f"🛑 {result['error']}")
            st.session_state.bot_wall_blocked = True
            st.session_state.bot_wall_url     = url
            st.session_state.bot_wall_domain  = _domain_from_url(url)
        else:
            st.error(f"❌ {result['error']}")
        return

    st.session_state.bot_wall_blocked = False

    if result.get("note"):
        st.info(f"ℹ️ {result['note']}")

    product = result["product"]

    bar.progress(90, "✨ Auto-optimizing title, description & tags...")
    product = auto_seo_optimize(product)
    product["seo_auto_applied"] = True

    bar.progress(100, "✅ Done!")
    bar.empty()
    st.session_state.import_result = product


def _show_import_result(p: dict):
    conf      = p.get("confidence", "low")
    conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf, "⚪")
    conf_msg  = {"high": "All key fields extracted", "medium": "Some fields may need filling",
                 "low":  "Limited data — JS site, fill manually"}.get(conf, "")

    st.markdown(f"""
    <div style="margin:18px 0 12px;display:flex;align-items:center;gap:10px;">
      <span style="font-size:16px;font-weight:800;">Extracted Product</span>
      <span class="badge b-draft">{conf_icon} {conf.title()} Confidence</span>
      <span style="font-size:12px;color:#718096;">{conf_msg}</span>
    </div>""", unsafe_allow_html=True)

    c1, c2 = st.columns([3, 2])
    with c1:
        for lbl, key, pfx, fallback in [
            ("Title",    "title",    "",   "_(not found)_"),
            ("Price",    "price",    "$",  "_(not found)_"),
            ("Brand",    "brand",    "",   "_(unknown)_"),
            ("Category", "category", "",   "_(not found)_"),
        ]:
            val = p.get(key, "")
            st.markdown(f"**{lbl}**")
            st.write((pfx + val) if val else fallback)
        desc = p.get("description", "")
        st.markdown("**Description preview**")
        st.write((desc[:280] + "…") if len(desc) > 280 else desc or "_(not found)_")
        if p.get("features"):
            st.markdown("**Key Features**")
            for f in p["features"][:4]:
                st.write(f"• {f}")
    with c2:
        imgs = p.get("images", [])
        st.markdown(f"**{len(imgs)} image{'s' if len(imgs) != 1 else ''} found**")
        for img in imgs[:4]:
            try:    st.image(img, use_container_width=True)
            except: st.caption(f"🖼️ {img[:50]}…")
        if len(imgs) > 4:
            st.caption(f"+ {len(imgs) - 4} more")

    with st.expander("📋 All extracted data", expanded=False):
        st.json({k: v for k, v in p.items() if k not in ("description", "ebay_html")})

    if p.get("seo_auto_applied"):
        st.success("✨ SEO auto-optimization already applied to this import.")

    st.markdown("---")
    ca, cb, cc = st.columns(3)
    with ca:
        if st.button("💾 Save Draft", type="primary", use_container_width=True):
            did = save_draft(p)
            st.success(f"Saved — ID `{did}`")
            st.session_state.import_result = None
            _set_tab("drafts"); st.rerun()
    with cb:
        if st.button("✏️ Edit Now", use_container_width=True):
            did = save_draft(p); _open_editor(did); st.rerun()
    with cc:
        if st.button("🗑️ Discard", use_container_width=True):
            st.session_state.import_result = None; st.rerun()


def _show_manual_entry_form():
    """
    Shown automatically when a source site blocks automated fetching with a
    bot-check/CAPTCHA page (Walmart, Amazon, and similar large retailers do
    this routinely). Pre-fills whatever context we have (domain, source URL)
    and lets the seller paste in the rest by hand — the durable, reliable
    path for sites with strong anti-automation protection.
    """
    domain = st.session_state.get("bot_wall_domain", "")
    src_url = st.session_state.get("bot_wall_url", "")

    st.markdown(f"""
    <div class="import-box" style="border-color:#fbd38d;background:linear-gradient(135deg,#fff8f0,#fff3e0);">
      <h3 style="color:#92400e;">✋ Manual Entry — {domain or "this site"} blocked automated access</h3>
      <p style="color:#744210;">
        This site uses anti-bot protection (CAPTCHA / "press and hold to verify you're human")
        that no page-fetching tool can pass — only a real browser with a human
        actually completing the challenge can get through. Paste the product details
        below and we'll still auto-apply SEO optimization, image handling, and
        eBay formatting exactly like an automatic import.
      </p>
    </div>""", unsafe_allow_html=True)

    with st.form("manual_entry_form", clear_on_submit=False):
        st.text_input("Source URL (for reference)", value=src_url, disabled=True)

        m_title = st.text_input("Product Title*", placeholder="Paste the exact product title from the page")
        col1, col2, col3 = st.columns(3)
        with col1:
            m_price = st.text_input("Price (USD)", placeholder="29.99")
        with col2:
            m_brand = st.text_input("Brand", placeholder="e.g. Crest")
        with col3:
            m_condition = st.selectbox("Condition", ["New", "New with tags", "Pre-owned", "Good", "For parts"])

        m_category = st.text_input("Category", placeholder="e.g. Health & Beauty > Oral Care")
        m_description = st.text_area("Description", height=150,
            placeholder="Paste the product description from the page")
        m_features = st.text_area("Key Features (one per line)", height=100,
            placeholder="Long-lasting mint flavor\n3-pack\nWhitens teeth")
        m_image_urls = st.text_area("Image URLs (one per line)", height=100,
            placeholder="Right-click each product photo → Copy Image Address → paste here, one per line")

        submitted = st.form_submit_button("✨ Create Draft & Auto-Optimize", type="primary")

    if submitted:
        if not m_title.strip():
            st.warning("Product title is required.")
            return

        clean_price = re.sub(r"[^\d.]", "", m_price) if m_price else ""

        product = {
            "title":          m_title.strip(),
            "price":          clean_price,
            "brand":          m_brand.strip(),
            "condition":      m_condition,
            "category":       m_category.strip(),
            "description":    m_description.strip(),
            "features":       [f.strip() for f in m_features.splitlines() if f.strip()],
            "specifications": {},
            "images":         [u.strip() for u in m_image_urls.splitlines() if u.strip().startswith("http")],
            "sku":            "",
            "weight":         "",
            "dimensions":     "",
            "variants":       [],
            "tags":           [],
            "domain":         domain,
            "source_url":     src_url,
            "status":         "draft",
            "currency":       "USD",
            "confidence":     "manual",
        }

        product = auto_seo_optimize(product)
        product["seo_auto_applied"] = True

        did = save_draft(product)
        st.success(f"✅ Draft created and SEO-optimized — ID `{did}`")
        st.session_state.bot_wall_blocked = False
        _open_editor(did)
        st.rerun()


# ─── DRAFTS TAB ───────────────────────────────────────────────────────────────

PAGE_SIZE = 12

def _tab_drafts():
    cs, cf = st.columns([4, 2])
    with cs:
        srch = st.text_input("Search", placeholder="🔍 Search by title…",
                              label_visibility="collapsed", key="dsrch")
    with cf:
        filt = st.selectbox("Filter", ["All", "Draft", "Ready", "Live", "Exported"],
                             label_visibility="collapsed", key="dfilt")

    page = st.session_state.drafts_page
    try:
        result = list_drafts(page=page, page_size=PAGE_SIZE)
    except Exception as e:
        st.error(f"⚠️ Could not load drafts: {e}")
        st.caption("Check that `core/draft_store.py` is the latest version and the `product_drafts` table exists in Supabase.")
        return
    drafts = result["items"]

    if result["total"] == 0:
        st.info("No drafts yet. Import a product to get started.")
        if st.button("⬇️ Go to Import", type="primary"):
            _set_tab("import"); st.rerun()
        return

    # Client-side search/filter only applies within current page for display;
    # for a true cross-page search you'd want a server-side query — but with
    # no item cap, paging + light client filtering keeps this fast.
    shown = [d for d in drafts if
             (not srch or srch.lower() in d.get("title","").lower()) and
             (filt == "All" or d.get("status","draft").lower() == filt.lower())]

    st.markdown(f"<div style='font-size:12px;color:#718096;margin-bottom:12px;'>"
                f"{result['total']} total drafts · page {result['page']} of {result['total_pages']}</div>",
                unsafe_allow_html=True)

    for d in shown:
        did    = d.get("draft_id", "?")
        title  = d.get("title", "Untitled")
        price  = d.get("price", "")
        domain = d.get("domain", "")
        imgs   = d.get("images", [])
        status = d.get("status", "draft")
        conf   = d.get("confidence", "low")
        upd    = d.get("updated_at","")[:16].replace("T"," ")

        bmap = {"draft":"b-draft","ready":"b-ready","live":"b-live","exported":"b-exported"}
        cdot = {"high":"#48bb78","medium":"#ed8936","low":"#fc8181"}.get(conf,"#a0aec0")

        ci, cd_info, ca = st.columns([1, 5, 2])
        with ci:
            if imgs:
                try:    st.image(imgs[0], width=70)
                except: st.markdown("📦")
            else:
                st.markdown("<div style='width:70px;height:70px;background:#f0f4f9;border-radius:8px;"
                            "display:flex;align-items:center;justify-content:center;font-size:26px;'>📦</div>",
                            unsafe_allow_html=True)
        with cd_info:
            st.markdown(f"""
            <div class="draft-title">{title[:65]}{"…" if len(title)>65 else ""}</div>
            <div class="draft-meta">
              <span class="badge {bmap.get(status,'b-draft')}">{status.title()}</span>
              <span>{"$"+price if price else "No price"}</span>
              {"<span>"+domain+"</span>" if domain else ""}
              <span>{len(imgs)} img{"s" if len(imgs)!=1 else ""}</span>
              <span style="color:{cdot};">● {conf.title()}</span>
              <span>Updated {upd}</span>
            </div>""", unsafe_allow_html=True)
        with ca:
            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("✏️", key=f"e_{did}", help="Edit"):
                    _open_editor(did); st.rerun()
            with b2:
                if st.button("⧉",  key=f"d_{did}", help="Duplicate"):
                    duplicate_draft(did); st.rerun()
            with b3:
                if st.button("🗑",  key=f"x_{did}", help="Delete"):
                    delete_draft(did); st.rerun()

        st.markdown("<div style='border-top:1px solid #f0f4f9;margin:5px 0;'></div>",
                    unsafe_allow_html=True)

    st.markdown("---")
    _pagination(result["page"], result["total_pages"], "drafts", "drafts_page")

    st.markdown("---")
    if st.button("✨ Auto-SEO This Page", use_container_width=False,
                 help="Optimize all draft-status listings on this page"):
        count = 0
        for d in drafts:
            if d.get("status", "draft") == "draft":
                save_draft(auto_seo_optimize(d), d.get("draft_id"))
                count += 1
        st.success(f"Optimized {count} drafts on this page!")
        st.rerun()


# ─── EDITOR TAB ───────────────────────────────────────────────────────────────

def _open_editor(draft_id: str):
    """
    Opens a draft in the editor. If SEO has never been auto-applied to this
    draft, it's applied automatically right now — no button click required.
    Subsequent manual edits are preserved; SEO only auto-runs once per draft
    unless the seller explicitly clicks ✨ Auto SEO again.
    """
    p = load_draft(draft_id)
    if p:
        if not p.get("seo_auto_applied"):
            p = auto_seo_optimize(p)
            p["seo_auto_applied"] = True
            save_draft(p, draft_id)

        st.session_state.editing_id    = draft_id
        st.session_state.edit_product  = dict(p)
        st.session_state.export_data   = None
        st.session_state.upload_result = None
        st.session_state.prod_tab      = "editor"

def _open_editor_from_ebay(sku: str):
    """Pull a LIVE eBay listing into the editor (creates a local draft mirror)."""
    result = fetch_inventory_item(sku)
    if not result["success"]:
        st.error(f"Could not load listing: {result['error']}")
        return
    product = result["product"]
    did = save_draft(product)
    _open_editor(did)


def _seo_score(p: dict) -> int:
    tl = len(p.get("title",""))
    return min(100, sum([
        20 if 30 <= tl <= 80 else (10 if tl > 0 else 0),
        10 if p.get("price") else 0,
        20 if len(p.get("description","")) > 200 else (10 if len(p.get("description","")) > 50 else 0),
        15 if len(p.get("images",[])) >= 3 else (8 if p.get("images") else 0),
        10 if len(p.get("features",[])) >= 3 else (5 if p.get("features") else 0),
        10 if p.get("specifications") else 0,
        10 if len(p.get("tags",[])) >= 5 else (5 if p.get("tags") else 0),
        5  if p.get("brand") else 0,
    ]))

def _checklist(p: dict) -> list:
    tl = len(p.get("title",""))
    return [
        (f"Title {tl}/80 chars",            20 <= tl <= 80),
        ("Price set",                        bool(p.get("price"))),
        ("3+ images",                        len(p.get("images",[])) >= 3),
        ("Description > 200 chars",          len(p.get("description","")) > 200),
        ("3+ key features",                  len(p.get("features",[])) >= 3),
        ("Specifications added",             bool(p.get("specifications"))),
        ("5+ search tags",                   len(p.get("tags",[])) >= 5),
        ("Brand filled in",                  bool(p.get("brand"))),
    ]


def _tab_editor():
    if not st.session_state.edit_product:
        st.info("No draft open. Go to Drafts and click ✏️ Edit.")
        if st.button("← Drafts"): _set_tab("drafts"); st.rerun()
        return

    p   = st.session_state.edit_product
    did = st.session_state.editing_id

    cb, cm, cseo, csave = st.columns([1, 4, 2, 1])
    with cb:
        if st.button("← Drafts"):
            st.session_state.edit_product = None
            _set_tab("drafts"); st.rerun()
    with cm:
        st.markdown(f"<div style='font-size:14px;font-weight:700;padding-top:7px;color:#1a202c;'>"
                    f"✏️ {p.get('title','Untitled')[:55]}</div>", unsafe_allow_html=True)
    with cseo:
        if st.button("🔄 Re-optimize", type="secondary", use_container_width=True,
                     help="SEO was already auto-applied when this draft was created. Click to re-run it against your current edits."):
            with st.spinner("Optimizing…"):
                p = auto_seo_optimize(p)
                p["seo_auto_applied"] = True
                st.session_state.edit_product = p
            save_draft(p, did)
            st.success("SEO re-applied & saved!")
            st.rerun()
    with csave:
        if st.button("💾", use_container_width=True, help="Save draft"):
            save_draft(p, did); st.toast("Saved!", icon="✅")

    score = _seo_score(p)
    color = "#48bb78" if score >= 70 else "#ed8936" if score >= 40 else "#fc8181"
    label = "Excellent" if score >= 85 else "Good" if score >= 70 else "Fair" if score >= 40 else "Needs Work"
    st.markdown(f"""
    <div class="seo-row">
      <span class="seo-lbl-txt">SEO Score</span>
      <div class="seo-track"><div class="seo-fill" style="width:{score}%;background:{color};"></div></div>
      <span class="seo-label" style="color:{color};">{score}/100</span>
      <span style="font-size:12px;color:#718096;">{label}</span>
    </div>""", unsafe_allow_html=True)

    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">🏷️</span>Title</div>',
                    unsafe_allow_html=True)
        ct, ctb = st.columns([6, 1])
        with ct:
            p["title"] = st.text_input("Title", value=p.get("title",""),
                                        label_visibility="collapsed", key="ed_title")
        with ctb:
            if st.button("✨", use_container_width=True, help="Clean & optimize title"):
                r = rewrite_title(p.get("title",""), brand=p.get("brand",""),
                                   category=p.get("category",""), features=p.get("features",[]))
                if r["success"]: p["title"] = r["title"]; st.rerun()

        tl = len(p.get("title",""))
        tcls = "title-ok" if tl <= 80 else "title-over"
        tcls = tcls if tl >= 20 else "title-warn"
        st.markdown(f'<div class="title-count {tcls}">{tl}/80 characters</div>',
                    unsafe_allow_html=True)

        st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">📋</span>Listing Details</div>',
                    unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1: p["price"]  = st.text_input("Price (USD)", value=p.get("price",""), key="ed_price")
        with c2:
            cond_opts = ["New","New with tags","New without tags","New with defects",
                         "Pre-owned","Good","Acceptable","For parts"]
            cur = p.get("condition","New")
            p["condition"] = st.selectbox("Condition", cond_opts,
                                           index=cond_opts.index(cur) if cur in cond_opts else 0,
                                           key="ed_cond")
        with c3: p["category"] = st.text_input("Category", value=p.get("category",""), key="ed_cat")

        c4, c5 = st.columns(2)
        with c4: p["brand"] = st.text_input("Brand", value=p.get("brand",""), key="ed_brand")
        with c5: p["sku"]   = st.text_input("SKU",   value=p.get("sku",""),   key="ed_sku")

        c6, c7 = st.columns(2)
        with c6: p["weight"]     = st.text_input("Weight",     value=p.get("weight",""),     key="ed_wt")
        with c7: p["dimensions"] = st.text_input("Dimensions", value=p.get("dimensions",""), key="ed_dims")

        p["quantity"] = st.number_input("Quantity", min_value=1, max_value=999999,
                                         value=int(p.get("quantity", 1)), key="ed_qty")

        st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">📝</span>Description</div>',
                    unsafe_allow_html=True)
        cdb, _ = st.columns([2, 5])
        with cdb:
            if st.button("✨ SEO Rewrite", use_container_width=True, key="btn_desc_rw"):
                with st.spinner("Rewriting…"):
                    r = rewrite_description(
                        title=p.get("title",""),
                        original_description=p.get("description",""),
                        features=p.get("features",[]),
                        specifications=p.get("specifications",{}),
                        category=p.get("category",""),
                        brand=p.get("brand",""),
                    )
                    if r["success"]: p["description"] = r["description"]; st.rerun()

        p["description"] = st.text_area("Description", value=p.get("description",""),
                                          height=260, label_visibility="collapsed", key="ed_desc")
        st.caption(f"{len(p.get('description',''))} characters")

        st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">✅</span>Key Features</div>',
                    unsafe_allow_html=True)
        feats_ed = st.text_area("Features (one per line)", value="\n".join(p.get("features",[])),
                                 height=120, label_visibility="collapsed", key="ed_feats")
        p["features"] = [f.strip() for f in feats_ed.splitlines() if f.strip()]

        st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">🔧</span>Specifications</div>',
                    unsafe_allow_html=True)
        specs_raw = "\n".join(f"{k}: {v}" for k,v in (p.get("specifications") or {}).items())
        specs_ed  = st.text_area("Key: Value per line", value=specs_raw, height=120,
                                  label_visibility="collapsed", key="ed_specs")
        new_specs = {}
        for line in specs_ed.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                if k.strip(): new_specs[k.strip()] = v.strip()
        p["specifications"] = new_specs

        st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">🏷️</span>Search Tags</div>',
                    unsafe_allow_html=True)
        ctg, ctgb = st.columns([6, 1])
        with ctgb:
            if st.button("✨", use_container_width=True, key="btn_tags", help="Generate tags"):
                r = generate_tags(p.get("title",""), p.get("description",""), p.get("category",""))
                if r["success"]: p["tags"] = r["tags"]; st.rerun()
        with ctg:
            tags_ed = st.text_input("Tags (comma-separated)", value=", ".join(p.get("tags",[])),
                                     label_visibility="collapsed", key="ed_tags")
            p["tags"] = [t.strip() for t in tags_ed.split(",") if t.strip()]

    with col_r:
        st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">🖼️</span>'
                    f'Images ({len(p.get("images",[]))}/12)</div>', unsafe_allow_html=True)
        imgs   = p.get("images", [])
        to_del = []
        for i in range(0, len(imgs), 3):
            row   = imgs[i:i+3]
            rcols = st.columns(3)
            for j, url in enumerate(row):
                with rcols[j]:
                    try:    st.image(url, use_container_width=True)
                    except: st.markdown("🖼️")
                    if st.button("✕", key=f"rim_{i+j}", use_container_width=True):
                        to_del.append(i+j)
                    st.caption(f"#{i+j+1}")
        if to_del:
            p["images"] = [u for idx,u in enumerate(imgs) if idx not in to_del]
            st.rerun()

        with st.expander("➕ Add image URLs"):
            new_urls_input = st.text_area("One per line", height=70,
                                           label_visibility="collapsed", key="add_imgs")
            if st.button("Add Images", use_container_width=True):
                added = [u.strip() for u in new_urls_input.splitlines()
                         if u.strip().startswith("http")]
                p["images"] = (p.get("images") or []) + added
                st.rerun()
        st.caption("💡 Image #1 = eBay cover photo")

        st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">📊</span>SEO Checklist</div>',
                    unsafe_allow_html=True)
        for item, ok in _checklist(p):
            icon = '<span class="chk-ok">✅</span>' if ok else '<span class="chk-warn">⚠️</span>'
            st.markdown(f'<div class="chk-row">{icon} {item}</div>', unsafe_allow_html=True)

        if p.get("source_url"):
            st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">🔗</span>Source</div>',
                        unsafe_allow_html=True)
            st.caption(f"[{p.get('domain','')}]({p['source_url']})")

        if p.get("ebay_listing_url"):
            st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">🛒</span>Live Listing</div>',
                        unsafe_allow_html=True)
            st.caption(f"[View on eBay]({p['ebay_listing_url']})")

    st.session_state.edit_product = p

    st.markdown("---")
    cg, cp2, cclose = st.columns([3, 2, 2])
    with cg:
        if st.button("📋 Generate eBay Listing", type="primary", use_container_width=True):
            p["ebay_html"] = generate_ebay_html(p)
            p["status"]    = "ready"
            save_draft(p, did)
            st.session_state.export_data  = generate_ebay_export(p)
            st.session_state.edit_product = p
            st.success("Listing generated! Go to the Publish tab to preview and post.")
    with cp2:
        if st.session_state.export_data:
            if st.button("🚀 Go to Publish", use_container_width=True):
                _set_tab("publish"); st.rerun()
    with cclose:
        if st.button("💾 Save & Close", use_container_width=True):
            save_draft(p, did)
            st.session_state.edit_product = None
            _set_tab("drafts"); st.rerun()


# ─── PUBLISH TAB ──────────────────────────────────────────────────────────────

def _tab_publish():
    p   = st.session_state.edit_product
    exp = st.session_state.export_data

    if not p:
        st.info("Open a draft in the Editor first.")
        if st.button("← Editor"): _set_tab("editor"); st.rerun()
        return

    if not exp:
        st.info("Click **Generate eBay Listing** in the Editor tab first.")
        if st.button("← Editor"): _set_tab("editor"); st.rerun()
        return

    st.markdown(f"<div style='font-size:16px;font-weight:800;margin-bottom:16px;'>"
                f"🛒 {p.get('title','')[:60]}</div>", unsafe_allow_html=True)

    t_copy, t_html, t_preview, t_upload = st.tabs(
        ["📋 Copy Fields", "📄 HTML Code", "👁️ Live Preview", "🚀 Upload to eBay"]
    )

    with t_copy:
        st.markdown('<div class="ef-card"><div class="ef-lbl">eBay Title</div>'
                    f'<div class="ef-val">{exp.get("title","")}</div></div>', unsafe_allow_html=True)
        st.code(exp.get("title",""), language=None)

        c1, c2 = st.columns(2)
        with c1:
            for lbl, key in [("Category","category"),("Condition","condition"),
                              ("Price","price"),("SKU","sku"),("Quantity","quantity")]:
                val = exp.get(key,"")
                if key == "price" and val: val = f"${val}"
                st.markdown(f'<div class="ef-card"><div class="ef-lbl">{lbl}</div>'
                            f'<div class="ef-val">{val or "—"}</div></div>', unsafe_allow_html=True)
        with c2:
            st.markdown(f'<div class="ef-card"><div class="ef-lbl">Search Tags</div>'
                        f'<div class="ef-val">{", ".join(exp.get("tags",[]))}</div></div>',
                        unsafe_allow_html=True)
            st.markdown('<div class="ef-card"><div class="ef-lbl">Item Specifics</div>'
                        '<div class="ef-val">' +
                        "".join(f"<div>· <b>{k}</b>: {v}</div>"
                                for k,v in list(exp.get("item_specifics",{}).items())[:8]) +
                        "</div></div>", unsafe_allow_html=True)

        if exp.get("images"):
            st.markdown("**Image URLs** — upload to eBay image manager:")
            for i, url in enumerate(exp["images"], 1):
                st.code(url, language=None)

    with t_html:
        st.info("Copy → eBay Create Listing → Description → click **HTML** button → paste.")
        st.text_area("HTML", value=exp.get("description_html",""),
                      height=400, label_visibility="collapsed", key="pub_html_out")

    with t_preview:
        st.markdown("**Live Preview:**")
        components.html(exp.get("description_html",""), height=960, scrolling=True)

    with t_upload:
        _upload_panel(p, exp)


def _upload_panel(p: dict, exp: dict):
    did   = st.session_state.editing_id
    owner = _owner()

    try:
        info = get_account_info()
    except Exception as e:
        st.error(f"❌ Could not read eBay account: {e}")
        st.caption(f"Resolved owner_name: `{owner}`")
        return

    if not info:
        st.markdown(f"""
        <div class="no-connect-panel">
          <h4>⚠️ No eBay Account Connected</h4>
          <p>Go to <strong>Settings → eBay</strong> to connect your store via OAuth,
          then return here to publish.</p>
        </div>""", unsafe_allow_html=True)
        st.caption(f"Resolved owner_name: `{owner}` · Make sure the eBay account was connected under this name.")
        with st.expander("📋 Publish manually instead"):
            st.markdown("""
1. Go to the **HTML Code** tab above and copy the HTML
2. On eBay → Create Listing → Description → click **HTML** → paste
3. Fill in Title, Price, Category from **Copy Fields**
4. Upload images from the image URL list
            """)
        return

    acct_label     = (info.get("store_name") or info.get("ebay_username")
                      or info.get("ebay_user_id") or "Your eBay Store")
    env            = info.get("environment", "production")
    env_tag        = " · Sandbox" if env != "production" else " · Live"
    resolved_owner = info.get("_resolved_owner", owner)

    st.markdown(f"""
    <div class="upload-panel">
      <h4>🚀 Publish to eBay{env_tag}</h4>
      <p>Listing will be posted to <strong>{acct_label}</strong>.
         Token auto-refreshes on every publish — never stale.</p>
    </div>""", unsafe_allow_html=True)
    st.caption(f"Owner: `{resolved_owner}` · Environment: `{env}`")

    st.markdown("**Listing Policies** — required by eBay Inventory API:")
    cload, _ = st.columns([2, 5])
    with cload:
        if st.button("🔄 Load my eBay policies", use_container_width=True, key="load_policies"):
            with st.spinner("Fetching from eBay…"):
                result = get_seller_policies()
                if result.get("error"):
                    st.error(f"Could not load policies: {result['error']}")
                else:
                    st.session_state.policies = result
                    st.rerun()

    policies = st.session_state.policies or {}
    cf, cpcol, cr = st.columns(3)

    with cf:
        ff = policies.get("fulfillment", [])
        if ff:
            sel = st.selectbox("Fulfillment", [x["name"] for x in ff], key="pol_f")
            p["fulfillment_policy_id"] = next(x["id"] for x in ff if x["name"] == sel)
        else:
            p["fulfillment_policy_id"] = st.text_input("Fulfillment Policy ID",
                value=p.get("fulfillment_policy_id",""), key="pol_f_txt")

    with cpcol:
        pp = policies.get("payment", [])
        if pp:
            sel = st.selectbox("Payment", [x["name"] for x in pp], key="pol_p")
            p["payment_policy_id"] = next(x["id"] for x in pp if x["name"] == sel)
        else:
            p["payment_policy_id"] = st.text_input("Payment Policy ID",
                value=p.get("payment_policy_id",""), key="pol_p_txt")

    with cr:
        rr = policies.get("return", [])
        if rr:
            sel = st.selectbox("Return", [x["name"] for x in rr], key="pol_r")
            p["return_policy_id"] = next(x["id"] for x in rr if x["name"] == sel)
        else:
            p["return_policy_id"] = st.text_input("Return Policy ID",
                value=p.get("return_policy_id",""), key="pol_r_txt")

    locs = policies.get("locations", []) or []
    enabled_locs = [x for x in locs if x.get("enabled", True)]
    if enabled_locs:
        labels = [f"{x.get('name') or x.get('key')} · key: {x.get('key')}" for x in enabled_locs]
        current_key = p.get("merchant_location_key", "")
        current_idx = 0
        for i, x in enumerate(enabled_locs):
            if x.get("key") == current_key:
                current_idx = i
                break
        sel = st.selectbox(
            "Inventory Location",
            labels,
            index=current_idx,
            key="loc_select",
            help="This uses the real eBay merchantLocationKey returned by Inventory API."
        )
        p["merchant_location_key"] = enabled_locs[labels.index(sel)]["key"]
        st.caption(f"Using real merchantLocationKey: `{p['merchant_location_key']}`")
    else:
        st.info("No enabled eBay inventory location was returned. The app will create/use `MAINWAREHOUSE` automatically before publishing.")
        p["merchant_location_key"] = "MAINWAREHOUSE"

        st.markdown("**Warehouse address for automatic eBay location creation**")
        ca, cb = st.columns([2, 1])
        with ca:
            p["warehouse_address"] = st.text_input("Warehouse address", value=p.get("warehouse_address", "2083 e 19th St"), key="wh_addr")
        with cb:
            p["warehouse_name"] = st.text_input("Location name", value=p.get("warehouse_name", "Main Warehouse"), key="wh_name")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            p["warehouse_city"] = st.text_input("City", value=p.get("warehouse_city", "Brooklyn"), key="wh_city")
        with c2:
            p["warehouse_state"] = st.text_input("State", value=p.get("warehouse_state", "NY"), key="wh_state")
        with c3:
            p["warehouse_postal"] = st.text_input("Postal / ZIP", value=p.get("warehouse_postal", "11229"), key="wh_zip")
        with c4:
            p["warehouse_country"] = st.text_input("Country", value=p.get("warehouse_country", "US"), key="wh_country")
        st.caption("eBay will create/use merchantLocationKey `MAINWAREHOUSE`. Keys cannot be renamed after creation.")

    p["ebay_html"] = exp.get("description_html", "")
    st.session_state.edit_product = p

    st.markdown("---")

    missing_policies = not all([
        p.get("fulfillment_policy_id"), p.get("payment_policy_id"), p.get("return_policy_id"),
    ])
    if missing_policies:
        st.warning("⚠️ Load your eBay policies above and select fulfillment, payment, and return policies before publishing.")

    if st.button("🚀 Publish to eBay", type="primary",
                 use_container_width=True, disabled=missing_policies):
        with st.spinner("Publishing to eBay…"):
            result = upload_to_ebay(p)
        st.session_state.upload_result = result
        if result["success"]:
            p["status"]           = "live"
            p["ebay_listing_id"]  = result["listing_id"]
            p["ebay_listing_url"] = result["listing_url"]
            if result.get("merchant_location_key"):
                p["merchant_location_key"] = result["merchant_location_key"]
            save_draft(p, did)
            st.session_state.edit_product = p
        st.rerun()

    ur = st.session_state.upload_result
    if ur:
        if ur.get("success"):
            env_lbl = "Sandbox" if ur.get("environment","production") != "production" else "Live"
            st.markdown(f"""
            <div class="listing-success">
              <div style="font-size:32px;margin-bottom:8px;">🎉</div>
              <div class="ls-id">{ur.get('listing_id','')}</div>
              <div class="ls-sub">Published to eBay {env_lbl} · SKU: {ur.get('sku','')}</div>
            </div>""", unsafe_allow_html=True)
            if ur.get("listing_url"):
                st.markdown(f"\n🔗 **[View your listing on eBay]({ur['listing_url']})**")
        else:
            st.error(f"❌ Upload failed: {ur['error']}")
            with st.expander("Troubleshooting"):
                st.markdown("""
- **Policy IDs**: Must be valid policies from your own eBay account, matching the environment (sandbox policies won't work in production and vice versa)
- **Merchant Location**: Must match a location key set up in eBay Seller Hub → Locations
- **Token**: If you reconnected eBay recently and still see auth errors, disconnect and reconnect in Settings
- **Content-Language**: Handled automatically (en-US) — no action needed
                """)


# ─── MY STORE TAB ─────────────────────────────────────────────────────────────

STORE_PAGE_SIZE = 25

def _tab_store():
    st.markdown("<div style='font-size:16px;font-weight:800;margin-bottom:4px;'>"
                "🏬 My eBay Store</div>", unsafe_allow_html=True)
    st.caption("Live listings pulled directly from your connected eBay account — not from our database.")

    try:
        info = get_account_info()
    except Exception as e:
        st.error(f"Could not check eBay connection: {e}")
        return

    if not info:
        st.markdown("""
        <div class="no-connect-panel">
          <h4>⚠️ No eBay Account Connected</h4>
          <p>Connect your store in <strong>Settings</strong> to see your live listings here.</p>
        </div>""", unsafe_allow_html=True)
        return

    crf, _ = st.columns([2, 5])
    with crf:
        if st.button("🔄 Refresh listings", use_container_width=True):
            st.session_state.store_data = None
            st.rerun()

    if st.session_state.store_data is None or st.session_state.store_data.get("page") != st.session_state.store_page:
        with st.spinner("Loading your eBay listings…"):
            st.session_state.store_data = fetch_my_listings(
                page=st.session_state.store_page, page_size=STORE_PAGE_SIZE
            )

    data = st.session_state.store_data

    if not data["success"]:
        st.error(f"❌ {data['error']}")
        return

    if data["total"] == 0:
        st.info("No live listings found on your eBay store yet.")
        return

    st.markdown(f"<div style='font-size:12px;color:#718096;margin-bottom:12px;'>"
                f"{data['total']} live listings · page {data['page']} of {data['total_pages']}</div>",
                unsafe_allow_html=True)

    for item in data["items"]:
        c1, c2, c3 = st.columns([4, 2, 2])
        with c1:
            st.markdown(f"""
            <div class="store-row">
              <div class="draft-title">{item['title'][:70]}</div>
              <div class="draft-meta">
                <span class="badge b-live">{item.get('status','ACTIVE')}</span>
                <span>${item.get('price','—')}</span>
                <span>Qty: {item.get('quantity', 0)}</span>
                <span class="store-sku">SKU: {item.get('sku','')}</span>
              </div>
            </div>""", unsafe_allow_html=True)
        with c2:
            if item.get("listing_url"):
                st.markdown(f"[🔗 View on eBay]({item['listing_url']})")
        with c3:
            if st.button("✏️ Edit this listing", key=f"store_edit_{item['sku']}", use_container_width=True):
                _open_editor_from_ebay(item["sku"])
                st.rerun()

    st.markdown("---")
    _pagination(data["page"], data["total_pages"], "store", "store_page")


# ─── Main ─────────────────────────────────────────────────────────────────────

def render_products() -> None:
    _init()
    st.markdown(CSS, unsafe_allow_html=True)

    try:
        drafts_count = list_drafts(page=1, page_size=1)["total"]
    except Exception as e:
        st.error(
            f"⚠️ Could not load drafts from the database: {e}\n\n"
            "This usually means `core/draft_store.py` in this deployment is an older "
            "version, or the `product_drafts` table doesn't exist yet in Supabase. "
            "Drafts will not work correctly until this is fixed."
        )
        drafts_count = 0

    try:
        store_err = get_last_error()
    except Exception:
        store_err = None
    if store_err:
        st.warning(f"⚠️ {store_err}", icon="⚠️")

    _hero(drafts_count)
    _account_banner()
    _tabs(drafts_count)

    tab = st.session_state.prod_tab
    if   tab == "import":  _tab_import()
    elif tab == "drafts":  _tab_drafts()
    elif tab == "editor":  _tab_editor()
    elif tab == "publish": _tab_publish()
    elif tab == "store":   _tab_store()
