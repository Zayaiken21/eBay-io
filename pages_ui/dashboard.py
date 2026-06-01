
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, time, timezone
from html import escape
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

try:
    from core.ebay_account_store import (
        call_ebay_api,
        get_connected_ebay_label,
        get_latest_ebay_account,
    )
except Exception:
    call_ebay_api = None
    get_connected_ebay_label = None
    get_latest_ebay_account = None


EASTERN = ZoneInfo("America/New_York")
PRODUCTS_PER_PAGE = 5
MAX_PRODUCTS = 50

DEFAULT_NICHES = [
    "wireless earbuds",
    "gaming keyboard",
    "car phone mount",
    "stanley tumbler",
    "pokemon cards",
    "portable blender",
    "led strip lights",
    "iphone case",
    "air fryer accessories",
    "men running shoes",
]


def _load_css() -> None:
    css_path = "pages_ui/dashboard.css"
    try:
        with open(css_path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        pass


def _owner_name() -> str:
    return (
        st.session_state.get("client_name")
        or st.session_state.get("owner_name")
        or st.session_state.get("username")
        or "default"
    )


def _now_est() -> datetime:
    return datetime.now(EASTERN)


def _start_of_today_est() -> datetime:
    now = _now_est()
    return datetime.combine(now.date(), time.min, tzinfo=EASTERN)


def _safe_utc(dt: datetime) -> str:
    """eBay wants UTC. Clamp away from the future by 2 minutes."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=EASTERN)

    utc_dt = dt.astimezone(timezone.utc)
    max_end = datetime.now(timezone.utc) - timedelta(minutes=2)
    if utc_dt > max_end:
        utc_dt = max_end

    return utc_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _currency(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _clean_keyword(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9\s\-&]", "", value or "")
    value = re.sub(r"\s+", " ", value).strip()
    return value[:80] or "trending products"


def _response_to_json(resp: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if resp is None:
        return None, "No response returned."

    if isinstance(resp, dict):
        return resp, None

    status = getattr(resp, "status_code", None)
    text = getattr(resp, "text", "")
    if status and status >= 400:
        return None, f"{status}: {text[:500]}"

    try:
        return resp.json(), None
    except Exception:
        return None, text[:500] or "Response was not JSON."


def _api_get(owner_name: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if call_ebay_api is None:
        return None, "call_ebay_api is not available."

    try:
        resp = call_ebay_api(owner_name, "GET", endpoint, params=params or {})
        return _response_to_json(resp)
    except Exception as exc:
        return None, str(exc)


def _item_image(item: Dict[str, Any]) -> str:
    image = item.get("image") or {}
    return (
        image.get("imageUrl")
        or item.get("thumbnailImages", [{}])[0].get("imageUrl")
        or "https://ir.ebaystatic.com/cr/v/c01/skin/image-placeholder.png"
    )


def _item_price(item: Dict[str, Any]) -> float:
    price = item.get("price") or {}
    return _currency(price.get("value"))


def _item_seller(item: Dict[str, Any]) -> str:
    seller = item.get("seller") or {}
    return seller.get("username") or seller.get("sellerUsername") or "eBay seller"


def _item_category(item: Dict[str, Any], fallback: str) -> str:
    path = item.get("categoryPath") or item.get("categoryName") or fallback
    if isinstance(path, list):
        return " > ".join([str(x) for x in path])
    return str(path or fallback)


def _normalize_active_items(data: Dict[str, Any], keyword: str) -> List[Dict[str, Any]]:
    raw_items = data.get("itemSummaries") or data.get("itemSales") or data.get("items") or []
    products: List[Dict[str, Any]] = []

    for item in raw_items:
        title = item.get("title") or item.get("itemTitle") or "Untitled eBay product"
        price = _item_price(item)
        shipping = item.get("shippingOptions", [{}])[0].get("shippingCost", {}) if item.get("shippingOptions") else {}
        seller_feedback = (item.get("seller") or {}).get("feedbackScore") or 0
        product_id = item.get("itemId") or item.get("legacyItemId") or item.get("itemHref") or title

        products.append(
            {
                "id": str(product_id),
                "title": title,
                "keyword": keyword,
                "category": _item_category(item, keyword),
                "price": price,
                "image": _item_image(item),
                "url": item.get("itemWebUrl") or item.get("itemHref") or "",
                "seller": _item_seller(item),
                "condition": item.get("condition") or "Not shown",
                "shipping": _currency(shipping.get("value")) if isinstance(shipping, dict) else 0.0,
                "location": (item.get("itemLocation") or {}).get("country", "US"),
                "feedback": int(seller_feedback or 0),
                "source": "Browse API",
            }
        )

    return products


def _normalize_sales_items(data: Dict[str, Any], keyword: str) -> Dict[str, Dict[str, Any]]:
    """Best-effort parser for Marketplace Insights item sales responses."""
    raw_items = data.get("itemSales") or data.get("itemSummaries") or data.get("sales") or []
    sales_by_title: Dict[str, Dict[str, Any]] = {}

    for item in raw_items:
        title = item.get("title") or item.get("itemTitle") or ""
        if not title:
            continue

        quantity = (
            item.get("totalSoldQuantity")
            or item.get("soldQuantity")
            or item.get("quantitySold")
            or item.get("quantity")
            or 0
        )
        amount = item.get("totalSalesAmount") or item.get("price") or {}
        amount_value = amount.get("value") if isinstance(amount, dict) else amount

        key = title.lower()[:80]
        sales_by_title[key] = {
            "sold_count": int(_currency(quantity)),
            "sales_value": _currency(amount_value),
            "source": "Marketplace Insights",
        }

    return sales_by_title


@st.cache_data(ttl=60 * 30, show_spinner=False)
def _fetch_market_products(
    owner_name: str,
    keyword_text: str,
    marketplace_id: str,
    reset_key: str,
) -> Dict[str, Any]:
    keywords = [_clean_keyword(k) for k in keyword_text.split(",") if _clean_keyword(k)]
    keywords = keywords[:10] or DEFAULT_NICHES[:6]

    all_products: List[Dict[str, Any]] = []
    errors: List[str] = []

    for keyword in keywords:
        browse_params = {
            "q": keyword,
            "limit": 10,
            "sort": "newlyListed",
        }

        data, err = _api_get(owner_name, "/buy/browse/v1/item_summary/search", browse_params)
        if err:
            errors.append(f"{keyword}: Browse search unavailable ({err})")
            continue

        products = _normalize_active_items(data or {}, keyword)

        # Try true sold-history data if this eBay app has Marketplace Insights access.
        end_est = min(_now_est(), _start_of_today_est() + timedelta(days=1) - timedelta(minutes=2))
        start_24 = _start_of_today_est()
        start_90 = end_est - timedelta(days=90)

        insights_24, err_24 = _api_get(
            owner_name,
            "/buy/marketplace_insights/v1_beta/item_sales/search",
            {
                "q": keyword,
                "limit": 50,
                "filter": f"lastSoldDate:[{_safe_utc(start_24)}..{_safe_utc(end_est)}]",
            },
        )

        insights_90, err_90 = _api_get(
            owner_name,
            "/buy/marketplace_insights/v1_beta/item_sales/search",
            {
                "q": keyword,
                "limit": 50,
                "filter": f"lastSoldDate:[{_safe_utc(start_90)}..{_safe_utc(end_est)}]",
            },
        )

        sales24 = _normalize_sales_items(insights_24 or {}, keyword) if insights_24 else {}
        sales90 = _normalize_sales_items(insights_90 or {}, keyword) if insights_90 else {}

        for product in products:
            key = product["title"].lower()[:80]
            product["sold_24h"] = sales24.get(key, {}).get("sold_count", 0)
            product["sold_90d"] = sales90.get(key, {}).get("sold_count", 0)

            # Factual fallback: if Marketplace Insights is unavailable, do NOT fake sales.
            product["sales_source"] = "eBay Marketplace Insights" if (sales24 or sales90) else "Active eBay marketplace listing"
            product["success_rate"] = (
                round((product["sold_24h"] / max(product["sold_90d"], 1)) * 100, 1)
                if product["sold_90d"]
                else None
            )
            # Ranking score prioritizes known sales; fallback ranks active comps by feedback/price.
            product["score"] = (
                product["sold_24h"] * 8
                + product["sold_90d"] * 1.5
                + min(product["feedback"], 10000) / 10000
                + min(product["price"], 500) / 500
            )
            all_products.append(product)

        if not insights_24 and err_24:
            # Only store one concise note, not full technical API text.
            if "marketplace_insights" not in " ".join(errors).lower():
                errors.append("Marketplace Insights sold-history data is not available for this app/account yet; showing active marketplace products.")

    deduped: Dict[str, Dict[str, Any]] = {}
    for product in all_products:
        key = product["id"] or product["title"].lower()
        if key not in deduped or product["score"] > deduped[key]["score"]:
            deduped[key] = product

    ranked = sorted(deduped.values(), key=lambda p: p.get("score", 0), reverse=True)[:MAX_PRODUCTS]

    return {
        "products": ranked,
        "errors": errors[:3],
        "generated_at_est": _now_est().strftime("%b %d, %Y %I:%M %p EST"),
        "reset_key": reset_key,
    }


def _product_card(product: Dict[str, Any], index: int) -> str:
    title = escape(product.get("title") or "Untitled")
    keyword = escape(product.get("keyword") or "market")
    image = escape(product.get("image") or "")
    url = escape(product.get("url") or "#")
    seller = escape(product.get("seller") or "eBay seller")
    condition = escape(product.get("condition") or "Not shown")
    price = product.get("price") or 0
    sold_24h = product.get("sold_24h")
    sold_90d = product.get("sold_90d")
    success = product.get("success_rate")

    success_text = f"{success}%" if success is not None else "Needs Insights access"
    sold_text = f"{sold_24h} / {sold_90d}" if sold_90d else "Active comp"

    return f"""
    <article class="trend-card">
        <div class="rank-pill">#{index}</div>
        <a href="{url}" target="_blank" rel="noopener noreferrer">
            <img src="{image}" class="trend-img" alt="{title}">
        </a>
        <div class="trend-content">
            <div class="trend-keyword">{keyword}</div>
            <h3>{title}</h3>
            <div class="trend-price">${price:,.2f}</div>
            <div class="trend-meta">
                <span>{condition}</span>
                <span>{seller}</span>
            </div>
            <div class="trend-stats">
                <div><b>{sold_text}</b><small>24h / 90d sold</small></div>
                <div><b>{success_text}</b><small>success rate</small></div>
            </div>
        </div>
    </article>
    """


def _render_products(products: List[Dict[str, Any]]) -> None:
    if not products:
        st.info("No marketplace products returned yet. Try broader keywords like 'electronics, shoes, collectibles'.")
        return

    total_pages = max(1, math.ceil(len(products) / PRODUCTS_PER_PAGE))
    page_key = "dashboard_market_page"
    st.session_state.setdefault(page_key, 1)
    st.session_state[page_key] = min(max(1, st.session_state[page_key]), total_pages)

    start = (st.session_state[page_key] - 1) * PRODUCTS_PER_PAGE
    page_products = products[start : start + PRODUCTS_PER_PAGE]

    cards = "".join(_product_card(p, start + i + 1) for i, p in enumerate(page_products))
    st.markdown(f'<div class="trend-strip">{cards}</div>', unsafe_allow_html=True)

    left, mid, right = st.columns([1, 2, 1])
    with left:
        if st.button("← Previous", use_container_width=True, disabled=st.session_state[page_key] <= 1):
            st.session_state[page_key] -= 1
            st.rerun()
    with mid:
        st.markdown(
            f"<div class='page-label'>Showing {start + 1}-{min(start + PRODUCTS_PER_PAGE, len(products))} of {len(products)} tracked products</div>",
            unsafe_allow_html=True,
        )
    with right:
        if st.button("Next →", use_container_width=True, disabled=st.session_state[page_key] >= total_pages):
            st.session_state[page_key] += 1
            st.rerun()


def _niche_summary(products: List[Dict[str, Any]]) -> pd.DataFrame:
    if not products:
        return pd.DataFrame(columns=["niche", "products", "sold_24h", "sold_90d", "avg_price", "success_rate"])

    df = pd.DataFrame(products)
    grouped = (
        df.groupby("keyword", dropna=False)
        .agg(
            products=("title", "count"),
            sold_24h=("sold_24h", "sum"),
            sold_90d=("sold_90d", "sum"),
            avg_price=("price", "mean"),
        )
        .reset_index()
        .rename(columns={"keyword": "niche"})
    )
    grouped["success_rate"] = grouped.apply(
        lambda r: round((r["sold_24h"] / r["sold_90d"]) * 100, 1) if r["sold_90d"] else None,
        axis=1,
    )
    return grouped.sort_values(["sold_24h", "products"], ascending=False)


def _render_charts(products: List[Dict[str, Any]]) -> None:
    df = _niche_summary(products)
    if df.empty:
        return

    st.markdown("<div class='section-title'>Highest-Signal Niches</div>", unsafe_allow_html=True)

    chart_df = df.head(8).set_index("niche")[["products"]]
    st.bar_chart(chart_df, height=230)

    with st.expander("Niche details", expanded=False):
        display = df.copy()
        display["avg_price"] = display["avg_price"].map(lambda x: f"${x:,.2f}")
        display["success_rate"] = display["success_rate"].map(lambda x: f"{x}%" if pd.notna(x) else "Requires Insights")
        st.dataframe(display, hide_index=True, use_container_width=True)


def _connected_label(owner_name: str) -> str:
    if get_connected_ebay_label:
        try:
            return get_connected_ebay_label(owner_name)
        except Exception:
            pass
    return "Connected eBay account"


def render_dashboard() -> None:
    _load_css()

    owner_name = _owner_name()
    name = st.session_state.get("client_name") or "User"

    st.markdown(
        f"""
        <section class="dash-hero">
            <div>
                <span class="eyebrow">Market Intelligence Dashboard</span>
                <h1>Find eBay products moving right now.</h1>
                <p>Research active marketplace products, niche demand signals, and sales-history availability in a compact SaaS workspace.</p>
            </div>
            <div class="hero-chip">
                <small>Connected account</small>
                <strong>{escape(_connected_label(owner_name))}</strong>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="control-panel">', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1:
        keyword_text = st.text_input(
            "Market keywords",
            value=", ".join(DEFAULT_NICHES[:6]),
            help="Comma-separated niches/products. The dashboard tracks up to 50 results and shows 5 at a time.",
        )
    with c2:
        marketplace_id = st.selectbox("Marketplace", ["EBAY_US", "EBAY_GB", "EBAY_CA", "EBAY_AU"], index=0)
    with c3:
        if st.button("Refresh scan", use_container_width=True):
            _fetch_market_products.clear()
            st.session_state["dashboard_market_page"] = 1
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    reset_key = _start_of_today_est().strftime("%Y-%m-%d")
    with st.spinner("Scanning live eBay marketplace products..."):
        result = _fetch_market_products(owner_name, keyword_text, marketplace_id, reset_key)

    products = result.get("products", [])

    m1, m2, m3, m4 = st.columns(4)
    niche_df = _niche_summary(products)
    known_90 = sum(p.get("sold_90d") or 0 for p in products)
    known_24 = sum(p.get("sold_24h") or 0 for p in products)
    with m1:
        st.metric("Tracked products", len(products))
    with m2:
        st.metric("Niches scanned", len(niche_df))
    with m3:
        st.metric("Known 24h sales", int(known_24))
    with m4:
        st.metric("Known 90d sales", int(known_90))

    if result.get("errors"):
        st.caption("Some sold-history signals require eBay Marketplace Insights approval. Active product discovery is still live.")

    st.markdown(
        f"""
        <div class="freshness">
            Daily research board resets at <b>12:00 AM EST</b>. Current board date: <b>{reset_key}</b>.
            Last refreshed: <b>{escape(result.get("generated_at_est", ""))}</b>.
        </div>
        """,
        unsafe_allow_html=True,
    )

    _render_charts(products)
    st.markdown("<div class='section-title'>Top Marketplace Product Signals</div>", unsafe_allow_html=True)
    _render_products(products)

    with st.expander("Export tracked products", expanded=False):
        if products:
            export_df = pd.DataFrame(products)
            keep_cols = [
                "title", "keyword", "category", "price", "seller", "condition",
                "sold_24h", "sold_90d", "success_rate", "url", "sales_source",
            ]
            keep_cols = [c for c in keep_cols if c in export_df.columns]
            st.download_button(
                "Download CSV",
                export_df[keep_cols].to_csv(index=False),
                "ebay_market_products.csv",
                "text/csv",
                use_container_width=True,
            )
        else:
            st.caption("No products available to export.")
