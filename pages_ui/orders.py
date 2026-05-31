import json
from datetime import datetime, timezone
import streamlit as st
import pandas as pd

from core.ebay_account_store import call_ebay_api


RATE_LIMIT_WARNING = 0.80
RATE_LIMIT_BLOCK = 0.95
DEFAULT_PAGE_SIZE = 50


def _safe_get(d, keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        if isinstance(value, dict):
            v = value.get("value")
            return float(v) if v not in (None, "") else default
        return float(value)
    except Exception:
        return default


def _normalize_inventory_item(item: dict) -> dict:
    product = item.get("product", {}) if isinstance(item, dict) else {}
    availability = item.get("availability", {}) if isinstance(item, dict) else {}
    ship_avail = availability.get("shipToLocationAvailability", {}) if isinstance(availability, dict) else {}

    sku = item.get("sku", "")
    title = product.get("title", "")
    aspects = product.get("aspects", {}) if isinstance(product, dict) else {}
    brand = ""
    if isinstance(aspects, dict):
        brand = ", ".join(aspects.get("Brand", [])[:1]) if aspects.get("Brand") else ""

    return {
        "record_type": "inventory_item",
        "order_id": "",
        "status": "LIVE",
        "payment_status": "",
        "fulfillment_status": "",
        "sku": sku,
        "title": title,
        "client": "",
        "source": "eBay Inventory",
        "quantity": ship_avail.get("quantity", ""),
        "cost": "",
        "price": "",
        "profit": "",
        "tracking": "",
        "ship_by": "",
        "updated_at": "",
        "brand": brand,
        "raw": item,
    }


def _normalize_fulfillment_order(order: dict) -> list[dict]:
    if not isinstance(order, dict):
        return []

    line_items = order.get("lineItems", []) or []
    rows = []
    for line in line_items:
        delivery_cost = line.get("deliveryCost", {}) if isinstance(line, dict) else {}
        line_cost = line.get("lineItemCost", {}) if isinstance(line, dict) else {}
        line_total = line.get("total", {}) if isinstance(line, dict) else {}
        fulfillment_instr = line.get("lineItemFulfillmentInstructions", {}) if isinstance(line, dict) else {}

        rows.append(
            {
                "record_type": "order",
                "order_id": order.get("orderId", ""),
                "status": order.get("orderFulfillmentStatus", ""),
                "payment_status": order.get("orderPaymentStatus", ""),
                "fulfillment_status": line.get("lineItemFulfillmentStatus", ""),
                "sku": line.get("sku", ""),
                "title": line.get("title", ""),
                "client": _safe_get(order, ["buyer", "username"], ""),
                "source": "eBay Order",
                "quantity": line.get("quantity", ""),
                "cost": _to_float(line_cost),
                "price": _to_float(line_total),
                "profit": _to_float(line_total) - _to_float(line_cost),
                "tracking": "",
                "ship_by": fulfillment_instr.get("shipByDate", ""),
                "updated_at": order.get("lastModifiedDate", ""),
                "brand": "",
                "raw": order,
            }
        )
    return rows


def _extract_rate_limits(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}

    rate_limits = payload.get("rateLimits")
    if isinstance(rate_limits, list) and rate_limits:
        rl = rate_limits[0]
        total = _to_float(rl.get("callLimit"), 0)
        used = _to_float(rl.get("callsUsed"), 0)
        remaining = _to_float(rl.get("remainingCalls"), max(total - used, 0))
        reset = rl.get("timeWindow") or rl.get("resetInMinutes") or ""
        pct = (used / total) if total else 0
        return {
            "total": total,
            "used": used,
            "remaining": remaining,
            "pct": pct,
            "reset": reset,
        }
    return {}


def _format_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _rate_status(pct: float) -> str:
    if pct >= RATE_LIMIT_BLOCK:
        return "BLOCKED"
    if pct >= RATE_LIMIT_WARNING:
        return "WARN"
    return "OK"


def _hero_css() -> str:
    return """
    <style>
    .orders-shell { padding-top: 0.35rem; }
    .orders-hero {
        background: linear-gradient(135deg, rgba(15,23,42,0.96), rgba(30,41,59,0.92));
        border: 1px solid rgba(148,163,184,0.16);
        border-radius: 28px;
        padding: 1.1rem 1.3rem;
        margin-bottom: 1rem;
    }
    .orders-hero h1 {
        margin: 0;
        font-size: 2.1rem;
        color: #f8fbff;
        letter-spacing: -0.03em;
    }
    .orders-hero p {
        margin: 0.35rem 0 0 0;
        color: #cbd5e1;
    }
    .block {
        background: rgba(15,23,42,0.78);
        border: 1px solid rgba(148,163,184,0.14);
        border-radius: 22px;
        padding: 1rem;
    }
    .tiny-label {
        font-size: 0.74rem;
        text-transform: uppercase;
        letter-spacing: 0.09em;
        color: #94a3b8;
        margin-bottom: 0.4rem;
    }
    .stat {
        padding: 0.9rem 1rem;
        border-radius: 18px;
        background: rgba(2,6,23,0.55);
        border: 1px solid rgba(148,163,184,0.12);
    }
    .stat b {
        font-size: 1.45rem;
        color: white;
    }
    .badge {
        display: inline-flex;
        align-items: center;
        padding: 0.28rem 0.55rem;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 700;
        margin-left: 0.25rem;
    }
    </style>
    """


def _status_badge(status: str) -> str:
    colors = {
        "READY": "#f59e0b",
        "FULFILLED": "#4ade80",
        "PENDING": "#38bdf8",
        "SHIPPED": "#a78bfa",
        "CANCELLED": "#fb7185",
        "LIVE": "#22c55e",
        "WARN": "#f59e0b",
        "BLOCKED": "#fb7185",
        "OK": "#38bdf8",
    }
    c = colors.get(str(status).upper(), "#94a3b8")
    return f"<span class='badge' style='background:{c};color:#0b1220;'>{status}</span>"


def _load_rate_limits(owner_name: str) -> dict:
    try:
        payload = call_ebay_api(owner_name, "GET", "/developer/analytics/v1_beta/rate_limit/")
        return _extract_rate_limits(payload if isinstance(payload, dict) else {})
    except Exception as e:
        return {"error": str(e)}


def _load_inventory(owner_name: str) -> list[dict]:
    rows = []
    offset = 0
    while True:
        path = f"/sell/inventory/v1/inventory_item?limit={DEFAULT_PAGE_SIZE}&offset={offset}"
        payload = call_ebay_api(owner_name, "GET", path)
        if not isinstance(payload, dict):
            break

        items = payload.get("inventoryItems") or payload.get("inventory_item") or payload.get("items") or []
        if not items:
            break

        for item in items:
            rows.append(_normalize_inventory_item(item))

        if len(items) < DEFAULT_PAGE_SIZE:
            break
        offset += DEFAULT_PAGE_SIZE

    return rows


def _load_orders(owner_name: str) -> list[dict]:
    rows = []
    offset = 0
    while True:
        path = f"/sell/fulfillment/v1/order?limit={DEFAULT_PAGE_SIZE}&offset={offset}"
        payload = call_ebay_api(owner_name, "GET", path)
        if not isinstance(payload, dict):
            break

        orders = payload.get("orders") or []
        if not orders:
            break

        for order in orders:
            rows.extend(_normalize_fulfillment_order(order))

        if len(orders) < DEFAULT_PAGE_SIZE:
            break
        offset += DEFAULT_PAGE_SIZE

    return rows


def render_orders() -> None:
    st.markdown(_hero_css(), unsafe_allow_html=True)

    st.markdown(
        """
        <div class="orders-shell">
            <div class="orders-hero">
                <h1>Orders Command Board</h1>
                <p>Live eBay inventory and fulfillment orders, merged into one working view.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    owner_name = st.session_state.get("owner_name") or st.session_state.get("user") or ""
    if not owner_name:
        st.error("Missing owner_name in session state. Log in or set the active eBay account first.")
        return

    rate = _load_rate_limits(owner_name)
    pct = float(rate.get("pct", 0.0) or 0.0)
    status = _rate_status(pct)

    top1, top2, top3, top4 = st.columns(4, gap="small")
    top1.markdown(f"<div class='stat'><div class='tiny-label'>Rate Status</div><b>{_status_badge(status)}</b></div>", unsafe_allow_html=True)
    top2.markdown(f"<div class='stat'><div class='tiny-label'>Used</div><b>{_format_pct(pct)}</b></div>", unsafe_allow_html=True)
    top3.markdown(f"<div class='stat'><div class='tiny-label'>Remaining</div><b>{rate.get('remaining', '—')}</b></div>", unsafe_allow_html=True)
    top4.markdown(f"<div class='stat'><div class='tiny-label'>Reset</div><b>{rate.get('reset', '—')}</b></div>", unsafe_allow_html=True)

    if status == "WARN":
        st.warning("eBay API usage is above 80%. Refresh carefully.")
    elif status == "BLOCKED":
        st.error("eBay API usage is above 95%. Live refresh is blocked until the quota window resets.")
        st.stop()

    left, center, right = st.columns([0.22, 0.48, 0.30], gap="large")

    with st.sidebar:
        st.subheader("Queue")
        kind_filter = st.radio("Mode", ["All", "Fulfillment", "Inventory"], horizontal=False)
        status_search = st.multiselect("Status", ["READY", "FULFILLED", "PENDING", "SHIPPED", "LIVE", "WARN", "BLOCKED", "OK"], default=["READY", "FULFILLED", "PENDING", "SHIPPED", "LIVE"])
        q = st.text_input("Search", placeholder="Order, SKU, client, item")
        only_untracked = st.checkbox("Only untracked")
        refresh = st.button("Refresh live data", use_container_width=True)

    if "orders_live_cache" not in st.session_state or refresh:
        inventory_rows = _load_inventory(owner_name)
        order_rows = _load_orders(owner_name)
        merged = inventory_rows + order_rows
        st.session_state.orders_live_cache = merged
        st.session_state.orders_last_sync = datetime.now(timezone.utc).isoformat()

    rows = st.session_state.get("orders_live_cache", [])
    df = pd.DataFrame(rows)

    if df.empty:
        st.warning("No eBay data returned from the live APIs.")
        return

    for col, default in {
        "record_type": "",
        "order_id": "",
        "status": "",
        "payment_status": "",
        "fulfillment_status": "",
        "sku": "",
        "title": "",
        "client": "",
        "source": "",
        "quantity": "",
        "cost": "",
        "price": "",
        "profit": "",
        "tracking": "",
        "ship_by": "",
        "updated_at": "",
        "brand": "",
    }.items():
        if col not in df.columns:
            df[col] = default

    view = df.copy()

    if kind_filter == "Fulfillment":
        view = view[view["record_type"] == "order"]
    elif kind_filter == "Inventory":
        view = view[view["record_type"] == "inventory_item"]

    if q:
        s = q.lower()
        view = view[
            view["order_id"].astype(str).str.lower().str.contains(s)
            | view["sku"].astype(str).str.lower().str.contains(s)
            | view["client"].astype(str).str.lower().str.contains(s)
            | view["title"].astype(str).str.lower().str.contains(s)
        ]

    if only_untracked:
        view = view[view["tracking"].astype(str).str.strip() == ""]

    if not status_search:
        view = view.iloc[0:0]
    else:
        view = view[
            view["status"].astype(str).str.upper().isin([s.upper() for s in status_search])
            | view["record_type"].eq("inventory_item")
        ]

    open_orders = len(view[(view["record_type"] == "order") & (~view["status"].astype(str).str.upper().isin(["FULFILLED", "CANCELLED"]))])
    inventory_live = len(view[view["record_type"] == "inventory_item"])
    order_count = len(view[view["record_type"] == "order"])
    profit_total = pd.to_numeric(view["profit"], errors="coerce").fillna(0).sum()

    with left:
        st.markdown("<div class='block'>", unsafe_allow_html=True)
        st.subheader("Queue")
        st.write(f"Orders: {order_count}")
        st.write(f"Inventory: {inventory_live}")
        st.write(f"Open: {open_orders}")
        st.write(f"Profit: ${profit_total:.2f}")
        st.markdown("</div>", unsafe_allow_html=True)

    with center:
        st.markdown("<div class='block'>", unsafe_allow_html=True)
        st.subheader("Live Stream")
        show = view.copy()
        show["status"] = show["status"].apply(_status_badge)
        cols = ["record_type", "order_id", "sku", "title", "client", "status", "payment_status", "fulfillment_status", "source", "quantity", "profit", "ship_by", "updated_at"]
        present_cols = [c for c in cols if c in show.columns]
        st.markdown(show[present_cols].to_html(index=False, escape=False), unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        with st.expander("Raw JSON", expanded=False):
            selected_idx = st.selectbox("Select row", list(range(len(view))), format_func=lambda i: f"{view.iloc[i].get('order_id', view.iloc[i].get('sku', 'row'))}")
            st.json(view.iloc[selected_idx].get("raw", {}))

    with right:
        st.markdown("<div class='block'>", unsafe_allow_html=True)
        st.subheader("Inspector")
        if not view.empty:
            pick = st.selectbox("Inspect", view.index.tolist(), format_func=lambda i: str(view.loc[i, "order_id"] or view.loc[i, "sku"]))
            row = view.loc[pick]
            st.write(f"**Type:** {row['record_type']}")
            st.write(f"**ID:** {row['order_id'] or row['sku']}")
            st.write(f"**Title:** {row['title']}")
            st.write(f"**Client:** {row['client']}")
            st.write(f"**Source:** {row['source']}")
            st.write(f"**Tracking:** {row['tracking'] or 'Pending'}")
            st.markdown(f"**Status:** {_status_badge(str(row['status']))}", unsafe_allow_html=True)
        else:
            st.info("Nothing to inspect.")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:0.75rem;'></div>", unsafe_allow_html=True)

        st.markdown("<div class='block'>", unsafe_allow_html=True)
        st.subheader("Actions")
        st.button("Mark Purchased", use_container_width=True)
        st.button("Add Tracking", use_container_width=True)
        st.button("Mark Shipped", use_container_width=True)
        st.button("Flag Issue", use_container_width=True)
        st.button("Archive", use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)