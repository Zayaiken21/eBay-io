"""
ebay_listings.py — Fetch real live eBay listings for the signed-in connected user.

Uses public Browse seller search first (real active listings buyers can see), then
falls back to Sell Inventory offers only when those offers have a real listingId.
It intentionally does NOT show raw inventory-only SKUs as live store products.
"""

import re
import requests
import streamlit as st

from core.ebay_account_store import get_valid_ebay_access_token


def _owner_name() -> str:
    client_name = st.session_state.get("client_name") or ""
    role = st.session_state.get("role") or ""
    if client_name:
        return client_name.strip()
    if role == "ceo":
        return "ceo"
    return "default"


def _resolve_env(account: dict) -> tuple[str, str]:
    env = account.get("environment", "production")
    api_base = "https://api.sandbox.ebay.com" if env != "production" else "https://api.ebay.com"
    return env, api_base


def _valid_ebay_sku(sku: str) -> bool:
    return bool(sku) and len(str(sku)) <= 50 and re.fullmatch(r"[A-Za-z0-9]+", str(sku)) is not None


def _safe_title(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", str(text or ""))
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_text(resp) -> str:
    try:
        data = resp.json()
        errs = data.get("errors", [])
        if errs:
            return "; ".join(f"{e.get('errorId','')}: {e.get('longMessage', e.get('message',''))}" for e in errs[:3])
        return data.get("error_description") or data.get("message") or resp.text[:300]
    except Exception:
        return resp.text[:300]


def _err(msg: str) -> dict:
    return {"success": False, "items": [], "total": 0, "page": 1,
            "page_size": 25, "total_pages": 1, "environment": "", "error": msg}


def _listing_url(env: str, listing_id: str) -> str:
    if not listing_id:
        return ""
    return ("https://www.ebay.com/itm/" if env == "production" else "https://sandbox.ebay.com/itm/") + str(listing_id)


def _seller_names(account: dict) -> list[str]:
    names = []
    for key in ("ebay_username", "ebay_user_id", "userId", "username"):
        val = str(account.get(key) or "").strip()
        if val and val not in names and val.lower() not in ("ebay account", "connected ebay account"):
            names.append(val)
    return names


def _fetch_browse_seller_items(api_base: str, token: str, marketplace: str, env: str, seller: str, page: int, page_size: int) -> dict | None:
    """Fetch real buyer-visible live listings for a seller using Browse API."""
    if not seller or env != "production":
        return None

    hdrs = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
    }
    offset = (page - 1) * page_size

    # Browse search supports seller filtering. q is intentionally broad so we get the seller's live items.
    filter_value = f"sellers:{{{seller}}},buyingOptions:{{FIXED_PRICE|AUCTION}}"
    attempts = [
        {"q": "*", "filter": filter_value, "limit": str(page_size), "offset": str(offset)},
        {"q": seller, "filter": filter_value, "limit": str(page_size), "offset": str(offset)},
        {"filter": filter_value, "limit": str(page_size), "offset": str(offset)},
    ]

    for params in attempts:
        try:
            r = requests.get(f"{api_base}/buy/browse/v1/item_summary/search", headers=hdrs, params=params, timeout=30)
        except Exception:
            continue
        if r.status_code >= 400:
            continue
        data = r.json() or {}
        rows = data.get("itemSummaries") or []
        items = []
        for row in rows:
            price = row.get("price") or row.get("currentBidPrice") or {}
            seller_info = row.get("seller") or {}
            listing_id = str(row.get("legacyItemId") or row.get("itemId") or "")
            items.append({
                "sku": row.get("sellerItemRevision") or row.get("itemId", ""),
                "offer_id": "",
                "listing_id": listing_id,
                "title": _safe_title(row.get("title") or "Untitled Listing"),
                "price": price.get("value", ""),
                "currency": price.get("currency", "USD"),
                "quantity": "",
                "status": "LIVE",
                "category_id": ((row.get("categories") or [{}])[0] or {}).get("categoryId", ""),
                "listing_url": row.get("itemWebUrl") or _listing_url(env, row.get("legacyItemId", "")),
                "image_url": ((row.get("image") or {}).get("imageUrl") or ""),
                "seller_username": seller_info.get("username") or seller,
            })
        if rows or int(data.get("total", 0) or 0) > 0:
            total = int(data.get("total", len(items)) or 0)
            return {"success": True, "items": items, "total": total, "page": page,
                    "page_size": page_size, "total_pages": max(1, (total + page_size - 1) // page_size),
                    "environment": env, "error": None, "source": "browse"}
    return None


def _fetch_inventory_offers(api_base: str, token: str, marketplace: str, env: str, page: int, page_size: int) -> dict:
    """Fetch Sell Inventory offers, but only return offers that are actually published/listed."""
    hdrs = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
    }
    offset = (page - 1) * page_size
    try:
        resp = requests.get(f"{api_base}/sell/inventory/v1/offer", headers=hdrs,
                            params={"limit": str(page_size), "offset": str(offset)}, timeout=30)
    except Exception as e:
        return _err(f"Could not reach eBay: {e}")
    if resp.status_code >= 400:
        return _err(_safe_text(resp))

    data = resp.json() or {}
    offers = data.get("offers", []) or []
    items = []
    for o in offers:
        listing = o.get("listing") or {}
        listing_id = listing.get("listingId") or o.get("listingId") or ""
        status = (o.get("status") or listing.get("listingStatus") or "").upper()
        # Do not show inventory-only offers as live listings.
        if not listing_id and status not in ("PUBLISHED", "LISTED", "ACTIVE"):
            continue
        title = _safe_title(o.get("listingDescription") or _title_from_sku(o.get("sku", "")))
        price = (o.get("pricingSummary", {}).get("price", {}) or {})
        items.append({
            "sku": o.get("sku", ""),
            "offer_id": o.get("offerId", ""),
            "listing_id": listing_id,
            "title": title or "Untitled Listing",
            "price": price.get("value", ""),
            "currency": price.get("currency", "USD"),
            "quantity": o.get("availableQuantity", ""),
            "status": status or "LIVE",
            "category_id": o.get("categoryId", ""),
            "listing_url": _listing_url(env, listing_id),
            "image_url": "",
        })

    total = len(items)
    return {"success": True, "items": items, "total": total, "page": page,
            "page_size": page_size, "total_pages": max(1, (total + page_size - 1) // page_size),
            "environment": env, "error": None, "source": "inventory_offers"}


def fetch_my_listings(page: int = 1, page_size: int = 25) -> dict:
    owner = _owner_name()
    try:
        access_token, account = get_valid_ebay_access_token(owner)
    except RuntimeError as e:
        return _err(str(e))

    env, api_base = _resolve_env(account)
    marketplace = account.get("marketplace_id", "EBAY_US")

    # First try real buyer-visible listings for the connected seller.
    for seller in _seller_names(account):
        browse = _fetch_browse_seller_items(api_base, access_token, marketplace, env, seller, page, page_size)
        if browse and browse.get("success") and browse.get("items"):
            return browse

    # Fallback to Sell Inventory offers, but only published/listed offers.
    offers = _fetch_inventory_offers(api_base, access_token, marketplace, env, page, page_size)
    if offers.get("success") and offers.get("items"):
        return offers

    return {"success": True, "items": [], "total": 0, "page": page, "page_size": page_size,
            "total_pages": 1, "environment": env, "error": None,
            "source": "none"}


def fetch_inventory_item(sku: str) -> dict:
    sku = str(sku or "").strip()
    if not _valid_ebay_sku(sku):
        return {"success": False, "product": None, "error": "This SKU contains characters this eBay account rejects. Create a new cleaned listing instead."}

    owner = _owner_name()
    try:
        access_token, account = get_valid_ebay_access_token(owner)
    except RuntimeError as e:
        return {"success": False, "product": None, "error": str(e)}

    env, api_base = _resolve_env(account)
    marketplace = account.get("marketplace_id", "EBAY_US")
    hdrs = {"Authorization": f"Bearer {access_token}", "Accept": "application/json",
            "Accept-Language": "en-US", "X-EBAY-C-MARKETPLACE-ID": marketplace}

    try:
        resp = requests.get(f"{api_base}/sell/inventory/v1/inventory_item/{sku}", headers=hdrs, timeout=30)
    except Exception as e:
        return {"success": False, "product": None, "error": f"Could not reach eBay: {e}"}
    if resp.status_code >= 400:
        return {"success": False, "product": None, "error": _safe_text(resp)}

    data = resp.json() or {}
    product = data.get("product", {}) or {}
    aspects = product.get("aspects", {}) or {}
    specifications = {k: (v[0] if isinstance(v, list) and v else v) for k, v in aspects.items()}
    return {"success": True, "product": {
        "title": product.get("title", ""),
        "description": product.get("description", ""),
        "images": product.get("imageUrls", []),
        "brand": product.get("brand", ""),
        "sku": sku,
        "condition": data.get("condition", "NEW").replace("_", " ").title(),
        "specifications": specifications,
        "quantity": data.get("availability", {}).get("shipToLocationAvailability", {}).get("quantity", 1),
        "status": "live",
        "_from_ebay_sku": sku,
    }, "error": None}


def _title_from_sku(sku: str) -> str:
    return sku.rsplit("-", 1)[0].replace("-", " ") if sku else "Untitled Listing"
