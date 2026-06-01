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
      .orders-shell { padding-top: 0.35rem; }
      .orders-hero {
        background: linear-gradient(135deg, rgba(6,10,20,0.98), rgba(15,23,42,0.95));
        border: 1px solid rgba(148,163,184,0.14);
        border-radius: 28px;
        padding: 1.2rem 1.35rem;
        margin-bottom: 1rem;
        box-shadow: 0 18px 50px rgba(0,0,0,0.18);
      }
      .orders-hero-top {
        display:flex;
        justify-content:space-between;
        align-items:flex-start;
        gap:1rem;
      }
      .orders-hero h1 {
        margin: 0;
        color: #f8fafc;
        font-size: 2.15rem;
        letter-spacing: -0.04em;
      }
      .orders-hero p {
        margin: 0.4rem 0 0 0;
        color: #cbd5e1;
        max-width: 72ch;
      }
      .hero-pill {
        display:inline-flex;
        align-items:center;
        gap:0.5rem;
        padding:0.35rem 0.7rem;
        border-radius:999px;
        background:rgba(56,189,248,0.10);
        border:1px solid rgba(56,189,248,0.18);
        color:#bae6fd;
        font-size:0.82rem;
        font-weight:700;
      }
      .panel {
        background: rgba(15,23,42,0.72);
        border: 1px solid rgba(148,163,184,0.13);
        border-radius: 22px;
        padding: 1rem;
        box-shadow: 0 10px 30px rgba(0,0,0,0.12);
      }
      .metric {
        background: rgba(2,6,23,0.55);
        border: 1px solid rgba(148,163,184,0.12);
        border-radius: 18px;
        padding: 0.95rem 1rem;
        min-height: 98px;
      }
      .metric .label {
        color:#94a3b8;
        font-size:0.76rem;
        text-transform:uppercase;
        letter-spacing:0.09em;
        margin-bottom:0.35rem;
      }
      .metric .value {
        color:#fff;
        font-size:1.5rem;
        font-weight:800;
        line-height:1.05;
      }
      .metric .hint {
        color:#94a3b8;
        font-size:0.82rem;
        margin-top:0.35rem;
      }
      .mini-badge {
        display:inline-flex;
        align-items:center;
        padding:0.25rem 0.55rem;
        border-radius:999px;
        font-size:0.76rem;
        font-weight:800;
        background:rgba(56,189,248,0.10);
        color:#bae6fd;
        border:1px solid rgba(56,189,248,0.18);
      }
      .detail-line {
        padding:0.22rem 0;
        color:#e2e8f0;
      }
      .detail-label {
        color:#94a3b8;
        font-size:0.78rem;
        text-transform:uppercase;
        letter-spacing:0.08em;
      }
      .table-wrap table {
        width:100%;
        border-collapse: collapse;
      }
      .table-wrap th, .table-wrap td {
        padding: 0.55rem 0.6rem;
        border-bottom: 1px solid rgba(148,163,184,0.12);
        text-align:left;
        vertical-align: top;
      }
      .table-wrap th {
        color:#cbd5e1;
        font-size:0.78rem;
        text-transform:uppercase;
        letter-spacing:0.07em;
      }
      .table-wrap td {
        color:#e5e7eb;
        font-size:0.92rem;
      }
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


def _extract_tracking(order: dict[str, Any]) -> str:
    values: list[str] = []

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
                    "tracking": _extract_tracking(order),
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
                    "tracking": _extract_tracking(order),
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
                params["filter"] += f",orderfulfillmentstatus:{{{fulfillment_status}}}"

            try:
                data = _call_orders(owner_name, params)
            except RuntimeError as first_error:
                if fulfillment_status != "All":
                    warnings.append("Status filter was rejected by eBay, so results were loaded by date only.")
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

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for order in all_orders:
        oid = order.get("orderId") or json.dumps(order, sort_keys=True)
        if oid not in seen:
            unique.append(order)
            seen.add(oid)

    unique.sort(key=lambda o: o.get("creationDate", ""))
    return unique, warnings


def _metric(label: str, value: str, hint: str = "") -> str:
    return f"""
    <div class="metric">
      <div class="label">{label}</div>
      <div class="value">{value}</div>
      <div class="hint">{hint}</div>
    </div>
    """


def _render_metrics(df: pd.DataFrame):
    total_orders = df["order_id"].nunique() if not df.empty else 0
    total_items = pd.to_numeric(df.get("quantity", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    total_sales = pd.to_numeric(df.get("total", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    open_rows = df[
        ~df.get("fulfillment_status", pd.Series(dtype=str)).astype(str).str.upper().isin(["FULFILLED", "SHIPPED", "CANCELLED"])
    ] if not df.empty else df

    a, b, c, d = st.columns(4)
    a.markdown(_metric("Orders", f"{total_orders:,}", "Unique order IDs"), unsafe_allow_html=True)
    b.markdown(_metric("Items", f"{int(total_items):,}", "Line items"), unsafe_allow_html=True)
    c.markdown(_metric("Sales", f"${total_sales:,.2f}", "Shown results"), unsafe_allow_html=True)
    d.markdown(_metric("Open / Unfulfilled", f"{len(open_rows):,}", "Needs attention"), unsafe_allow_html=True)


def _tag(text: str) -> str:
    txt = (text or "").upper()
    if txt in {"FULFILLED", "SHIPPED"}:
        kind = "green"
    elif txt in {"READY", "PENDING", "NOT_STARTED"}:
        kind = "yellow"
    elif txt in {"CANCELLED", "ISSUE"}:
        kind = "red"
    else:
        kind = "blue"
    palette = {"green": "#4ade80", "yellow": "#f59e0b", "red": "#fb7185", "blue": "#38bdf8"}
    c = palette[kind]
    return f"<span class='mini-badge' style='background:{c};color:#0b1220;'>{text}</span>"


def render_orders():
    st.markdown(_css(), unsafe_allow_html=True)
    st.markdown(
        """
        <div class="orders-shell">
          <div class="orders-hero">
            <div class="orders-hero-top">
              <div>
                <h1>Orders Command Board</h1>
                <p>Live eBay orders from your connected seller account. Choose an Eastern Time date range, load only what you need, and work the queue from a clean SaaS-style board.</p>
              </div>
              <div class="hero-pill">Live eBay Sync</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    owner_name, account, label = _resolve_connected_owner()
    if not owner_name or not account:
        st.warning("No connected eBay account found. Go to Settings and connect eBay first.")
        if st.button("Go to Settings"):
            st.session_state.active_page = "Settings"
            st.rerun()
        return

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
        elif preset == "Last 90 days":
            start_day, end_day = today_est - timedelta(days=90), today_est
        elif preset == "Year to date":
            start_day, end_day = date(today_est.year, 1, 1), today_est
        elif preset == "Custom":
            start_day = st.date_input("Start date (EST)", value=default_start)
            end_day = st.date_input("End date (EST)", value=today_est)
        else:
            start_day, end_day = today_est - timedelta(days=30), today_est

        fulfillment_status = st.selectbox(
            "Fulfillment status",
            ["All", "NOT_STARTED", "IN_PROGRESS", "FULFILLED"],
            index=0,
        )
        search = st.text_input("Search orders", placeholder="Order ID, buyer, SKU, title")
        refresh = st.button("Refresh live orders", use_container_width=True)

    start_utc, end_utc = _safe_est_bounds(start_day, end_day)

    st.caption(f"Showing {start_day:%b %d, %Y} through {end_day:%b %d, %Y} Eastern Time.")
    st.info(f"Connected account: {label}")

    cache_key = f"ebay_orders_{owner_name}_{start_utc.isoformat()}_{end_utc.isoformat()}_{fulfillment_status}"
    if refresh:
        st.session_state.pop(cache_key, None)

    if cache_key not in st.session_state:
        with st.spinner("Loading live eBay orders..."):
            try:
                orders, warnings = _fetch_orders(owner_name, start_utc, end_utc, fulfillment_status)
                st.session_state[cache_key] = {
                    "orders": orders,
                    "warnings": warnings,
                    "synced_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as exc:
                st.error("eBay rejected the order query or the saved connection needs reconnecting.")
                st.info("Use a recent date range, confirm the eBay account is connected in Settings, then refresh.")
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

    left, right = st.columns([0.68, 0.32], gap="large")

    with left:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.subheader("Order stream")

        display = df.copy()
        display["status_badge"] = display["fulfillment_status"].apply(_tag)
        table = display[
            ["created", "order_id", "buyer", "sku", "title", "quantity", "total", "payment_status", "fulfillment_status", "ship_by", "tracking"]
        ].copy()
        table.rename(
            columns={
                "created": "Created",
                "order_id": "Order ID",
                "buyer": "Buyer",
                "sku": "SKU",
                "title": "Item",
                "quantity": "Qty",
                "total": "Total",
                "payment_status": "Payment",
                "fulfillment_status": "Fulfillment",
                "ship_by": "Ship by",
                "tracking": "Tracking",
            },
            inplace=True,
        )

        st.dataframe(
            table,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Total": st.column_config.NumberColumn("Total", format="$%.2f"),
            },
        )

        st.download_button(
            "Download orders CSV",
            data=df.drop(columns=["raw"], errors="ignore").to_csv(index=False).encode("utf-8"),
            file_name=f"ebay_orders_{start_day}_to_{end_day}_est.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.subheader("Selected order")

        if not df.empty:
            selected_key = st.selectbox(
                "Choose order",
                df.index.tolist(),
                format_func=lambda i: str(df.loc[i, "order_id"] or df.loc[i, "sku"] or f"row {i}"),
            )
            row = df.loc[selected_key]

            st.markdown(f"<div class='detail-line'><div class='detail-label'>Order ID</div><div>{row['order_id'] or '—'}</div></div>", unsafe_allow_html=True)
            st.markdown(f"<div class='detail-line'><div class='detail-label'>Buyer</div><div>{row['buyer'] or '—'}</div></div>", unsafe_allow_html=True)
            st.markdown(f"<div class='detail-line'><div class='detail-label'>Item</div><div>{row['title'] or '—'}</div></div>", unsafe_allow_html=True)
            st.markdown(f"<div class='detail-line'><div class='detail-label'>SKU</div><div>{row['sku'] or '—'}</div></div>", unsafe_allow_html=True)
            st.markdown(f"<div class='detail-line'><div class='detail-label'>Fulfillment</div><div>{row['fulfillment_status'] or '—'}</div></div>", unsafe_allow_html=True)
            st.markdown(f"<div class='detail-line'><div class='detail-label'>Payment</div><div>{row['payment_status'] or '—'}</div></div>", unsafe_allow_html=True)
            st.markdown(f"<div class='detail-line'><div class='detail-label'>Ship by</div><div>{row['ship_by'] or '—'}</div></div>", unsafe_allow_html=True)
            st.markdown(f"<div class='detail-line'><div class='detail-label'>Tracking</div><div>{row['tracking'] or 'Pending'}</div></div>", unsafe_allow_html=True)
        else:
            st.info("No selected order.")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:0.75rem;'></div>", unsafe_allow_html=True)

        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.subheader("Actions")
        st.button("Mark Purchased", use_container_width=True)
        st.button("Add Tracking", use_container_width=True)
        st.button("Mark Shipped", use_container_width=True)
        st.button("Flag Issue", use_container_width=True)
        st.button("Archive", use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("Debug details", expanded=False):
        st.write("Synced at:", payload.get("synced_at"))
        st.write("Loaded orders:", len(payload.get("orders", [])))
        st.write("Date range UTC:", _fmt_ebay_utc(start_utc), "to", _fmt_ebay_utc(end_utc))
        st.write("Account owner:", owner_name)
        st.json({"fulfillment_status": fulfillment_status, "search": search, "presets": preset})


if __name__ == "__main__":
    render_orders()