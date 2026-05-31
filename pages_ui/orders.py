from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import streamlit as st

from core.ebay_account_store import (
    call_ebay_api,
    get_connected_ebay_label,
    get_ebay_api_context,
    get_latest_ebay_account,
)

PAGE_SIZE = 50
MAX_PAGES = 20


def _css() -> str:
    return """
    <style>
    .orders-hero {
        background: linear-gradient(135deg, rgba(15,23,42,0.96), rgba(30,41,59,0.92));
        border: 1px solid rgba(148,163,184,0.16);
        border-radius: 28px;
        padding: 1.1rem 1.3rem;
        margin-bottom: 1rem;
    }
    .orders-hero h1 { margin: 0; font-size: 2.0rem; color: #f8fbff; letter-spacing: -0.03em; }
    .orders-hero p { margin: 0.35rem 0 0 0; color: #cbd5e1; }
    .metric-card {
        padding: 0.9rem 1rem;
        border-radius: 18px;
        background: rgba(2,6,23,0.55);
        border: 1px solid rgba(148,163,184,0.12);
    }
    .metric-card .label {
        font-size: 0.74rem;
        text-transform: uppercase;
        letter-spacing: 0.09em;
        color: #94a3b8;
        margin-bottom: 0.4rem;
    }
    .metric-card .value { font-size: 1.35rem; font-weight: 800; color: white; }
    </style>
    """


def _get_current_owner_name() -> str:
    """
    Must match the owner_name used by settings/OAuth saving.

    The OAuth flow now stores owner_name inside signed state, but Streamlit can still
    land in a new session after eBay redirects back. This helper tries the common
    session keys first, then falls back to the default owner used by the OAuth callback.
    """
    for key in ("owner_name", "active_owner_name", "active_user", "username", "user"):
        value = st.session_state.get(key)
        if value:
            return str(value)

    # Your OAuth callback has been saving fallback/client accounts under "default"
    # when the app redirects to the public login page.
    return "default"


def _safe_json(resp: Any) -> dict[str, Any]:
    if isinstance(resp, dict):
        return resp
    if hasattr(resp, "json"):
        return resp.json()
    return {}


def _raise_for_ebay_error(resp: Any, label: str) -> None:
    status_code = getattr(resp, "status_code", 200)
    if status_code >= 400:
        text = getattr(resp, "text", "")
        raise RuntimeError(f"{label} failed: {status_code} {text}")


def _money(value: Any) -> float:
    try:
        if isinstance(value, dict):
            value = value.get("value", 0)
        return float(value or 0)
    except Exception:
        return 0.0


def _date_to_ebay(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _normalize_tracking(order: dict[str, Any]) -> str:
    fulfillments = order.get("fulfillmentHrefs") or []
    if fulfillments:
        return ", ".join(str(x) for x in fulfillments)

    shipments = order.get("shipments") or []
    numbers: list[str] = []
    for shipment in shipments:
        for tracking in shipment.get("shipmentTrackingNumber", []) or []:
            numbers.append(str(tracking))
        if shipment.get("shipmentTrackingNumber"):
            numbers.append(str(shipment.get("shipmentTrackingNumber")))
    return ", ".join(sorted(set([x for x in numbers if x])))


def _line_item_row(order: dict[str, Any], line_item: dict[str, Any]) -> dict[str, Any]:
    buyer = order.get("buyer", {}) or {}
    pricing = order.get("pricingSummary", {}) or {}
    instructions = line_item.get("lineItemFulfillmentInstructions", {}) or {}

    line_total = _money(line_item.get("total") or line_item.get("lineItemCost"))
    order_total = _money(pricing.get("total"))

    return {
        "order_id": order.get("orderId", ""),
        "legacy_order_id": order.get("legacyOrderId", ""),
        "created": order.get("creationDate", ""),
        "last_modified": order.get("lastModifiedDate", ""),
        "buyer_username": buyer.get("username", ""),
        "buyer_email": (buyer.get("taxAddress") or {}).get("email", ""),
        "order_status": order.get("orderFulfillmentStatus", ""),
        "payment_status": order.get("orderPaymentStatus", ""),
        "cancel_status": (order.get("cancelStatus") or {}).get("cancelState", ""),
        "sku": line_item.get("sku", ""),
        "line_item_id": line_item.get("lineItemId", ""),
        "title": line_item.get("title", ""),
        "quantity": line_item.get("quantity", 0),
        "line_total": line_total,
        "order_total": order_total,
        "currency": (line_item.get("total") or pricing.get("total") or {}).get("currency", ""),
        "ship_by": instructions.get("shipByDate", ""),
        "ship_to": ((order.get("fulfillmentStartInstructions") or [{}])[0].get("shippingStep") or {}).get("shipTo", {}).get("fullName", ""),
        "tracking": _normalize_tracking(order),
        "raw_order": order,
        "raw_line_item": line_item,
    }


def _fetch_orders(owner_name: str, start_dt: datetime, end_dt: datetime, status_filter: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0

    # eBay Fulfillment API filter syntax. Keeps this page usable for past-to-present history.
    filters = [f"creationdate:[{_date_to_ebay(start_dt)}..{_date_to_ebay(end_dt)}]"]
    if status_filter and status_filter != "All":
        filters.append(f"orderfulfillmentstatus:{{{status_filter}}}")

    for _ in range(MAX_PAGES):
        params = {
            "limit": PAGE_SIZE,
            "offset": offset,
            "filter": ",".join(filters),
        }

        resp = call_ebay_api(
            owner_name,
            "GET",
            "/sell/fulfillment/v1/order",
            params=params,
        )
        _raise_for_ebay_error(resp, "eBay orders API")
        data = _safe_json(resp)

        orders = data.get("orders", []) or []
        for order in orders:
            line_items = order.get("lineItems", []) or []
            if not line_items:
                rows.append(_line_item_row(order, {}))
            for line_item in line_items:
                rows.append(_line_item_row(order, line_item))

        total = int(data.get("total", 0) or 0)
        if not orders or len(orders) < PAGE_SIZE or offset + PAGE_SIZE >= total:
            break
        offset += PAGE_SIZE

    return rows


def _fetch_inventory_summary(owner_name: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    offset = 0

    for _ in range(5):
        resp = call_ebay_api(
            owner_name,
            "GET",
            "/sell/inventory/v1/inventory_item",
            params={"limit": PAGE_SIZE, "offset": offset},
        )
        _raise_for_ebay_error(resp, "eBay inventory API")
        data = _safe_json(resp)

        items = data.get("inventoryItems", []) or []
        for item in items:
            product = item.get("product", {}) or {}
            availability = item.get("availability", {}) or {}
            ship = availability.get("shipToLocationAvailability", {}) or {}
            rows.append(
                {
                    "sku": item.get("sku", ""),
                    "title": product.get("title", ""),
                    "quantity": ship.get("quantity", 0),
                    "condition": product.get("condition", ""),
                }
            )

        if len(items) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return pd.DataFrame(rows)


def _show_connection(owner_name: str) -> bool:
    try:
        account = get_latest_ebay_account(owner_name)
    except Exception as exc:
        st.error(f"Could not read saved eBay account: {exc}")
        return False

    if not account:
        st.warning("No connected eBay account was found for this user. Go to Settings → Connect eBay Account first.")
        with st.expander("Debug owner used for lookup"):
            st.code(owner_name)
            st.write("If your account connected under a different owner, reconnect from the same logged-in user.")
        return False

    label = get_connected_ebay_label(owner_name)
    st.success(f"Using connected eBay account: {label}")

    try:
        ctx = get_ebay_api_context(owner_name)
        with st.expander("eBay API context", expanded=False):
            st.write(
                {
                    "owner_name": owner_name,
                    "environment": ctx.get("environment"),
                    "marketplace_id": ctx.get("marketplace_id"),
                    "api_base": ctx.get("api_base"),
                }
            )
    except Exception:
        pass

    return True


def render_orders():
    st.markdown(_css(), unsafe_allow_html=True)
    st.markdown(
        """
        <div class="orders-hero">
            <h1>Orders Command Board</h1>
            <p>Live eBay orders from past to present using the connected OAuth seller account.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    owner_name = _get_current_owner_name()

    if not _show_connection(owner_name):
        return

    today = datetime.now(timezone.utc)
    default_start = today - timedelta(days=90)

    with st.sidebar:
        st.subheader("Order Tracking")
        date_range = st.date_input(
            "Created date range",
            value=(default_start.date(), today.date()),
            help="eBay order history can be pulled by creation date. Use smaller ranges if your account has high volume.",
        )
        fulfillment_status = st.selectbox(
            "Fulfillment status",
            ["All", "NOT_STARTED", "IN_PROGRESS", "FULFILLED", "CANCELED"],
        )
        search = st.text_input("Search orders", placeholder="Order ID, SKU, buyer, title")
        show_inventory = st.checkbox("Also load inventory summary", value=False)
        refresh = st.button("Refresh live eBay data", use_container_width=True)

    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = default_start.date()
        end_date = today.date()

    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc)

    cache_key = f"orders_live_{owner_name}_{start_date}_{end_date}_{fulfillment_status}"

    if refresh or cache_key not in st.session_state:
        with st.spinner("Loading live eBay orders..."):
            st.session_state[cache_key] = _fetch_orders(
                owner_name=owner_name,
                start_dt=start_dt,
                end_dt=end_dt,
                status_filter=fulfillment_status,
            )
            st.session_state[f"{cache_key}_synced"] = datetime.now(timezone.utc).isoformat()

    rows = st.session_state.get(cache_key, [])
    df = pd.DataFrame(rows)

    if df.empty:
        st.info("No eBay orders returned for this date range.")
        return

    if search:
        q = search.lower().strip()
        mask = (
            df["order_id"].astype(str).str.lower().str.contains(q, na=False)
            | df["legacy_order_id"].astype(str).str.lower().str.contains(q, na=False)
            | df["sku"].astype(str).str.lower().str.contains(q, na=False)
            | df["buyer_username"].astype(str).str.lower().str.contains(q, na=False)
            | df["title"].astype(str).str.lower().str.contains(q, na=False)
        )
        df = df[mask]

    total_orders = df["order_id"].nunique()
    total_items = pd.to_numeric(df["quantity"], errors="coerce").fillna(0).sum()
    total_sales = pd.to_numeric(df["line_total"], errors="coerce").fillna(0).sum()
    open_orders = df[~df["order_status"].astype(str).str.upper().isin(["FULFILLED", "CANCELED", "CANCELLED"])]["order_id"].nunique()

    m1, m2, m3, m4 = st.columns(4)
    m1.markdown(f"<div class='metric-card'><div class='label'>Orders</div><div class='value'>{total_orders}</div></div>", unsafe_allow_html=True)
    m2.markdown(f"<div class='metric-card'><div class='label'>Items Sold</div><div class='value'>{int(total_items)}</div></div>", unsafe_allow_html=True)
    m3.markdown(f"<div class='metric-card'><div class='label'>Gross Sales</div><div class='value'>${total_sales:,.2f}</div></div>", unsafe_allow_html=True)
    m4.markdown(f"<div class='metric-card'><div class='label'>Open Orders</div><div class='value'>{open_orders}</div></div>", unsafe_allow_html=True)

    st.caption(f"Last synced: {st.session_state.get(f'{cache_key}_synced', 'not synced')}")

    tab_orders, tab_tracking, tab_raw, tab_inventory = st.tabs(
        ["Orders", "Tracking Queue", "Raw eBay JSON", "Inventory Summary"]
    )

    display_cols = [
        "created",
        "order_id",
        "buyer_username",
        "order_status",
        "payment_status",
        "sku",
        "title",
        "quantity",
        "line_total",
        "currency",
        "ship_by",
        "tracking",
    ]

    with tab_orders:
        st.dataframe(
            df[[c for c in display_cols if c in df.columns]].sort_values("created", ascending=False),
            use_container_width=True,
            hide_index=True,
        )

    with tab_tracking:
        tracking_df = df[
            (df["tracking"].astype(str).str.strip() == "")
            & (~df["order_status"].astype(str).str.upper().isin(["FULFILLED", "CANCELED", "CANCELLED"]))
        ]
        st.write(f"Orders/items needing tracking: {len(tracking_df)}")
        st.dataframe(
            tracking_df[[c for c in display_cols if c in tracking_df.columns]].sort_values("ship_by", ascending=True),
            use_container_width=True,
            hide_index=True,
        )

    with tab_raw:
        options = df.index.tolist()
        idx = st.selectbox(
            "Inspect order",
            options,
            format_func=lambda i: f"{df.loc[i, 'order_id']} | {df.loc[i, 'sku']} | {df.loc[i, 'buyer_username']}",
        )
        st.json(df.loc[idx, "raw_order"])

    with tab_inventory:
        if show_inventory:
            inv_df = _fetch_inventory_summary(owner_name)
            if inv_df.empty:
                st.info("No inventory items returned.")
            else:
                st.dataframe(inv_df, use_container_width=True, hide_index=True)
        else:
            st.info("Enable “Also load inventory summary” in the sidebar, then refresh.")


# Compatibility alias if your app imports a differently named page renderer.
render = render_orders
