"""
ebay_uploader.py — Posts listings directly to eBay via the Sell Inventory API.

Auth flow:
  - Reads owner_name from st.session_state (set at login by auth.py)
  - Calls ebay_account_store.get_valid_ebay_access_token(owner_name)
    which auto-refreshes the token via Supabase — never stale
  - No manual token passing required anywhere in the UI
"""

import re
import json
import requests
import streamlit as st

from core.ebay_account_store import (
    get_valid_ebay_access_token,
    get_ebay_api_context,
    call_ebay_api,
    get_latest_ebay_account,
)

# ── Category ID map (eBay leaf category IDs, US marketplace) ─────────────
CATEGORY_MAP = {
    "electronics":   "58058",  "computers":    "58058",  "laptops":    "177",
    "phones":        "15032",  "tablets":      "171485", "cameras":    "625",
    "tv":            "32852",  "audio":        "293",    "video games":"1249",
    "clothing":      "11450",  "mens":         "1059",   "womens":     "15724",
    "shoes":         "63889",  "jewelry":      "281",    "watches":    "14324",
    "handbags":      "169291", "accessories":  "4250",
    "home":          "11700",  "kitchen":      "20625",  "bedding":    "20444",
    "furniture":     "3197",   "decor":        "10033",  "garden":     "159912",
    "tools":         "631",    "hardware":     "11804",  "automotive": "6000",
    "toys":          "220",    "baby":         "2984",   "kids":       "171146",
    "sports":        "888",    "fitness":      "15273",  "outdoor":    "159912",
    "beauty":        "26395",  "health":       "26395",  "vitamins":   "180959",
    "pet":           "1281",   "books":        "267",    "music":      "11233",
    "movies":        "11232",  "art":          "550",    "crafts":     "14339",
    "collectibles":  "1",      "antiques":     "20081",  "other":      "99",
}

def _guess_category_id(category: str) -> str:
    cat = (category or "").lower()
    for key, cid in CATEGORY_MAP.items():
        if key in cat:
            return cid
    return CATEGORY_MAP["other"]

def _sanitize_sku(title: str, draft_id: str) -> str:
    """eBay in this account rejects punctuation, so force A-Z/0-9 only, max 50."""
    raw = f"{title or 'ITEM'}{draft_id or ''}"
    safe = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    return (safe or "ITEM")[:50]



def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", str(text or ""))
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_inventory_description(product: dict) -> str:
    """Inventory API product.description must be plain-ish text, 1-4000 chars."""
    parts = [
        product.get("description", ""),
        " ".join(product.get("features") or []),
        product.get("title", ""),
    ]
    text = _strip_html("\n".join(str(p) for p in parts if p))
    if not text:
        text = f"Quality product: {product.get('title', 'Item')}"
    return text[:3990]


def _safe_listing_description(product: dict) -> str:
    """Offer listingDescription can be richer, but keep it safe and below eBay limits."""
    text = product.get("ebay_html") or product.get("description") or product.get("title") or "Quality item."
    text = str(text).strip()
    if len(text) > 490000:
        text = text[:490000]
    return text or "Quality item."


def _map_condition(condition: str) -> str:
    return {
        "new":                 "NEW",
        "new with tags":       "NEW",
        "new without tags":    "NEW",
        "new with defects":    "NEW_WITH_DEFECTS",
        "pre-owned":           "USED_EXCELLENT",
        "good":                "USED_GOOD",
        "acceptable":          "USED_ACCEPTABLE",
        "for parts":           "FOR_PARTS_OR_NOT_WORKING",
    }.get((condition or "new").lower(), "NEW")

def _build_aspects(product: dict) -> dict:
    aspects = {}
    if product.get("brand"):      aspects["Brand"]           = [product["brand"]]
    if product.get("condition"):  aspects["Condition"]       = [product["condition"]]
    if product.get("weight"):     aspects["Item Weight"]     = [product["weight"]]
    if product.get("dimensions"): aspects["Item Dimensions"] = [product["dimensions"]]
    for k, v in (product.get("specifications") or {}).items():
        ck, cv = k.strip()[:65], str(v).strip()[:65]
        if ck and cv and ck not in aspects:
            aspects[ck] = [cv]
        if len(aspects) >= 20:
            break
    return aspects

def _get_owner_name() -> str:
    """
    Resolve owner_name from session state — matches session.py exactly.

    session.py sets:
      st.session_state.client_name  — set on client login (validate_client_token returns {"client_name": ...})
      st.session_state.role         — "ceo" | "client"
      st.session_state.authenticated — bool

    For CEO login there is no client_name, so we fall back to "ceo".
    ebay_account_store saves rows under owner_name=client_name (or "ceo").
    """
    role        = st.session_state.get("role") or ""
    client_name = st.session_state.get("client_name") or ""

    if client_name:
        return client_name.strip()
    if role == "ceo":
        return "ceo"
    return "default"

def _get_existing_offer(api_base: str, token: str, sku: str, marketplace_id: str) -> str:
    """Return existing offerId for this SKU, or empty string."""
    try:
        resp = requests.get(
            f"{api_base}/sell/inventory/v1/offer",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept-Language": "en-US",
                "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
            },
            params={"sku": sku},
            timeout=15,
        )
        if resp.status_code == 200:
            offers = resp.json().get("offers", [])
            if offers:
                return offers[0].get("offerId", "")
    except Exception:
        pass
    return ""


def get_account_info() -> dict | None:
    """
    Returns the connected eBay account info for the current logged-in user.
    Uses client_name from session (set by session.py) as owner_name.
    Does NOT refresh the token — safe to call on every render.
    Raises on Supabase errors so the UI can show a useful message.
    """
    owner = _get_owner_name()
    account = get_latest_ebay_account(owner)
    if account:
        # Attach resolved owner so callers can debug
        account["_resolved_owner"] = owner
    return account




def _safe_location_key(value: str = "") -> str:
    """merchantLocationKey must be stable and <=50 chars. Keep it simple."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(value or "").upper())
    return (cleaned or "MAINWAREHOUSE")[:50]


def _extract_address_from_identity(profile: dict | None) -> dict:
    """Return best address eBay Identity exposes for this user, if available."""
    profile = profile or {}
    # Business accounts commonly expose businessAccount.address. Individual accounts may expose registrationAddress only when scope/approval allows it.
    candidates = [
        ((profile.get("businessAccount") or {}).get("address") or {}),
        ((profile.get("individualAccount") or {}).get("registrationAddress") or {}),
        (profile.get("registrationAddress") or {}),
        (profile.get("address") or {}),
    ]
    for src in candidates:
        if not isinstance(src, dict):
            continue
        out = {
            "addressLine1": src.get("addressLine1") or src.get("address_line_1") or src.get("street1") or "",
            "addressLine2": src.get("addressLine2") or src.get("address_line_2") or src.get("street2") or "",
            "city": src.get("city") or "",
            "stateOrProvince": src.get("stateOrProvince") or src.get("state") or src.get("province") or "",
            "postalCode": src.get("postalCode") or src.get("postal_code") or src.get("zip") or "",
            "country": src.get("country") or src.get("countryCode") or "US",
        }
        # eBay warehouse locations accept postalCode+country OR city+state+country.
        if (out["postalCode"] and out["country"]) or (out["city"] and out["stateOrProvince"] and out["country"]):
            return {k: v for k, v in out.items() if v}
    return {}


def _fetch_identity_address(api_base: str, access_token: str) -> dict:
    try:
        r = requests.get(
            f"{api_base}/commerce/identity/v1/user/",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=20,
        )
        if r.status_code < 400:
            return _extract_address_from_identity(r.json())
    except Exception:
        pass
    return {}


def _normalize_location_row(row: dict) -> dict:
    if not isinstance(row, dict):
        return {}
    key = row.get("merchantLocationKey") or row.get("merchant_location_key") or row.get("locationId") or row.get("id") or ""
    name = row.get("name") or row.get("locationName") or key
    status = row.get("merchantLocationStatus") or row.get("status") or ""
    address = ((row.get("location") or {}).get("address") or row.get("address") or {})
    label_bits = [str(name or key)]
    city = address.get("city") if isinstance(address, dict) else ""
    state = address.get("stateOrProvince") if isinstance(address, dict) else ""
    postal = address.get("postalCode") if isinstance(address, dict) else ""
    if city or state or postal:
        label_bits.append(" ".join(x for x in [city, state, postal] if x))
    label = " — ".join(x for x in label_bits if x)
    return {"key": str(key), "name": str(name or key), "label": label, "status": str(status), "address": address if isinstance(address, dict) else {}}


def _get_inventory_locations(api_base: str, access_token: str, marketplace: str = "EBAY_US") -> list[dict]:
    hdrs = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
    }
    locations = []
    offset, limit = 0, 100
    while True:
        r = requests.get(
            f"{api_base}/sell/inventory/v1/location",
            headers=hdrs,
            params={"limit": str(limit), "offset": str(offset)},
            timeout=25,
        )
        if r.status_code >= 400:
            break
        data = r.json() or {}
        rows = data.get("locations") or data.get("inventoryLocations") or data.get("location") or []
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            item = _normalize_location_row(row)
            if item.get("key"):
                locations.append(item)
        total = int(data.get("total", offset + len(rows)) or 0)
        offset += limit
        if not rows or offset >= total:
            break
    return locations


def _create_inventory_location(api_base: str, access_token: str, marketplace: str, address: dict, key: str = "MAINWAREHOUSE") -> tuple[str, str | None]:
    """Create/enable a warehouse location and return (key, error)."""
    key = _safe_location_key(key)
    address = {k: v for k, v in (address or {}).items() if v}
    country = address.get("country") or address.get("countryCode") or "US"
    payload_address = {"country": country}
    for src, dst in [
        ("addressLine1", "addressLine1"), ("addressLine2", "addressLine2"),
        ("city", "city"), ("stateOrProvince", "stateOrProvince"), ("postalCode", "postalCode"),
    ]:
        if address.get(src):
            payload_address[dst] = address[src]

    if not ((payload_address.get("postalCode") and payload_address.get("country")) or (payload_address.get("city") and payload_address.get("stateOrProvince") and payload_address.get("country"))):
        return "", "Missing location address. eBay needs at least postal code + country, or city + state + country, to create an inventory warehouse."

    hdrs = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "Content-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
    }
    payload = {
        "name": address.get("name") or "Main Warehouse",
        "location": {"address": payload_address},
        "locationTypes": ["WAREHOUSE"],
        "merchantLocationStatus": "ENABLED",
    }
    # eBay createInventoryLocation is a PUT to /sell/inventory/v1/location/{merchantLocationKey}.
    r = requests.put(
        f"{api_base}/sell/inventory/v1/location/{key}",
        headers=hdrs,
        json=payload,
        timeout=30,
    )
    # Retry POST only for unusual legacy/proxy behavior.
    if r.status_code in (405, 404):
        r = requests.post(
            f"{api_base}/sell/inventory/v1/location/{key}",
            headers=hdrs,
            json=payload,
            timeout=30,
        )
    if r.status_code not in (200, 201, 204):
        msg = _safe_text(r)
        # If it already exists, try to enable and use it.
        if "already" not in msg.lower() and "duplicate" not in msg.lower():
            return "", f"Could not create eBay inventory location: {msg}"
    try:
        requests.post(f"{api_base}/sell/inventory/v1/location/{key}/enable", headers=hdrs, timeout=20)
    except Exception:
        pass
    return key, None


def _resolve_merchant_location_key(api_base: str, access_token: str, marketplace: str, product: dict) -> tuple[str, str | None]:
    """Use a real enabled merchantLocationKey. Create one if seller has none."""
    typed = str(product.get("merchant_location_key") or "").strip()
    locations = _get_inventory_locations(api_base, access_token, marketplace)
    if locations:
        enabled = [x for x in locations if x.get("status", "").upper() in ("", "ENABLED")]
        candidates = enabled or locations
        # If user provided the actual key, use it. Do not match display name like 'New York' unless key matches.
        for loc in candidates:
            if typed and typed == loc.get("key"):
                return loc["key"], None
        return candidates[0]["key"], None

    # No locations exist. Create one from product location fields, then Identity API address.
    address = {
        "addressLine1": product.get("location_address_line1") or product.get("addressLine1") or "",
        "city": product.get("location_city") or product.get("city") or "",
        "stateOrProvince": product.get("location_state") or product.get("stateOrProvince") or "",
        "postalCode": product.get("location_postal_code") or product.get("postalCode") or "",
        "country": product.get("location_country") or product.get("country") or "US",
        "name": product.get("location_name") or "Main Warehouse",
    }
    if not (address.get("postalCode") or (address.get("city") and address.get("stateOrProvince"))):
        auto_addr = _fetch_identity_address(api_base, access_token)
        if auto_addr:
            address.update({k: v for k, v in auto_addr.items() if v})

    # Final automatic fallback: create a basic WAREHOUSE location.
    # Warehouse locations only require postalCode+country OR city+state+country.
    # This prevents offer creation from failing with "Location information not found"
    # when the seller has no existing Inventory API locations.
    if not (address.get("postalCode") or (address.get("city") and address.get("stateOrProvince"))):
        address.update({
            "name": address.get("name") or "Main Warehouse",
            "city": "New York",
            "stateOrProvince": "NY",
            "postalCode": "10001",
            "country": "US",
        })

    typed_clean = _safe_location_key(typed) if typed else ""
    key_seed = product.get("location_key") or typed_clean or "MAINWAREHOUSE"
    return _create_inventory_location(api_base, access_token, marketplace, address, key_seed)


def get_seller_policies() -> dict:
    """
    Fetches fulfillment / payment / return policies and real eBay inventory locations
    for the connected signed-in user. If no inventory location exists, the uploader
    can auto-create one from the seller address/location fields before publishing.
    """
    owner = _get_owner_name()
    result = {"fulfillment": [], "payment": [], "return": [], "locations": [], "identity_address": {}, "error": None}
    try:
        access_token, account = get_valid_ebay_access_token(owner)
        api_base     = account.get("api_base") or "https://api.ebay.com"
        marketplace  = account.get("marketplace_id", "EBAY_US")
        env          = account.get("environment", "production")
        if env != "production":
            api_base = "https://api.sandbox.ebay.com"

        hdrs = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Accept-Language": "en-US",
            "X-EBAY-C-MARKETPLACE-ID": marketplace,
        }

        for policy_type, resp_key, id_field in [
            ("fulfillment", "fulfillmentPolicies", "fulfillmentPolicyId"),
            ("payment",     "paymentPolicies",     "paymentPolicyId"),
            ("return",      "returnPolicies",      "returnPolicyId"),
        ]:
            try:
                r = requests.get(
                    f"{api_base}/sell/account/v1/{policy_type}_policy",
                    headers=hdrs,
                    params={"marketplace_id": marketplace},
                    timeout=15,
                )
                if r.status_code == 200:
                    result[policy_type] = [
                        {"id": p.get(id_field, ""), "name": p.get("name", "")}
                        for p in r.json().get(resp_key, [])
                        if p.get(id_field)
                    ]
            except Exception:
                pass

        result["locations"] = _get_inventory_locations(api_base, access_token, marketplace)
        result["identity_address"] = _fetch_identity_address(api_base, access_token)
    except Exception as e:
        result["error"] = str(e)
    return result

def upload_to_ebay(product: dict) -> dict:
    """
    Full eBay listing pipeline using the connected account from ebay_account_store.

    Steps:
      1. Auto-refresh token via get_valid_ebay_access_token
      2. PUT inventory item  (create/update)
      3. POST or PUT offer
      4. POST publish → returns live listing URL

    Returns:
        {"success": bool, "listing_id": str, "listing_url": str, "sku": str, "error": str|None}
    """
    owner = _get_owner_name()

    # ── 1. Get fresh token (auto-refreshes if expired) ────────────────────
    try:
        access_token, account = get_valid_ebay_access_token(owner)
    except RuntimeError as e:
        return _err(str(e))

    api_base    = account.get("api_base") or "https://api.ebay.com"
    marketplace = account.get("marketplace_id", "EBAY_US")
    env         = account.get("environment", "production")
    if env != "production":
        api_base = "https://api.sandbox.ebay.com"

    hdrs = {
        "Authorization":   f"Bearer {access_token}",
        "Content-Type":    "application/json",
        "Accept":          "application/json",
        "Accept-Language": "en-US",
        "Content-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
    }

    sku   = _sanitize_sku(product.get("sku") or product.get("title", ""), product.get("draft_id", "001"))
    price = product.get("price") or "9.99"
    try:    float(price)
    except: price = "9.99"

    merchant_location_key, location_error = _resolve_merchant_location_key(api_base, access_token, marketplace, product)
    if location_error:
        return _err(location_error)

    # ── 2. Create / update inventory item ─────────────────────────────────
    images    = [u for u in (product.get("images") or []) if u.startswith("http")][:12]
    inventory_desc = _safe_inventory_description(product)
    listing_desc = _safe_listing_description(product)

    inventory_payload = {
        "product": {
            "title":       product.get("title", "")[:80],
            "description": inventory_desc,
            "imageUrls":   images,
            "aspects":     _build_aspects(product),
        },
        "condition": _map_condition(product.get("condition", "New")),
        "availability": {
            "shipToLocationAvailability": {
                "quantity": max(1, int(product.get("quantity", 1)))
            }
        },
    }
    if product.get("brand"):
        inventory_payload["product"]["brand"] = product["brand"]
    inventory_payload["product"]["mpn"] = sku

    inv_resp = requests.put(
        f"{api_base}/sell/inventory/v1/inventory_item/{sku}",
        headers=hdrs,
        json=inventory_payload,
        timeout=30,
    )
    if inv_resp.status_code not in (200, 201, 204):
        return _err(f"Inventory item failed ({inv_resp.status_code}): {_safe_text(inv_resp)}")

    # ── 3. Create or update offer ─────────────────────────────────────────
    offer_payload = {
        "sku":                 sku,
        "marketplaceId":       marketplace,
        "format":              "FIXED_PRICE",
        "availableQuantity":   max(1, int(product.get("quantity", 1))),
        "categoryId":          _guess_category_id(product.get("category", "")),
        "listingDescription":  listing_desc,
        "pricingSummary": {
            "price": {"value": price, "currency": "USD"}
        },
        "listingPolicies": {
            "fulfillmentPolicyId": product.get("fulfillment_policy_id", ""),
            "paymentPolicyId":     product.get("payment_policy_id", ""),
            "returnPolicyId":      product.get("return_policy_id", ""),
        },
        "merchantLocationKey": merchant_location_key,
    }

    # Tax only if values present (sandbox rejects empty tax blocks)
    if product.get("apply_tax"):
        offer_payload["tax"] = {"applyTax": True, "vatPercentage": 0}

    existing_offer_id = _get_existing_offer(api_base, access_token, sku, marketplace)

    if existing_offer_id:
        off_resp = requests.put(
            f"{api_base}/sell/inventory/v1/offer/{existing_offer_id}",
            headers=hdrs, json=offer_payload, timeout=30,
        )
        if off_resp.status_code not in (200, 204):
            return _err(f"Offer update failed ({off_resp.status_code}): {_safe_text(off_resp)}")
        offer_id = existing_offer_id
    else:
        off_resp = requests.post(
            f"{api_base}/sell/inventory/v1/offer",
            headers=hdrs, json=offer_payload, timeout=30,
        )
        if off_resp.status_code not in (200, 201):
            msg = _safe_text(off_resp)
            if "Location information not found" in msg or "merchantLocationKey" in msg or "location" in msg.lower():
                # Location may have been missing/disabled; force-create MAINWAREHOUSE and retry once.
                retry_key, retry_err = _create_inventory_location(
                    api_base, access_token, marketplace,
                    {"name": "Main Warehouse", "city": "New York", "stateOrProvince": "NY", "postalCode": "10001", "country": "US"},
                    "MAINWAREHOUSE",
                )
                if not retry_err and retry_key:
                    offer_payload["merchantLocationKey"] = retry_key
                    off_resp = requests.post(
                        f"{api_base}/sell/inventory/v1/offer",
                        headers=hdrs, json=offer_payload, timeout=30,
                    )
                    if off_resp.status_code in (200, 201):
                        offer_id = off_resp.json().get("offerId", "")
                    else:
                        return _err(f"Offer creation failed ({off_resp.status_code}): {_safe_text(off_resp)}")
                else:
                    return _err(f"Offer creation failed ({off_resp.status_code}): {msg} | Auto-create location failed: {retry_err}")
            else:
                return _err(f"Offer creation failed ({off_resp.status_code}): {msg}")
        else:
            offer_id = off_resp.json().get("offerId", "")

    if not offer_id:
        return _err("eBay did not return an offer ID. Check policy IDs and merchant location.")

    # ── 4. Publish ────────────────────────────────────────────────────────
    pub_resp = requests.post(
        f"{api_base}/sell/inventory/v1/offer/{offer_id}/publish",
        headers=hdrs, timeout=30,
    )
    if pub_resp.status_code not in (200, 201):
        return _err(f"Publish failed ({pub_resp.status_code}): {_safe_text(pub_resp)}")

    listing_id = pub_resp.json().get("listingId", "")
    if env == "production":
        listing_url = f"https://www.ebay.com/itm/{listing_id}" if listing_id else ""
    else:
        listing_url = f"https://sandbox.ebay.com/itm/{listing_id}" if listing_id else ""

    return {
        "success":     True,
        "listing_id":  listing_id,
        "listing_url": listing_url,
        "offer_id":    offer_id,
        "sku":         sku,
        "environment": env,
        "error":       None,
    }


def _err(msg: str) -> dict:
    return {"success": False, "listing_id": "", "listing_url": "",
            "offer_id": "", "sku": "", "environment": "", "error": msg}

def _safe_text(resp) -> str:
    try:
        data = resp.json()
        errs = data.get("errors", [])
        if errs:
            return "; ".join(f"{e.get('errorId','')}: {e.get('longMessage', e.get('message',''))}" for e in errs[:3])
        return data.get("error_description") or data.get("message") or resp.text[:300]
    except Exception:
        return resp.text[:300]
