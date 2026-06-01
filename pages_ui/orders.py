
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable

import pandas as pd
import streamlit as st

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

from core.ebay_account_store import call_ebay_api, get_connected_ebay_label, get_latest_ebay_account


PAGE_SIZE = 100
MAX_CHUNK_DAYS = 30
EASTERN_TZ = ZoneInfo("America/New_York") if ZoneInfo else timezone(timedelta(hours=-5))


def _css() -> str:
    return """
    <style>
      .orders-hero {
        background: linear-gradient(135deg, rgba(15,23,42,0.96), rgba(30,41,59,0.92));
        border: 1px solid rgba(148,163,184,0.16);
        border-radius: 26px;
        padding: 1.1rem 1.25rem;
        margin-bottom: 1rem;
      }
      .orders-hero h1 { margin: 0; color: #f8fafc; font-size: 2rem; letter-spacing: -0.03em; }
      .orders-hero p { margin: 0.35rem 0 0 0; color: #cbd5e1; }
      .soft-card {
        background: rgba(15,23,42,0.72);
        border: 1px solid rgba(148,163,184,0.14);
        border-radius: 20px;
        padding: 1rem;
      }
      .small-muted { color:#94a3b8; font-size:0.9rem; }
    </style>
    """


def _owner_candidates() -> list[str]:
    candidates = [
        st.session_state.get("owner_name"),
        st.session_state.get("active_ebay_account"),
        st.session_state.get("user"),
        st.session_state.get("username"),
        st.session_state.get("client_name"),
        "default",
    ]
    clean: list[str] = []
    for item in candidates:
        if item and str(item) not in clean:
            clean.append(str(item))
    return clean


def _resolve_connected_owner() -> tuple[str | None, dict | None, str]:
    """
    Finds the saved OAuth account without relying on the current Streamlit page/session.
    This protects the app after eBay redirects users back to the login/root page.
    """
    for owner in _owner_candidates():
        try:
            account = get_latest_ebay_account(owner)
            if account:
                try:
                    label = get_connected_ebay_label(owner)
                except Exception:
                    label = (
                        account.get("ebay_username")
                        or account.get("store_name")
                        or account.get("ebay_user_id")
                        or "Connected eBay account"
                    )
                return owner, account, label
        except Exception:
            continue
    return None, None, "No connected eBay account"


def _money_value(value: Any) -> float:
    try:
        if isinstance(value, dict):
            value = value.get("value", 0)
        return float(value or 0)
    except Exception:
        return 0.0


def _fmt_ebay_utc(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _safe_est_bounds(start_day: date, end_day: date) -> tuple[datetime, datetime]:
    """
    User picks EST/ET dates. eBay requires UTC.
    We clamp the end time to now - 5 minutes so eBay never receives a future date.
    """
    if end_day < start_day:
        start_day, end_day = end_day, start_day

    start_est = datetime.combine(start_day, time.min).replace(tzinfo=EASTERN_TZ)
    end_est = datetime.combine(end_day, time(23, 59, 59)).replace(tzinfo=EASTERN_TZ)

    start_utc = start_est.astimezone(timezone.utc)
    end_utc = end_est.astimezone(timezone.utc)

    safe_now_utc = datetime.now(timezone.utc) - timedelta(minutes=5)
    if end_utc > safe_now_utc:
        end_utc = safe_now_utc

    if start_utc >= end_utc:
        start_utc = end_utc - timedelta(days=1)

    return start_utc, end_utc


def _chunk_range(start_utc: datetime, end_utc: datetime, max_days: int = MAX_CHUNK_DAYS) -> Iterable[tuple[datetime, datetime]]:
    cur = start_utc
    while cur < end_utc:
        nxt = min(cur + timedelta(days=max_days), end_utc)
        yield cur, nxt
        cur = nxt + timedelta(seconds=1)


def _extract_tracking(line_item: dict[str, Any], order: dict[str, Any]) -> str:
    values: list[str] = []

    for fulfillment in order.get("fulfillmentStartInstructions", []) or []:
        for shipment in fulfillment.get("shippingStep", {}).get("shipTo", {}).get("contactAddress", []) or []:
            if shipment:
                values.append(str(shipment))

    for fulfillment in order.get("shippingFulfillments", []) or []:
        tracking = fulfillment.get("shipmentTrackingNumber")
        carrier = fulfillment.get("shippingCarrierCode")
        if tracking and carrier:
            values.append(f"{carrier}: {tracking}")
        elif tracking:
            values.append(str(tracking))

    return ", ".join(dict.fromkeys(values))


def _order_rows(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for order in orders:
        buyer = order.get("buyer", {}) or {}
        pricing = order.get("pricingSummary", {}) or {}
        order_total = _money_value(pricing.get("total"))

        line_items = order.get("lineItems", []) or []
        if not line_items:
            rows.append(
                {
                    "order_id": order.get("orderId", ""),
                    "created": order.get("creationDate", ""),
                    "updated": order.get("lastModifiedDate", ""),
                    "buyer": buyer.get("username", ""),
                    "sku": "",
                    "title": "",
                    "quantity": "",
                    "total": order_total,
                    "payment_status": order.get("orderPaymentStatus", ""),
                    "fulfillment_status": order.get("orderFulfillmentStatus", ""),
                    "cancel_status": (order.get("cancelStatus", {}) or {}).get("cancelState", ""),
                    "ship_by": "",
                    "tracking": _extract_tracking({}, order),
                    "raw": order,
                }
            )
            continue

        for item in line_items:
            item_total = _money_value(item.get("total") or item.get("lineItemCost"))
            instructions = item.get("lineItemFulfillmentInstructions", {}) or {}
            rows.append(
                {
                    "order_id": order.get("orderId", ""),
                    "created": order.get("creationDate", ""),
                    "updated": order.get("lastModifiedDate", ""),
                    "buyer": buyer.get("username", ""),
                    "sku": item.get("sku", ""),
                    "title": item.get("title", ""),
                    "quantity": item.get("quantity", ""),
                    "total": item_total,
                    "payment_status": order.get("orderPaymentStatus", ""),
                    "fulfillment_status": item.get("lineItemFulfillmentStatus") or order.get("orderFulfillmentStatus", ""),
                    "cancel_status": (order.get("cancelStatus", {}) or {}).get("cancelState", ""),
                    "ship_by": instructions.get("shipByDate", ""),
                    "tracking": _extract_tracking(item, order),
                    "raw": order,
                }
            )

    return rows


def _call_orders(owner_name: str, params: dict[str, Any]):
    resp = call_ebay_api(owner_name, "GET", "/sell/fulfillment/v1/order", params=params)
    if hasattr(resp, "status_code"):
        if resp.status_code >= 400:
            raise RuntimeError(f"{resp.status_code}: {getattr(resp, 'text', '')}")
        return resp.json()
    return resp


def _fetch_orders(owner_name: str, start_utc: datetime, end_utc: datetime, fulfillment_status: str) -> tuple[list[dict[str, Any]], list[str]]:
    all_orders: list[dict[str, Any]] = []
    warnings: list[str] = []

    for chunk_start, chunk_end in _chunk_range(start_utc, end_utc):
        offset = 0
        while True:
            params: dict[str, Any] = {
                "limit": PAGE_SIZE,
                "offset": offset,
                "filter": f"creationdate:[{_fmt_ebay_utc(chunk_start)}..{_fmt_ebay_utc(chunk_end)}]",
            }

            if fulfillment_status != "All":
                # eBay can be picky here, so if this fails we retry this chunk without the status filter.
                params["filter"] += f",orderfulfillmentstatus:{{{fulfillment_status}}}"

            try:
                data = _call_orders(owner_name, params)
            except RuntimeError as first_error:
                if fulfillment_status != "All":
                    warnings.append("Status filter was rejected by eBay, so results were loaded by date only.")
                    params.pop("filter", None)
                    params = {
                        "limit": PAGE_SIZE,
                        "offset": offset,
                        "filter": f"creationdate:[{_fmt_ebay_utc(chunk_start)}..{_fmt_ebay_utc(chunk_end)}]",
                    }
                    data = _call_orders(owner_name, params)
                else:
                    raise first_error

            orders = data.get("orders", []) if isinstance(data, dict) else []
            all_orders.extend(orders)

            total = int(data.get("total", len(orders)) or 0) if isinstance(data, dict) else len(orders)
            if len(orders) < PAGE_SIZE or offset + PAGE_SIZE >= total:
                break

            offset += PAGE_SIZE

    # De-dupe by order id but preserve order.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for order in all_orders:
        oid = order.get("orderId") or json.dumps(order, sort_keys=True)
        if oid not in seen:
            unique.append(order)
            seen.add(oid)

    unique.sort(key=lambda o: o.get("creationDate", ""))
    return unique, warnings


def _render_metrics(df: pd.DataFrame):
    total_orders = df["order_id"].nunique() if not df.empty else 0
    total_items = pd.to_numeric(df.get("quantity", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    total_sales = pd.to_numeric(df.get("total", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    open_rows = df[
        ~df.get("fulfillment_status", pd.Series(dtype=str)).astype(str).str.upper().isin(["FULFILLED", "SHIPPED", "CANCELLED"])
    ] if not df.empty else df

    a, b, c, d = st.columns(4)
    a.metric("Orders", f"{total_orders:,}")
    b.metric("Items", f"{int(total_items):,}")
    c.metric("Sales", f"${total_sales:,.2f}")
    d.metric("Open / Unfulfilled", f"{len(open_rows):,}")


def render_orders():
    st.markdown(_css(), unsafe_allow_html=True)
    st.markdown(
        """
        <div class="orders-hero">
          <h1>Orders Command Board</h1>
          <p>Live eBay orders from your connected OAuth seller account. Dates are selected in Eastern Time and safely converted for eBay.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    owner_name, account, label = _resolve_connected_owner()
    if not owner_name or not account:
        st.warning("No connected eBay account found. Go to Settings and connect eBay first.")
        if st.button("Go to Settings", use_container_width=False):
            st.session_state.active_page = "Settings"
            st.rerun()
        return

    st.success(f"Using connected eBay account: {label}")

    today_est = datetime.now(EASTERN_TZ).date()
    default_start = today_est - timedelta(days=30)

    with st.sidebar:
        st.subheader("Order filters")
        preset = st.selectbox(
            "Date range",
            ["Last 7 days", "Last 30 days", "Last 90 days", "Year to date", "Custom"],
            index=1,
        )

        if preset == "Last 7 days":
            start_day, end_day = today_est - timedelta(days=7), today_est
        elif preset == "Last 30 days":
            start_day, end_day = today_est - timedelta(days=30), today_est
        elif preset == "Last 90 days":
            start_day, end_day = today_est - timedelta(days=90), today_est
        elif preset == "Year to date":
            start_day, end_day = date(today_est.year, 1, 1), today_est
        else:
            start_day = st.date_input("Start date (EST)", value=default_start)
            end_day = st.date_input("End date (EST)", value=today_est)

        fulfillment_status = st.selectbox(
            "Fulfillment status",
            ["All", "NOT_STARTED", "IN_PROGRESS", "FULFILLED"],
            index=0,
        )
        search = st.text_input("Search orders", placeholder="Order ID, buyer, SKU, title")
        refresh = st.button("Refresh live orders", use_container_width=True)

    start_utc, end_utc = _safe_est_bounds(start_day, end_day)

    cache_key = f"ebay_orders_{owner_name}_{start_utc.isoformat()}_{end_utc.isoformat()}_{fulfillment_status}"
    if refresh:
        st.session_state.pop(cache_key, None)

    with st.caption(f"Showing {start_day:%b %d, %Y} through {end_day:%b %d, %Y} Eastern Time."):
        pass

    if cache_key not in st.session_state:
        with st.spinner("Loading live eBay orders..."):
            try:
                orders, warnings = _fetch_orders(owner_name, start_utc, end_utc, fulfillment_status)
                st.session_state[cache_key] = {"orders": orders, "warnings": warnings, "synced_at": datetime.now(timezone.utc).isoformat()}
            except Exception as exc:
                st.error("eBay rejected the order query or the saved connection needs reconnecting.")
                st.info("Use Last 30 days, confirm your eBay account is connected in Settings, then refresh.")
                with st.expander("Technical details", expanded=False):
                    st.write(str(exc))
                    st.write("Start UTC:", _fmt_ebay_utc(start_utc))
                    st.write("End UTC:", _fmt_ebay_utc(end_utc))
                return

    payload = st.session_state[cache_key]
    for warning in payload.get("warnings", []):
        st.warning(warning)

    rows = _order_rows(payload.get("orders", []))
    df = pd.DataFrame(rows)

    if df.empty:
        st.info("No eBay orders were returned for this date range.")
        return

    if search:
        q = search.lower().strip()
        mask = (
            df["order_id"].astype(str).str.lower().str.contains(q, na=False)
            | df["buyer"].astype(str).str.lower().str.contains(q, na=False)
            | df["sku"].astype(str).str.lower().str.contains(q, na=False)
            | df["title"].astype(str).str.lower().str.contains(q, na=False)
        )
        df = df[mask]

    _render_metrics(df)

    display_columns = [
        "created",
        "order_id",
        "buyer",
        "sku",
        "title",
        "quantity",
        "total",
        "payment_status",
        "fulfillment_status",
        "ship_by",
        "tracking",
    ]

    st.dataframe(
        df[display_columns],
        use_container_width=True,
        hide_index=True,
        column_config={
            "created": "Created",
            "order_id": "Order ID",
            "buyer": "Buyer",
            "sku": "SKU",
            "title": "Item",
            "quantity": "Qty",
            "total": st.column_config.NumberColumn("Total", format="$%.2f"),
            "payment_status": "Payment",
            "fulfillment_status": "Fulfillment",
            "ship_by": "Ship by",
            "tracking": "Tracking",
        },
    )

    export_df = df.drop(columns=["raw"], errors="ignore")
    st.download_button(
        "Download orders CSV",
        data=export_df.to_csv(index=False).encode("utf-8"),
        file_name=f"ebay_orders_{start_day}_to_{end_day}_est.csv",
        mime="text/csv",
        use_container_width=True,
    )

    with st.expander("Order JSON inspector", expanded=False):
        order_ids = df["order_id"].dropna().astype(str).unique().tolist()
        if order_ids:
            selected = st.selectbox("Select order", order_ids)
            raw = df[df["order_id"].astype(str) == selected].iloc[0].get("raw", {})
            st.json(raw)


if __name__ == "__main__":
    render_orders()
