"""
Live eBay Orders page.

This page intentionally does not display API keys, raw OAuth context, or raw API errors by default.
It uses the connected OAuth seller account saved by core.ebay_account_store.

Expected helper in core.ebay_account_store:
    call_ebay_api(owner_name, method, endpoint, params=None, json_body=None)
Optional helpers:
    get_latest_ebay_account(owner_name)
    get_connected_ebay_label(owner_name)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd
import streamlit as st

try:
    from core.ebay_account_store import (
        call_ebay_api,
        get_connected_ebay_label,
        get_latest_ebay_account,
    )
except ImportError:
    # Keep the app from hard-crashing if one optional label helper is missing.
    from core.ebay_account_store import call_ebay_api, get_latest_ebay_account  # type: ignore

    def get_connected_ebay_label(owner_name: str, *_, **__) -> str:  # type: ignore
        account = get_latest_ebay_account(owner_name)
        if not account:
            return "No eBay account connected"
        return (
            account.get("store_name")
            or account.get("ebay_username")
            or account.get("ebay_user_id")
            or "Connected eBay account"
        )


PAGE_SIZE = 50
MAX_PAGES = 20


def _css() -> str:
    return """
    <style>
      .orders-hero {
        background: linear-gradient(135deg, rgba(15,23,42,.96), rgba(30,41,59,.92));
        border: 1px solid rgba(148,163,184,.16);
        border-radius: 24px;
        padding: 1.1rem 1.25rem;
        margin-bottom: 1rem;
      }
      .orders-hero h1 { margin:0; font-size:2rem; color:#f8fafc; letter-spacing:-.03em; }
      .orders-hero p { margin:.35rem 0 0; color:#cbd5e1; }
      .metric-card {
        border: 1px solid rgba(148,163,184,.16);
        background: rgba(15,23,42,.70);
        border-radius: 18px;
        padding: .85rem .95rem;
      }
      .metric-card small {
        color:#94a3b8;
        text-transform: uppercase;
        letter-spacing: .08em;
        font-size: .72rem;
      }
      .metric-card b { display:block; margin-top:.2rem; font-size:1.25rem; color:#f8fafc; }
      .safe-note { color:#94a3b8; font-size:.86rem; }
    </style>
    """


def _session_owner_candidates() -> list[str]:
    """Return possible owner keys used by the app.

    OAuth callback may have saved under a stable app username, email, client name,
    or older builds may have saved under "default". We try all safely.
    """
    candidates: list[str] = []

    for key in (
        "owner_name",
        "active_owner",
        "username",
        "user_name",
        "user",
        "email",
        "client_name",
        "current_user",
    ):
        value = st.session_state.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())

    user_obj = st.session_state.get("user")
    if isinstance(user_obj, dict):
        for key in ("owner_name", "username", "email", "name", "id"):
            value = user_obj.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())

    # Backward compatibility with earlier OAuth saves.
    candidates.append("default")

    # Deduplicate while preserving order.
    seen: set[str] = set()
    clean: list[str] = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            clean.append(item)
    return clean


def _find_connected_owner() -> tuple[str | None, dict[str, Any] | None, str]:
    for owner in _session_owner_candidates():
        try:
            account = get_latest_ebay_account(owner)
        except Exception:
            account = None

        if account:
            try:
                label = get_connected_ebay_label(owner)
            except Exception:
                label = (
                    account.get("store_name")
                    or account.get("ebay_username")
                    or account.get("ebay_user_id")
                    or "Connected eBay account"
                )
            return owner, account, label

    return None, None, "No eBay account connected"


def _money(value: Any) -> float:
    try:
        if isinstance(value, dict):
            value = value.get("value", 0)
        if value is None or value == "":
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _currency(value: Any) -> str:
    amount = _money(value)
    return f"${amount:,.2f}"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except Exception:
        return None


def _iso_ebay(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _safe_error_message(status_code: int | None, text: str) -> str:
    lowered = (text or "").lower()

    if status_code in (401, 403):
        return (
            "eBay rejected this request. Reconnect your eBay account in Settings. "
            "If it continues, your OAuth scopes may be missing Fulfillment access."
        )
    if status_code == 400:
        return (
            "eBay rejected the order query. Try a smaller date range or use the default filters."
        )
    if status_code == 429:
        return "eBay rate limit reached. Try again after the quota window resets."
    if status_code and status_code >= 500:
        return "eBay is temporarily unavailable. Try again shortly."
    if "scope" in lowered:
        return "Your connected eBay account is missing the required order/fulfillment OAuth scope."
    return "Could not load live eBay orders. Check the app logs for the full API response."


def _call_orders_api(owner_name: str, params: dict[str, Any]):
    """Call eBay fulfillment order endpoint and return parsed JSON.

    call_ebay_api in this app may return either a requests.Response or already parsed JSON.
    This function handles both.
    """
    response = call_ebay_api(
        owner_name,
        "GET",
        "/sell/fulfillment/v1/order",
        params=params,
    )

    status_code = getattr(response, "status_code", 200)
    if status_code >= 400:
        text = getattr(response, "text", "")
        raise RuntimeError(_safe_error_message(status_code, text))

    if hasattr(response, "json"):
        return response.json()

    return response if isinstance(response, dict) else {}


def _flatten_orders(orders: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for order in orders:
        buyer = order.get("buyer", {}) or {}
        pricing_summary = order.get("pricingSummary", {}) or {}
        payment_summary = order.get("paymentSummary", {}) or {}
        fulfillments = order.get("fulfillmentStartInstructions", []) or []
        ship_to = {}
        if fulfillments:
            ship_to = (
                fulfillments[0]
                .get("shippingStep", {})
                .get("shipTo", {})
                or {}
            )

        line_items = order.get("lineItems", []) or [{}]
        tracking_numbers: list[str] = []
        for fulfillment in order.get("fulfillmentHrefs", []) or []:
            if isinstance(fulfillment, str):
                tracking_numbers.append(fulfillment)

        for line in line_items:
            item_cost = line.get("lineItemCost", {}) or {}
            total = line.get("total", {}) or item_cost
            creation_date = order.get("creationDate", "")
            modified_date = order.get("lastModifiedDate", "")

            rows.append(
                {
                    "Order ID": order.get("orderId", ""),
                    "Created": creation_date,
                    "Last Updated": modified_date,
                    "Buyer": buyer.get("username", ""),
                    "Order Status": order.get("orderFulfillmentStatus", ""),
                    "Payment Status": order.get("orderPaymentStatus", ""),
                    "Cancel Status": (order.get("cancelStatus", {}) or {}).get("cancelState", ""),
                    "SKU": line.get("sku", ""),
                    "Title": line.get("title", ""),
                    "Quantity": line.get("quantity", 0),
                    "Line Total": _money(total),
                    "Order Total": _money(pricing_summary.get("total")),
                    "Currency": (total or {}).get("currency", ""),
                    "Ship By": (
                        (line.get("lineItemFulfillmentInstructions", {}) or {}).get("shipByDate")
                        or ""
                    ),
                    "Shipping City": ship_to.get("city", ""),
                    "Shipping State": ship_to.get("stateOrProvince", ""),
                    "Shipping Postal": ship_to.get("postalCode", ""),
                    "Shipping Country": ship_to.get("countryCode", ""),
                    "Tracking": ", ".join(tracking_numbers),
                    "Raw Order": order,
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["_created_dt"] = df["Created"].apply(_parse_date)
    df = df.sort_values("_created_dt", ascending=False, na_position="last")
    return df


def _fetch_orders(
    owner_name: str,
    start_date: date,
    end_date: date,
    fulfillment_status: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)

    filters = [f"creationdate:[{_iso_ebay(start_dt)}..{_iso_ebay(end_dt)}]"]
    if fulfillment_status and fulfillment_status != "ALL":
        filters.append(f"orderfulfillmentstatus:{{{fulfillment_status}}}")

    params: dict[str, Any] = {
        "limit": PAGE_SIZE,
        "offset": 0,
        "filter": ",".join(filters),
    }

    all_orders: list[dict[str, Any]] = []
    last_payload: dict[str, Any] = {}

    for page in range(MAX_PAGES):
        params["offset"] = page * PAGE_SIZE
        payload = _call_orders_api(owner_name, params)
        last_payload = payload

        orders = payload.get("orders", []) if isinstance(payload, dict) else []
        all_orders.extend(orders)

        total = int(payload.get("total", len(all_orders)) or len(all_orders))
        if len(orders) < PAGE_SIZE or len(all_orders) >= total:
            break

    meta = {
        "total_orders_returned": len(all_orders),
        "api_total": last_payload.get("total", len(all_orders)) if isinstance(last_payload, dict) else len(all_orders),
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
    }

    return _flatten_orders(all_orders), meta


def _download_filename(start_date: date, end_date: date) -> str:
    return f"ebay_orders_{start_date.isoformat()}_to_{end_date.isoformat()}.csv"


def render_orders():
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

    owner_name, account, label = _find_connected_owner()

    if not owner_name or not account:
        st.warning("No eBay account is connected yet. Go to Settings and connect your eBay account first.")
        if st.button("Go to Settings", use_container_width=False):
            st.session_state.active_page = "Settings"
            st.rerun()
        return

    st.success(f"Using connected eBay account: {label}")

    today = datetime.now(timezone.utc).date()
    default_start = today - timedelta(days=90)

    with st.sidebar:
        st.subheader("Order filters")
        start_date = st.date_input("Start date", value=default_start)
        end_date = st.date_input("End date", value=today)
        fulfillment_status = st.selectbox(
            "Fulfillment status",
            ["ALL", "NOT_STARTED", "IN_PROGRESS", "FULFILLED"],
            index=0,
        )
        search = st.text_input("Search orders", placeholder="Order ID, buyer, SKU, title")
        refresh = st.button("Refresh live orders", use_container_width=True)

        st.caption("This page uses your saved OAuth token. API keys and token context are hidden.")

    if start_date > end_date:
        st.error("Start date must be before end date.")
        return

    cache_key = f"orders_live_{owner_name}_{start_date}_{end_date}_{fulfillment_status}"

    if refresh or cache_key not in st.session_state:
        with st.spinner("Loading live eBay orders..."):
            try:
                df, meta = _fetch_orders(
                    owner_name=owner_name,
                    start_date=start_date,
                    end_date=end_date,
                    fulfillment_status=fulfillment_status,
                )
                st.session_state[cache_key] = df
                st.session_state[f"{cache_key}_meta"] = meta
                st.session_state[f"{cache_key}_loaded_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            except Exception as exc:
                st.error(str(exc))
                with st.expander("Troubleshooting"):
                    st.markdown(
                        """
                        - Reconnect eBay in Settings if this is a 401/403 issue.
                        - Confirm your OAuth scopes include `sell.fulfillment`.
                        - Try a shorter date range.
                        - Check Streamlit Cloud logs for the full redacted backend error.
                        """
                    )
                return

    df = st.session_state.get(cache_key, pd.DataFrame())
    meta = st.session_state.get(f"{cache_key}_meta", {})
    loaded_at = st.session_state.get(f"{cache_key}_loaded_at", "Not loaded")

    if df.empty:
        st.info("No eBay orders were returned for this date range.")
        st.caption(f"Last checked: {loaded_at}")
        return

    view = df.copy()

    if search:
        q = search.strip().lower()
        mask = (
            view["Order ID"].astype(str).str.lower().str.contains(q, na=False)
            | view["Buyer"].astype(str).str.lower().str.contains(q, na=False)
            | view["SKU"].astype(str).str.lower().str.contains(q, na=False)
            | view["Title"].astype(str).str.lower().str.contains(q, na=False)
        )
        view = view[mask]

    total_orders = view["Order ID"].nunique() if not view.empty else 0
    total_items = pd.to_numeric(view["Quantity"], errors="coerce").fillna(0).sum()
    total_sales = pd.to_numeric(view["Line Total"], errors="coerce").fillna(0).sum()
    open_orders = view[~view["Order Status"].astype(str).str.upper().isin(["FULFILLED", "CANCELLED"])]["Order ID"].nunique()

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(f"<div class='metric-card'><small>Orders</small><b>{total_orders}</b></div>", unsafe_allow_html=True)
    c2.markdown(f"<div class='metric-card'><small>Items Sold</small><b>{int(total_items)}</b></div>", unsafe_allow_html=True)
    c3.markdown(f"<div class='metric-card'><small>Sales</small><b>{_currency(total_sales)}</b></div>", unsafe_allow_html=True)
    c4.markdown(f"<div class='metric-card'><small>Open Orders</small><b>{open_orders}</b></div>", unsafe_allow_html=True)

    st.caption(f"Last synced: {loaded_at} · Returned rows: {len(view)} · API order count: {meta.get('api_total', '—')}")

    display_cols = [
        "Created",
        "Order ID",
        "Buyer",
        "Order Status",
        "Payment Status",
        "SKU",
        "Title",
        "Quantity",
        "Line Total",
        "Ship By",
        "Tracking",
        "Shipping City",
        "Shipping State",
    ]

    show = view[[col for col in display_cols if col in view.columns]].copy()
    if "Line Total" in show.columns:
        show["Line Total"] = show["Line Total"].apply(_currency)

    st.dataframe(show, use_container_width=True, hide_index=True)

    csv = view.drop(columns=["Raw Order", "_created_dt"], errors="ignore").to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download orders CSV",
        data=csv,
        file_name=_download_filename(start_date, end_date),
        mime="text/csv",
        use_container_width=True,
    )

    with st.expander("Order inspector"):
        if view.empty:
            st.info("No order selected.")
        else:
            order_ids = sorted([x for x in view["Order ID"].dropna().unique().tolist() if x])
            selected = st.selectbox("Select order", order_ids)
            selected_rows = view[view["Order ID"] == selected]
            st.dataframe(
                selected_rows[[c for c in display_cols if c in selected_rows.columns]],
                use_container_width=True,
                hide_index=True,
            )

            # Keep raw JSON hidden behind an explicit debug checkbox.
            if st.checkbox("Show raw selected order JSON for debugging"):
                raw = selected_rows.iloc[0].get("Raw Order", {})
                st.json(raw)
