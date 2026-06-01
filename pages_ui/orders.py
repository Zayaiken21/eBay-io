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
      .orders-shell { padding-top: 0.25rem; }
      .orders-hero {
        background: radial-gradient(circle at top left, rgba(56,189,248,0.16), transparent 30%),
                    linear-gradient(135deg, rgba(6,10,20,0.98), rgba(15,23,42,0.95));
        border: 1px solid rgba(148,163,184,0.14);
        border-radius: 28px;
        padding: 1.25rem 1.35rem;
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
        font-size: 2.2rem;
        letter-spacing: -0.05em;
      }
      .orders-hero p {
        margin: 0.45rem 0 0 0;
        color: #cbd5e1;
        max-width: 84ch;
        line-height: 1.5;
      }
      .hero-pill {
        display:inline-flex;
        align-items:center;
        gap:0.5rem;
        padding:0.35rem 0.75rem;
        border-radius:999px;
        background:rgba(56,189,248,0.10);
        border:1px solid rgba(56,189,248,0.18);
        color:#bae6fd;
        font-size:0.82rem;
        font-weight:700;
        white-space: nowrap;
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
        padding:0.28rem 0;
        color:#e2e8f0;
      }
      .detail-label {
        color:#94a3b8;
        font-size:0.78rem;
        text-transform:uppercase;
        letter-spacing:0.08em;
      }
      .section-title {
        color:#f8fafc;
        font-size:1.05rem;
        font-weight:800;
        letter-spacing:-0.02em;
        margin-bottom:0.5rem;
      }
      .subtle {
        color:#94a3b8;
        font-size:0.88rem;
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


def _workflow_stage(fulfillment_status: str, payment_status: str, tracking: str, cancel_status: str) -> str:
    fs = (fulfillment_status or "").upper()
    ps = (payment_status or "").upper()
    if cancel_status:
        return "CANCELLED"
    if fs == "FULFILLED" or tracking:
        return "SHIPPED"
    if "PAID" in ps and fs in {"NOT_STARTED", "IN_PROGRESS", ""}:
        return "TO_BUY"
    if fs == "IN_PROGRESS":
        return "BUYING"
    return "WAITING_TRACKING"


def _month_key(value: str) -> str:
    try:
        if not value:
            return ""
        dt = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.isna(dt):
            return ""
        return dt.strftime("%Y-%m")
    except Exception:
        return ""


def _order_rows(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for order in orders:
        buyer = order.get("buyer", {}) or {}
        pricing = order.get("pricingSummary", {}) or {}
        order_total = _money_value(pricing.get("total"))
        line_items = order.get("lineItems", []) or []
        cancel_status = (order.get("cancelStatus", {}) or {}).get("cancelState", "")
        base_tracking = _extract_tracking(order)

        if not line_items:
            payment_status = order.get("orderPaymentStatus", "")
            fulfillment_status = order.get("orderFulfillmentStatus", "")
            stage = _workflow_stage(fulfillment_status, payment_status, base_tracking, cancel_status)
            rows.append(
                {
                    "order_id": order.get("orderId", ""),
                    "created": order.get("creationDate", ""),
                    "updated": order.get("lastModifiedDate", ""),
                    "buyer": buyer.get("username", ""),
                    "sku": "",
                    "title": "",
                    "quantity": 1,
                    "total": order_total,
                    "cost_basis": 0.0,
                    "fees": 0.0,
                    "net_profit": round(order_total, 2),
                    "payment_status": payment_status,
                    "fulfillment_status": fulfillment_status,
                    "cancel_status": cancel_status,
                    "ship_by": "",
                    "tracking": base_tracking,
                    "workflow_stage": stage,
                    "supplier_name": "",
                    "supplier_order_id": "",
                    "supplier_order_status": "",
                    "tracking_carrier": "",
                    "tracking_number": "",
                    "tracking_added_at": "",
                    "attention_flag": "YES" if stage in {"TO_BUY", "BUYING", "WAITING_TRACKING"} else "",
                    "month_key": _month_key(order.get("creationDate", "")),
                    "raw": order,
                }
            )
            continue

        for item in line_items:
            item_total = _money_value(item.get("total") or item.get("lineItemCost"))
            instructions = item.get("lineItemFulfillmentInstructions", {}) or {}
            payment_status = order.get("orderPaymentStatus", "")
            fulfillment_status = item.get("lineItemFulfillmentStatus") or order.get("orderFulfillmentStatus", "")
            stage = _workflow_stage(fulfillment_status, payment_status, base_tracking, cancel_status)
            cost_basis = 0.0
            fees = 0.0
            net_profit = round(item_total - cost_basis - fees, 2)

            rows.append(
                {
                    "order_id": order.get("orderId", ""),
                    "created": order.get("creationDate", ""),
                    "updated": order.get("lastModifiedDate", ""),
                    "buyer": buyer.get("username", ""),
                    "sku": item.get("sku", ""),
                    "title": item.get("title", ""),
                    "quantity": item.get("quantity", 1),
                    "total": item_total,
                    "cost_basis": cost_basis,
                    "fees": fees,
                    "net_profit": net_profit,
                    "payment_status": payment_status,
                    "fulfillment_status": fulfillment_status,
                    "cancel_status": cancel_status,
                    "ship_by": instructions.get("shipByDate", ""),
                    "tracking": base_tracking,
                    "workflow_stage": stage,
                    "supplier_name": "",
                    "supplier_order_id": "",
                    "supplier_order_status": "",
                    "tracking_carrier": "",
                    "tracking_number": "",
                    "tracking_added_at": "",
                    "attention_flag": "YES" if stage in {"TO_BUY", "BUYING", "WAITING_TRACKING"} else "",
                    "month_key": _month_key(order.get("creationDate", "")),
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
    total_profit = pd.to_numeric(df.get("net_profit", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    open_rows = df[df.get("workflow_stage", pd.Series(dtype=str)).astype(str).isin(["TO_BUY", "BUYING", "WAITING_TRACKING"])]

    a, b, c, d = st.columns(4)
    a.markdown(_metric("Orders", f"{total_orders:,}", "Unique order IDs"), unsafe_allow_html=True)
    b.markdown(_metric("Items", f"{int(total_items):,}", "Line items"), unsafe_allow_html=True)
    c.markdown(_metric("Sales", f"${total_sales:,.2f}", "Shown results"), unsafe_allow_html=True)
    d.markdown(_metric("Est. Profit", f"${total_profit:,.2f}", "After cost/fees"), unsafe_allow_html=True)

    e, f, g, h = st.columns(4)
    e.markdown(_metric("To Buy", f"{len(df[df['workflow_stage']=='TO_BUY']):,}", "Needs supplier purchase"), unsafe_allow_html=True)
    f.markdown(_metric("Buying", f"{len(df[df['workflow_stage']=='BUYING']):,}", "Awaiting buy"), unsafe_allow_html=True)
    g.markdown(_metric("Tracking", f"{len(df[df['workflow_stage']=='WAITING_TRACKING']):,}", "Needs tracking"), unsafe_allow_html=True)
    h.markdown(_metric("Exceptions", f"{len(df[df['attention_flag']=='YES']):,}", "Manual review"), unsafe_allow_html=True)


def _tag(text: str) -> str:
    txt = (text or "").upper()
    if txt in {"SHIPPED", "FULFILLED"}:
        kind = "green"
    elif txt in {"TO_BUY", "WAITING_TRACKING"}:
        kind = "yellow"
    elif txt in {"BUYING"}:
        kind = "blue"
    elif txt in {"CANCELLED", "ISSUE"}:
        kind = "red"
    else:
        kind = "blue"
    palette = {"green": "#4ade80", "yellow": "#f59e0b", "red": "#fb7185", "blue": "#38bdf8"}
    c = palette[kind]
    return f"<span class='mini-badge' style='background:{c};color:#0b1220;'>{text}</span>"


def _month_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = df.groupby("month_key", dropna=False).agg(
        orders=("order_id", "nunique"),
        items=("quantity", "sum"),
        sales=("total", "sum"),
        profit=("net_profit", "sum"),
        to_buy=("workflow_stage", lambda s: (s == "TO_BUY").sum()),
        buying=("workflow_stage", lambda s: (s == "BUYING").sum()),
        tracking=("workflow_stage", lambda s: (s == "WAITING_TRACKING").sum()),
        shipped=("workflow_stage", lambda s: (s == "SHIPPED").sum()),
        exceptions=("attention_flag", lambda s: (s == "YES").sum()),
    ).reset_index()
    g["month_key"] = g["month_key"].fillna("")
    return g.sort_values("month_key", ascending=False)


def render_orders():
    st.markdown(_css(), unsafe_allow_html=True)
    st.markdown(
        """
        <div class="orders-shell">
          <div class="orders-hero">
            <div class="orders-hero-top">
              <div>
                <h1>Orders Command Board</h1>
                <p>AutoDS-style order operations for eBay dropshipping. Track what needs to be purchased, what is already bought, what needs tracking, and what is shipped — all in one premium control panel.</p>
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
        st.subheader("Board filters")
        preset = st.selectbox(
            "Date range",
            ["This month", "Last month", "Last 7 days", "Last 30 days", "Last 90 days", "Year to date", "Custom"],
            index=3,
        )

        if preset == "This month":
            start_day = date(today_est.year, today_est.month, 1)
            end_day = today_est
        elif preset == "Last month":
            first_this = date(today_est.year, today_est.month, 1)
            last_last = first_this - timedelta(days=1)
            start_day = date(last_last.year, last_last.month, 1)
            end_day = last_last
        elif preset == "Last 7 days":
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
        workflow_view = st.selectbox(
            "Workflow view",
            ["All", "TO_BUY", "BUYING", "WAITING_TRACKING", "SHIPPED", "CANCELLED"],
            index=0,
        )
        month_filter = st.text_input("Month filter", placeholder="2026-05")
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
        df = df[
            df["order_id"].astype(str).str.lower().str.contains(q, na=False)
            | df["buyer"].astype(str).str.lower().str.contains(q, na=False)
            | df["sku"].astype(str).str.lower().str.contains(q, na=False)
            | df["title"].astype(str).str.lower().str.contains(q, na=False)
        ]

    if workflow_view != "All":
        df = df[df["workflow_stage"] == workflow_view]

    if month_filter.strip():
        df = df[df["month_key"].astype(str).str.contains(month_filter.strip(), na=False)]

    _render_metrics(df)
    month_df = _month_summary(df)

    tab_overview, tab_queue, tab_months, tab_exceptions = st.tabs(
        ["Overview", "Work Queue", "Monthly Profit", "Exceptions"]
    )

    with tab_overview:
        left, right = st.columns([0.68, 0.32], gap="large")

        with left:
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.markdown('<div class="section-title">Order stream</div>', unsafe_allow_html=True)
            st.markdown('<div class="subtle">Sorted by creation date. Workflow stage maps your supplier operations to each eBay order.</div>', unsafe_allow_html=True)

            table = df[
                [
                    "created",
                    "order_id",
                    "buyer",
                    "sku",
                    "title",
                    "quantity",
                    "total",
                    "cost_basis",
                    "fees",
                    "net_profit",
                    "payment_status",
                    "fulfillment_status",
                    "workflow_stage",
                    "tracking",
                    "month_key",
                ]
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
                    "cost_basis": "Cost",
                    "fees": "Fees",
                    "net_profit": "Profit",
                    "payment_status": "Payment",
                    "fulfillment_status": "Fulfillment",
                    "workflow_stage": "Stage",
                    "tracking": "Tracking",
                    "month_key": "Month",
                },
                inplace=True,
            )

            st.dataframe(
                table,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Total": st.column_config.NumberColumn("Total", format="$%.2f"),
                    "Cost": st.column_config.NumberColumn("Cost", format="$%.2f"),
                    "Fees": st.column_config.NumberColumn("Fees", format="$%.2f"),
                    "Profit": st.column_config.NumberColumn("Profit", format="$%.2f"),
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
            st.markdown('<div class="section-title">Selected order</div>', unsafe_allow_html=True)

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
            st.markdown(f"<div class='detail-line'><div class='detail-label'>Stage</div><div>{_tag(row['workflow_stage'])}</div></div>", unsafe_allow_html=True)
            st.markdown(f"<div class='detail-line'><div class='detail-label'>Fulfillment</div><div>{row['fulfillment_status'] or '—'}</div></div>", unsafe_allow_html=True)
            st.markdown(f"<div class='detail-line'><div class='detail-label'>Tracking</div><div>{row['tracking'] or 'Pending'}</div></div>", unsafe_allow_html=True)
            st.markdown(f"<div class='detail-line'><div class='detail-label'>Month</div><div>{row['month_key'] or '—'}</div></div>", unsafe_allow_html=True)

            st.markdown("<hr style='border-color: rgba(148,163,184,0.16);'/>", unsafe_allow_html=True)
            st.button("Mark To Buy", use_container_width=True)
            st.button("Mark Buying", use_container_width=True)
            st.button("Attach Tracking", use_container_width=True)
            st.button("Mark Shipped", use_container_width=True)
            st.button("Flag Exception", use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)

    with tab_queue:
        queue_cols = ["created", "order_id", "buyer", "sku", "title", "workflow_stage", "supplier_name", "supplier_order_status", "tracking"]
        qdf = df[df["workflow_stage"].isin(["TO_BUY", "BUYING", "WAITING_TRACKING"])].copy()
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">AutoDS queue</div>', unsafe_allow_html=True)
        st.markdown('<div class="subtle">This is the operational queue your future agent can work from: buy, wait, attach tracking, and close.</div>', unsafe_allow_html=True)
        if qdf.empty:
            st.info("No queue items in the current filter set.")
        else:
            st.dataframe(
                qdf[queue_cols],
                use_container_width=True,
                hide_index=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with tab_months:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">Monthly profit</div>', unsafe_allow_html=True)
        st.markdown('<div class="subtle">Use this section to review revenue, profit, and operational volume by month.</div>', unsafe_allow_html=True)

        if month_df.empty:
            st.info("No monthly data in the current filter set.")
        else:
            st.dataframe(
                month_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "sales": st.column_config.NumberColumn("Sales", format="$%.2f"),
                    "profit": st.column_config.NumberColumn("Profit", format="$%.2f"),
                },
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with tab_exceptions:
        exdf = df[(df["attention_flag"] == "YES") | (df["workflow_stage"] == "CANCELLED")].copy()
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">Exceptions</div>', unsafe_allow_html=True)
        st.markdown('<div class="subtle">Orders that need manual review, supplier follow-up, or cancellation handling.</div>', unsafe_allow_html=True)
        if exdf.empty:
            st.success("No exceptions found.")
        else:
            st.dataframe(
                exdf[
                    [
                        "created",
                        "order_id",
                        "buyer",
                        "sku",
                        "title",
                        "fulfillment_status",
                        "workflow_stage",
                        "tracking",
                        "month_key",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("Debug details", expanded=False):
        st.write("Synced at:", payload.get("synced_at"))
        st.write("Loaded orders:", len(payload.get("orders", [])))
        st.write("Date range UTC:", _fmt_ebay_utc(start_utc), "to", _fmt_ebay_utc(end_utc))
        st.write("Account owner:", owner_name)
        st.write("Connected account:", label)
        st.json({"fulfillment_status": fulfillment_status, "workflow_view": workflow_view, "search": search, "month_filter": month_filter})


if __name__ == "__main__":
    render_orders()