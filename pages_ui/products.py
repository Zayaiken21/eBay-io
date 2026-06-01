"""
products.py — Pro seller product management page.
Import → Draft Edit → SEO Optimize → Publish to eBay.
"""

import streamlit as st
import streamlit.components.v1 as components

from ui.scraper        import fetch_product_page
from ui.ai_rewriter    import rewrite_title, rewrite_description, generate_tags, auto_seo_optimize
from ui.ebay_formatter import generate_ebay_html, generate_ebay_export
from ui.ebay_uploader  import upload_to_ebay, get_seller_policies
from core.draft_store  import save_draft, load_draft, list_drafts, delete_draft, duplicate_draft


# ─── CSS ─────────────────────────────────────────────────────────────────────

CSS = """
<style>
/* ── Hero ── */
.pro-hero {
    background: linear-gradient(135deg, #0053a0 0%, #002d6b 100%);
    color: white;
    padding: 30px 36px;
    border-radius: 14px;
    margin-bottom: 28px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 16px;
}
.pro-hero-left h1 { font-size: 28px; font-weight: 800; margin: 0 0 4px; letter-spacing: -0.5px; }
.pro-hero-left p  { opacity: 0.8; font-size: 14px; margin: 0; }
.pro-hero-stats { display: flex; gap: 24px; }
.stat-box { text-align: center; }
.stat-box .num { font-size: 28px; font-weight: 800; line-height: 1; }
.stat-box .lbl { font-size: 11px; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.05em; }

/* ── Tab nav ── */
.tab-nav {
    display: flex;
    gap: 6px;
    border-bottom: 2px solid #e2e8f0;
    margin-bottom: 24px;
    padding-bottom: 0;
}
.tab-btn {
    padding: 10px 20px;
    border-radius: 8px 8px 0 0;
    font-size: 14px;
    font-weight: 600;
    border: none;
    cursor: pointer;
    background: transparent;
    color: #718096;
    border-bottom: 3px solid transparent;
    margin-bottom: -2px;
}
.tab-btn.active { color: #0053a0; border-bottom-color: #0053a0; background: #f0f6ff; }

/* ── Import box ── */
.import-box {
    background: linear-gradient(135deg, #f0f6ff 0%, #e8f0fe 100%);
    border: 1.5px solid #c3d5f7;
    border-radius: 12px;
    padding: 28px;
    margin-bottom: 24px;
}
.import-box h3 { margin: 0 0 6px; color: #1a3a6b; font-size: 16px; }
.import-box p  { margin: 0 0 18px; color: #4a5568; font-size: 13px; }

/* ── Site badges ── */
.site-badges { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.site-badge {
    background: white;
    border: 1px solid #d1ddf5;
    border-radius: 20px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
    color: #2b5ba8;
}

/* ── Draft grid ── */
.draft-row {
    background: white;
    border: 1px solid #e8edf5;
    border-radius: 12px;
    padding: 16px 18px;
    margin-bottom: 10px;
    display: grid;
    grid-template-columns: 72px 1fr auto;
    gap: 14px;
    align-items: center;
    transition: box-shadow 0.15s, border-color 0.15s;
}
.draft-row:hover { box-shadow: 0 3px 14px rgba(0,83,160,0.10); border-color: #b8ccf0; }
.draft-thumb {
    width: 72px; height: 72px;
    border-radius: 8px;
    object-fit: cover;
    background: #f0f4f9;
    display: flex; align-items: center; justify-content: center;
    font-size: 28px; color: #a0aec0;
}
.draft-title { font-weight: 700; font-size: 14px; color: #1a202c; margin-bottom: 4px; line-height: 1.3; }
.draft-meta  { font-size: 12px; color: #718096; display: flex; gap: 10px; flex-wrap: wrap; }

/* ── Badges ── */
.badge { display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 11px; font-weight: 700; }
.badge-draft    { background: #ebf4ff; color: #2b6cb0; }
.badge-ready    { background: #f0fff4; color: #276749; }
.badge-live     { background: #fff3cd; color: #856404; }
.badge-exported { background: #fef3c7; color: #92400e; }

/* ── Editor sections ── */
.section-hdr {
    font-size: 11px; font-weight: 800;
    letter-spacing: 0.10em; text-transform: uppercase;
    color: #4a5568; margin: 28px 0 10px;
    padding-bottom: 6px; border-bottom: 1.5px solid #e8edf5;
}
.seo-score-bar {
    height: 6px; border-radius: 3px;
    background: #e2e8f0; overflow: hidden; margin: 6px 0;
}
.seo-score-fill {
    height: 100%; border-radius: 3px;
    transition: width 0.4s ease;
}

/* ── Image grid ── */
.img-grid { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0; }
.img-card {
    position: relative; width: 96px; height: 96px;
    border-radius: 8px; overflow: hidden;
    border: 2px solid #e2e8f0;
}
.img-card img { width: 100%; height: 100%; object-fit: cover; }
.img-card .img-num {
    position: absolute; top: 4px; left: 4px;
    background: rgba(0,0,0,0.55); color: white;
    font-size: 10px; font-weight: 700;
    border-radius: 4px; padding: 1px 5px;
}

/* ── eBay export ── */
.export-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 16px 0; }
.export-field {
    background: #f8fafc; border: 1px solid #e2e8f0;
    border-radius: 8px; padding: 12px 14px;
}
.export-field .ef-label { font-size: 11px; font-weight: 700; color: #718096; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
.export-field .ef-value { font-size: 14px; font-weight: 600; color: #1a202c; word-break: break-all; }

/* ── Upload panel ── */
.upload-panel {
    background: linear-gradient(135deg, #f0fff4 0%, #e6ffee 100%);
    border: 1.5px solid #9ae6b4;
    border-radius: 12px;
    padding: 22px;
    margin-top: 20px;
}
.upload-panel h4 { color: #276749; margin: 0 0 6px; font-size: 15px; }
.upload-panel p  { color: #4a5568; font-size: 13px; margin: 0 0 14px; }

/* ── Confidence meter ── */
.conf-row { display: flex; align-items: center; gap: 8px; margin: 8px 0; }
.conf-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }

/* ── Streamlit tweaks ── */
.stTextInput > div > div > input  { border-radius: 8px !important; font-size: 14px !important; }
.stTextArea  > div > div > textarea { border-radius: 8px !important; font-size: 14px !important; line-height: 1.6 !important; }
.stButton    > button              { border-radius: 8px !important; font-weight: 600 !important; font-size: 13px !important; }
.stSelectbox > div > div          { border-radius: 8px !important; }
div[data-testid="stExpander"]     { border-radius: 8px !important; }
</style>
"""


# ─── Init ─────────────────────────────────────────────────────────────────────

def _init():
    defaults = {
        "tab": "import",
        "import_result": None,
        "editing_id": None,
        "edit_product": None,
        "export_data": None,
        "upload_result": None,
        "seller_policies": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ─── Hero ─────────────────────────────────────────────────────────────────────

def _hero(drafts: list):
    live    = sum(1 for d in drafts if d.get("status") == "live")
    ready   = sum(1 for d in drafts if d.get("status") == "ready")
    total   = len(drafts)
    st.markdown(f"""
    <div class="pro-hero">
        <div class="pro-hero-left">
            <h1>📦 Product Manager</h1>
            <p>Import · Optimize · Publish — all in one place</p>
        </div>
        <div class="pro-hero-stats">
            <div class="stat-box"><div class="num">{total}</div><div class="lbl">Drafts</div></div>
            <div class="stat-box"><div class="num">{ready}</div><div class="lbl">Ready</div></div>
            <div class="stat-box"><div class="num">{live}</div><div class="lbl">Live</div></div>
        </div>
    </div>""", unsafe_allow_html=True)


# ─── Tab navigation ───────────────────────────────────────────────────────────

def _tabs(draft_count: int):
    c1, c2, c3, c4, _ = st.columns([2,2,2,2,5])
    tabs = [("import","⬇️ Import"), ("drafts",f"📝 Drafts ({draft_count})"),
            ("editor","✏️ Editor"), ("publish","🚀 Publish")]
    cols = [c1, c2, c3, c4]
    for col, (key, label) in zip(cols, tabs):
        with col:
            disabled = key == "editor" and not st.session_state.edit_product
            ptype    = "primary" if st.session_state.tab == key else "secondary"
            if st.button(label, key=f"tab_{key}", type=ptype,
                         use_container_width=True, disabled=disabled):
                st.session_state.tab = key
                st.rerun()
    st.markdown("<hr style='margin: 10px 0 24px; border-color: #e2e8f0;'>", unsafe_allow_html=True)


# ─── IMPORT TAB ───────────────────────────────────────────────────────────────

def _tab_import():
    st.markdown("""
    <div class="import-box">
        <h3>🔗 Import Product from Any Store</h3>
        <p>Paste any product URL and we'll automatically extract the title, images, price, description, features, and specs.</p>
        <div class="site-badges">
            <span class="site-badge">🛒 Amazon</span>
            <span class="site-badge">🏪 eBay</span>
            <span class="site-badge">🔵 Walmart</span>
            <span class="site-badge">🟠 AliExpress</span>
            <span class="site-badge">🛍️ Temu</span>
            <span class="site-badge">🟢 Etsy</span>
            <span class="site-badge">🎯 Target</span>
            <span class="site-badge">💛 Best Buy</span>
            <span class="site-badge">🏠 Home Depot</span>
            <span class="site-badge">+ Any Store</span>
        </div>
    </div>""", unsafe_allow_html=True)

    url = st.text_input("Product URL", placeholder="https://www.amazon.com/dp/... or any product page URL",
                        label_visibility="collapsed", key="import_url_field")

    c1, c2 = st.columns([2, 5])
    with c1:
        go = st.button("⬇️  Import Product", type="primary", use_container_width=True)
    with c2:
        st.caption("Works with Amazon, eBay, Walmart, AliExpress, Temu, Etsy, Target, Best Buy, Home Depot, Shopify stores, and more.")

    if go:
        if not url.strip():
            st.warning("Please paste a product URL first.")
        else:
            _run_import(url.strip())

    if st.session_state.import_result:
        _show_import_result(st.session_state.import_result)


def _run_import(url: str):
    bar = st.progress(0, text="Starting import...")
    bar.progress(20, "📡 Connecting to store...")
    result = fetch_product_page(url)
    bar.progress(80, "🔍 Parsing product data...")

    if not result["success"]:
        bar.empty()
        st.error(f"❌ {result['error']}")
        return

    if result.get("note"):
        st.info(f"ℹ️ {result['note']}")

    bar.progress(100, "✅ Done!")
    bar.empty()
    st.session_state.import_result = result["product"]


def _show_import_result(p: dict):
    conf      = p.get("confidence","low")
    conf_icon = {"high":"🟢","medium":"🟡","low":"🔴"}.get(conf,"⚪")
    conf_col  = {"high":"#276749","medium":"#744210","low":"#9b2c2c"}.get(conf,"#718096")

    st.markdown(f"""
    <div style="margin:20px 0 12px; display:flex; align-items:center; gap:12px;">
        <span style="font-size:17px;font-weight:800;">Extracted Product</span>
        <span class="badge badge-draft">{conf_icon} {conf.title()} Confidence</span>
        <span style="font-size:12px;color:{conf_col};">
            {"All key fields found" if conf=="high" else "Some fields missing — fill in the editor" if conf=="medium" else "Limited data — JS-heavy site. Fill details manually."}
        </span>
    </div>""", unsafe_allow_html=True)

    c1, c2 = st.columns([3, 2])
    with c1:
        for label, key, fallback in [
            ("Title",       "title",    "_(not found)_"),
            ("Price",       "price",    "_(not found)_"),
            ("Brand",       "brand",    "_(unknown)_"),
            ("Category",    "category", "_(not found)_"),
        ]:
            val = p.get(key,"")
            if key == "price" and val: val = f"${val}"
            st.markdown(f"**{label}**")
            st.write(val or fallback)
        desc = p.get("description","")
        st.markdown("**Description preview**")
        st.write((desc[:280] + "...") if len(desc) > 280 else desc or "_(not found)_")

        if p.get("features"):
            st.markdown("**Features found**")
            for f in p["features"][:4]:
                st.write(f"• {f}")

    with c2:
        imgs = p.get("images",[])
        st.markdown(f"**{len(imgs)} image{'s' if len(imgs)!=1 else ''} found**")
        for img in imgs[:4]:
            try:    st.image(img, use_container_width=True)
            except: st.caption(f"🖼️ {img[:50]}...")
        if len(imgs) > 4:
            st.caption(f"+ {len(imgs)-4} more images")

    with st.expander("📋 All extracted fields", expanded=False):
        display = {k:v for k,v in p.items() if k not in ("description","ebay_html")}
        st.json(display)

    st.markdown("---")
    c_a, c_b, c_c, c_d = st.columns(4)
    with c_a:
        if st.button("💾 Save to Drafts", type="primary", use_container_width=True):
            did = save_draft(p)
            st.success(f"Saved! Draft ID: `{did}`")
            st.session_state.import_result = None
            st.session_state.tab = "drafts"
            st.rerun()
    with c_b:
        if st.button("✏️ Edit Now", use_container_width=True):
            did = save_draft(p)
            _open_editor(did)
            st.rerun()
    with c_c:
        if st.button("🚀 SEO + Save", use_container_width=True, help="Auto-optimize then save"):
            optimized = auto_seo_optimize(p)
            did = save_draft(optimized)
            st.success(f"SEO-optimized and saved! Draft: `{did}`")
            st.session_state.import_result = None
            st.session_state.tab = "drafts"
            st.rerun()
    with c_d:
        if st.button("🗑️ Discard", use_container_width=True):
            st.session_state.import_result = None
            st.rerun()


# ─── DRAFTS TAB ───────────────────────────────────────────────────────────────

def _tab_drafts():
    drafts = list_drafts()
    if not drafts:
        st.info("No drafts yet — import a product to get started.")
        if st.button("⬇️ Import a Product", type="primary"):
            st.session_state.tab = "import"
            st.rerun()
        return

    # ── Search / filter ────────────────────────────────────────────────
    c_srch, c_filt = st.columns([4, 2])
    with c_srch:
        search = st.text_input("🔍 Search drafts", placeholder="Search by title...",
                               label_visibility="collapsed", key="draft_search")
    with c_filt:
        filt = st.selectbox("Filter", ["All","Draft","Ready","Live","Exported"],
                             label_visibility="collapsed", key="draft_filter")

    filtered = [d for d in drafts if
                (not search or search.lower() in d.get("title","").lower()) and
                (filt == "All" or d.get("status","draft").lower() == filt.lower())]

    st.markdown(f"<div style='color:#718096;font-size:13px;margin-bottom:12px;'>{len(filtered)} of {len(drafts)} drafts</div>",
                unsafe_allow_html=True)

    for d in filtered:
        did     = d.get("draft_id","?")
        title   = d.get("title","Untitled")
        price   = d.get("price","")
        domain  = d.get("domain","")
        imgs    = d.get("images",[])
        status  = d.get("status","draft")
        updated = (d.get("updated_at","")[:16]).replace("T"," ")
        conf    = d.get("confidence","low")

        badge_map = {"draft":"badge-draft","ready":"badge-ready","live":"badge-live","exported":"badge-exported"}
        badge_cls = badge_map.get(status,"badge-draft")

        c_img, c_info, c_acts = st.columns([1, 5, 2])
        with c_img:
            if imgs:
                try:    st.image(imgs[0], width=72)
                except: st.markdown("📦")
            else:
                st.markdown("<div style='width:72px;height:72px;background:#f0f4f9;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:28px;'>📦</div>", unsafe_allow_html=True)

        with c_info:
            conf_dot = {"high":"#48bb78","medium":"#ed8936","low":"#fc8181"}.get(conf,"#a0aec0")
            st.markdown(f"""
            <div class="draft-title">{title[:68]}{"..." if len(title)>68 else ""}</div>
            <div class="draft-meta">
                <span class="badge {badge_cls}">{status.title()}</span>
                <span>{"$"+price if price else "No price"}</span>
                <span>{"from "+domain if domain else ""}</span>
                <span>{len(imgs)} img{"s" if len(imgs)!=1 else ""}</span>
                <span style="color:{conf_dot};">● {conf.title()}</span>
                <span>Updated {updated}</span>
            </div>""", unsafe_allow_html=True)

        with c_acts:
            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("✏️", key=f"e_{did}", help="Edit"):
                    _open_editor(did); st.rerun()
            with b2:
                if st.button("⧉", key=f"dup_{did}", help="Duplicate"):
                    duplicate_draft(did); st.rerun()
            with b3:
                if st.button("🗑", key=f"del_{did}", help="Delete"):
                    delete_draft(did); st.rerun()

        st.markdown("<div style='border-top:1px solid #f0f4f9;margin:6px 0;'></div>", unsafe_allow_html=True)

    # Bulk actions
    st.markdown("---")
    c_b1, c_b2 = st.columns(2)
    with c_b1:
        if st.button("🚀 Auto-SEO All Drafts", use_container_width=True,
                     help="Run SEO optimization on all drafts in bulk"):
            for d in list_drafts():
                if d.get("status") in ("draft",""):
                    optimized = auto_seo_optimize(d)
                    save_draft(optimized, d.get("draft_id"))
            st.success("All drafts have been SEO-optimized!")
            st.rerun()


# ─── EDITOR TAB ───────────────────────────────────────────────────────────────

def _open_editor(draft_id: str):
    product = load_draft(draft_id)
    if product:
        st.session_state.editing_id   = draft_id
        st.session_state.edit_product = dict(product)
        st.session_state.export_data  = None
        st.session_state.upload_result = None
        st.session_state.tab          = "editor"


def _tab_editor():
    if not st.session_state.edit_product:
        st.info("No draft open. Go to Drafts and click ✏️ Edit.")
        if st.button("← Back to Drafts"):
            st.session_state.tab = "drafts"; st.rerun()
        return

    p   = st.session_state.edit_product
    did = st.session_state.editing_id

    # ── Top bar ──────────────────────────────────────────────────────────
    c_back, c_mid, c_seo, c_save = st.columns([1, 4, 2, 2])
    with c_back:
        if st.button("← Drafts"):
            st.session_state.tab = "drafts"; st.session_state.edit_product = None; st.rerun()
    with c_mid:
        st.markdown(f"<div style='font-size:15px;font-weight:700;padding-top:6px;'>"
                    f"✏️ {p.get('title','Untitled')[:50]}</div>", unsafe_allow_html=True)
    with c_seo:
        if st.button("🚀 Auto SEO", type="primary", use_container_width=True,
                     help="Auto-optimize title, description & tags"):
            with st.spinner("Optimizing for eBay SEO..."):
                updated = auto_seo_optimize(p)
                st.session_state.edit_product = updated
                p = updated
            st.success("SEO optimization applied!")
            st.rerun()
    with c_save:
        if st.button("💾 Save", use_container_width=True):
            save_draft(p, did)
            st.success("Saved!")

    # ── SEO Score bar ─────────────────────────────────────────────────
    score = _seo_score(p)
    color = "#48bb78" if score >= 70 else "#ed8936" if score >= 40 else "#fc8181"
    label = "Great" if score >= 70 else "Fair" if score >= 40 else "Needs Work"
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:10px;margin:6px 0 20px;">
        <div style="font-size:12px;font-weight:700;color:#718096;min-width:80px;">SEO Score</div>
        <div class="seo-score-bar" style="flex:1;">
            <div class="seo-score-fill" style="width:{score}%;background:{color};"></div>
        </div>
        <div style="font-size:13px;font-weight:700;color:{color};min-width:90px;">{score}/100 — {label}</div>
    </div>""", unsafe_allow_html=True)

    # ── Main two-column layout ─────────────────────────────────────────
    col_left, col_right = st.columns([3, 2])

    with col_left:
        # Title
        st.markdown('<div class="section-hdr">Title</div>', unsafe_allow_html=True)
        c_t, c_tb = st.columns([5, 1])
        with c_t:
            p["title"] = st.text_input("Title", value=p.get("title",""),
                                        label_visibility="collapsed", key="ed_title")
        with c_tb:
            if st.button("✨ Fix", use_container_width=True, help="Clean & optimize title"):
                r = rewrite_title(p.get("title",""), brand=p.get("brand",""),
                                   category=p.get("category",""), features=p.get("features",[]))
                if r["success"]: p["title"] = r["title"]; st.rerun()

        tlen = len(p.get("title",""))
        tcol = "#276749" if tlen <= 80 else "#9b2c2c"
        st.caption(f"<span style='color:{tcol};'>{tlen}/80 characters</span>", unsafe_allow_html=True)

        # Listing details
        st.markdown('<div class="section-hdr">Listing Details</div>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1: p["price"]     = st.text_input("Price (USD $)", value=p.get("price",""), key="ed_price")
        with c2:
            cond_opts = ["New","New with tags","New without tags","New with defects","Pre-owned","Good","Acceptable","For parts"]
            cur_cond  = p.get("condition","New")
            p["condition"] = st.selectbox("Condition", cond_opts,
                                           index=cond_opts.index(cur_cond) if cur_cond in cond_opts else 0,
                                           key="ed_condition")
        with c3: p["category"]  = st.text_input("Category",      value=p.get("category",""), key="ed_cat")

        c4, c5 = st.columns(2)
        with c4: p["brand"] = st.text_input("Brand", value=p.get("brand",""), key="ed_brand")
        with c5: p["sku"]   = st.text_input("SKU",   value=p.get("sku",""),   key="ed_sku")

        c6, c7 = st.columns(2)
        with c6: p["weight"]     = st.text_input("Weight",     value=p.get("weight",""),     key="ed_weight")
        with c7: p["dimensions"] = st.text_input("Dimensions", value=p.get("dimensions",""), key="ed_dims")

        p["quantity"] = st.number_input("Quantity available", min_value=1, max_value=9999,
                                         value=int(p.get("quantity",1)), key="ed_qty")

        # Description
        st.markdown('<div class="section-hdr">Description</div>', unsafe_allow_html=True)
        c_db, _ = st.columns([1, 4])
        with c_db:
            if st.button("✨ SEO Rewrite", use_container_width=True):
                with st.spinner("Rewriting description..."):
                    r = rewrite_description(
                        title=p.get("title",""), original_description=p.get("description",""),
                        features=p.get("features",[]), specifications=p.get("specifications",{}),
                        category=p.get("category",""), brand=p.get("brand",""),
                    )
                    if r["success"]: p["description"] = r["description"]; st.rerun()

        p["description"] = st.text_area("Description", value=p.get("description",""),
                                          height=280, label_visibility="collapsed", key="ed_desc")

        # Features
        st.markdown('<div class="section-hdr">Key Features</div>', unsafe_allow_html=True)
        feats_raw = "\n".join(p.get("features",[]))
        feats_ed  = st.text_area("Features (one per line)", value=feats_raw, height=130,
                                   label_visibility="collapsed", key="ed_feats")
        p["features"] = [f.strip() for f in feats_ed.splitlines() if f.strip()]

        # Specs
        st.markdown('<div class="section-hdr">Specifications</div>', unsafe_allow_html=True)
        specs_raw = "\n".join(f"{k}: {v}" for k,v in (p.get("specifications") or {}).items())
        specs_ed  = st.text_area("Specs (Key: Value per line)", value=specs_raw, height=130,
                                   label_visibility="collapsed", key="ed_specs")
        new_specs = {}
        for line in specs_ed.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                if k.strip(): new_specs[k.strip()] = v.strip()
        p["specifications"] = new_specs

        # Tags
        st.markdown('<div class="section-hdr">Search Tags</div>', unsafe_allow_html=True)
        c_tg, c_tgb = st.columns([5, 1])
        with c_tgb:
            if st.button("✨ Generate", use_container_width=True):
                r = generate_tags(p.get("title",""), p.get("description",""), p.get("category",""))
                if r["success"]: p["tags"] = r["tags"]; st.rerun()
        with c_tg:
            tags_raw = ", ".join(p.get("tags",[]))
            tags_ed  = st.text_input("Tags (comma-separated)", value=tags_raw,
                                      label_visibility="collapsed", key="ed_tags")
            p["tags"] = [t.strip() for t in tags_ed.split(",") if t.strip()]

    with col_right:
        # Images
        st.markdown('<div class="section-hdr">Images</div>', unsafe_allow_html=True)
        imgs = p.get("images", [])
        st.caption(f"{len(imgs)}/12 images (eBay max)")

        if imgs:
            to_remove = []
            for i in range(0, len(imgs), 3):
                row_imgs = imgs[i:i+3]
                rcols = st.columns(3)
                for j, img_url in enumerate(row_imgs):
                    with rcols[j]:
                        try:    st.image(img_url, use_container_width=True)
                        except: st.markdown(f"🖼️")
                        if st.button("✕", key=f"rmimg_{i+j}", use_container_width=True):
                            to_remove.append(i+j)
                        st.caption(f"#{i+j+1}")
            if to_remove:
                p["images"] = [u for idx,u in enumerate(imgs) if idx not in to_remove]
                st.rerun()

        with st.expander("➕ Add image URLs"):
            new_urls = st.text_area("One URL per line", height=80,
                                     label_visibility="collapsed", key="new_img_urls")
            if st.button("Add", use_container_width=True):
                added = [u.strip() for u in new_urls.splitlines() if u.strip().startswith("http")]
                p["images"] = (p.get("images") or []) + added
                st.rerun()

        # Image reorder tip
        st.caption("💡 First image = eBay cover photo. Remove and re-add to reorder.")

        # SEO Checklist
        st.markdown('<div class="section-hdr">SEO Checklist</div>', unsafe_allow_html=True)
        checks = _seo_checklist(p)
        for item, ok in checks:
            icon = "✅" if ok else "⚠️"
            st.markdown(f"{icon} {item}")

        # Source info
        if p.get("source_url"):
            st.markdown('<div class="section-hdr">Source</div>', unsafe_allow_html=True)
            st.caption(f"🔗 {p.get('domain','')} — [view original]({p['source_url']})")

    st.session_state.edit_product = p

    # ── Generate eBay listing ──────────────────────────────────────────
    st.markdown("---")
    c_gen, c_pre, c_pub = st.columns(3)
    with c_gen:
        if st.button("📋 Generate eBay Listing", type="primary", use_container_width=True):
            p["status"] = "ready"
            from ui.ebay_formatter import generate_ebay_export
            p["ebay_html"] = generate_ebay_html(p)
            save_draft(p, did)
            st.session_state.export_data = generate_ebay_export(p)
            st.rerun()
    with c_pre:
        if st.session_state.export_data:
            if st.button("👁️ Preview Listing", use_container_width=True):
                st.session_state.tab = "publish"; st.rerun()
    with c_pub:
        if st.session_state.export_data:
            if st.button("🚀 Publish to eBay", use_container_width=True):
                st.session_state.tab = "publish"; st.rerun()

    if st.session_state.export_data:
        _inline_export_summary(st.session_state.export_data)


def _inline_export_summary(exp: dict):
    st.markdown("**eBay Listing Preview**")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"""
        <div class="export-field">
            <div class="ef-label">eBay Title</div>
            <div class="ef-value">{exp.get("title","")}</div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="export-field">
            <div class="ef-label">Price · Condition</div>
            <div class="ef-value">${exp.get("price","—")} · {exp.get("condition","New")}</div>
        </div>""", unsafe_allow_html=True)


# ─── PUBLISH TAB ──────────────────────────────────────────────────────────────

def _tab_publish():
    p   = st.session_state.edit_product
    exp = st.session_state.export_data

    if not p:
        st.info("Open a draft in the Editor first, then generate a listing.")
        return

    if not exp:
        st.info("Go to the Editor tab and click **Generate eBay Listing** first.")
        return

    st.markdown("### 🛒 eBay Listing")

    t_copy, t_html, t_preview, t_upload = st.tabs(
        ["📋 Copy Fields", "📄 HTML Code", "👁️ Live Preview", "🚀 Upload to eBay"]
    )

    with t_copy:
        st.markdown("**eBay Title** — paste into eBay title field:")
        st.code(exp["title"], language=None)

        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown("**Category**"); st.write(exp.get("category",""))
            st.markdown("**Condition**"); st.write(exp.get("condition","New"))
            st.markdown("**Price**");    st.write(f"${exp.get('price','')}" if exp.get("price") else "Set your price")
            st.markdown("**SKU**");      st.write(exp.get("sku","") or "—")
        with col_r:
            st.markdown("**Search Tags**")
            st.write(", ".join(exp.get("tags",[])))
            st.markdown("**Item Specifics**")
            for k,v in list(exp.get("item_specifics",{}).items())[:8]:
                st.markdown(f"• **{k}:** {v}")

        st.markdown("**Product Images** — upload to eBay image manager:")
        for i,url in enumerate(exp.get("images",[]),1):
            st.code(url, language=None)

    with t_html:
        st.info("Copy this HTML → go to eBay Create Listing → Description → click **HTML** → paste.")
        st.text_area("eBay HTML Description", value=exp.get("description_html",""),
                      height=420, label_visibility="collapsed", key="pub_html")

    with t_preview:
        st.markdown("**Live preview of your eBay listing:**")
        components.html(exp.get("description_html",""), height=950, scrolling=True)

    with t_upload:
        _upload_panel(p, exp)


def _upload_panel(p: dict, exp: dict):
    # Check for eBay credentials
    try:
        import streamlit as st
        has_creds = bool(st.secrets.get("EBAY_PROD_CLIENT_ID","") or st.secrets.get("EBAY_SANDBOX_CLIENT_ID",""))
    except Exception:
        has_creds = False

    if not has_creds:
        st.markdown("""
        <div class="upload-panel">
            <h4>🔐 Connect Your eBay Account to Enable Direct Publishing</h4>
            <p>To publish listings directly to eBay, add your eBay developer credentials to your Streamlit secrets.</p>
        </div>""", unsafe_allow_html=True)

        with st.expander("📋 How to set up eBay API access", expanded=True):
            st.markdown("""
**Step 1 — Get eBay developer credentials:**
1. Go to [developer.ebay.com](https://developer.ebay.com) and sign in
2. Create an application → copy your **Client ID** and **Client Secret**
3. Set up an **RuName** (redirect URL) pointing to your app's URL

**Step 2 — Add to Streamlit secrets** (`.streamlit/secrets.toml`):
```toml
EBAY_PROD_CLIENT_ID     = "YourApp-PROD-xxxx"
EBAY_PROD_CLIENT_SECRET = "PROD-xxxx"
EBAY_PROD_RU_NAME       = "Your_RuName"
OAUTH_STATE_SECRET      = "any-random-secret-string"
```

**Step 3 — Connect via Settings page** using the eBay OAuth flow.

Until then, use the **Copy Fields** or **HTML Code** tabs to manually create listings on eBay.
            """)
        return

    # ── Has credentials — show upload UI ─────────────────────────────────
    st.markdown("""
    <div class="upload-panel">
        <h4>🚀 Publish Directly to eBay</h4>
        <p>This will create a live listing on your eBay store using the Sell Inventory API.</p>
    </div>""", unsafe_allow_html=True)

    # Get tokens from session (set by ebay_oauth.py flow in Settings)
    token_data = st.session_state.get("ebay_token_data", {})
    access_token = token_data.get("access_token","")
    environment  = st.session_state.get("ebay_environment","production")

    if not access_token:
        st.warning("⚠️ No eBay session found. Please connect your eBay account in **Settings** first.")
        return

    # ── Listing policies ──────────────────────────────────────────────
    st.markdown("**eBay Listing Policies** — required for publishing:")
    if st.button("🔄 Load my eBay policies", use_container_width=False):
        with st.spinner("Fetching policies from eBay..."):
            st.session_state.seller_policies = get_seller_policies(access_token, environment)

    policies = st.session_state.seller_policies or {}

    c_f, c_p, c_r = st.columns(3)
    with c_f:
        ff = policies.get("fulfillment",[])
        ff_opts = {x["name"]:x["id"] for x in ff} if ff else {}
        if ff_opts:
            sel_f = st.selectbox("Fulfillment Policy", list(ff_opts.keys()), key="pol_fulfil")
            p["fulfillment_policy_id"] = ff_opts[sel_f]
        else:
            p["fulfillment_policy_id"] = st.text_input("Fulfillment Policy ID", value=p.get("fulfillment_policy_id",""), key="pol_f_txt")

    with c_p:
        pp = policies.get("payment",[])
        pp_opts = {x["name"]:x["id"] for x in pp} if pp else {}
        if pp_opts:
            sel_p = st.selectbox("Payment Policy", list(pp_opts.keys()), key="pol_pay")
            p["payment_policy_id"] = pp_opts[sel_p]
        else:
            p["payment_policy_id"] = st.text_input("Payment Policy ID", value=p.get("payment_policy_id",""), key="pol_p_txt")

    with c_r:
        rr = policies.get("return",[])
        rr_opts = {x["name"]:x["id"] for x in rr} if rr else {}
        if rr_opts:
            sel_r = st.selectbox("Return Policy", list(rr_opts.keys()), key="pol_ret")
            p["return_policy_id"] = rr_opts[sel_r]
        else:
            p["return_policy_id"] = st.text_input("Return Policy ID", value=p.get("return_policy_id",""), key="pol_r_txt")

    st.markdown("**Merchant Location Key** (set up in eBay Seller Hub):")
    p["merchant_location_key"] = st.text_input("Location Key", value=p.get("merchant_location_key","default"), key="loc_key")

    st.markdown("---")

    # ── Upload button ─────────────────────────────────────────────────
    # Attach the formatted HTML to the product before uploading
    p["ebay_html"] = exp.get("description_html","")
    st.session_state.edit_product = p

    col_up, col_test = st.columns(2)
    with col_up:
        if st.button("🚀 Publish to eBay (LIVE)", type="primary", use_container_width=True):
            with st.spinner("Publishing to eBay..."):
                result = upload_to_ebay(p, access_token, environment)
            st.session_state.upload_result = result

    with col_test:
        if st.button("🧪 Test (Sandbox)", use_container_width=True,
                     help="Publish to eBay sandbox for testing"):
            with st.spinner("Publishing to eBay sandbox..."):
                result = upload_to_ebay(p, access_token, "sandbox")
            st.session_state.upload_result = result

    # ── Upload result ─────────────────────────────────────────────────
    ur = st.session_state.upload_result
    if ur:
        if ur.get("success"):
            st.success(f"✅ Published! Listing ID: `{ur['listing_id']}`")
            if ur.get("listing_url"):
                st.markdown(f"🔗 [View on eBay]({ur['listing_url']})")
            p["status"] = "live"
            p["ebay_listing_id"] = ur["listing_id"]
            p["ebay_listing_url"] = ur["listing_url"]
            save_draft(p, st.session_state.editing_id)
        else:
            st.error(f"❌ Upload failed: {ur['error']}")
            st.caption("Check your eBay credentials, policy IDs, and merchant location key.")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _seo_score(p: dict) -> int:
    score = 0
    title = p.get("title","")
    if 30 <= len(title) <= 80:           score += 20
    elif len(title) > 0:                 score += 10
    if p.get("price"):                   score += 10
    if len(p.get("description","")) > 200: score += 20
    if len(p.get("images",[])) >= 3:     score += 15
    if len(p.get("features",[])) >= 3:   score += 10
    if p.get("specifications"):          score += 10
    if len(p.get("tags",[])) >= 5:       score += 10
    if p.get("brand"):                   score += 5
    return min(score, 100)


def _seo_checklist(p: dict) -> list:
    title = p.get("title","")
    return [
        (f"Title length ({len(title)}/80 chars)", 20 <= len(title) <= 80),
        ("Price set",                  bool(p.get("price"))),
        ("3+ images uploaded",         len(p.get("images",[])) >= 3),
        ("Description filled in",      len(p.get("description","")) > 100),
        ("3+ key features listed",     len(p.get("features",[])) >= 3),
        ("Specifications added",       bool(p.get("specifications"))),
        ("5+ search tags",             len(p.get("tags",[])) >= 5),
        ("Brand set",                  bool(p.get("brand"))),
    ]


# ─── Main ─────────────────────────────────────────────────────────────────────

def render_products() -> None:
    _init()
    st.markdown(CSS, unsafe_allow_html=True)

    drafts = list_drafts()
    _hero(drafts)
    _tabs(len(drafts))

    if st.session_state.tab == "import":  _tab_import()
    elif st.session_state.tab == "drafts": _tab_drafts()
    elif st.session_state.tab == "editor": _tab_editor()
    elif st.session_state.tab == "publish": _tab_publish()
