from __future__ import annotations

import csv
import io
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st


BASE_DIR = Path(__file__).resolve().parent
CSS_PATH = BASE_DIR / "catalog_pro.css"
DB_PATH = BASE_DIR / "catalog_pro.sqlite3"

REQUIRED_PRODUCT_COLUMNS = [
    "supplier",
    "sku",
    "title",
    "category",
    "cost",
    "map_price",
    "shipping_days_min",
    "shipping_days_max",
    "stock",
    "product_url",
    "image_url",
]

SUPPLIER_COLUMNS = [
    "name",
    "type",
    "categories",
    "region",
    "shipping_min_days",
    "shipping_max_days",
    "source_url",
    "notes",
]

SEED_SUPPLIER_SOURCES = [
    ("Faire", "Wholesale marketplace", "Home, gifts, beauty, apparel", "US/EU", 2, 5, "https://www.faire.com", "Marketplace discovery source; verify brand resale terms."),
    ("Tundra", "Wholesale marketplace", "General merchandise", "US", 1, 5, "https://www.tundra.com", "Useful for fast domestic wholesale discovery."),
    ("Mable", "Wholesale marketplace", "Grocery, wellness, household", "US", 2, 5, "https://www.mable.com", "Good for consumable brands; check eBay category restrictions."),
    ("Abound", "Wholesale marketplace", "Boutique, gifts, home", "US", 2, 5, "https://helloabound.com", "Brand discovery source; verify MSRP/MAP."),
    ("Handshake / Shopify Collective", "Supplier network", "DTC brands", "US", 2, 5, "https://www.shopify.com/collective", "Use only approved supplier feeds."),
    ("Syncee", "Supplier directory", "Dropship/wholesale", "US/EU", 2, 7, "https://syncee.co", "Filter to US warehouses for 3-5 day shipping."),
    ("Spocket", "Supplier directory", "Dropship/wholesale", "US/EU", 2, 7, "https://www.spocket.co", "Use US/EU warehouse filters."),
    ("Inventory Source", "Supplier automation", "General wholesale", "US", 1, 5, "https://www.inventorysource.com", "Feed automation; confirm supplier approval."),
    ("Worldwide Brands", "Supplier directory", "Certified wholesalers", "US", 2, 5, "https://www.worldwidebrands.com", "Directory research source."),
    ("SaleHoo", "Supplier directory", "General wholesale", "Global", 3, 7, "https://www.salehoo.com", "Use domestic shipping filters before importing."),
    ("Doba", "Dropship marketplace", "General merchandise", "US", 2, 5, "https://www.doba.com", "Check margins, shipping, return terms."),
    ("Wholesale Central", "Supplier directory", "General wholesale", "US", 1, 5, "https://www.wholesalecentral.com", "Lead source; contact suppliers directly."),
    ("Thomasnet", "Supplier directory", "Industrial, B2B", "US", 2, 5, "https://www.thomasnet.com", "Best for industrial and commercial categories."),
    ("RangeMe", "Brand discovery", "Retail-ready brands", "US", 2, 5, "https://www.rangeme.com", "Discovery source; approval required."),
    ("Bstock", "Liquidation marketplace", "Liquidation", "US", 3, 7, "https://bstock.com", "Use only lots with clear condition and domestic shipping."),
    ("Bulq", "Liquidation marketplace", "General liquidation", "US", 2, 5, "https://www.bulq.com", "Condition varies; avoid risky categories."),
    ("Via Trading", "Liquidation supplier", "General liquidation", "US", 3, 7, "https://www.viatrading.com", "Verify manifest accuracy."),
    ("DollarDays", "Wholesale store", "Bulk general goods", "US", 2, 5, "https://www.dollardays.com", "Bulk wholesale source."),
    ("Kole Imports", "Wholesale store", "General merchandise", "US", 2, 5, "https://www.koleimports.com", "Domestic general merchandise."),
    ("EE Distribution", "Wholesale distributor", "Toys, collectibles", "US", 2, 5, "https://www.eedistribution.com", "Approval and brand restrictions may apply."),
    ("Entertainment Earth Wholesale", "Wholesale distributor", "Toys, collectibles", "US", 2, 5, "https://www.entertainmentearth.com/wholesale", "Check official resale rules."),
    ("S&S Activewear", "Wholesale distributor", "Apparel", "US", 1, 4, "https://www.ssactivewear.com", "Account approval required."),
    ("SanMar", "Wholesale distributor", "Apparel", "US", 1, 4, "https://www.sanmar.com", "Account approval required."),
    ("Faire Beauty", "Wholesale marketplace", "Beauty", "US/EU", 2, 5, "https://www.faire.com/category/Beauty-Wellness", "Check expiration and restricted item rules."),
    ("KeHE", "Distributor", "Natural grocery", "US", 2, 5, "https://www.kehe.com", "Usually retail account/distribution approval."),
    ("UNFI", "Distributor", "Grocery/wellness", "US", 2, 5, "https://www.unfi.com", "Approval required; watch perishable restrictions."),
    ("Faire Home", "Wholesale marketplace", "Home decor", "US/EU", 2, 5, "https://www.faire.com/category/Home-Decor", "Good home goods discovery source."),
    ("Global Sources", "Supplier directory", "Manufacturers", "Global", 5, 14, "https://www.globalsources.com", "Only use domestic-ready inventory for 3-5 days."),
    ("Alibaba US Warehouse", "Supplier marketplace", "General wholesale", "US/Global", 3, 7, "https://www.alibaba.com", "Only import if warehouse and shipping SLA are verified."),
    ("DHgate US Warehouse", "Supplier marketplace", "General merchandise", "US/Global", 3, 7, "https://www.dhgate.com", "High risk for brand/IP; verify authenticity."),
]

CATEGORY_RULES = {
    "Electronics": {"margin": 0.22, "risk": "high", "notes": "Avoid branded accessories unless supplier authorization is clear."},
    "Home & Garden": {"margin": 0.35, "risk": "medium", "notes": "Good evergreen eBay category; verify dimensions and shipping cost."},
    "Health & Beauty": {"margin": 0.40, "risk": "high", "notes": "Avoid restricted claims, expired items, and regulated products."},
    "Clothing": {"margin": 0.38, "risk": "medium", "notes": "Need clean size/color variations and return policy."},
    "Toys & Hobbies": {"margin": 0.30, "risk": "medium", "notes": "Check brand authorization and age warnings."},
    "Business & Industrial": {"margin": 0.32, "risk": "low", "notes": "Strong for less crowded B2B items."},
    "Collectibles": {"margin": 0.28, "risk": "medium", "notes": "Condition and authenticity must be clear."},
    "Pet Supplies": {"margin": 0.34, "risk": "medium", "notes": "Avoid supplements/medical claims."},
}


def _load_css() -> None:
    if CSS_PATH.exists():
        st.markdown(f"<style>{CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                type TEXT,
                categories TEXT,
                region TEXT,
                shipping_min_days INTEGER DEFAULT 0,
                shipping_max_days INTEGER DEFAULT 99,
                source_url TEXT,
                notes TEXT,
                approved INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplier TEXT NOT NULL,
                sku TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT,
                cost REAL DEFAULT 0,
                map_price REAL DEFAULT 0,
                shipping_days_min INTEGER DEFAULT 0,
                shipping_days_max INTEGER DEFAULT 99,
                stock INTEGER DEFAULT 0,
                product_url TEXT,
                image_url TEXT,
                draft_status TEXT DEFAULT 'review',
                profit_estimate REAL DEFAULT 0,
                ebay_title TEXT,
                seo_notes TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(supplier, sku)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_ship ON catalog_items(shipping_days_max, stock)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_category ON catalog_items(category)")


def _seed_suppliers() -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO suppliers
            (name, type, categories, region, shipping_min_days, shipping_max_days, source_url, notes, approved, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            [(*row, now) for row in SEED_SUPPLIER_SOURCES],
        )


def _read_table(table: str) -> pd.DataFrame:
    with _connect() as conn:
        return pd.read_sql_query(f"SELECT * FROM {table} ORDER BY id DESC", conn)


def _clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", str(title)).strip()
    banned = ["free shipping", "cheap", "best", "wow", "100% guaranteed"]
    for word in banned:
        title = re.sub(word, "", title, flags=re.I)
    return title[:80].strip(" -|,")


def _calc_profit(cost: float, sell_price: float) -> float:
    ebay_fee_rate = 0.135
    payment_buffer = 0.30
    return round(float(sell_price) - float(cost) - (float(sell_price) * ebay_fee_rate) - payment_buffer, 2)


def _import_products(df: pd.DataFrame) -> tuple[int, list[str]]:
    missing = [c for c in REQUIRED_PRODUCT_COLUMNS if c not in df.columns]
    if missing:
        return 0, ["Missing columns: " + ", ".join(missing)]

    errors: list[str] = []
    imported = 0
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for idx, row in df.iterrows():
        try:
            supplier = str(row["supplier"]).strip()
            sku = str(row["sku"]).strip()
            title = _clean_title(row["title"])
            if not supplier or not sku or not title:
                raise ValueError("supplier, sku, and title are required")
            cost = float(row.get("cost", 0) or 0)
            price = float(row.get("map_price", 0) or 0)
            profit = _calc_profit(cost, price)
            ebay_title = _clean_title(title)
            seo_notes = "Title cleaned, risky hype removed, margin estimated. Review category leaf ID before publish."
            rows.append((
                supplier, sku, title, str(row.get("category", "")).strip(), cost, price,
                int(float(row.get("shipping_days_min", 0) or 0)), int(float(row.get("shipping_days_max", 99) or 99)),
                int(float(row.get("stock", 0) or 0)), str(row.get("product_url", "")).strip(),
                str(row.get("image_url", "")).strip(), profit, ebay_title, seo_notes, now,
            ))
        except Exception as exc:
            errors.append(f"Row {idx + 2}: {exc}")

    with _connect() as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO catalog_items
                (supplier, sku, title, category, cost, map_price, shipping_days_min, shipping_days_max,
                 stock, product_url, image_url, profit_estimate, ebay_title, seo_notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(supplier, sku) DO UPDATE SET
                    title=excluded.title,
                    category=excluded.category,
                    cost=excluded.cost,
                    map_price=excluded.map_price,
                    shipping_days_min=excluded.shipping_days_min,
                    shipping_days_max=excluded.shipping_days_max,
                    stock=excluded.stock,
                    product_url=excluded.product_url,
                    image_url=excluded.image_url,
                    profit_estimate=excluded.profit_estimate,
                    ebay_title=excluded.ebay_title,
                    seo_notes=excluded.seo_notes
                """,
                row,
            )
            imported += 1
    return imported, errors


def _template_csv(columns: Iterable[str]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    writer.writerow(["Example Supplier", "EX-001", "Premium Storage Organizer", "Home & Garden", "8.50", "19.99", "2", "4", "120", "https://example.com/item", "https://example.com/image.jpg"])
    return output.getvalue().encode("utf-8")


def _draft_export(df: pd.DataFrame) -> bytes:
    if df.empty:
        return b""
    out = pd.DataFrame({
        "Action": "Draft",
        "Custom label (SKU)": df["supplier"].astype(str).str.upper().str[:12] + "-" + df["sku"].astype(str),
        "Title": df["ebay_title"].fillna(df["title"]).map(_clean_title),
        "Category name": df["category"],
        "Quantity": df["stock"],
        "Start price": df["map_price"],
        "Condition": "New",
        "Description": df["seo_notes"].fillna("") + "\nSupplier: " + df["supplier"].astype(str),
        "PicURL": df["image_url"],
        "ShippingProfileName": "3-5 Day Shipping",
        "ReturnProfileName": "30 Day Returns",
        "PaymentProfileName": "eBay Managed Payments",
        "Supplier URL": df["product_url"],
        "Estimated profit": df["profit_estimate"],
    })
    return out.to_csv(index=False).encode("utf-8")


def render_catalog_pro() -> None:
    _init_db()
    _seed_suppliers()
    _load_css()

    st.markdown(
        """
        <section class="catalog-hero">
            <div>
                <span class="catalog-pill">eBay IO • Catalog-Pro</span>
                <h1>Wholesale Catalog Engine</h1>
                <p>Import supplier feeds, filter to 3–5 day shipping, clean SEO titles, and push winners into eBay-ready draft exports.</p>
            </div>
            <div class="catalog-hero-card">
                <b>Goal</b><br>
                Build a supplier database that can scale past 100,000 approved companies without fake listings or unsafe category uploads.
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )

    suppliers = _read_table("suppliers")
    items = _read_table("catalog_items")

    fast_items = items[(items["shipping_days_max"] <= 5) & (items["stock"] > 0)] if not items.empty else items
    approved_suppliers = suppliers[suppliers["approved"] == 1] if not suppliers.empty else suppliers

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Supplier sources", f"{len(suppliers):,}")
    c2.metric("Approved suppliers", f"{len(approved_suppliers):,}")
    c3.metric("Catalog items", f"{len(items):,}")
    c4.metric("3–5 day ready", f"{len(fast_items):,}")

    tab_sources, tab_import, tab_catalog, tab_export, tab_rules = st.tabs([
        "Supplier Sources", "Import Products", "Category-Pro Drafts", "Export / Push Queue", "Rules"
    ])

    with tab_sources:
        st.markdown("### Supplier discovery list")
        st.caption("These are real discovery sources/directories. Approve individual suppliers only after checking resale terms, shipping SLA, returns, and brand/IP risk.")
        region = st.multiselect("Region", sorted(suppliers["region"].dropna().unique().tolist())) if not suppliers.empty else []
        max_ship = st.slider("Max shipping days", 1, 14, 5)
        view = suppliers.copy()
        if region:
            view = view[view["region"].isin(region)]
        view = view[view["shipping_max_days"] <= max_ship]
        st.dataframe(view[SUPPLIER_COLUMNS + ["approved"]], use_container_width=True, hide_index=True)

        with st.form("manual_supplier_form"):
            st.markdown("#### Add approved supplier")
            name = st.text_input("Supplier/company name")
            typ = st.text_input("Type", "Wholesale supplier")
            cats = st.text_input("Categories")
            reg = st.text_input("Region", "US")
            mn, mx = st.columns(2)
            min_days = mn.number_input("Min ship days", min_value=0, value=2)
            max_days = mx.number_input("Max ship days", min_value=0, value=5)
            url = st.text_input("Supplier URL")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Add supplier")
            if submitted and name.strip():
                with _connect() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO suppliers
                        (name, type, categories, region, shipping_min_days, shipping_max_days, source_url, notes, approved, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (name.strip(), typ, cats, reg, int(min_days), int(max_days), url, notes, datetime.now(timezone.utc).isoformat()),
                    )
                st.success("Supplier saved as approved.")
                st.rerun()

    with tab_import:
        st.markdown("### Import supplier product feed")
        st.caption("Upload CSV files from approved suppliers. The importer upserts by supplier + SKU and keeps only clean, reviewable data.")
        st.download_button("Download product CSV template", _template_csv(REQUIRED_PRODUCT_COLUMNS), "catalog_product_template.csv", "text/csv")
        uploaded = st.file_uploader("Upload supplier CSV", type=["csv"])
        if uploaded:
            df = pd.read_csv(uploaded)
            st.dataframe(df.head(50), use_container_width=True, hide_index=True)
            if st.button("Import / update catalog items", type="primary"):
                count, errors = _import_products(df)
                st.success(f"Imported or updated {count:,} items.")
                if errors:
                    st.warning("Some rows were skipped:\n" + "\n".join(errors[:20]))
                st.rerun()

    with tab_catalog:
        st.markdown("### Category-Pro draft builder")
        if items.empty:
            st.info("Import products first. Use the template in Import Products.")
        else:
            categories = sorted([c for c in items["category"].dropna().unique().tolist() if str(c).strip()])
            selected_cats = st.multiselect("Category", categories)
            min_profit = st.number_input("Minimum estimated profit", value=2.00, step=0.50)
            only_fast = st.toggle("Only 3–5 day shipping items", value=True)
            filtered = items.copy()
            if selected_cats:
                filtered = filtered[filtered["category"].isin(selected_cats)]
            if only_fast:
                filtered = filtered[filtered["shipping_days_max"] <= 5]
            filtered = filtered[(filtered["stock"] > 0) & (filtered["profit_estimate"] >= min_profit)]
            st.dataframe(filtered[["supplier", "sku", "ebay_title", "category", "map_price", "profit_estimate", "shipping_days_max", "stock", "draft_status"]], use_container_width=True, hide_index=True)

            ids = st.multiselect("Select item IDs to mark ready", filtered["id"].tolist())
            if st.button("Move selected to draft queue") and ids:
                with _connect() as conn:
                    conn.executemany("UPDATE catalog_items SET draft_status='ready_for_ebay' WHERE id=?", [(int(i),) for i in ids])
                st.success("Selected items moved to the eBay draft queue.")
                st.rerun()

    with tab_export:
        st.markdown("### Export / push queue")
        queue = items[items["draft_status"] == "ready_for_ebay"] if not items.empty else items
        st.caption("Use this CSV as the safe bridge into your existing eBay draft/publish pipeline. Final publish still needs category leaf ID, policies, aspects, and inventory-safe SKU validation.")
        st.dataframe(queue[["id", "supplier", "sku", "ebay_title", "category", "map_price", "profit_estimate", "shipping_days_max", "stock"]] if not queue.empty else queue, use_container_width=True, hide_index=True)
        st.download_button("Export eBay draft CSV", _draft_export(queue), "ebay_category_pro_drafts.csv", "text/csv", disabled=queue.empty)
        st.info("Next integration step: connect this queue to your existing eBay Inventory API/Sell Feed module after category leaf ID resolution and business policy validation pass.")

    with tab_rules:
        st.markdown("### Category-Pro rules")
        rules_df = pd.DataFrame([
            {"category": k, **v} for k, v in CATEGORY_RULES.items()
        ])
        st.dataframe(rules_df, use_container_width=True, hide_index=True)
        st.markdown(
            """
            <div class="catalog-note">
                <b>Production guardrails:</b> no fake supplier names, no fake stock, no non-leaf eBay categories, no restricted products, no brand/IP-risk items, and no publish until shipping, returns, payment policy, SKU, category aspects, image URL, and profit checks pass.
            </div>
            """,
            unsafe_allow_html=True,
        )
