
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import streamlit as st

from core.ebay_account_store import call_ebay_api, get_connected_ebay_label, get_latest_ebay_account


PAGE_SIZE = 100
MAX_ORDER_WINDOW_DAYS = 89


def _css() -> str:
    return """
    <style>
    .orders-hero {
        background: linear-gradient(135deg, rgba(15,23,42,0.96), rgba(30,41,59,0.92));
        border: 1px solid rgba(148,163,184,0.16);
        border-radius: 26px;
        padding: 1.1rem 1.3rem;
        margin-bottom: 1rem;
    }
    .orders-hero h1 { margin: 0; font-size: 2rem; color: #f8fbff; letter-spacing: -0.03em; }
    .orders-hero p { margin: 0.35rem 0 0 0; color: #cbd5e1; }
    .metric-card {
        border: 1px solid rgba(148,163,184,0.16);
        border-radius: 18px;
        padding: 0.85rem 1rem;
        background: rgba(15,23,42,0.66);
    }
    .metric-card .label { color: #94a3b8; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; }
    .metric-card .value { color: #f8fafc; font-size: 1.35rem; font-weight: 800; margin-top: 0.15rem; }
    </style>
    """


def _current_owner_name() -> str:
    return (
        st.session_state.get("client_name")
        or st.session_state.get("owner_name")
        or st.session_state.get("username")
        or "default"
    )


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, dict):
            value = value.get("value")
        return float(value)
    except Exception:
        return default


def _money(value: Any) -> float:
    return _as_float(value, 0.0)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _date_chunks(start_day: date, end_day: date) -> list[tuple[datetime, datetime]]:
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(end_day, datetime.max.time(), tzinfo=timezone.utc)

    chunks: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=MAX_ORDER_WINDOW_DAYS), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(seconds=1)
    return chunks


def _safe_error_message(response) -> str:
    status = getattr(response, "status_code", "unknown")
    try:
        data = response.json()
        errors = data.get("errors") or data.get("warnings") or []
        if errors:
            first = errors[0]
            return first.get("message") or first.get("longMessage") or f"eBay API returned {status}"
        if data.get("message"):
            return data["message"]
    except Exception:
        pass
    return f"eBay API returned {status}"


def _order_filter(start_dt: datetime, end_dt: datetime, fulfillment_status: str | None) -> str:
    filters = [f"creationdate:[{_iso_z(start_dt)}..{_iso_z(end_dt)}]"]
    if fulfillment_status and fulfillment_status != "ALL":
        filters.append(f"orderfulfillmentstatus:{{{fulfillment_status}}}")
    return ",".join(filters)


def _fetch_orders_window(
    owner_name: str,
    start_dt: datetime,
    end_dt: datetime,
    fulfillment_status: str | None,
) -> list[dict[str, Any]]:
    all_orders: list[dict[str, Any]] = []
    offset = 0

    while True:
        params = {
            "limit": PAGE_SIZE,
            "offset": offset,
            "filter": _order_filter(start_dt, end_dt, fulfillment_status),
        }

        response = call_ebay_api(
            owner_name,
            "GET",
            "/sell/fulfillment/v1/order",
            params=params,
        )

        if getattr(response, "status_code", 200) >= 400:
            # Retry without status filter. Some eBay accounts reject certain
            # status combinations, but date-only order queries are more stable.
            if fulfillment_status and fulfillment_status != "ALL":
                params["filter"] = _order_filter(start_dt, end_dt, None)
                response = call_ebay_api(
                    owner_name,
                    "GET",
                    "/sell/fulfillment/v1/order",
                    params=params,
                )

        if getattr(response, "status_code", 200) >= 400:
            raise RuntimeError(_safe_error_message(response))

        data = response.json()
        orders = data.get("orders", []) if isinstance(data, dict) else []
        all_orders.extend(orders)

        total = data.get("total")
        if not orders or len(orders) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        if isinstance(total, int) and offset >= total:
            break

        # Avoid very large accidental loops.
        if offset >= 5000:
            break

    return all_orders


def _fetch_orders(
    owner_name: str,
    start_day: date,
    end_day: date,
    fulfillment_status: str | None,
) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []

    for start_dt, end_dt in _date_chunks(start_day, end_day):
        orders.extend(_fetch_orders_window(owner_name, start_dt, end_dt, fulfillment_status))

    # de-dupe when chunk boundaries overlap
    seen = set()
    unique = []
    for order in orders:
        oid = order.get("orderId")
        if oid in seen:
            continue
        seen.add(oid)
        unique.append(order)

    unique.sort(key=lambda x: x.get("creationDate", ""), reverse=True)
    return unique


def _tracking_for_line(order: dict[str, Any], line_item_id: str | None = None) -> str:
    fulfillments = order.get("fulfillmentStartInstructions") or []
    tracking_numbers: list[str] = []

    for fulfillment in order.get("shippingFulfillments", []) or []:
        for item in fulfillment.get("lineItems", []) or []:
            if line_item_id and item.get("lineItemId") != line_item_id:
                continue
            tracking = fulfillment.get("shipmentTrackingNumber")
            if tracking:
                tracking_numbers.append(tracking)

    # Some responses put tracking under fulfillment instructions.
    for item in fulfillments:
        tracking = item.get("shipmentTrackingNumber")
        if tracking:
            tracking_numbers.append(tracking)

    return ", ".join(sorted(set(tracking_numbers)))


def _flatten_orders(orders: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for order in orders:
        buyer = order.get("buyer", {}) or {}
        pricing = order.get("pricingSummary", {}) or {}
        order_total = pricing.get("total", {}) or {}
        created_dt = _parse_dt(order.get("creationDate"))

        line_items = order.get("lineItems") or []
        if not line_items:
            line_items = [{}]

        for item in line_items:
            line_id = item.get("lineItemId")
            line_total = item.get("lineItemCost") or item.get("total") or {}
            quantity = item.get("quantity", 1) or 1
            ship_instruction = item.get("lineItemFulfillmentInstructions", {}) or {}

            rows.append(
                {
                    "created": created_dt,
                    "created_at": order.get("creationDate", ""),
                    "order_id": order.get("orderId", ""),
                    "line_item_id": line_id or "",
                    "buyer": buyer.get("username") or buyer.get("buyerRegistrationAddress", {}).get("fullName", ""),
                    "sku": item.get("sku", ""),
                    "title": item.get("title", ""),
                    "quantity": quantity,
                    "order_status": order.get("orderFulfillmentStatus", ""),
                    "payment_status": order.get("orderPaymentStatus", ""),
                    "line_status": item.get("lineItemFulfillmentStatus", ""),
                    "total": _money(line_total) or (_money(order_total) if len(line_items) == 1 else 0.0),
                    "currency": (line_total.get("currency") if isinstance(line_total, dict) else None)
                    or order_total.get("currency")
                    or "USD",
                    "ship_by": ship_instruction.get("shipByDate", ""),
                    "tracking": _tracking_for_line(order, line_id),
                    "raw": order,
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["created_sort"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    df = df.sort_values("created_sort", ascending=False)
    return df


def _render_metric(label: str, value: str) -> None:
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="label">{label}</div>
            <div class="value">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_orders() -> None:
    st.markdown(_css(), unsafe_allow_html=True)
    st.markdown(
        """
        <div class="orders-hero">
            <h1>Orders Command Board</h1>
            <p>Live eBay orders from your connected OAuth seller account.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    owner_name = _current_owner_name()
    account = get_latest_ebay_account(owner_name)

    if not account:
        st.warning("No eBay account is connected yet. Go to Settings and connect eBay first.")
        if st.button("Go to Settings", use_container_width=True):
            st.session_state.active_page = "Settings"
            st.rerun()
        return

    st.caption(f"Using connected eBay account: {get_connected_ebay_label(owner_name)}")

    today = datetime.now(timezone.utc).date()
    default_start = today - timedelta(days=30)

    with st.sidebar:
        st.subheader("Order Filters")
        preset = st.selectbox(
            "Date range",
            ["Last 7 days", "Last 30 days", "Last 90 days", "Custom"],
            index=1,
        )

        if preset == "Last 7 days":
            start_day, end_day = today - timedelta(days=7), today
        elif preset == "Last 30 days":
            start_day, end_day = default_start, today
        elif preset == "Last 90 days":
            start_day, end_day = today - timedelta(days=90), today
        else:
            start_day = st.date_input("Start date", value=default_start)
            end_day = st.date_input("End date", value=today)

        fulfillment_status = st.selectbox(
            "Fulfillment status",
            ["ALL", "NOT_STARTED", "IN_PROGRESS", "FULFILLED"],
            index=0,
        )
        search = st.text_input("Search orders", placeholder="Order ID, buyer, SKU, title")
        only_without_tracking = st.checkbox("Only missing tracking")
        refresh = st.button("Refresh live orders", use_container_width=True, type="primary")

    if start_day > end_day:
        st.error("Start date must be before end date.")
        return

    cache_key = f"orders_cache_{owner_name}_{start_day}_{end_day}_{fulfillment_status}"

    if refresh or cache_key not in st.session_state:
        with st.spinner("Loading live eBay orders..."):
            try:
                orders = _fetch_orders(owner_name, start_day, end_day, fulfillment_status)
                st.session_state[cache_key] = orders
                st.session_state["orders_last_sync"] = datetime.now(timezone.utc).isoformat()
            except Exception as exc:
                st.error(
                    "eBay rejected the order query or the saved connection needs reconnecting. "
                    "Try Last 30 days, reconnect eBay in Settings, then refresh."
                )
                with st.expander("Technical details", expanded=False):
                    st.write(str(exc))
                return

    orders = st.session_state.get(cache_key, [])
    df = _flatten_orders(orders)

    if df.empty:
        st.info("No orders were returned for this date range.")
        return

    view = df.copy()

    if search:
        q = search.lower().strip()
        mask = (
            view["order_id"].astype(str).str.lower().str.contains(q, na=False)
            | view["buyer"].astype(str).str.lower().str.contains(q, na=False)
            | view["sku"].astype(str).str.lower().str.contains(q, na=False)
            | view["title"].astype(str).str.lower().str.contains(q, na=False)
        )
        view = view[mask]

    if only_without_tracking:
        view = view[view["tracking"].astype(str).str.strip() == ""]

    total_orders = df["order_id"].nunique()
    total_lines = len(df)
    total_sales = pd.to_numeric(df["total"], errors="coerce").fillna(0).sum()
    unfulfilled = df[~df["order_status"].astype(str).str.upper().isin(["FULFILLED", "CANCELLED"])]["order_id"].nunique()

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _render_metric("Orders", str(total_orders))
    with c2:
        _render_metric("Line items", str(total_lines))
    with c3:
        _render_metric("Sales", f"${total_sales:,.2f}")
    with c4:
        _render_metric("Open orders", str(unfulfilled))

    st.divider()

    display_cols = [
        "created_at",
        "order_id",
        "buyer",
        "sku",
        "title",
        "quantity",
        "order_status",
        "payment_status",
        "line_status",
        "total",
        "currency",
        "ship_by",
        "tracking",
    ]

    st.dataframe(
        view[display_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "created_at": "Created",
            "order_id": "Order ID",
            "buyer": "Buyer",
            "sku": "SKU",
            "title": "Title",
            "quantity": "Qty",
            "order_status": "Order Status",
            "payment_status": "Payment",
            "line_status": "Line Status",
            "total": st.column_config.NumberColumn("Total", format="$%.2f"),
            "ship_by": "Ship By",
            "tracking": "Tracking",
        },
    )

    csv = view.drop(columns=["raw", "created", "created_sort"], errors="ignore").to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download orders CSV",
        data=csv,
        file_name=f"ebay_orders_{start_day}_to_{end_day}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    with st.expander("Inspect one order", expanded=False):
        order_ids = view["order_id"].dropna().astype(str).unique().tolist()
        if order_ids:
            selected = st.selectbox("Order ID", order_ids)
            raw = view[view["order_id"].astype(str) == selected].iloc[0]["raw"]
            st.json(raw)
