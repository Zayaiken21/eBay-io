"""
ebay_listings.py — Fetches the current signed-in user's REAL active eBay listings.

Primary route: Trading API GetMyeBaySelling ActiveList. This is the correct
route for listings already live on the seller's eBay account, including legacy
Seller Hub listings that were not created by the Inventory API.

Fallback route: Sell Inventory offers, but only offers with a real listingId.
Raw inventory-only placeholders are intentionally not shown as live listings.
"""

import re
import math
import requests
import xml.etree.ElementTree as ET
import streamlit as st

from core.ebay_account_store import get_valid_ebay_access_token


TRADING_COMPAT_LEVEL = "1231"


def _owner_name() -> str:
    client_name = st.session_state.get("client_name") or ""
    role        = st.session_state.get("role") or ""
    if client_name:
        return client_name.strip()
    if role == "ceo":
        return "ceo"
    return "default"


def _resolve_env(account: dict) -> tuple[str, str, str]:
    env = account.get("environment", "production")
    api_base = "https://api.sandbox.ebay.com" if env != "production" else "https://api.ebay.com"
    trading_url = "https://api.sandbox.ebay.com/ws/api.dll" if env != "production" else "https://api.ebay.com/ws/api.dll"
    return env, api_base, trading_url


def _safe_title(text: str) -> str:
    text = re.sub(r"(?s)<[^>]+>", " ", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _text(node, path: str, ns: dict) -> str:
    found = node.find(path, ns)
    return (found.text or "").strip() if found is not None and found.text is not None else ""


def _int_text(node, path: str, ns: dict, default: int = 0) -> int:
    try:
        return int(float(_text(node, path, ns) or default))
    except Exception:
        return default


def _trading_headers(call_name: str, token: str) -> dict:
    return {
        "Content-Type": "text/xml",
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": TRADING_COMPAT_LEVEL,
        "X-EBAY-API-IAF-TOKEN": token,
    }


def _get_my_ebay_selling(access_token: str, trading_url: str, page: int, page_size: int) -> dict:
    body = f'''<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <DetailLevel>ReturnAll</DetailLevel>
  <ActiveList>
    <Include>true</Include>
    <Pagination>
      <EntriesPerPage>{int(page_size)}</EntriesPerPage>
      <PageNumber>{int(page)}</PageNumber>
    </Pagination>
  </ActiveList>
</GetMyeBaySellingRequest>'''
    resp = requests.post(trading_url, headers=_trading_headers("GetMyeBaySelling", access_token), data=body.encode("utf-8"), timeout=45)
    if resp.status_code >= 400:
        return {"success": False, "error": f"Trading API failed ({resp.status_code}): {resp.text[:500]}", "items": [], "total": 0}

    root = ET.fromstring(resp.text)
    ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
    ack = _text(root, "e:Ack", ns)
    if ack and ack.lower() not in ("success", "warning"):
        errors = []
        for err in root.findall("e:Errors", ns):
            code = _text(err, "e:ErrorCode", ns)
            msg = _text(err, "e:LongMessage", ns) or _text(err, "e:ShortMessage", ns)
            errors.append(f"{code}: {msg}" if code else msg)
        return {"success": False, "error": "; ".join(errors) or "Trading API did not return active listings.", "items": [], "total": 0}

    active = root.find("e:ActiveList", ns)
    if active is None:
        return {"success": True, "items": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 1, "environment": "", "error": None}

    total = _int_text(active, "e:PaginationResult/e:TotalNumberOfEntries", ns, 0)
    total_pages = max(1, _int_text(active, "e:PaginationResult/e:TotalNumberOfPages", ns, math.ceil(total / max(1, page_size)) if total else 1))
    items = []
    for item in active.findall("e:ItemArray/e:Item", ns):
        listing_id = _text(item, "e:ItemID", ns)
        title = _safe_title(_text(item, "e:Title", ns)) or "Untitled Listing"
        price = _text(item, "e:SellingStatus/e:CurrentPrice", ns) or _text(item, "e:StartPrice", ns)
        currency = "USD"
        price_node = item.find("e:SellingStatus/e:CurrentPrice", ns)
        if price_node is None:
            price_node = item.find("e:StartPrice", ns)
        if price_node is not None:
            currency = price_node.attrib.get("currencyID", "USD")
        qty = _int_text(item, "e:QuantityAvailable", ns, 0)
        if qty == 0:
            qty = max(0, _int_text(item, "e:Quantity", ns, 0) - _int_text(item, "e:SellingStatus/e:QuantitySold", ns, 0))
        url = _text(item, "e:ListingDetails/e:ViewItemURL", ns)
        sku = _text(item, "e:SKU", ns) or listing_id
        image = _text(item, "e:PictureDetails/e:GalleryURL", ns) or _text(item, "e:PictureDetails/e:PictureURL", ns)
        status = _text(item, "e:SellingStatus/e:ListingStatus", ns) or "Active"
        items.append({
            "sku": sku,
            "offer_id": "",
            "listing_id": listing_id,
            "title": title,
            "price": price,
            "currency": currency,
            "quantity": qty,
            "status": status,
            "category_id": _text(item, "e:PrimaryCategory/e:CategoryID", ns),
            "listing_url": url or (f"https://www.ebay.com/itm/{listing_id}" if listing_id else ""),
            "image_url": image,
            "source": "Trading ActiveList",
        })
    return {"success": True, "items": items, "total": total or len(items), "page": page, "page_size": page_size, "total_pages": total_pages, "environment": "", "error": None}


def _fetch_inventory_offers(api_base: str, access_token: str, marketplace: str, env: str, page: int, page_size: int) -> dict:
    hdrs = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
    }
    offset = (page - 1) * page_size
    try:
        resp = requests.get(f"{api_base}/sell/inventory/v1/offer", headers=hdrs, params={"limit": str(page_size), "offset": str(offset)}, timeout=30)
    except Exception as e:
        return _err(f"Could not reach eBay: {e}")
    if resp.status_code >= 400:
        return _err(_safe_text(resp))
    data = resp.json()
    offers = data.get("offers", [])
    items = []
    listing_id_base = "https://www.ebay.com/itm/" if env == "production" else "https://sandbox.ebay.com/itm/"
    for o in offers:
        listing_id = (o.get("listing", {}) or {}).get("listingId", "")
        if not listing_id:
            continue
        title = _safe_title(o.get("listingDescription") or o.get("sku") or "Untitled Listing")
        price_obj = (o.get("pricingSummary", {}).get("price", {}) or {})
        items.append({
            "sku": o.get("sku", ""),
            "offer_id": o.get("offerId", ""),
            "listing_id": listing_id,
            "title": title,
            "price": price_obj.get("value", ""),
            "currency": price_obj.get("currency", "USD"),
            "quantity": o.get("availableQuantity", 0),
            "status": o.get("status", "") or (o.get("listing", {}) or {}).get("listingStatus", "Active"),
            "category_id": o.get("categoryId", ""),
            "listing_url": f"{listing_id_base}{listing_id}",
            "image_url": "",
            "source": "Inventory Offer",
        })
    total = len(items)
    return {"success": True, "items": items, "total": total, "page": page, "page_size": page_size, "total_pages": max(1, math.ceil(total / max(1, page_size))), "environment": env, "error": None}


def fetch_my_listings(page: int = 1, page_size: int = 25) -> dict:
    owner = _owner_name()
    try:
        access_token, account = get_valid_ebay_access_token(owner)
    except RuntimeError as e:
        return _err(str(e))

    env, api_base, trading_url = _resolve_env(account)
    marketplace = account.get("marketplace_id", "EBAY_US")

    trading = _get_my_ebay_selling(access_token, trading_url, page, page_size)
    if trading.get("success") and trading.get("items"):
        trading["environment"] = env
        return trading

    fallback = _fetch_inventory_offers(api_base, access_token, marketplace, env, page, page_size)
    if fallback.get("success") and fallback.get("items"):
        return fallback

    if trading.get("success"):
        return {"success": True, "items": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 1, "environment": env, "error": None}
    return _err(trading.get("error") or fallback.get("error") or "Could not load active eBay listings.")


def fetch_inventory_item(sku: str) -> dict:
    sku = str(sku or "").strip()
    if not sku:
        return {"success": False, "product": None, "error": "Missing SKU/listing reference."}

    owner = _owner_name()
    try:
        access_token, account = get_valid_ebay_access_token(owner)
    except RuntimeError as e:
        return {"success": False, "product": None, "error": str(e)}

    env, api_base, _ = _resolve_env(account)
    marketplace = account.get("marketplace_id", "EBAY_US")
    hdrs = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
    }

    if not re.fullmatch(r"[A-Za-z0-9]{1,50}", sku):
        return {"success": False, "product": None, "error": "This live listing is a legacy eBay listing, not an Inventory API draft. Open it directly on eBay to edit, or import the product URL as a new cleaned draft."}

    try:
        resp = requests.get(f"{api_base}/sell/inventory/v1/inventory_item/{sku}", headers=hdrs, timeout=30)
    except Exception as e:
        return {"success": False, "product": None, "error": f"Could not reach eBay: {e}"}
    if resp.status_code >= 400:
        return {"success": False, "product": None, "error": _safe_text(resp)}

    data = resp.json()
    product = data.get("product", {}) or {}
    aspects = product.get("aspects", {}) or {}
    specifications = {k: (v[0] if isinstance(v, list) and v else v) for k, v in aspects.items()}
    return {"success": True, "product": {"title": product.get("title", ""), "description": product.get("description", ""), "images": product.get("imageUrls", []), "brand": product.get("brand", ""), "sku": sku, "condition": data.get("condition", "NEW").replace("_", " ").title(), "specifications": specifications, "quantity": data.get("availability", {}).get("shipToLocationAvailability", {}).get("quantity", 1), "status": "live", "_from_ebay_sku": sku}, "error": None}


def _err(msg: str) -> dict:
    return {"success": False, "items": [], "total": 0, "page": 1, "page_size": 25, "total_pages": 1, "environment": "", "error": msg}


def _safe_text(resp) -> str:
    try:
        data = resp.json()
        errs = data.get("errors", [])
        if errs:
            return "; ".join(f"{e.get('errorId','')}: {e.get('longMessage', e.get('message',''))}" for e in errs[:3])
        return data.get("error_description") or data.get("message") or resp.text[:500]
    except Exception:
        return resp.text[:500]
