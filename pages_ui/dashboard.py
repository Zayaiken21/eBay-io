
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
    css_path = "styles/dashboard.css"
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
    # Marketplace Insights is an application-level API and rejects the user
    # token that call_ebay_api sends. Reconnecting the account could never fix
    # it; the call needs a client-credentials token carrying the Insights
    # scope, which is a different grant entirely.
    if "marketplace_insights" in endpoint:
        try:
            from core.ebay_oauth import call_ebay_app_api
        except Exception as exc:
            return None, f"application-token helper unavailable: {exc}"
        try:
            return _response_to_json(call_ebay_app_api(endpoint, params or {}))
        except Exception as exc:
            return None, str(exc)

    if call_ebay_api is None:
        return None, "call_ebay_api is not available."

    try:
        resp = call_ebay_api(owner_name, "GET", endpoint, params=params or {})
        return _response_to_json(resp)
    except Exception as exc:
        return None, str(exc)


# Rendered when a listing genuinely has no image. The previous fallback was a
# hardcoded ir.ebaystatic.com placeholder that no longer resolves, so missing
# images showed the browser's broken-image icon. An inline SVG always renders.
_NO_IMAGE = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
    "viewBox='0 0 160 120'%3E%3Crect width='160' height='120' fill='%23f1f5f9'/%3E"
    "%3Ctext x='80' y='64' font-family='sans-serif' font-size='11' fill='%2394a3b8' "
    "text-anchor='middle'%3ENo image%3C/text%3E%3C/svg%3E"
)


def _first_image_url(entries: Any) -> str:
    """First usable imageUrl from a Browse image list, tolerating [] and None."""
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return ""
    for entry in entries:
        if isinstance(entry, dict) and entry.get("imageUrl"):
            return str(entry["imageUrl"])
        if isinstance(entry, str) and entry:
            return entry
    return ""


def _item_image(item: Dict[str, Any]) -> str:
    """
    Browse does not return `image` on every summary — some listings only carry
    thumbnailImages, and a few only additionalImages. Indexing
    thumbnailImages[0] directly also raised IndexError when the key was
    present but empty.
    """
    url = (
        _first_image_url(item.get("image"))
        or _first_image_url(item.get("thumbnailImages"))
        or _first_image_url(item.get("additionalImages"))
    )
    if not url:
        return _NO_IMAGE
    if url.startswith("//"):
        url = f"https:{url}"
    # eBay serves s-l225 thumbs in search results; request a larger rendition
    # so the card image is not upscaled and blurry.
    return re.sub(r"/s-l\d+\.", "/s-l500.", url)


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


_STOPWORDS = {
    "the", "and", "for", "with", "new", "free", "set", "pack", "size",
    "pcs", "pc", "lot", "kit", "usa", "ship", "shipping", "inch", "in",
}


def _title_tokens(title: str) -> set:
    """Significant lowercase tokens, used to match sold items to active ones."""
    words = re.findall(r"[a-z0-9]+", str(title).lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _match_sold(title: str, sales: Dict[str, Dict[str, Any]]) -> int:
    """
    Sold quantity for the best-matching sold listing.

    eBay titles for the same product vary by seller, so an exact string
    comparison finds almost nothing. Jaccard overlap over significant
    tokens, with a conservative threshold, matches real pairs without
    collapsing distinct products together.
    """
    want = _title_tokens(title)
    if not want:
        return 0

    best, best_score = 0, 0.0
    for entry in sales.values():
        have = entry.get("_tokens") or set()
        if not have:
            continue
        union = want | have
        score = len(want & have) / len(union) if union else 0.0
        if score > best_score:
            best_score, best = score, entry.get("sold_count", 0)

    return best if best_score >= 0.45 else 0


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
            # Precomputed so matching does not retokenize per comparison.
            "_tokens": _title_tokens(title),
            "price": _currency(amount_value),
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
    niche_totals: Dict[str, int] = {}
    niche_sold: Dict[str, Dict[str, int]] = {}

    for keyword in keywords:
        # 10 listings is too thin a sample to compute a median, a price
        # spread or seller concentration from. Browse allows up to 200.
        browse_params = {
            "q": keyword,
            "limit": 100,
            "sort": "newlyListed",
        }

        data, err = _api_get(owner_name, "/buy/browse/v1/item_summary/search", browse_params)
        if err:
            errors.append(f"{keyword}: Browse search unavailable ({err})")
            continue

        # `total` is the count of ALL active listings matching the keyword,
        # not just the page we fetched — the real supply signal for a niche.
        niche_totals[keyword] = int((data or {}).get("total") or 0)

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
                "limit": 200,
                "filter": f"lastSoldDate:[{_safe_utc(start_24)}..{_safe_utc(end_est)}]",
            },
        )

        insights_90, err_90 = _api_get(
            owner_name,
            "/buy/marketplace_insights/v1_beta/item_sales/search",
            {
                "q": keyword,
                "limit": 200,
                "filter": f"lastSoldDate:[{_safe_utc(start_90)}..{_safe_utc(end_est)}]",
            },
        )

        sales24 = _normalize_sales_items(insights_24 or {}, keyword) if insights_24 else {}
        sales90 = _normalize_sales_items(insights_90 or {}, keyword) if insights_90 else {}

        # Niche-level sold totals. `total` counts every sale matching the
        # query in the window, independent of whether a sold listing can be
        # matched to an active one, so it is the trustworthy figure.
        sold_24_total = int((insights_24 or {}).get("total") or 0)
        sold_90_total = int((insights_90 or {}).get("total") or 0)
        niche_sold[keyword] = {"sold_24h": sold_24_total, "sold_90d": sold_90_total}

        for product in products:
            # Sold and active listings rarely share a byte-identical title,
            # so exact-string keying returned 0 for almost everything even
            # with Insights access. Match on significant-token overlap.
            product["sold_24h"] = _match_sold(product["title"], sales24)
            product["sold_90d"] = _match_sold(product["title"], sales90)

            product["sales_source"] = (
                "eBay Marketplace Insights" if (sales24 or sales90)
                else "Active eBay marketplace listing"
            )
            # Sell-through: share of this niche's active supply that sold in
            # the window. This is the "is it moving" number. The previous
            # formula divided 24h sold by 90d sold, which measures recency
            # of sales, not whether anything sells at all.
            active = niche_totals.get(keyword, 0)
            product["sell_through_90d"] = (
                round(sold_90_total / active * 100, 1) if active else None
            )
            product["niche_sold_24h"] = sold_24_total
            product["niche_sold_90d"] = sold_90_total
            product["success_rate"] = product["sell_through_90d"]
            # Ranking score prioritizes known sales; fallback ranks active comps by feedback/price.
            product["score"] = (
                product["sold_24h"] * 8
                + product["sold_90d"] * 1.5
                + min(product["feedback"], 10000) / 10000
                + min(product["price"], 500) / 500
            )
            all_products.append(product)

        # Report what actually went wrong. Collapsing every failure into
        # "not available for this app/account" hid real, fixable faults: a
        # rejected date filter, a missing OAuth scope and a genuine access
        # denial all produced the same message, so a working Insights
        # subscription looked identical to a broken request.
        for window, err in (("24h", err_24), ("90d", err_90)):
            if not err:
                continue
            text = str(err)
            low = text.lower()
            if "403" in low or "insufficient" in low or "scope" in low:
                msg = (f"Marketplace Insights ({window}) refused the request: {text}. "
                       f"This usually means the app's OAuth token is missing the "
                       f"buy.marketplace.insights scope — reconnect the eBay account "
                       f"in Settings so the new scope is granted.")
            elif "400" in low or "invalid" in low:
                msg = (f"Marketplace Insights ({window}) rejected the query as invalid: "
                       f"{text}. The date filter or keyword is malformed.")
            else:
                msg = f"Marketplace Insights ({window}) failed: {text}"
            if msg not in errors:
                errors.append(msg)

        # Access worked but matched nothing — a real, different answer.
        if (insights_24 or insights_90) and not (sold_24_total or sold_90_total):
            note = (f"Marketplace Insights returned no completed sales for "
                    f"'{keyword}' in the last 90 days.")
            if note not in errors:
                errors.append(note)

    deduped: Dict[str, Dict[str, Any]] = {}
    for product in all_products:
        key = product["id"] or product["title"].lower()
        if key not in deduped or product["score"] > deduped[key]["score"]:
            deduped[key] = product

    ranked = sorted(deduped.values(), key=lambda p: p.get("score", 0), reverse=True)[:MAX_PRODUCTS]

    return {
        "products": ranked,
        # The full sample backs the statistics; `ranked` is only the top slice
        # shown as cards, and medians taken from it would be badly skewed.
        "sample": all_products,
        "niche_totals": niche_totals,
        "niche_sold": niche_sold,
        "errors": errors[:6],
        "generated_at_est": _now_est().strftime("%b %d, %Y %I:%M %p EST"),
        "reset_key": reset_key,
    }


def _niche_analytics(sample: List[Dict[str, Any]], totals: Dict[str, int],
                     sold: Dict[str, Dict[str, int]] = None) -> "pd.DataFrame":
    """
    Supply-side statistics per niche, computed from the active-listing sample.

    Every column here is derived from data the Browse API returns for all
    accounts. None of it is sold-velocity: that requires Marketplace
    Insights, and inventing a proxy for it would be worse than leaving the
    column out, because a seller would act on it.
    """
    rows = []
    by_niche: Dict[str, List[Dict[str, Any]]] = {}
    for p in sample:
        by_niche.setdefault(p.get("keyword") or "unknown", []).append(p)

    for niche, items in by_niche.items():
        prices = sorted(p["price"] for p in items if p.get("price"))
        if not prices:
            continue
        s = pd.Series(prices)

        sellers = [p.get("seller") for p in items if p.get("seller")]
        top_share = 0.0
        if sellers:
            counts = pd.Series(sellers).value_counts()
            top_share = round(counts.iloc[0] / len(sellers) * 100, 1)

        free_ship = sum(1 for p in items if not p.get("shipping")) / len(items) * 100
        new_share = sum(1 for p in items
                        if "new" in str(p.get("condition", "")).lower()) / len(items) * 100

        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        sold_n = (sold or {}).get(niche, {})
        active_n = totals.get(niche, 0)
        sold_90 = sold_n.get("sold_90d", 0)

        rows.append({
            "niche": niche,
            "active_listings": active_n,
            "sold_24h": sold_n.get("sold_24h", 0),
            "sold_90d": sold_90,
            # The headline number: what share of active supply actually sold.
            "sell_through_pct": round(sold_90 / active_n * 100, 1) if active_n else None,
            "sold_per_day": round(sold_90 / 90, 1) if sold_90 else 0.0,
            "sampled": len(items),
            "median_price": round(s.median(), 2),
            "p25": round(q1, 2),
            "p75": round(q3, 2),
            # Wide dispersion means the niche is not commoditised — there is
            # room to position on price rather than race to the bottom.
            "spread_pct": round((q3 - q1) / s.median() * 100, 1) if s.median() else 0.0,
            "free_shipping_pct": round(free_ship, 1),
            "new_condition_pct": round(new_share, 1),
            "unique_sellers": len(set(sellers)),
            "top_seller_share_pct": top_share,
            "avg_seller_feedback": int(pd.Series(
                [p.get("feedback", 0) for p in items]).mean()),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Competition: more listings and a more concentrated top seller both make
    # a niche harder to enter. Shown as an explicit, inspectable number
    # rather than a black-box "opportunity score".
    df["competition"] = (
        df["active_listings"].rank(pct=True) * 70
        + df["top_seller_share_pct"].rank(pct=True) * 30
    ).round(0).astype(int)

    # Rank by what actually sells when sold data is present; fall back to
    # supply only when it is not, rather than pretending to know demand.
    if df["sell_through_pct"].notna().any():
        return df.sort_values("sell_through_pct", ascending=False,
                              na_position="last").reset_index(drop=True)
    return df.sort_values("active_listings", ascending=False).reset_index(drop=True)


def _product_card(product: Dict[str, Any], index: int) -> str:
    title = escape(product.get("title") or "Untitled")
    keyword = escape(product.get("keyword") or "market")
    image = escape(product.get("image") or "")
    url = escape(product.get("url") or "#")
    seller = escape(product.get("seller") or "eBay seller")
    condition = escape(product.get("condition") or "")
    price = product.get("price") or 0
    sold_24h = product.get("sold_24h")
    sold_90d = product.get("sold_90d")
    success = product.get("success_rate")

    # Render a stat tile only when real data backs it. Placeholder strings
    # ("Not shown", "Needs Insights access") read as filler on the card.
    tiles = []
    if sold_90d:
        tiles.append(
            f"<div><b>{sold_24h} / {sold_90d}</b><small>24h / 90d sold</small></div>"
        )
    if success is not None:
        tiles.append(f"<div><b>{success}%</b><small>success rate</small></div>")
    stats_html = (
        f'<div class="trend-stats">{"".join(tiles)}</div>' if tiles else ""
    )
    cond_html = f"<span>{condition}</span>" if condition else ""

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
{cond_html}
<span>{seller}</span>
</div>
{stats_html}
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


def _render_charts(products: List[Dict[str, Any]], result: Dict[str, Any] = None) -> None:
    result = result or {}
    sample = result.get("sample") or products
    df = _niche_analytics(sample, result.get("niche_totals") or {},
                          result.get("niche_sold") or {})
    if df.empty:
        return

    has_sold = df["sell_through_pct"].notna().any()

    st.markdown("<div class='section-title'>Niche Comparison</div>", unsafe_allow_html=True)

    top = df.iloc[0]

    # Built as markup rather than st.metric. delta_color="off" only greys the
    # delta — Streamlit still draws an up-arrow beside it, which reads as
    # growth on figures that have not moved, "0.0% of supply" included.
    def _kpi(label: str, value: str, hint: str) -> str:
        return (f'<div class="kpi"><small>{escape(label)}</small>'
                f'<b>{escape(value)}</b><span>{escape(hint)}</span></div>')

    if has_sold:
        tiles = [
            _kpi("Best sell-through", str(top["niche"]),
                 f"{top['sell_through_pct']}% of supply"),
            _kpi("Sold / day", f"{top['sold_per_day']:,.1f}", "90-day avg"),
            _kpi("Median price", f"${top['median_price']:,.0f}",
                 f"${top['p25']:,.0f}–${top['p75']:,.0f} typical"),
            _kpi("Competition", f"{top['competition']}/100",
                 f"{top['active_listings']:,} listings"),
        ]
    else:
        # No sold data: show only what is measured, rather than a row of
        # zeroes implying nothing sells in these niches.
        tiles = [
            _kpi("Largest supply", str(top["niche"]),
                 f"{top['active_listings']:,} listings"),
            _kpi("Median price", f"${top['median_price']:,.0f}",
                 f"${top['p25']:,.0f}–${top['p75']:,.0f} typical"),
            _kpi("Competition", f"{top['competition']}/100",
                 f"{top['unique_sellers']} sellers sampled"),
        ]

    st.markdown(f'<div class="kpi-row">{"".join(tiles)}</div>',
                unsafe_allow_html=True)

    if not has_sold:
        st.caption(
            "Sold-history columns are empty — Marketplace Insights returned no "
            "completed sales, so these figures describe supply only, not demand."
        )

    # Sorted by whatever the seller is actually deciding on.
    metric_label = {
        "Sell-through %": "sell_through_pct",
        "Units sold (90d)": "sold_90d",
        "Units sold (24h)": "sold_24h",
        "Median price": "median_price",
        "Active listings": "active_listings",
        "Competition": "competition",
    }
    choices = [k for k, v in metric_label.items()
               if has_sold or v not in ("sell_through_pct", "sold_90d", "sold_24h")]

    ctl1, ctl2 = st.columns([1, 1])
    with ctl1:
        pick = st.selectbox("Rank niches by", choices, index=0)
    with ctl2:
        # Without this the table always showed every niche in a fixed order,
        # so comparing two of them meant scanning past the rest.
        shown = st.multiselect(
            "Niches shown", list(df["niche"]), default=list(df["niche"]),
            help="Narrow the chart and table to the niches you are comparing.",
        )
    if shown:
        df = df[df["niche"].isin(shown)].reset_index(drop=True)
    if df.empty:
        st.caption("No niches selected.")
        return

    col = metric_label[pick]

    # Column groups, so the table shows what is being decided on rather than
    # every statistic at once.
    GROUPS = {
        "Demand": ["niche", "sold_24h", "sold_90d", "sell_through_pct", "sold_per_day"],
        "Pricing": ["niche", "median_price", "p25", "p75", "spread_pct"],
        "Competition": ["niche", "active_listings", "unique_sellers",
                        "top_seller_share_pct", "competition"],
        "Listing quality": ["niche", "new_condition_pct", "free_shipping_pct",
                            "avg_seller_feedback"],
    }
    group_names = [g for g in GROUPS if has_sold or g != "Demand"]
    group = st.radio("Columns", ["All"] + group_names,
                     horizontal=True, label_visibility="collapsed")

    ranked = df.sort_values(col, ascending=False, na_position="last")
    chart_df = ranked.head(10)[["niche", col]].copy()

    # Active-listing counts span orders of magnitude (9.4M pokemon cards
    # against 14k blenders), so a linear axis renders every other bar as a
    # hairline. Horizontal bars on a log axis keep all ten readable and put
    # the niche names on the axis instead of rotated vertically.
    values = chart_df[col].dropna()
    needs_log = (
        len(values) > 1 and values.min() > 0
        and values.max() / max(values.min(), 1) > 50
    )
    try:
        import altair as alt

        scale = alt.Scale(type="log") if needs_log else alt.Scale(type="linear")
        chart = (
            alt.Chart(chart_df)
            .mark_bar(cornerRadiusEnd=3, color="#2457ff")
            .encode(
                x=alt.X(f"{col}:Q", scale=scale,
                        title=f"{pick} (log scale)" if needs_log else pick),
                y=alt.Y("niche:N", sort="-x", title=None),
                tooltip=["niche", col],
            )
            .properties(height=max(150, 26 * len(chart_df)))
        )
        st.altair_chart(chart, use_container_width=True)
        if needs_log:
            st.caption(
                "Log scale — these niches differ by orders of magnitude, so a "
                "linear axis would hide everything but the largest."
            )
    except Exception:
        st.bar_chart(chart_df.set_index("niche")[[col]], height=200)

    display = ranked.copy()
    for money in ("median_price", "p25", "p75"):
        display[money] = display[money].map(lambda x: f"${x:,.2f}")
    for pct in ("sell_through_pct", "free_shipping_pct",
                "new_condition_pct", "top_seller_share_pct", "spread_pct"):
        display[pct] = display[pct].map(
            lambda x: f"{x}%" if pd.notna(x) else "—")
    display["active_listings"] = display["active_listings"].map(lambda x: f"{x:,}")

    if group != "All":
        keep = [c for c in GROUPS[group] if c in display.columns]
        display = display[keep]

    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "niche": "Niche",
            "active_listings": st.column_config.TextColumn(
                "Active", help="Total live listings matching this keyword."),
            "sold_24h": "Sold 24h",
            "sold_90d": "Sold 90d",
            "sell_through_pct": st.column_config.TextColumn(
                "Sell-through",
                help="Units sold in 90 days as a share of active listings. "
                     "Higher means demand is absorbing supply."),
            "sold_per_day": "Units/day",
            "median_price": "Median",
            "p25": "25th pct",
            "p75": "75th pct",
            "spread_pct": st.column_config.TextColumn(
                "Spread", help="Interquartile range over the median. Wide "
                               "spread means room to position on price."),
            "free_shipping_pct": "Free ship",
            "new_condition_pct": "New",
            "unique_sellers": "Sellers",
            "top_seller_share_pct": st.column_config.TextColumn(
                "Top seller", help="Share of sampled listings held by the "
                                   "single largest seller."),
            "avg_seller_feedback": "Avg feedback",
            "competition": st.column_config.ProgressColumn(
                "Competition", min_value=0, max_value=100, format="%d"),
            "sampled": None,
        },
    )

    # Drill-down: one niche at a time, with its real price distribution.
    st.markdown("<div class='section-title'>Niche Detail</div>", unsafe_allow_html=True)
    focus = st.selectbox("Inspect a niche", list(df["niche"]), key="niche_focus")
    items = [p for p in sample if p.get("keyword") == focus and p.get("price")]
    row = df[df["niche"] == focus].iloc[0]

    if items:
        left, right = st.columns([1, 1])
        with left:
            prices = pd.Series(sorted(p["price"] for p in items))
            bins = pd.cut(prices, bins=min(10, max(3, len(prices) // 3)))
            hist = prices.groupby(bins, observed=True).count()

            # st.bar_chart sorts its index as text, so "$9–80" landed after
            # "$642–712". Carry the numeric lower bound and sort on it.
            dist = pd.DataFrame({
                "band": [f"${int(i.left)}–{int(i.right)}" for i in hist.index],
                "lower": [float(i.left) for i in hist.index],
                "listings": hist.values,
            }).sort_values("lower")

            st.caption(f"Price distribution — {len(items)} sampled listings")
            # Drawn with altair rather than st.bar_chart: the built-in chart
            # was falling back to a raw table with an index column, and it
            # cannot hold the bands in numeric order without this.
            try:
                import altair as alt

                order = list(dist["band"])
                st.altair_chart(
                    alt.Chart(dist)
                    .mark_bar(cornerRadiusEnd=3, color="#2457ff")
                    .encode(
                        x=alt.X("band:N", sort=order, title=None,
                                axis=alt.Axis(labelAngle=-40, labelFontSize=10)),
                        y=alt.Y("listings:Q", title=None,
                                axis=alt.Axis(labelFontSize=10, tickMinStep=1)),
                        tooltip=[alt.Tooltip("band:N", title="Price"),
                                 alt.Tooltip("listings:Q", title="Listings")],
                    )
                    .properties(height=190),
                    use_container_width=True,
                )
            except Exception:
                st.bar_chart(dist.set_index("band")[["listings"]], height=190)
        with right:
            st.caption("Where this niche stands")

            # Proportions read far faster as filled bars than as sentences,
            # and a bar makes "11% of listings" instantly comparable against
            # the other rows. Values stay on the label so nothing is lost.
            def _meter(label: str, pct: float, hint: str = "") -> str:
                pct = max(0.0, min(100.0, float(pct or 0)))
                return (
                    f'<div class="meter-row">'
                    f'<div class="meter-head"><span>{escape(label)}</span>'
                    f'<b>{pct:g}%</b></div>'
                    f'<div class="meter-track">'
                    f'<div class="meter-fill" style="width:{pct}%"></div></div>'
                    + (f'<small>{escape(hint)}</small>' if hint else "")
                    + '</div>'
                )

            blocks = [
                f'<div class="stat-grid">'
                f'<div class="stat"><small>Sellers</small>'
                f'<b>{row["unique_sellers"]}</b></div>'
                f'<div class="stat"><small>Active listings</small>'
                f'<b>{row["active_listings"]:,}</b></div>'
                f'<div class="stat"><small>Median</small>'
                f'<b>${row["median_price"]:,.0f}</b></div>'
                f'<div class="stat"><small>Typical range</small>'
                f'<b>${row["p25"]:,.0f}–${row["p75"]:,.0f}</b></div>'
                f'</div>',
                _meter("Listed as new", row["new_condition_pct"]),
                _meter("Free shipping", row["free_shipping_pct"]),
                _meter("Largest seller's share", row["top_seller_share_pct"],
                       "Lower means supply is spread across many sellers"),
            ]
            if pd.notna(row["sell_through_pct"]):
                blocks.append(_meter(
                    "Sell-through (90d)", row["sell_through_pct"],
                    f'{row["sold_90d"]:,} sold vs {row["active_listings"]:,} active'))

            st.markdown("".join(blocks), unsafe_allow_html=True)

        top_sellers = pd.Series([p.get("seller") for p in items if p.get("seller")])
        if not top_sellers.empty:
            st.caption("Sellers holding the most listings in this niche")
            counts = top_sellers.value_counts().head(8)
            sellers_df = pd.DataFrame({
                # Full seller IDs rotated vertically and ate the chart; clip
                # them so the bars stay readable.
                "seller": [s if len(s) <= 14 else s[:13] + "\u2026"
                           for s in counts.index],
                "listings": counts.values,
            })
            st.bar_chart(sellers_df, x="seller", y="listings", height=190,
                         x_label="", y_label="")


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

    # A raw <div> opened here and closed in a later st.markdown does not wrap
    # the widgets between them — Streamlit puts every markdown call in its own
    # container, so the two halves rendered as empty styled bars above and
    # below the controls. st.container(border=True) is the real wrapper.
    panel = st.container(border=True)
    c1, c2, c3 = panel.columns([3, 1, 1])
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

    _render_charts(products, result)
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
