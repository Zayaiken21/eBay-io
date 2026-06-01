
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

try:
    from core.ebay_account_store import (
        call_ebay_api,
        get_connected_ebay_label,
        get_latest_ebay_account,
    )
except Exception:  # pragma: no cover - keeps dashboard from crashing during local setup
    call_ebay_api = None
    get_connected_ebay_label = None
    get_latest_ebay_account = None


EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
MARKETPLACE_ID = "EBAY_US"
PRODUCTS_PER_PAGE = 5
MAX_TRACKED_PRODUCTS = 50


# Public-domain KJV scripture seed list.
# The app rotates these by day of week and quote index. Add more verified KJV verses here
# or load from Supabase/JSON later if you want a full 1000-verse local library.
SCRIPTURE_BY_DAY = {
    0: [
        ("Genesis 1:3", "And God said, Let there be light: and there was light."),
        ("Psalm 118:24", "This is the day which the LORD hath made; we will rejoice and be glad in it."),
        ("Proverbs 3:5", "Trust in the LORD with all thine heart; and lean not unto thine own understanding."),
        ("Matthew 5:16", "Let your light so shine before men, that they may see your good works, and glorify your Father which is in heaven."),
        ("John 8:12", "I am the light of the world: he that followeth me shall not walk in darkness, but shall have the light of life."),
        ("Romans 8:31", "If God be for us, who can be against us?"),
        ("Philippians 4:13", "I can do all things through Christ which strengtheneth me."),
    ],
    1: [
        ("Psalm 23:1", "The LORD is my shepherd; I shall not want."),
        ("Isaiah 40:31", "But they that wait upon the LORD shall renew their strength; they shall mount up with wings as eagles."),
        ("Matthew 6:33", "But seek ye first the kingdom of God, and his righteousness; and all these things shall be added unto you."),
        ("John 14:6", "I am the way, the truth, and the life: no man cometh unto the Father, but by me."),
        ("Romans 12:2", "Be ye transformed by the renewing of your mind."),
        ("2 Timothy 1:7", "For God hath not given us the spirit of fear; but of power, and of love, and of a sound mind."),
        ("James 1:5", "If any of you lack wisdom, let him ask of God."),
    ],
    2: [
        ("Psalm 46:1", "God is our refuge and strength, a very present help in trouble."),
        ("Psalm 119:105", "Thy word is a lamp unto my feet, and a light unto my path."),
        ("Proverbs 16:3", "Commit thy works unto the LORD, and thy thoughts shall be established."),
        ("Matthew 11:28", "Come unto me, all ye that labour and are heavy laden, and I will give you rest."),
        ("John 15:5", "Without me ye can do nothing."),
        ("Galatians 6:9", "Let us not be weary in well doing: for in due season we shall reap, if we faint not."),
        ("Hebrews 11:1", "Now faith is the substance of things hoped for, the evidence of things not seen."),
    ],
    3: [
        ("Joshua 1:9", "Be strong and of a good courage; be not afraid, neither be thou dismayed."),
        ("Psalm 37:4", "Delight thyself also in the LORD; and he shall give thee the desires of thine heart."),
        ("Proverbs 18:10", "The name of the LORD is a strong tower: the righteous runneth into it, and is safe."),
        ("Matthew 7:7", "Ask, and it shall be given you; seek, and ye shall find."),
        ("John 3:16", "For God so loved the world, that he gave his only begotten Son."),
        ("Ephesians 2:8", "For by grace are ye saved through faith; and that not of yourselves: it is the gift of God."),
        ("1 Peter 5:7", "Casting all your care upon him; for he careth for you."),
    ],
    4: [
        ("Psalm 34:8", "O taste and see that the LORD is good: blessed is the man that trusteth in him."),
        ("Psalm 55:22", "Cast thy burden upon the LORD, and he shall sustain thee."),
        ("Proverbs 4:23", "Keep thy heart with all diligence; for out of it are the issues of life."),
        ("Matthew 28:20", "Lo, I am with you alway, even unto the end of the world."),
        ("John 10:10", "I am come that they might have life, and that they might have it more abundantly."),
        ("Colossians 3:23", "And whatsoever ye do, do it heartily, as to the Lord, and not unto men."),
        ("1 John 4:19", "We love him, because he first loved us."),
    ],
    5: [
        ("Exodus 14:14", "The LORD shall fight for you, and ye shall hold your peace."),
        ("Psalm 27:1", "The LORD is my light and my salvation; whom shall I fear?"),
        ("Proverbs 3:6", "In all thy ways acknowledge him, and he shall direct thy paths."),
        ("Mark 10:27", "With God all things are possible."),
        ("John 16:33", "In the world ye shall have tribulation: but be of good cheer; I have overcome the world."),
        ("Romans 15:13", "Now the God of hope fill you with all joy and peace in believing."),
        ("Revelation 21:4", "And God shall wipe away all tears from their eyes."),
    ],
    6: [
        ("Numbers 6:24", "The LORD bless thee, and keep thee."),
        ("Psalm 91:1", "He that dwelleth in the secret place of the most High shall abide under the shadow of the Almighty."),
        ("Proverbs 11:25", "The liberal soul shall be made fat: and he that watereth shall be watered also himself."),
        ("Luke 1:37", "For with God nothing shall be impossible."),
        ("John 14:27", "Peace I leave with you, my peace I give unto you."),
        ("2 Corinthians 5:7", "For we walk by faith, not by sight."),
        ("Hebrews 13:8", "Jesus Christ the same yesterday, and to day, and for ever."),
    ],
}


@dataclass
class DashboardWindow:
    label: str
    start_utc: datetime
    end_utc: datetime


def _owner_name() -> str:
    return (
        st.session_state.get("client_name")
        or st.session_state.get("owner_name")
        or st.session_state.get("username")
        or "default"
    )


def _is_ceo() -> bool:
    role = str(st.session_state.get("role", "")).lower()
    return bool(st.session_state.get("is_ceo")) or role == "ceo"


def _load_dashboard_css() -> None:
    paths = ["pages_ui/dashboard.css", "dashboard.css"]
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
                return
        except FileNotFoundError:
            continue


def _now_est() -> datetime:
    return datetime.now(EASTERN)


def _api_safe_utc(dt: datetime) -> datetime:
    """eBay rejects future times; clamp to now minus a small safety buffer."""
    safe_now = datetime.now(UTC) - timedelta(minutes=3)
    dt_utc = dt.astimezone(UTC)
    return min(dt_utc, safe_now)


def _fmt_ebay_time(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _windows() -> Tuple[DashboardWindow, DashboardWindow]:
    now_est = _now_est()
    end_utc = _api_safe_utc(now_est)
    start_24_est = now_est - timedelta(hours=24)
    start_90_est = now_est - timedelta(days=90)
    return (
        DashboardWindow("Last 24 Hours", _api_safe_utc(start_24_est), end_utc),
        DashboardWindow("Last 90 Days", _api_safe_utc(start_90_est), end_utc),
    )


def _midnight_countdown() -> str:
    now = _now_est()
    tomorrow_midnight = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=EASTERN)
    delta = tomorrow_midnight - now
    hours, rem = divmod(int(delta.total_seconds()), 3600)
    minutes, _ = divmod(rem, 60)
    return f"{hours}h {minutes}m"


def _quote_for_today() -> Tuple[str, str, str]:
    now = _now_est()
    verses = SCRIPTURE_BY_DAY[now.weekday()]
    index = (now.timetuple().tm_yday + now.hour // 4) % len(verses)
    ref, text = verses[index]
    theme = ["gold", "blue", "purple", "green", "rose", "teal", "sunrise"][now.weekday()]
    return ref, text, theme


def _get_connected_account(owner_name: str) -> Optional[Dict[str, Any]]:
    if not get_latest_ebay_account:
        return None
    try:
        account = get_latest_ebay_account(owner_name)
        if account:
            return account
        if owner_name != "default":
            return get_latest_ebay_account("default")
    except Exception:
        return None
    return None


def _connection_label(owner_name: str) -> str:
    if get_connected_ebay_label:
        try:
            label = get_connected_ebay_label(owner_name)
            if label and "No eBay" not in label:
                return label
        except Exception:
            pass
    account = _get_connected_account(owner_name)
    if not account:
        return "No connected eBay account"
    return account.get("store_name") or account.get("ebay_username") or account.get("ebay_user_id") or "Connected eBay account"


@st.cache_data(ttl=900, show_spinner=False)
def _fetch_orders_cached(owner_name: str, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
    if call_ebay_api is None:
        raise RuntimeError("eBay API helper is not available.")

    orders: List[Dict[str, Any]] = []
    offset = 0
    limit = 100

    while offset < 1000:
        params = {
            "limit": str(limit),
            "offset": str(offset),
            "filter": f"creationdate:[{start_iso}..{end_iso}]",
        }
        resp = call_ebay_api(owner_name, "GET", "/sell/fulfillment/v1/order", params=params)

        if isinstance(resp, dict):
            data = resp
        elif hasattr(resp, "json"):
            if resp.status_code >= 400:
                # Try fallback without filter once. Some seller accounts reject date filters depending on account state.
                fallback = call_ebay_api(
                    owner_name,
                    "GET",
                    "/sell/fulfillment/v1/order",
                    params={"limit": str(limit), "offset": str(offset)},
                )
                data = fallback if isinstance(fallback, dict) else fallback.json()
            else:
                data = resp.json()
        else:
            data = {}

        batch = data.get("orders", []) or data.get("orderSummaries", []) or []
        orders.extend(batch)

        total = int(data.get("total", len(orders)) or len(orders))
        if not batch or len(orders) >= total:
            break
        offset += limit

    return orders


def _fetch_orders(owner_name: str, window: DashboardWindow) -> List[Dict[str, Any]]:
    return _fetch_orders_cached(owner_name, _fmt_ebay_time(window.start_utc), _fmt_ebay_time(window.end_utc))


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_inventory_cached(owner_name: str) -> List[Dict[str, Any]]:
    if call_ebay_api is None:
        return []
    items: List[Dict[str, Any]] = []
    offset = 0
    limit = 100
    while len(items) < MAX_TRACKED_PRODUCTS and offset < 500:
        params = {"limit": str(limit), "offset": str(offset)}
        resp = call_ebay_api(owner_name, "GET", "/sell/inventory/v1/inventory_item", params=params)
        data = resp if isinstance(resp, dict) else resp.json()
        batch = data.get("inventoryItems", []) or []
        items.extend(batch)
        if not batch or len(batch) < limit:
            break
        offset += limit
    return items[:MAX_TRACKED_PRODUCTS]


def _money_value(value: Any) -> float:
    if not value:
        return 0.0
    if isinstance(value, dict):
        try:
            return float(value.get("value", 0) or 0)
        except Exception:
            return 0.0
    try:
        return float(value)
    except Exception:
        return 0.0


def _order_total(order: Dict[str, Any]) -> float:
    for key in ("pricingSummary", "total", "orderTotal"):
        obj = order.get(key)
        if isinstance(obj, dict):
            if "total" in obj:
                return _money_value(obj.get("total"))
            if "value" in obj:
                return _money_value(obj)
    return sum(_money_value(li.get("lineItemCost")) for li in order.get("lineItems", []) or [])


def _parse_order_time(order: Dict[str, Any]) -> Optional[datetime]:
    raw = order.get("creationDate") or order.get("createdDate") or order.get("orderCreationDate")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(EASTERN)
    except Exception:
        return None


def _line_items(orders: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for order in orders:
        order_id = order.get("orderId") or order.get("legacyOrderId") or "Unknown"
        order_time = _parse_order_time(order)
        order_status = order.get("orderFulfillmentStatus") or order.get("orderPaymentStatus") or order.get("status") or "Unknown"
        for item in order.get("lineItems", []) or []:
            qty = int(item.get("quantity", 1) or 1)
            title = item.get("title") or item.get("sku") or "Unknown item"
            sku = item.get("sku") or item.get("legacyItemId") or title[:48]
            rows.append(
                {
                    "order_id": order_id,
                    "created_est": order_time,
                    "status": order_status,
                    "title": title,
                    "sku": sku,
                    "qty": qty,
                    "revenue": _money_value(item.get("lineItemCost")) * max(qty, 1),
                    "niche": _infer_niche(title),
                }
            )
    return rows


def _infer_niche(title: str) -> str:
    words = [w.strip(" -_/|,.:;()[]{}").title() for w in str(title).split() if len(w.strip()) > 2]
    stop = {"The", "And", "For", "With", "New", "Used", "Lot", "Pack", "Set", "Mens", "Womens"}
    words = [w for w in words if w not in stop]
    if not words:
        return "General"
    return " ".join(words[:2])


def _product_stats(line_rows_90: List[Dict[str, Any]], line_rows_24: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stats: Dict[str, Dict[str, Any]] = {}
    for row in line_rows_90:
        key = row["sku"] or row["title"]
        item = stats.setdefault(
            key,
            {
                "sku": row["sku"],
                "title": row["title"],
                "niche": row["niche"],
                "qty_90": 0,
                "revenue_90": 0.0,
                "orders_90": set(),
                "qty_24": 0,
                "revenue_24": 0.0,
            },
        )
        item["qty_90"] += row["qty"]
        item["revenue_90"] += row["revenue"]
        item["orders_90"].add(row["order_id"])

    for row in line_rows_24:
        key = row["sku"] or row["title"]
        item = stats.setdefault(
            key,
            {
                "sku": row["sku"],
                "title": row["title"],
                "niche": row["niche"],
                "qty_90": 0,
                "revenue_90": 0.0,
                "orders_90": set(),
                "qty_24": 0,
                "revenue_24": 0.0,
            },
        )
        item["qty_24"] += row["qty"]
        item["revenue_24"] += row["revenue"]

    products: List[Dict[str, Any]] = []
    for item in stats.values():
        days_active = 90
        sell_rate = item["qty_90"] / days_active
        success_score = min(100, round((item["qty_90"] * 2.0) + (item["revenue_90"] / 25.0) + (item["qty_24"] * 5.0), 1))
        item["orders_90"] = len(item["orders_90"])
        item["daily_sell_rate_90"] = round(sell_rate, 2)
        item["success_score"] = success_score
        products.append(item)

    products.sort(key=lambda x: (x["qty_24"], x["qty_90"], x["revenue_90"]), reverse=True)
    return products[:MAX_TRACKED_PRODUCTS]


def _niche_stats(line_rows_90: List[Dict[str, Any]], line_rows_24: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stats = defaultdict(lambda: {"qty_24": 0, "qty_90": 0, "revenue_24": 0.0, "revenue_90": 0.0, "orders": set()})
    for row in line_rows_90:
        s = stats[row["niche"]]
        s["qty_90"] += row["qty"]
        s["revenue_90"] += row["revenue"]
        s["orders"].add(row["order_id"])
    for row in line_rows_24:
        s = stats[row["niche"]]
        s["qty_24"] += row["qty"]
        s["revenue_24"] += row["revenue"]

    result = []
    for niche, s in stats.items():
        score = min(100, round((s["qty_24"] * 8) + (s["qty_90"] * 1.5) + (s["revenue_90"] / 40), 1))
        result.append(
            {
                "niche": niche,
                "qty_24": s["qty_24"],
                "qty_90": s["qty_90"],
                "revenue_24": s["revenue_24"],
                "revenue_90": s["revenue_90"],
                "success_score": score,
                "daily_rate_90": round(s["qty_90"] / 90, 2),
            }
        )
    result.sort(key=lambda x: (x["qty_24"], x["qty_90"], x["revenue_90"]), reverse=True)
    return result[:12]


def _render_scripture_border() -> None:
    ref, text, theme = _quote_for_today()
    st.markdown(
        f"""
        <div class="scripture-shell scripture-{theme}">
            <div class="scripture-glow"></div>
            <div class="scripture-content">
                <span class="scripture-label">Today’s Word</span>
                <strong>{ref}</strong>
                <span>{text}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _metric(label: str, value: str, sub: str = "") -> str:
    return f"""
    <div class="pro-metric">
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{sub}</small>
    </div>
    """


def _render_metrics(orders_24: List[Dict[str, Any]], orders_90: List[Dict[str, Any]], products: List[Dict[str, Any]]) -> None:
    revenue_24 = sum(_order_total(o) for o in orders_24)
    revenue_90 = sum(_order_total(o) for o in orders_90)
    sold_24 = sum(p.get("qty_24", 0) for p in products)
    sold_90 = sum(p.get("qty_90", 0) for p in products)
    avg_order = revenue_90 / max(len(orders_90), 1)

    st.markdown(
        f"""
        <div class="pro-metric-grid">
            {_metric("24h Revenue", f"${revenue_24:,.2f}", f"{len(orders_24)} orders")}
            {_metric("90d Revenue", f"${revenue_90:,.2f}", f"{len(orders_90)} orders")}
            {_metric("Items Sold", f"{sold_24}", f"{sold_90} in 90 days")}
            {_metric("Avg Order", f"${avg_order:,.2f}", "90 day average")}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_charts(line_rows_90: List[Dict[str, Any]], niches: List[Dict[str, Any]]) -> None:
    chart_left, chart_right = st.columns([1.2, 1])

    with chart_left:
        st.markdown('<div class="panel-title">Daily Sales Trend · 90 Days</div>', unsafe_allow_html=True)
        if line_rows_90:
            df = pd.DataFrame(line_rows_90)
            df = df.dropna(subset=["created_est"])
            if not df.empty:
                df["date"] = df["created_est"].dt.date
                trend = df.groupby("date", as_index=False).agg(qty=("qty", "sum"), revenue=("revenue", "sum"))
                st.line_chart(trend.set_index("date")[["qty", "revenue"]], height=260)
            else:
                st.info("No dated order rows available yet.")
        else:
            st.info("No order data found for the selected 90-day window.")

    with chart_right:
        st.markdown('<div class="panel-title">Highest Selling Niches</div>', unsafe_allow_html=True)
        if niches:
            ndf = pd.DataFrame(niches[:8]).set_index("niche")
            st.bar_chart(ndf[["qty_24", "qty_90"]], height=260)
        else:
            st.info("Niche data appears after orders are found.")


def _render_product_carousel(products: List[Dict[str, Any]]) -> None:
    st.markdown('<div class="panel-title">Tracked Products · Top 50 · 5 Per Page</div>', unsafe_allow_html=True)

    if not products:
        st.info("No sold products found yet. Once orders come in, your top products will appear here.")
        return

    total_pages = max(1, math.ceil(len(products) / PRODUCTS_PER_PAGE))
    page_key = "dashboard_product_page"
    st.session_state.setdefault(page_key, 0)

    nav_left, nav_mid, nav_right = st.columns([1, 2, 1])
    with nav_left:
        if st.button("← Previous", key="dash_prev_products", use_container_width=True):
            st.session_state[page_key] = max(0, st.session_state[page_key] - 1)
    with nav_mid:
        st.markdown(
            f"<div class='pager-label'>Page {st.session_state[page_key] + 1} of {total_pages} · {len(products)} tracked</div>",
            unsafe_allow_html=True,
        )
    with nav_right:
        if st.button("Next →", key="dash_next_products", use_container_width=True):
            st.session_state[page_key] = min(total_pages - 1, st.session_state[page_key] + 1)

    start = st.session_state[page_key] * PRODUCTS_PER_PAGE
    page_products = products[start : start + PRODUCTS_PER_PAGE]

    cards = ""
    for p in page_products:
        cards += f"""
        <div class="product-tile">
            <div class="product-topline">
                <span>{p.get("niche", "General")}</span>
                <b>{p.get("success_score", 0)}%</b>
            </div>
            <h4>{p.get("title", "Untitled")}</h4>
            <div class="product-mini-grid">
                <div><strong>{p.get("qty_24", 0)}</strong><small>24h sold</small></div>
                <div><strong>{p.get("qty_90", 0)}</strong><small>90d sold</small></div>
                <div><strong>${p.get("revenue_90", 0):,.0f}</strong><small>90d sales</small></div>
                <div><strong>{p.get("daily_sell_rate_90", 0)}</strong><small>/day</small></div>
            </div>
        </div>
        """

    st.markdown(f"<div class='product-carousel'>{cards}</div>", unsafe_allow_html=True)


def _render_niche_strip(niches: List[Dict[str, Any]]) -> None:
    st.markdown('<div class="panel-title">Daily Highest Selling Niches · Compact Scroll</div>', unsafe_allow_html=True)
    if not niches:
        st.info("No niche data found yet.")
        return
    cards = ""
    for n in niches:
        cards += f"""
        <div class="niche-pill">
            <span>{n["niche"]}</span>
            <strong>{n["qty_24"]}</strong>
            <small>24h sold · {n["daily_rate_90"]}/day · {n["success_score"]}% score</small>
        </div>
        """
    st.markdown(f"<div class='niche-strip'>{cards}</div>", unsafe_allow_html=True)


def _render_requirements_box() -> None:
    with st.expander("Requirements for these dashboard features"):
        st.markdown(
            """
            **Python packages**
            ```bash
            pip install pandas requests cryptography
            ```

            **eBay OAuth scopes needed**
            - `https://api.ebay.com/oauth/api_scope`
            - `https://api.ebay.com/oauth/api_scope/sell.fulfillment`
            - `https://api.ebay.com/oauth/api_scope/sell.inventory`
            - `https://api.ebay.com/oauth/api_scope/sell.analytics.readonly`
            - `https://api.ebay.com/oauth/api_scope/sell.account`

            **App files used**
            - `core/ebay_account_store.py` must expose `call_ebay_api`
            - `core/ebay_oauth.py` must refresh tokens without resending scopes
            - Supabase table `ebay_accounts` stores encrypted OAuth tokens
            - Settings page must show connected/disconnect/reconnect

            **Data rule**
            The dashboard UI uses Eastern Time. eBay API requests are sent in UTC and clamped so the end date is never in the future.
            """
        )


def render_dashboard() -> None:
    _load_dashboard_css()

    owner_name = _owner_name()
    connected_account = _get_connected_account(owner_name)
    account_label = _connection_label(owner_name)
    name = st.session_state.get("client_name") or st.session_state.get("username") or "User"

    _render_scripture_border()

    st.markdown(
        f"""
        <section class="dashboard-hero-pro">
            <div>
                <span class="eyebrow">eBay IO Command Center</span>
                <h1>Welcome back, {name}</h1>
                <p>Live seller performance, niche movement, product velocity, and order intelligence in one production dashboard.</p>
            </div>
            <div class="hero-status-card">
                <span>Connected Seller</span>
                <strong>{account_label}</strong>
                <small>Daily product generation resets at 12:00 AM EST · next reset in {_midnight_countdown()}</small>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )

    if not connected_account:
        st.warning("Connect your eBay account in Settings to unlock live dashboard data.")
        _render_requirements_box()
        return

    window_24, window_90 = _windows()

    with st.spinner("Loading live eBay performance data..."):
        try:
            orders_24 = _fetch_orders(owner_name, window_24)
            orders_90 = _fetch_orders(owner_name, window_90)
        except Exception as exc:
            st.error("Live eBay dashboard data could not load. Reconnect eBay in Settings, then refresh.")
            with st.expander("Technical details"):
                st.code(str(exc))
            _render_requirements_box()
            return

    line_rows_24 = _line_items(orders_24)
    line_rows_90 = _line_items(orders_90)
    products = _product_stats(line_rows_90, line_rows_24)
    niches = _niche_stats(line_rows_90, line_rows_24)

    _render_metrics(orders_24, orders_90, products)
    _render_charts(line_rows_90, niches)

    st.markdown("<div class='dashboard-panel'>", unsafe_allow_html=True)
    _render_niche_strip(niches)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='dashboard-panel'>", unsafe_allow_html=True)
    _render_product_carousel(products)
    st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("Recent live order sample"):
        rows = []
        for row in line_rows_90[:50]:
            rows.append(
                {
                    "Order": row["order_id"],
                    "Created EST": row["created_est"].strftime("%Y-%m-%d %I:%M %p") if row["created_est"] else "",
                    "SKU": row["sku"],
                    "Title": row["title"],
                    "Qty": row["qty"],
                    "Revenue": round(row["revenue"], 2),
                    "Niche": row["niche"],
                    "Status": row["status"],
                }
            )
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No recent line items found.")

    _render_requirements_box()
