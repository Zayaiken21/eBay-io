import json
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from core.ebay_account_store import call_ebay_api

RATE_LIMIT_WARNING = 0.80
RATE_LIMIT_BLOCK = 0.95
PAGE_SIZE = 50


def _status_badge(text, kind="blue"):
    colors = {
        "blue": "#38bdf8",
        "green": "#4ade80",
        "yellow": "#f59e0b",
        "red": "#fb7185",
        "purple": "#a78bfa",
        "gray": "#94a3b8",
    }
    c = colors.get(kind, colors["gray"])
    return f"<span style='display:inline-flex;align-items:center;padding:0.28rem 0.58rem;border-radius:999px;background:{c};color:#0b1220;font-weight:800;font-size:0.78rem;'>{text}</span>"


def _css():
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
    .orders-hero h1 { margin: 0; font-size: 2.1rem; color: #f8fbff; letter-spacing: -0.03em; }
    .orders-hero p { margin: 0.35rem 0 0 0; color: #cbd5e1; }
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
    .stat b { font-size: 1.45rem; color: white; }
    </style>
    """


def _pick_session_owner():
    return (
        st.session_state.get("owner_name")
        or st.session_state.get("active_ebay_account")
        or st.session_state.get("user")
        or st.session_state.get("username")
        or ""
    )


def _to_float(v, default=0.0):
    try:
        if isinstance(v, dict):
            v = v.get("value")
        return float(v)
    except Exception:
        return default


def _rate_info(owner_name):
    try:
        payload = call_ebay_api(owner_name, "GET", "/developer/analytics/v1_beta/rate_limit/")
        data = payload.json() if hasattr(payload, "json") else payload
        limits = data.get("rateLimits", []) if isinstance(data, dict) else []
        if limits:
            rl = limits[0]
            total = _to_float(rl.get("callLimit"), 0)
            used = _to_float(rl.get("callsUsed"), 0)
            remaining = rl.get("remainingCalls")
            if remaining is None and total:
                remaining = max(total - used, 0)
            pct = (used / total) if total else 0
            return {
                "ok": True,
                "total": total,
                "used": used,
                "remaining": remaining,
                "pct": pct,
                "status": "BLOCKED" if pct >= RATE_LIMIT_BLOCK else ("WARN" if pct >= RATE_LIMIT_WARNING else "OK"),
                "raw": data,
            }
    except Exception as e:
        return {"ok": False, "error": str(e), "status": "OK"}
    return {"ok": False, "status": "OK"}


def _load_inventory(owner_name):
    rows = []
    offset = 0
    while True:
        resp = call_ebay_api(owner_name, "GET", "/sell/inventory/v1/inventory_item", params={"limit": PAGE_SIZE, "offset": offset})
        if getattr(resp, "status_code", 200) >= 400:
            raise RuntimeError(f"Inventory API error {resp.status_code}: {resp.text}")
        data = resp.json() if hasattr(resp, "json") else {}
        items = data.get("inventoryItems", []) if isinstance(data, dict) else []
        if not items:
            break
        for item in items:
            p = item.get("product", {}) or {}
            a = item.get("availability", {}) or {}
            ship = a.get("shipToLocationAvailability", {}) or {}
            rows.append({
                "record_type": "inventory_item",
                "order_id": "",
                "sku": item.get("sku", ""),
                "title": p.get("title", ""),
                "client": "",
                "status": "LIVE",
                "payment_status": "",
                "fulfillment_status": "",
                "source": "eBay Inventory",
                "quantity": ship.get("quantity", ""),
                "cost": "",
                "price": "",
                "profit": "",
                "tracking": "",
                "ship_by": "",
                "updated_at": item.get("locale", ""),
                "raw": item,
            })
        if len(items) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def _load_orders(owner_name):
    rows = []
    resp = call_ebay_api(owner_name, "GET", "/sell/fulfillment/v1/order")
    if getattr(resp, "status_code", 200) >= 400:
        raise RuntimeError(f"Fulfillment API error {resp.status_code}: {resp.text}")
    data = resp.json() if hasattr(resp, "json") else {}
    orders = data.get("orders", []) if isinstance(data, dict) else []
    for order in orders:
        buyer = order.get("buyer", {}) or {}
        for li in order.get("lineItems", []) or []:
            li_cost = li.get("lineItemCost", {}) or {}
            li_total = li.get("total", {}) or {}
            finstr = li.get("lineItemFulfillmentInstructions", {}) or {}
            status = order.get("orderFulfillmentStatus", "") or li.get("lineItemFulfillmentStatus", "")
            rows.append({
                "record_type": "order",
                "order_id": order.get("orderId", ""),
                "sku": li.get("sku", ""),
                "title": li.get("title", ""),
                "client": buyer.get("username", ""),
                "status": status,
                "payment_status": order.get("orderPaymentStatus", ""),
                "fulfillment_status": li.get("lineItemFulfillmentStatus", ""),
                "source": "eBay Order",
                "quantity": li.get("quantity", ""),
                "cost": _to_float(li_cost),
                "price": _to_float(li_total),
                "profit": round(_to_float(li_total) - _to_float(li_cost), 2),
                "tracking": "",
                "ship_by": finstr.get("shipByDate", ""),
                "updated_at": order.get("lastModifiedDate", "") or order.get("creationDate", ""),
                "raw": order,
            })
    return rows


def render_orders():
    st.markdown(_css(), unsafe_allow_html=True)
    st.markdown(
        """
        <div class="orders-shell">
            <div class="orders-hero">
                <h1>Orders Command Board</h1>
                <p>Live eBay inventory items and fulfillment orders in one merged workspace.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    owner_name = _pick_session_owner()
    if not owner_name:
        st.warning("Please log in and choose an eBay account first.")
        return

    rate = _rate_info(owner_name)
    status = rate.get("status", "OK")
    if status == "WARN":
        st.warning("eBay API usage is above 80%. Refresh carefully.")
    elif status == "BLOCKED":
        st.error("eBay API usage is above 95%. Live refresh is blocked until the quota window resets.")
        st.stop()

    m1, m2, m3, m4 = st.columns(4, gap="small")
    m1.markdown(
        f"<div class='stat'><div class='tiny-label'>Rate Status</div><b>{_status_badge(status, 'yellow' if status=='WARN' else 'red' if status=='BLOCKED' else 'blue')}</b></div>",
        unsafe_allow_html=True,
    )
    m2.markdown(f"<div class='stat'><div class='tiny-label'>Used</div><b>{rate.get('pct', 0)*100:.1f}%</b></div>", unsafe_allow_html=True)
    m3.markdown(f"<div class='stat'><div class='tiny-label'>Remaining</div><b>{rate.get('remaining', '—')}</b></div>", unsafe_allow_html=True)
    m4.markdown(
        f"<div class='stat'><div class='tiny-label'>Reset</div><b>{rate.get('raw', {}).get('rateLimits', [{}])[0].get('reset', '—') if rate.get('ok') else '—'}</b></div>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.subheader("Queue")
        kind = st.radio("Mode", ["All", "Fulfillment", "Inventory"], horizontal=False)
        search = st.text_input("Search", placeholder="Order, SKU, client, item")
        statuses = st.multiselect(
            "Status",
            ["LIVE", "READY", "FULFILLED", "PENDING", "SHIPPED"],
            default=["LIVE", "READY", "FULFILLED", "PENDING", "SHIPPED"],
        )
        only_untracked = st.checkbox("Only untracked")
        refresh = st.button("Refresh live data", use_container_width=True)

    if "orders_live_cache" not in st.session_state or refresh:
        with st.spinner("Loading live eBay data..."):
            inv = _load_inventory(owner_name)
            ords = _load_orders(owner_name)
            st.session_state.orders_live_cache = inv + ords
            st.session_state.orders_last_sync = datetime.now(timezone.utc).isoformat()

    df = pd.DataFrame(st.session_state.get("orders_live_cache", []))
    if df.empty:
        st.warning("No eBay data returned from the live APIs.")
        return

    for col in ["record_type", "order_id", "sku", "title", "client", "status", "payment_status", "fulfillment_status", "source", "quantity", "cost", "price", "profit", "tracking", "ship_by", "updated_at", "raw"]:
        if col not in df.columns:
            df[col] = ""

    view = df.copy()
    if kind == "Fulfillment":
        view = view[view["record_type"] == "order"]
    elif kind == "Inventory":
        view = view[view["record_type"] == "inventory_item"]

    if search:
        q = search.lower()
        view = view[
            view["order_id"].astype(str).str.lower().str.contains(q)
            | view["sku"].astype(str).str.lower().str.contains(q)
            | view["client"].astype(str).str.lower().str.contains(q)
            | view["title"].astype(str).str.lower().str.contains(q)
        ]

    if only_untracked:
        view = view[view["tracking"].astype(str).str.strip() == ""]

    if statuses:
        wanted = [s.upper() for s in statuses]
        view = view[view["status"].astype(str).str.upper().isin(wanted) | (view["record_type"] == "inventory_item")]

    open_orders = len(view[(view["record_type"] == "order") & (~view["status"].astype(str).str.upper().isin(["FULFILLED", "CANCELLED"]))])
    inventory_live = len(view[view["record_type"] == "inventory_item"])
    order_count = len(view[view["record_type"] == "order"])
    profit_total = pd.to_numeric(view["profit"], errors="coerce").fillna(0).sum()

    l, c, r = st.columns([0.22, 0.48, 0.30], gap="large")
    with l:
        st.markdown("<div class='block'>", unsafe_allow_html=True)
        st.subheader("Queue")
        st.write(f"Orders: {order_count}")
        st.write(f"Inventory: {inventory_live}")
        st.write(f"Open: {open_orders}")
        st.write(f"Profit: ${profit_total:.2f}")
        st.markdown("</div>", unsafe_allow_html=True)

    with c:
        st.markdown("<div class='block'>", unsafe_allow_html=True)
        st.subheader("Live Stream")
        show = view.copy()
        show["status"] = show["status"].apply(
            lambda x: _status_badge(
                str(x),
                "green" if str(x).upper() in ["FULFILLED", "SHIPPED"] else "yellow" if str(x).upper() in ["READY", "PENDING"] else "blue",
            )
        )
        cols = [c for c in ["record_type", "order_id", "sku", "title", "client", "status", "payment_status", "fulfillment_status", "source", "quantity", "profit", "ship_by", "updated_at"] if c in show.columns]
        st.markdown(show[cols].to_html(index=False, escape=False), unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
        with st.expander("Raw JSON", expanded=False):
            idx = st.selectbox("Select row", list(range(len(view))), format_func=lambda i: str(view.iloc[i].get("order_id") or view.iloc[i].get("sku") or f"row {i}"))
            st.json(view.iloc[idx].get("raw", {}))

    with r:
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
            st.write(f"**Ship By:** {row['ship_by'] or '—'}")
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