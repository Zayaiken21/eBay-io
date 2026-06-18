"""
products.py — Pro seller product manager.
Auth: reads owner_name from st.session_state (set at login by auth.py).
eBay: all token management via ebay_account_store — auto-refreshes, never stale.
"""

import streamlit as st
import streamlit.components.v1 as components

from ui.scraper         import fetch_product_page
from ui.ai_rewriter     import rewrite_title, rewrite_description, generate_tags, auto_seo_optimize
from ui.ebay_formatter  import generate_ebay_html, generate_ebay_export
from ui.ebay_uploader   import upload_to_ebay, get_seller_policies, get_account_info
from core.draft_store   import save_draft, load_draft, list_drafts, delete_draft, duplicate_draft


# ─── CSS ──────────────────────────────────────────────────────────────────────
CSS = """<style>
/* ── Reset & base ── */
*, *::before, *::after { box-sizing: border-box; }

/* ── Hero ── */
.pro-hero {
    background: linear-gradient(135deg, #0053a0 0%, #002d6b 100%);
    color: #fff;
    padding: 28px 34px;
    border-radius: 14px;
    margin-bottom: 26px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 16px;
    box-shadow: 0 4px 24px rgba(0,83,160,0.18);
}
.hero-left h1  { font-size: 26px; font-weight: 800; margin: 0 0 4px; letter-spacing: -0.4px; }
.hero-left p   { opacity: 0.78; font-size: 13px; margin: 0; }
.hero-stats    { display: flex; gap: 28px; }
.hstat         { text-align: center; }
.hstat-num     { font-size: 30px; font-weight: 900; line-height: 1; }
.hstat-lbl     { font-size: 10px; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 2px; }

/* ── Account banner ── */
.acct-banner {
    display: flex; align-items: center; gap: 10px;
    background: #f0fff4; border: 1px solid #9ae6b4;
    border-radius: 9px; padding: 10px 16px;
    font-size: 13px; color: #276749; font-weight: 600;
    margin-bottom: 16px;
}
.acct-banner.disconnected {
    background: #fff8f0; border-color: #fbd38d; color: #744210;
}

/* ── Import box ── */
.import-box {
    background: linear-gradient(135deg, #f0f6ff 0%, #eaf0fe 100%);
    border: 1.5px solid #c3d5f7;
    border-radius: 12px;
    padding: 26px 28px;
    margin-bottom: 22px;
}
.import-box h3 { margin: 0 0 5px; color: #1a3a6b; font-size: 16px; font-weight: 800; }
.import-box p  { margin: 0 0 16px; color: #4a5568; font-size: 13px; }
.site-pills    { display: flex; flex-wrap: wrap; gap: 6px; }
.site-pill {
    background: white; border: 1px solid #d1ddf5;
    border-radius: 20px; padding: 3px 11px;
    font-size: 11px; font-weight: 700; color: #2b5ba8;
}

/* ── Draft rows ── */
.draft-row {
    background: white; border: 1px solid #e8edf5;
    border-radius: 12px; padding: 14px 16px;
    margin-bottom: 9px;
    transition: box-shadow .15s, border-color .15s;
}
.draft-row:hover { box-shadow: 0 3px 16px rgba(0,83,160,.09); border-color: #b8ccf0; }
.draft-title { font-weight: 700; font-size: 14px; color: #1a202c; margin-bottom: 4px; line-height: 1.3; }
.draft-meta  { font-size: 12px; color: #718096; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }

/* ── Badges ── */
.badge { display:inline-block; padding:2px 9px; border-radius:20px; font-size:11px; font-weight:700; }
.b-draft    { background:#ebf4ff; color:#2b6cb0; }
.b-ready    { background:#f0fff4; color:#276749; }
.b-live     { background:#fffbeb; color:#92400e; border:1px solid #fbd38d; }
.b-exported { background:#fef3c7; color:#92400e; }

/* ── Section headers in editor ── */
.sec-hdr {
    font-size: 10px; font-weight: 800;
    letter-spacing: .12em; text-transform: uppercase;
    color: #4a5568; margin: 26px 0 9px;
    padding-bottom: 6px;
    border-bottom: 1.5px solid #e8edf5;
    display: flex; align-items: center; gap: 7px;
}
.sec-hdr-icon { font-size: 13px; }

/* ── SEO score ── */
.seo-row {
    display: flex; align-items: center; gap: 10px;
    background: #f8fafc; border: 1px solid #e2e8f0;
    border-radius: 8px; padding: 10px 14px;
    margin-bottom: 18px;
}
.seo-track { flex: 1; height: 7px; background: #e2e8f0; border-radius: 4px; overflow: hidden; }
.seo-fill  { height: 100%; border-radius: 4px; transition: width .4s ease; }
.seo-label { font-size: 13px; font-weight: 800; min-width: 100px; }
.seo-lbl-txt { font-size: 12px; font-weight: 700; color: #718096; min-width: 70px; }

/* ── Checklist ── */
.chk-row   { display:flex; align-items:center; gap:8px; font-size:13px; padding:4px 0; }
.chk-ok    { color:#276749; font-size:15px; }
.chk-warn  { color:#d97706; font-size:15px; }

/* ── Export fields ── */
.ef-card {
    background:#f8fafc; border:1px solid #e2e8f0;
    border-radius:8px; padding:12px 14px; margin-bottom:8px;
}
.ef-lbl  { font-size:10px; font-weight:800; color:#718096; text-transform:uppercase; letter-spacing:.08em; margin-bottom:3px; }
.ef-val  { font-size:14px; font-weight:600; color:#1a202c; word-break:break-all; }

/* ── Upload panel ── */
.upload-panel {
    background: linear-gradient(135deg,#f0fff4 0%,#e6ffee 100%);
    border: 1.5px solid #9ae6b4; border-radius: 12px;
    padding: 22px; margin-top: 4px;
}
.upload-panel h4 { color:#276749; margin:0 0 5px; font-size:15px; font-weight:800; }
.upload-panel p  { color:#4a5568; font-size:13px; margin:0 0 14px; }

.no-connect-panel {
    background: #fff8f0; border: 1.5px solid #fbd38d;
    border-radius: 12px; padding: 22px;
}
.no-connect-panel h4 { color:#92400e; margin:0 0 6px; font-size:15px; font-weight:800; }
.no-connect-panel p  { color:#744210; font-size:13px; margin:0; }

/* ── Success result ── */
.listing-success {
    background: linear-gradient(135deg,#f0fff4,#e6ffee);
    border: 2px solid #48bb78; border-radius: 12px;
    padding: 22px; text-align: center; margin-top: 16px;
}
.listing-success .ls-id  { font-size: 28px; font-weight: 900; color: #276749; }
.listing-success .ls-sub { font-size: 13px; color: #4a7c59; margin-top: 4px; }

/* ── Title char counter ── */
.title-count { font-size: 12px; font-weight: 600; }
.title-ok    { color: #276749; }
.title-warn  { color: #d97706; }
.title-over  { color: #e53e3e; }

/* ── Streamlit overrides ── */
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
        "prod_tab":       "import",
        "import_result":  None,
        "editing_id":     None,
        "edit_product":   None,
        "export_data":    None,
        "upload_result":  None,
        "policies":       None,
        "policies_loaded":False,
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v

def _owner() -> str:
    """
    Resolve owner_name — matches session.py exactly.
    session.py sets: client_name, role, authenticated.
    validate_client_token() returns {"client_name": ..., "token": ..., "active": ...}
    """
    client_name = st.session_state.get("client_name") or ""
    role        = st.session_state.get("role") or ""
    if client_name:
        return client_name.strip()
    if role == "ceo":
        return "ceo"
    return "default"

def _set_tab(tab: str):
    st.session_state.prod_tab = tab


# ─── Hero ─────────────────────────────────────────────────────────────────────

def _hero(drafts: list):
    live        = sum(1 for d in drafts if d.get("status") == "live")
    ready       = sum(1 for d in drafts if d.get("status") == "ready")
    owner       = _owner()
    role        = st.session_state.get("role") or ""
    role_label  = "CEO" if role == "ceo" else owner
    st.markdown(f"""
    <div class="pro-hero">
      <div class="hero-left">
        <h1>📦 Product Manager</h1>
        <p>Logged in as <strong>{role_label}</strong> · Import · Optimize · Publish</p>
      </div>
      <div class="hero-stats">
        <div class="hstat"><div class="hstat-num">{len(drafts)}</div><div class="hstat-lbl">Total</div></div>
        <div class="hstat"><div class="hstat-num">{ready}</div><div class="hstat-lbl">Ready</div></div>
        <div class="hstat"><div class="hstat-num">{live}</div><div class="hstat-lbl">Live</div></div>
      </div>
    </div>""", unsafe_allow_html=True)


def _account_banner():
    """
    Show connected eBay account status.
    Reads fresh from Supabase via get_account_info() which uses
    _owner() → st.session_state.client_name (set by session.py at login).
    Never uses cached tokens — always reads the latest row.
    """
    try:
        info = get_account_info()
    except Exception as e:
        st.markdown(f"""
        <div class="acct-banner disconnected">
            ⚠️ &nbsp;Could not read eBay account: {e}
        </div>""", unsafe_allow_html=True)
        return

    if info:
        label   = (info.get("store_name") or info.get("ebay_username")
                   or info.get("ebay_user_id") or "eBay Account")
        env     = info.get("environment", "production")
        env_tag = " · Sandbox" if env != "production" else " · Live"
        st.markdown(f"""
        <div class="acct-banner">
            ✅ &nbsp;Connected: <strong>{label}</strong>{env_tag}
            &nbsp;·&nbsp; Listings will post to this account
        </div>""", unsafe_allow_html=True)
    else:
        owner = _owner()
        hint  = f"(looking for owner: <code>{owner}</code>)" if owner != "default" else ""
        st.markdown(f"""
        <div class="acct-banner disconnected">
            ⚠️ &nbsp;No eBay account connected {hint} — go to
            <strong>Settings</strong> to connect your eBay store
        </div>""", unsafe_allow_html=True)


# ─── Tab navigation ───────────────────────────────────────────────────────────

def _tabs(draft_count: int):
    c1, c2, c3, c4, _ = st.columns([2, 2.5, 2, 2, 4])
    for col, key, label in zip(
        [c1, c2, c3, c4],
        ["import", "drafts", "editor", "publish"],
        ["⬇️ Import", f"📝 Drafts ({draft_count})", "✏️ Editor", "🚀 Publish"],
    ):
        with col:
            disabled = (key == "editor" and not st.session_state.edit_product)
            ptype    = "primary" if st.session_state.prod_tab == key else "secondary"
            if st.button(label, key=f"ptab_{key}", type=ptype,
                         use_container_width=True, disabled=disabled):
                _set_tab(key); st.rerun()
    st.markdown("<hr style='margin:10px 0 22px;border-color:#e2e8f0;'>", unsafe_allow_html=True)


# ─── IMPORT TAB ───────────────────────────────────────────────────────────────

def _tab_import():
    st.markdown("""
    <div class="import-box">
      <h3>🔗 Import Product from Any Store</h3>
      <p>Paste any product URL — automatically extracts title, images, price, description, features, and specs.</p>
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

    c1, c2 = st.columns([2, 5])
    with c1:
        go = st.button("⬇️ Import Product", type="primary", use_container_width=True)
    with c2:
        fast = st.button("🚀 Import + Auto SEO", use_container_width=True,
                         help="Import then immediately apply SEO optimization")

    if (go or fast) and url.strip():
        _run_import(url.strip(), auto_seo=fast)
    elif go or fast:
        st.warning("Paste a product URL above first.")

    if st.session_state.import_result:
        _show_import_result(st.session_state.import_result)


def _run_import(url: str, auto_seo: bool = False):
    bar = st.progress(0, text="Starting...")
    bar.progress(15, "📡 Connecting to store...")
    result = fetch_product_page(url)
    bar.progress(75, "🔍 Parsing product data...")

    if not result["success"]:
        bar.empty()
        st.error(f"❌ {result['error']}")
        return

    if result.get("note"):
        st.info(f"ℹ️ {result['note']}")

    product = result["product"]

    if auto_seo:
        bar.progress(90, "✨ Applying SEO optimization...")
        product = auto_seo_optimize(product)

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

    st.markdown("---")
    ca, cb, cc, cd = st.columns(4)
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
        if st.button("✨ SEO + Save", use_container_width=True):
            did = save_draft(auto_seo_optimize(p))
            st.success(f"SEO optimized — ID `{did}`")
            st.session_state.import_result = None
            _set_tab("drafts"); st.rerun()
    with cd:
        if st.button("🗑️ Discard", use_container_width=True):
            st.session_state.import_result = None; st.rerun()


# ─── DRAFTS TAB ───────────────────────────────────────────────────────────────

def _tab_drafts():
    drafts = list_drafts()
    if not drafts:
        st.info("No drafts yet. Import a product to get started.")
        if st.button("⬇️ Go to Import", type="primary"):
            _set_tab("import"); st.rerun()
        return

    cs, cf = st.columns([4, 2])
    with cs:
        srch = st.text_input("Search", placeholder="🔍 Search by title…",
                              label_visibility="collapsed", key="dsrch")
    with cf:
        filt = st.selectbox("Filter", ["All", "Draft", "Ready", "Live", "Exported"],
                             label_visibility="collapsed", key="dfilt")

    shown = [d for d in drafts if
             (not srch or srch.lower() in d.get("title","").lower()) and
             (filt == "All" or d.get("status","draft").lower() == filt.lower())]

    st.markdown(f"<div style='font-size:12px;color:#718096;margin-bottom:12px;'>"
                f"{len(shown)} of {len(drafts)} drafts</div>", unsafe_allow_html=True)

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
    if st.button("✨ Auto-SEO All Drafts", use_container_width=False,
                 help="Optimize all draft-status listings in bulk"):
        count = 0
        for d in list_drafts():
            if d.get("status", "draft") == "draft":
                save_draft(auto_seo_optimize(d), d.get("draft_id"))
                count += 1
        st.success(f"Optimized {count} drafts!")
        st.rerun()


# ─── EDITOR TAB ───────────────────────────────────────────────────────────────

def _open_editor(draft_id: str):
    p = load_draft(draft_id)
    if p:
        st.session_state.editing_id    = draft_id
        st.session_state.edit_product  = dict(p)
        st.session_state.export_data   = None
        st.session_state.upload_result = None
        st.session_state.prod_tab      = "editor"


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

    # Always reload fresh from store to avoid stale data
    p   = st.session_state.edit_product
    did = st.session_state.editing_id

    # ── Top action bar ────────────────────────────────────────────────
    cb, cm, cseo, csave = st.columns([1, 4, 2, 1])
    with cb:
        if st.button("← Drafts"):
            st.session_state.edit_product = None
            _set_tab("drafts"); st.rerun()
    with cm:
        st.markdown(f"<div style='font-size:14px;font-weight:700;padding-top:7px;color:#1a202c;'>"
                    f"✏️ {p.get('title','Untitled')[:55]}</div>", unsafe_allow_html=True)
    with cseo:
        if st.button("✨ Auto SEO", type="primary", use_container_width=True):
            with st.spinner("Optimizing…"):
                p = auto_seo_optimize(p)
                st.session_state.edit_product = p
            save_draft(p, did)
            st.success("SEO applied & saved!")
            st.rerun()
    with csave:
        if st.button("💾", use_container_width=True, help="Save draft"):
            save_draft(p, did); st.toast("Saved!", icon="✅")

    # ── SEO score bar ─────────────────────────────────────────────────
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

    # ── Two-column layout ─────────────────────────────────────────────
    col_l, col_r = st.columns([3, 2])

    with col_l:
        # Title
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

        # Listing details
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

        p["quantity"] = st.number_input("Quantity", min_value=1, max_value=9999,
                                         value=int(p.get("quantity", 1)), key="ed_qty")

        # Description
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

        # Features
        st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">✅</span>Key Features</div>',
                    unsafe_allow_html=True)
        feats_ed = st.text_area("Features (one per line)", value="\n".join(p.get("features",[])),
                                 height=120, label_visibility="collapsed", key="ed_feats")
        p["features"] = [f.strip() for f in feats_ed.splitlines() if f.strip()]

        # Specs
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

        # Tags
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
        # Images
        st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">🖼️</span>'
                    f'Images ({len(p.get("images",[]))}/12)</div>', unsafe_allow_html=True)
        imgs     = p.get("images", [])
        to_del   = []
        for i in range(0, len(imgs), 3):
            row  = imgs[i:i+3]
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

        # SEO checklist
        st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">📊</span>SEO Checklist</div>',
                    unsafe_allow_html=True)
        for item, ok in _checklist(p):
            icon = '<span class="chk-ok">✅</span>' if ok else '<span class="chk-warn">⚠️</span>'
            st.markdown(f'<div class="chk-row">{icon} {item}</div>', unsafe_allow_html=True)

        # Source link
        if p.get("source_url"):
            st.markdown('<div class="sec-hdr"><span class="sec-hdr-icon">🔗</span>Source</div>',
                        unsafe_allow_html=True)
            st.caption(f"[{p.get('domain','')}]({p['source_url']})")

    st.session_state.edit_product = p

    # ── Generate listing ──────────────────────────────────────────────
    st.markdown("---")
    cg, cp2, cclose = st.columns([3, 2, 2])
    with cg:
        if st.button("📋 Generate eBay Listing", type="primary", use_container_width=True):
            p["ebay_html"] = generate_ebay_html(p)
            p["status"]    = "ready"
            save_draft(p, did)
            st.session_state.export_data   = generate_ebay_export(p)
            st.session_state.edit_product  = p
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
    did = st.session_state.editing_id

    # ── Show connected account ────────────────────────────────────────
    owner = _owner()
    try:
        info = get_account_info()
    except Exception as e:
        st.error(f"❌ Could not read eBay account from Supabase: {e}")
        st.caption(f"Resolved owner_name: `{owner}` · Check SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in secrets.")
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

    # Connected — show account info
    acct_label    = (info.get("store_name") or info.get("ebay_username")
                     or info.get("ebay_user_id") or "Your eBay Store")
    env           = info.get("environment", "production")
    env_tag       = " · Sandbox" if env != "production" else " · Live"
    resolved_owner = info.get("_resolved_owner", owner)

    st.markdown(f"""
    <div class="upload-panel">
      <h4>🚀 Publish to eBay{env_tag}</h4>
      <p>Listing will be posted to <strong>{acct_label}</strong>.
         Token auto-refreshes on every publish — never stale.</p>
    </div>""", unsafe_allow_html=True)
    st.caption(f"Owner: `{resolved_owner}` · Environment: `{env}`")

    st.markdown("")

    # ── Listing policies ──────────────────────────────────────────────
    st.markdown("**Listing Policies** — required by eBay Inventory API:")
    cload, _ = st.columns([2, 5])
    with cload:
        if st.button("🔄 Load my eBay policies", use_container_width=True,
                     key="load_policies"):
            with st.spinner("Fetching from eBay…"):
                result = get_seller_policies()
                if result.get("error"):
                    st.error(f"Could not load policies: {result['error']}")
                else:
                    st.session_state.policies        = result
                    st.session_state.policies_loaded = True
                    st.rerun()

    policies = st.session_state.policies or {}
    cf, cp, cr = st.columns(3)

    with cf:
        ff = policies.get("fulfillment", [])
        if ff:
            sel = st.selectbox("Fulfillment", [x["name"] for x in ff], key="pol_f")
            p["fulfillment_policy_id"] = next(x["id"] for x in ff if x["name"] == sel)
        else:
            p["fulfillment_policy_id"] = st.text_input("Fulfillment Policy ID",
                value=p.get("fulfillment_policy_id",""), key="pol_f_txt")

    with cp:
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

    p["merchant_location_key"] = st.text_input(
        "Merchant Location Key",
        value=p.get("merchant_location_key","default"),
        key="loc_key",
        help="Set up in eBay Seller Hub → Locations. Usually 'default' for single-location sellers."
    )

    # Attach generated HTML to product before upload
    p["ebay_html"] = exp.get("description_html", "")
    st.session_state.edit_product = p

    st.markdown("---")

    # ── Publish buttons ───────────────────────────────────────────────
    missing_policies = not all([
        p.get("fulfillment_policy_id"),
        p.get("payment_policy_id"),
        p.get("return_policy_id"),
    ])

    if missing_policies:
        st.warning("⚠️ Load your eBay policies above and select fulfillment, payment, and return policies before publishing.")

    cpub, ctest = st.columns(2)
    with cpub:
        if st.button("🚀 Publish to eBay (LIVE)", type="primary",
                     use_container_width=True, disabled=missing_policies):
            with st.spinner("Publishing to eBay…"):
                result = upload_to_ebay(p)
            st.session_state.upload_result = result
            if result["success"]:
                p["status"]            = "live"
                p["ebay_listing_id"]   = result["listing_id"]
                p["ebay_listing_url"]  = result["listing_url"]
                save_draft(p, did)
                st.session_state.edit_product = p
            st.rerun()

    with ctest:
        if st.button("🧪 Test on Sandbox", use_container_width=True):
            st.info("To test on sandbox, connect a sandbox eBay account in Settings "
                    "and this will route automatically.")

    # ── Upload result ─────────────────────────────────────────────────
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
- **Policy IDs**: Make sure you selected valid policies from your eBay account
- **Merchant Location**: Must match a location key set up in eBay Seller Hub
- **Token**: If your eBay connection is old, disconnect and reconnect in Settings
- **Sandbox vs Live**: Sandbox policies ≠ production policies
                """)


# ─── Main ─────────────────────────────────────────────────────────────────────

def render_products() -> None:
    _init()
    st.markdown(CSS, unsafe_allow_html=True)

    drafts = list_drafts()
    _hero(drafts)
    _account_banner()
    _tabs(len(drafts))

    tab = st.session_state.prod_tab
    if   tab == "import":  _tab_import()
    elif tab == "drafts":  _tab_drafts()
    elif tab == "editor":  _tab_editor()
    elif tab == "publish": _tab_publish()
