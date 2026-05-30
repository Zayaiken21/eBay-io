import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

def _sample_orders() -> pd.DataFrame:
    now = datetime.now()
    return pd.DataFrame([
        {
            "order_id": "EB-1001",
            "order_type": "fulfillment",
            "status": "ready_to_buy",
            "client": "Alpha",
            "item": "Wireless Mouse",
            "supplier": "Walmart",
            "cost": 42.50,
            "sell_price": 60.70,
            "profit": 18.20,
            "priority": "high",
            "tracking": "",
            "created_at": (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M"),
        },
        {
            "order_id": "CP-1002",
            "order_type": "client_purchase",
            "status": "purchased",
            "client": "Beta",
            "item": "Laptop Stand",
            "supplier": "Target",
            "cost": 68.10,
            "sell_price": 93.00,
            "profit": 24.90,
            "priority": "medium",
            "tracking": "9400X",
            "created_at": (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M"),
        },
        {
            "order_id": "EB-1003",
            "order_type": "fulfillment",
            "status": "shipped",
            "client": "Gamma",
            "item": "USB Hub",
            "supplier": "Amazon",
            "cost": 31.00,
            "sell_price": 43.75,
            "profit": 12.75,
            "priority": "low",
            "tracking": "9401Y",
            "created_at": (now - timedelta(hours=2, minutes=15)).strftime("%Y-%m-%d %H:%M"),
        },
        {
            "order_id": "CP-1004",
            "order_type": "client_purchase",
            "status": "problem",
            "client": "Delta",
            "item": "Desk Mat",
            "supplier": "Best Buy",
            "cost": 55.40,
            "sell_price": 52.20,
            "profit": -3.20,
            "priority": "high",
            "tracking": "",
            "created_at": (now - timedelta(hours=1, minutes=20)).strftime("%Y-%m-%d %H:%M"),
        },
        {
            "order_id": "EB-1005",
            "order_type": "fulfillment",
            "status": "delivered",
            "client": "Alpha",
            "item": "Phone Holder",
            "supplier": "Temu",
            "cost": 29.99,
            "sell_price": 44.09,
            "profit": 14.10,
            "priority": "medium",
            "tracking": "9402Z",
            "created_at": (now - timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M"),
        },
    ])

def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    defaults = {
        "order_id": "",
        "order_type": "fulfillment",
        "status": "new",
        "client": "",
        "item": "",
        "supplier": "",
        "cost": 0.0,
        "sell_price": 0.0,
        "profit": 0.0,
        "priority": "medium",
        "tracking": "",
        "created_at": "",
        "channel": "",
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default
    return df

def _status_badge(status: str) -> str:
    colors = {
        "ready_to_buy": "#ffb347",
        "purchased": "#4db6ff",
        "shipped": "#8ce99a",
        "delivered": "#b197fc",
        "problem": "#ff6b6b",
        "new": "#94a3b8",
    }
    return f"<span style='padding:0.35rem 0.65rem;border-radius:999px;background:{colors.get(status,'#777')};color:#111;font-weight:700;font-size:0.8rem;'>{status}</span>"

def render_orders() -> None:
    st.markdown(
        """
        <style>
        .hero-card {
            background: linear-gradient(135deg, #101828 0%, #1d2939 100%);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 24px;
            padding: 1.4rem 1.5rem;
            margin-bottom: 1rem;
        }
        .hero-card h1 { margin: 0; font-size: 2.2rem; color: #f8fbff; }
        .hero-card p { margin: 0.4rem 0 0 0; color: #cbd5e1; }
        .stat-card {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 18px;
            padding: 1rem;
            min-height: 92px;
        }
        .stat-card label {
            font-size: 0.8rem;
            color: #9aa4b2;
            display: block;
            margin-bottom: 0.25rem;
        }
        .stat-card strong {
            font-size: 1.6rem;
            color: #ffffff;
        }
        .panel {
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 18px;
            padding: 1rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="hero-card">
            <h1>Orders Control Center</h1>
            <p>Unified view for eBay fulfillment, client purchases, exceptions, and profit tracking.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "orders_df" not in st.session_state:
        st.session_state.orders_df = _sample_orders()

    df = _ensure_columns(st.session_state.orders_df.copy())

    open_orders = len(df[df["status"].isin(["ready_to_buy", "purchased", "problem"])])
    ebay_fill = len(df[(df["order_type"] == "fulfillment") & (df["order_id"].astype(str).str.startswith("EB-"))])
    client_orders = len(df[df["order_type"] == "client_purchase"])
    total_profit = float(df["profit"].sum()) if "profit" in df.columns else 0.0

    s1, s2, s3, s4 = st.columns(4)
    for col, label, value in [
        (s1, "Open Orders", open_orders),
        (s2, "eBay To Fill", ebay_fill),
        (s3, "Client Orders", client_orders),
        (s4, "Total Profit", f"${total_profit:.2f}"),
    ]:
        with col:
            st.markdown(f"<div class='stat-card'><label>{label}</label><strong>{value}</strong></div>", unsafe_allow_html=True)

    left, right = st.columns([0.28, 0.72], gap="large")

    with left:
        st.markdown("<div class='panel'>", unsafe_allow_html=True)
        st.subheader("Filters")

        order_type = st.multiselect("Order type", sorted(df["order_type"].dropna().unique().tolist()), default=sorted(df["order_type"].dropna().unique().tolist()))
        status = st.multiselect("Status", sorted(df["status"].dropna().unique().tolist()), default=sorted(df["status"].dropna().unique().tolist()))
        priority = st.multiselect("Priority", sorted(df["priority"].dropna().unique().tolist()), default=sorted(df["priority"].dropna().unique().tolist()))
        search = st.text_input("Search", placeholder="Order ID, client, item, supplier...")
        only_problem = st.checkbox("Only problems", value=False)
        only_untracked = st.checkbox("Only untracked", value=False)
        st.markdown("</div>", unsafe_allow_html=True)

    filtered = df[
        df["order_type"].isin(order_type)
        & df["status"].isin(status)
        & df["priority"].isin(priority)
    ].copy()

    if search:
        q = search.lower()
        filtered = filtered[
            filtered["order_id"].astype(str).str.lower().str.contains(q)
            | filtered["client"].astype(str).str.lower().str.contains(q)
            | filtered["item"].astype(str).str.lower().str.contains(q)
            | filtered["supplier"].astype(str).str.lower().str.contains(q)
        ]

    if only_problem:
        filtered = filtered[filtered["status"] == "problem"]

    if only_untracked:
        filtered = filtered[filtered["tracking"].astype(str).str.strip() == ""]

    with right:
        st.markdown("<div class='panel'>", unsafe_allow_html=True)
        st.subheader("Live Orders")
        view = filtered.copy()
        view["status_badge"] = view["status"].apply(_status_badge)
        show_cols = ["order_id", "order_type", "status", "client", "item", "supplier", "cost", "profit", "tracking", "created_at"]
        st.markdown(view[show_cols].to_html(index=False, escape=False), unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    a, b, c = st.columns([0.42, 0.28, 0.30], gap="large")

    with a:
        st.markdown("<div class='panel'>", unsafe_allow_html=True)
        st.subheader("Order Focus")
        if not filtered.empty:
            selected_id = st.selectbox("Select order", filtered["order_id"].tolist(), label_visibility="collapsed")
            selected = filtered[filtered["order_id"] == selected_id].iloc[0]
            st.write(f"**Client:** {selected['client']}")
            st.write(f"**Item:** {selected['item']}")
            st.write(f"**Supplier:** {selected['supplier']}")
            st.write(f"**Tracking:** {selected['tracking'] or 'Pending'}")
            st.markdown(f"**Status:** {_status_badge(selected['status'])}", unsafe_allow_html=True)
        else:
            st.warning("No matching orders.")
        st.markdown("</div>", unsafe_allow_html=True)

    with b:
        st.markdown("<div class='panel'>", unsafe_allow_html=True)
        st.subheader("Actions")
        st.button("Mark Purchased", use_container_width=True)
        st.button("Add Tracking", use_container_width=True)
        st.button("Mark Shipped", use_container_width=True)
        st.button("Mark Problem", use_container_width=True)
        st.button("Archive Order", use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with c:
        st.markdown("<div class='panel'>", unsafe_allow_html=True)
        st.subheader("Insights")
        st.write(f"**Filtered:** {len(filtered)}")
        st.write(f"**Problems:** {len(filtered[filtered['status'] == 'problem'])}")
        st.write(f"**Untracked:** {len(filtered[filtered['tracking'].astype(str).str.strip() == ''])}")
        st.write(f"**Profit:** ${filtered['profit'].sum():.2f}")
        st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("Edit orders table", expanded=False):
        st.data_editor(
            filtered[["order_id", "order_type", "status", "client", "item", "supplier", "cost", "sell_price", "profit", "tracking", "priority"]],
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            key="orders_editor",
        )
        if st.button("Save edited view"):
            st.success("Edited orders captured for testing.")