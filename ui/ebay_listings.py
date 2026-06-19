"""
ebay_listings.py — Fetches the current user's LIVE eBay inventory/offers
directly from eBay (not from our database), paginated, with no item cap.

Used by the "My Store" tab so sellers can see what's already listed and
pull any of them back into the draft editor to make changes.
"""

import re
import requests
import streamlit as st
from urllib.parse import quote

from core.ebay_account_store import get_valid_ebay_access_token


def _owner_name() -> str:
    client_name = st.session_state.get("client_name") or ""
    role        = st.session_state.get("role") or ""
    if client_name:
        return client_name.strip()
    if role == "ceo":
        return "ceo"
    return "default"


def _resolve_env(account: dict) -> tuple[str, str]:
    env      = account.get("environment", "production")
    api_base = "https://api.sandbox.ebay.com" if env != "production" else "https://api.ebay.com"
    return env, api_base




def _valid_ebay_sku(sku: str) -> bool:
    return bool(sku) and len(str(sku)) <= 50 and re.fullmatch(r"[A-Za-z0-9]+", str(sku)) is not None

def _safe_title(text: str) -> str:
    text = re.sub(r"(?s)<[^>]+>", " ", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()

def fetch_my_listings(page: int = 1, page_size: int = 25) -> dict:
    """
    Fetches the current owner's live eBay offers (their actual store listings),
    paginated using eBay's own offset/limit. No cap on total pages — eBay
    sellers commonly have thousands of listings and this will page through
    all of them.

    Returns:
        {
          "success": bool,
          "items": [ { sku, offerId, listingId, title, price, quantity,
                       status, listingUrl, imageUrl }, ... ],
          "total": int,
          "page": int,
          "page_size": int,
          "total_pages": int,
          "error": str | None,
        }
    """
    owner = _owner_name()

    try:
        access_token, account = get_valid_ebay_access_token(owner)
    except RuntimeError as e:
        return _err(str(e))

    env, api_base = _resolve_env(account)
    marketplace   = account.get("marketplace_id", "EBAY_US")

    hdrs = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
    }

    offset = (page - 1) * page_size

    try:
        resp = requests.get(
            f"{api_base}/sell/inventory/v1/offer",
            headers=hdrs,
            params={"limit": str(page_size), "offset": str(offset)},
            timeout=30,
        )
    except Exception as e:
        return _err(f"Could not reach eBay: {e}")

    if resp.status_code >= 400:
        msg = _safe_text(resp)
        if "25707" in msg or "invalid value for a SKU" in msg.lower():
            return _fetch_inventory_items(api_base, hdrs, env, page, page_size)
        return _err(msg)

    data  = resp.json()
    total = data.get("total", 0)
    offers = data.get("offers", [])

    items = []
    for o in offers:
        items.append({
            "sku":         o.get("sku", ""),
            "offer_id":    o.get("offerId", ""),
            "listing_id":  o.get("listing", {}).get("listingId", ""),
            "title":       _safe_title(o.get("listingDescription") or _title_from_sku(o.get("sku",""))),
            "price":       (o.get("pricingSummary", {}).get("price", {}) or {}).get("value", ""),
            "currency":    (o.get("pricingSummary", {}).get("price", {}) or {}).get("currency", "USD"),
            "quantity":    o.get("availableQuantity", 0),
            "status":      o.get("status", "") or o.get("listing", {}).get("listingStatus", ""),
            "category_id": o.get("categoryId", ""),
        })

    total_pages = max(1, (total + page_size - 1) // page_size)
    listing_id_base = "https://www.ebay.com/itm/" if env == "production" else "https://sandbox.ebay.com/itm/"
    for it in items:
        it["listing_url"] = f"{listing_id_base}{it['listing_id']}" if it["listing_id"] else ""

    return {
        "success": True, "items": items, "total": total,
        "page": page, "page_size": page_size, "total_pages": total_pages,
        "environment": env, "error": None,
    }



def _fetch_inventory_items(api_base: str, hdrs: dict, env: str, page: int, page_size: int) -> dict:
    """Fallback listing reader that uses Inventory Items directly when Offers fails."""
    offset = (max(1, int(page or 1)) - 1) * max(1, int(page_size or 25))
    try:
        resp = requests.get(
            f"{api_base}/sell/inventory/v1/inventory_item",
            headers=hdrs,
            params={"limit": str(page_size), "offset": str(offset)},
            timeout=30,
        )
    except Exception as e:
        return _err(f"Could not reach eBay inventory: {e}")

    if resp.status_code >= 400:
        return _err(_safe_text(resp))

    try:
        data = resp.json()
    except Exception:
        return _err("eBay returned an unreadable inventory response.")

    rows = data.get("inventoryItems", []) or []
    total = int(data.get("total", offset + len(rows)) or 0)
    items = []
    for row in rows:
        sku = str(row.get("sku", "") or "")
        inv = row.get("inventoryItem", {}) or {}
        prod = inv.get("product", {}) or {}
        qty = (inv.get("availability", {}) or {}).get("shipToLocationAvailability", {}).get("quantity", 0)
        title = _safe_title(prod.get("title") or _title_from_sku(sku))
        items.append({
            "sku": sku,
            "offer_id": "",
            "listing_id": "",
            "title": title,
            "price": "",
            "currency": "USD",
            "quantity": qty,
            "status": "INVENTORY",
            "category_id": "",
            "listing_url": "",
            "image_url": (prod.get("imageUrls") or [""])[0] if isinstance(prod.get("imageUrls"), list) else "",
        })

    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "success": True, "items": items, "total": total,
        "page": page, "page_size": page_size, "total_pages": total_pages,
        "environment": env, "error": None,
        "source": "inventory_items",
    }

def fetch_inventory_item(sku: str) -> dict:
    sku = str(sku or "").strip()
    """
    Fetches full inventory item detail (images, description, aspects) for a
    single SKU — used when a seller clicks "Edit" on a live listing so we can
    pull it back into the draft editor with all fields populated.
    """
    owner = _owner_name()
    try:
        access_token, account = get_valid_ebay_access_token(owner)
    except RuntimeError as e:
        return {"success": False, "product": None, "error": str(e)}

    env, api_base = _resolve_env(account)
    marketplace   = account.get("marketplace_id", "EBAY_US")

    hdrs = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
    }

    try:
        resp = requests.get(f"{api_base}/sell/inventory/v1/inventory_item/{quote(sku, safe='')}", headers=hdrs, timeout=30)
    except Exception as e:
        return {"success": False, "product": None, "error": f"Could not reach eBay: {e}"}

    if resp.status_code >= 400:
        return {"success": False, "product": None, "error": _safe_text(resp)}

    data    = resp.json()
    product = data.get("product", {})

    aspects = product.get("aspects", {}) or {}
    specifications = {k: (v[0] if isinstance(v, list) and v else v) for k, v in aspects.items()}

    return {
        "success": True,
        "product": {
            "title":          product.get("title", ""),
            "description":    product.get("description", ""),
            "images":         product.get("imageUrls", []),
            "brand":          product.get("brand", ""),
            "sku":            sku,
            "condition":      data.get("condition", "NEW").replace("_"," ").title(),
            "specifications": specifications,
            "quantity":       data.get("availability", {})
                                   .get("shipToLocationAvailability", {})
                                   .get("quantity", 1),
            "status":         "live",
            "_from_ebay_sku": sku,
        },
        "error": None,
    }


def _title_from_sku(sku: str) -> str:
    return sku.rsplit("-", 1)[0].replace("-", " ") if sku else "Untitled Listing"

def _err(msg: str) -> dict:
    return {"success": False, "items": [], "total": 0, "page": 1,
            "page_size": 25, "total_pages": 1, "environment": "", "error": msg}

def _safe_text(resp) -> str:
    try:
        data = resp.json()
        errs = data.get("errors", [])
        if errs:
            return "; ".join(f"{e.get('errorId','')}: {e.get('longMessage', e.get('message',''))}" for e in errs[:3])
        return data.get("error_description") or data.get("message") or resp.text[:300]
    except Exception:
        return resp.text[:300]
