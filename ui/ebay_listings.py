"""
ebay_listings.py — Real live seller listings for the connected eBay account.

Primary route:
  Trading API GetMyeBaySelling / ActiveList (the seller's actual live listings).
Fallback:
  Sell Inventory API offers, but ONLY published offers with a real listingId.
No raw inventory placeholders are shown as "live listings".
"""

import re
import xml.etree.ElementTree as ET
import requests
import streamlit as st

from core.ebay_account_store import get_valid_ebay_access_token


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
    if env != "production":
        return env, "https://api.sandbox.ebay.com", "https://api.sandbox.ebay.com/ws/api.dll"
    return env, "https://api.ebay.com", "https://api.ebay.com/ws/api.dll"


def fetch_my_listings(page: int = 1, page_size: int = 25) -> dict:
    owner = _owner_name()
    try:
        access_token, account = get_valid_ebay_access_token(owner)
    except RuntimeError as e:
        return _err(str(e), page_size=page_size)

    env, api_base, trading_url = _resolve_env(account)
    marketplace = account.get("marketplace_id", "EBAY_US")

    # First: real live active seller listings, same source as My eBay ActiveList.
    trading = _fetch_active_trading_listings(
        trading_url=trading_url,
        token=access_token,
        page=page,
        page_size=page_size,
        env=env,
    )
    if trading.get("success") and trading.get("items"):
        return trading

    # Second: Inventory API offers, but only already-published offers with listingId.
    inventory = _fetch_published_inventory_offers(
        api_base=api_base,
        token=access_token,
        marketplace=marketplace,
        page=page,
        page_size=page_size,
        env=env,
    )
    if inventory.get("success") and inventory.get("items"):
        return inventory

    if trading.get("error") and inventory.get("error"):
        return _err(f"Trading API: {trading['error']} | Inventory API: {inventory['error']}", page_size=page_size)

    return {
        "success": True, "items": [], "total": 0, "page": page, "page_size": page_size,
        "total_pages": 1, "environment": env, "error": None,
    }


def _fetch_active_trading_listings(trading_url: str, token: str, page: int, page_size: int, env: str) -> dict:
    headers = {
        "X-EBAY-API-CALL-NAME": "GetMyeBaySelling",
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "1193",
        "X-EBAY-API-IAF-TOKEN": token,
        "Content-Type": "text/xml",
    }
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials/>
  <ActiveList>
    <Include>true</Include>
    <Pagination>
      <EntriesPerPage>{int(page_size)}</EntriesPerPage>
      <PageNumber>{int(page)}</PageNumber>
    </Pagination>
    <Sort>TimeLeft</Sort>
  </ActiveList>
  <DetailLevel>ReturnAll</DetailLevel>
  <ErrorLanguage>en_US</ErrorLanguage>
  <WarningLevel>High</WarningLevel>
</GetMyeBaySellingRequest>"""
    try:
        resp = requests.post(trading_url, headers=headers, data=body.encode("utf-8"), timeout=45)
    except Exception as e:
        return _err(f"Could not reach eBay Trading API: {e}", page_size=page_size)

    if resp.status_code >= 400:
        return _err(_safe_resp(resp), page_size=page_size)

    try:
        root = ET.fromstring(resp.text)
    except Exception as e:
        return _err(f"Could not parse eBay Trading response: {e}", page_size=page_size)

    ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
    ack = _txt(root.find("e:Ack", ns))
    if ack and ack.lower() == "failure":
        errs = []
        for err in root.findall(".//e:Errors", ns):
            msg = _txt(err.find("e:LongMessage", ns)) or _txt(err.find("e:ShortMessage", ns))
            code = _txt(err.find("e:ErrorCode", ns))
            if msg:
                errs.append(f"{code}: {msg}" if code else msg)
        return _err("; ".join(errs) or "Trading API failed", page_size=page_size)

    total = int(_txt(root.find(".//e:ActiveList/e:PaginationResult/e:TotalNumberOfEntries", ns)) or "0")
    total_pages = int(_txt(root.find(".//e:ActiveList/e:PaginationResult/e:TotalNumberOfPages", ns)) or "1")

    items = []
    for item in root.findall(".//e:ActiveList/e:ItemArray/e:Item", ns):
        item_id = _txt(item.find("e:ItemID", ns))
        title = _txt(item.find("e:Title", ns)) or "Untitled Listing"
        price_node = item.find("e:SellingStatus/e:CurrentPrice", ns)
        price = price_node.text if price_node is not None and price_node.text else ""
        currency = price_node.attrib.get("currencyID", "USD") if price_node is not None else "USD"
        qty = _txt(item.find("e:Quantity", ns)) or "0"
        sold = _txt(item.find("e:SellingStatus/e:QuantitySold", ns)) or "0"
        try:
            available = max(0, int(qty) - int(sold))
        except Exception:
            available = qty
        sku = _txt(item.find("e:SKU", ns))
        view_url = _txt(item.find("e:ListingDetails/e:ViewItemURL", ns))
        image = _txt(item.find("e:PictureDetails/e:GalleryURL", ns)) or _txt(item.find("e:PictureDetails/e:PictureURL", ns))
        items.append({
            "sku": sku,
            "offer_id": "",
            "listing_id": item_id,
            "title": title,
            "price": price,
            "currency": currency,
            "quantity": available,
            "status": "ACTIVE",
            "category_id": _txt(item.find("e:PrimaryCategory/e:CategoryID", ns)),
            "listing_url": view_url or (f"https://www.ebay.com/itm/{item_id}" if env == "production" else f"https://sandbox.ebay.com/itm/{item_id}"),
            "image_url": image,
            "source": "TRADING_ACTIVE_LIST",
        })

    return {
        "success": True, "items": items, "total": total, "page": page, "page_size": page_size,
        "total_pages": max(1, total_pages), "environment": env, "error": None,
    }


def _fetch_published_inventory_offers(api_base: str, token: str, marketplace: str, page: int, page_size: int, env: str) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
    }
    offset = (page - 1) * page_size
    try:
        resp = requests.get(
            f"{api_base}/sell/inventory/v1/offer",
            headers=headers,
            params={"limit": str(page_size), "offset": str(offset)},
            timeout=30,
        )
    except Exception as e:
        return _err(f"Could not reach eBay Inventory API: {e}", page_size=page_size)

    if resp.status_code >= 400:
        return _err(_safe_resp(resp), page_size=page_size)

    data = resp.json()
    offers = data.get("offers", []) or []
    items = []
    listing_base = "https://www.ebay.com/itm/" if env == "production" else "https://sandbox.ebay.com/itm/"

    for o in offers:
        listing_id = (o.get("listing") or {}).get("listingId", "")
        status = o.get("status", "") or (o.get("listing") or {}).get("listingStatus", "")
        if not listing_id:
            continue
        title = _safe_title(o.get("listingDescription") or o.get("sku") or "Untitled Listing")
        items.append({
            "sku": o.get("sku", ""),
            "offer_id": o.get("offerId", ""),
            "listing_id": listing_id,
            "title": title,
            "price": ((o.get("pricingSummary") or {}).get("price") or {}).get("value", ""),
            "currency": ((o.get("pricingSummary") or {}).get("price") or {}).get("currency", "USD"),
            "quantity": o.get("availableQuantity", 0),
            "status": status or "PUBLISHED",
            "category_id": o.get("categoryId", ""),
            "listing_url": f"{listing_base}{listing_id}",
            "image_url": "",
            "source": "INVENTORY_PUBLISHED_OFFER",
        })

    total = len(items) if not data.get("total") else int(data.get("total") or len(items))
    return {
        "success": True, "items": items, "total": total, "page": page, "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size), "environment": env, "error": None,
    }


def fetch_inventory_item(sku: str, listing_id: str = "") -> dict:
    """
    Load a live listing into the editor.
    Inventory API is used for safe SKUs. Trading API GetItem is used for older
    live listings whose SKU is blank/unsafe for Inventory API.
    """
    sku = str(sku or "").strip()
    listing_id = str(listing_id or "").strip()
    owner = _owner_name()
    try:
        access_token, account = get_valid_ebay_access_token(owner)
    except RuntimeError as e:
        return {"success": False, "product": None, "error": str(e)}

    env, api_base, trading_url = _resolve_env(account)
    marketplace = account.get("marketplace_id", "EBAY_US")

    if sku and len(sku) <= 50 and re.fullmatch(r"[A-Za-z0-9]+", sku) is not None:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Accept-Language": "en-US", "X-EBAY-C-MARKETPLACE-ID": marketplace}
        try:
            resp = requests.get(f"{api_base}/sell/inventory/v1/inventory_item/{sku}", headers=headers, timeout=30)
        except Exception as e:
            return {"success": False, "product": None, "error": f"Could not reach eBay: {e}"}
        if resp.status_code < 400:
            data = resp.json(); product = data.get("product", {}) or {}; aspects = product.get("aspects", {}) or {}
            specifications = {k: (v[0] if isinstance(v, list) and v else v) for k, v in aspects.items()}
            return {"success": True, "product": {"title": product.get("title", ""), "description": product.get("description", ""), "images": product.get("imageUrls", []), "brand": product.get("brand", ""), "sku": sku, "condition": data.get("condition", "NEW").replace("_", " ").title(), "specifications": specifications, "quantity": data.get("availability", {}).get("shipToLocationAvailability", {}).get("quantity", 1), "status": "live", "_from_ebay_sku": sku, "ebay_listing_id": listing_id}, "error": None}
        if not listing_id:
            return {"success": False, "product": None, "error": _safe_resp(resp)}

    if not listing_id:
        return {"success": False, "product": None, "error": "This live listing has no Inventory-safe SKU and no eBay ItemID was available for Trading API fallback."}
    return _fetch_trading_item(trading_url, access_token, listing_id, env)


def _fetch_trading_item(trading_url: str, token: str, listing_id: str, env: str) -> dict:
    headers = {"X-EBAY-API-CALL-NAME": "GetItem", "X-EBAY-API-SITEID": "0", "X-EBAY-API-COMPATIBILITY-LEVEL": "1193", "X-EBAY-API-IAF-TOKEN": token, "Content-Type": "text/xml"}
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials/>
  <ItemID>{listing_id}</ItemID>
  <DetailLevel>ReturnAll</DetailLevel>
  <IncludeItemSpecifics>true</IncludeItemSpecifics>
  <ErrorLanguage>en_US</ErrorLanguage>
  <WarningLevel>High</WarningLevel>
</GetItemRequest>"""
    try:
        resp = requests.post(trading_url, headers=headers, data=body.encode("utf-8"), timeout=45)
    except Exception as e:
        return {"success": False, "product": None, "error": f"Could not reach eBay Trading API: {e}"}
    if resp.status_code >= 400:
        return {"success": False, "product": None, "error": resp.text[:500]}
    try:
        root = ET.fromstring(resp.text)
    except Exception as e:
        return {"success": False, "product": None, "error": f"Could not parse eBay GetItem response: {e}"}
    ns = {"e": "urn:ebay:apis:eBLBaseComponents"}; ack = _txt(root.find("e:Ack", ns)).lower()
    if ack == "failure":
        msgs = []
        for err in root.findall(".//e:Errors", ns):
            code = _txt(err.find("e:ErrorCode", ns)); msg = _txt(err.find("e:LongMessage", ns)) or _txt(err.find("e:ShortMessage", ns))
            if msg: msgs.append(f"{code}: {msg}" if code else msg)
        return {"success": False, "product": None, "error": "; ".join(msgs) or "GetItem failed"}
    item = root.find("e:Item", ns)
    if item is None:
        return {"success": False, "product": None, "error": "eBay GetItem returned no item."}
    title = _txt(item.find("e:Title", ns)); desc = _txt(item.find("e:Description", ns)); old_sku = _txt(item.find("e:SKU", ns))
    price_node = item.find("e:StartPrice", ns) or item.find("e:SellingStatus/e:CurrentPrice", ns)
    price = price_node.text if price_node is not None and price_node.text else ""; qty = _txt(item.find("e:Quantity", ns)) or "1"
    images = [_txt(x) for x in item.findall("e:PictureDetails/e:PictureURL", ns) if _txt(x)]
    category_id = _txt(item.find("e:PrimaryCategory/e:CategoryID", ns)); category_name = _txt(item.find("e:PrimaryCategory/e:CategoryName", ns))
    specifics = {}
    for nv in item.findall(".//e:ItemSpecifics/e:NameValueList", ns):
        name = _txt(nv.find("e:Name", ns)); value = _txt(nv.find("e:Value", ns))
        if name and value: specifics[name] = value
    return {"success": True, "product": {"title": title, "description": desc, "images": images, "brand": specifics.get("Brand", ""), "sku": old_sku, "condition": "New", "specifications": specifics, "quantity": int(qty) if str(qty).isdigit() else 1, "price": price, "category": category_name, "category_id_override": category_id, "category_id_override_name": category_name, "status": "live", "_from_ebay_sku": old_sku, "_edit_via_trading": True, "ebay_listing_id": listing_id, "ebay_listing_url": f"https://www.ebay.com/itm/{listing_id}" if env == "production" else f"https://sandbox.ebay.com/itm/{listing_id}"}, "error": None}

def delete_ebay_listing(listing_id: str) -> dict:
    """
    Ends a live eBay listing using the Trading API's EndFixedPriceItem call.

    Why EndFixedPriceItem and not the Inventory API: the Inventory API has
    no direct "delete a published offer" call that immediately removes the
    item from the live store — withdrawOffer only works on unpublished
    offers. EndFixedPriceItem is eBay's documented, correct way to end a
    LIVE fixed-price listing (which is what this app always creates, since
    upload_to_ebay always uses format=FIXED_PRICE).

    EndingReason "NotAvailable" is the standard reason for a seller-initiated
    removal when the item simply isn't for sale anymore (vs. price/listing
    errors or items being sold elsewhere) and requires no special handling
    for bids, since fixed-price listings have no bidding.

    Returns: {"success": bool, "listing_id": str, "error": str|None}
    """
    listing_id = str(listing_id or "").strip()
    if not listing_id:
        return {"success": False, "listing_id": "", "error": "No listing ID provided."}

    owner = _owner_name()
    try:
        access_token, account = get_valid_ebay_access_token(owner)
    except RuntimeError as e:
        return {"success": False, "listing_id": listing_id, "error": str(e)}

    env, _, trading_url = _resolve_env(account)

    headers = {
        "X-EBAY-API-CALL-NAME": "EndFixedPriceItem",
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "1193",
        "X-EBAY-API-IAF-TOKEN": access_token,
        "Content-Type": "text/xml",
    }
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<EndFixedPriceItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials/>
  <ItemID>{listing_id}</ItemID>
  <EndingReason>NotAvailable</EndingReason>
  <ErrorLanguage>en_US</ErrorLanguage>
  <WarningLevel>High</WarningLevel>
</EndFixedPriceItemRequest>"""

    try:
        resp = requests.post(trading_url, headers=headers, data=body.encode("utf-8"), timeout=30)
    except Exception as e:
        return {"success": False, "listing_id": listing_id, "error": f"Could not reach eBay: {e}"}

    if resp.status_code >= 400:
        return {"success": False, "listing_id": listing_id, "error": _safe_resp(resp)}

    try:
        root = ET.fromstring(resp.text)
    except Exception as e:
        return {"success": False, "listing_id": listing_id, "error": f"Could not parse eBay response: {e}"}

    ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
    ack = _txt(root.find("e:Ack", ns))

    if ack and ack.lower() in ("success", "warning"):
        return {"success": True, "listing_id": listing_id, "error": None}

    errs = []
    for err in root.findall(".//e:Errors", ns):
        msg = _txt(err.find("e:LongMessage", ns)) or _txt(err.find("e:ShortMessage", ns))
        code = _txt(err.find("e:ErrorCode", ns))
        if msg:
            errs.append(f"{code}: {msg}" if code else msg)

    return {"success": False, "listing_id": listing_id, "error": "; ".join(errs) or "Could not end this listing."}


def _txt(node) -> str:
    return node.text.strip() if node is not None and node.text else ""


def _safe_title(text: str) -> str:
    text = re.sub(r"(?is)<[^>]+>", " ", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()[:120]


def _safe_resp(resp) -> str:
    try:
        data = resp.json()
        errs = data.get("errors", [])
        if errs:
            return "; ".join(f"{e.get('errorId','')}: {e.get('longMessage', e.get('message',''))}" for e in errs[:3])
        return data.get("error_description") or data.get("message") or resp.text[:300]
    except Exception:
        return resp.text[:300]


def _err(msg: str, page_size: int = 25) -> dict:
    return {
        "success": False, "items": [], "total": 0, "page": 1,
        "page_size": page_size, "total_pages": 1, "environment": "", "error": msg,
    }
