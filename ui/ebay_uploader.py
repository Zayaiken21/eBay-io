"""
ebay_uploader.py — Posts listings directly to eBay via the Sell Inventory API.
Uses OAuth tokens stored from ebay_oauth.py.
Requires: EBAY_PROD_CLIENT_ID / EBAY_PROD_CLIENT_SECRET in st.secrets.
"""

import json, re, requests
from datetime import datetime, timezone


API_BASE_PROD    = "https://api.ebay.com"
API_BASE_SANDBOX = "https://api.sandbox.ebay.com"

# eBay category IDs for common categories (fallback mapping)
CATEGORY_MAP = {
    "electronics":       "58058",
    "computers":         "58058",
    "phones":            "15032",
    "clothing":          "11450",
    "shoes":             "63889",
    "jewelry":           "281",
    "home":              "11700",
    "garden":            "159912",
    "toys":              "220",
    "sports":            "888",
    "automotive":        "6000",
    "beauty":            "26395",
    "health":            "26395",
    "pet":               "1281",
    "tools":             "631",
    "books":             "267",
    "music":             "11233",
    "movies":            "11232",
    "video games":       "1249",
    "other":             "99",
}

def _api_base(environment: str) -> str:
    return API_BASE_PROD if environment == "production" else API_BASE_SANDBOX

def _auth_header(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        "Accept": "application/json",
    }

def _guess_category_id(category: str) -> str:
    cat = category.lower()
    for key, cid in CATEGORY_MAP.items():
        if key in cat:
            return cid
    return CATEGORY_MAP["other"]

def _sanitize_sku(title: str, draft_id: str) -> str:
    safe = re.sub(r'[^a-zA-Z0-9\-]', '-', title[:30])
    return f"{safe}-{draft_id}"[:50]


def upload_to_ebay(product: dict, access_token: str, environment: str = "production") -> dict:
    """
    Full upload pipeline:
      1. Create/update inventory item
      2. Create offer
      3. Publish offer → returns listing URL

    Returns: {"success": bool, "listing_id": str, "listing_url": str, "error": str|None}
    """
    base  = _api_base(environment)
    hdrs  = _auth_header(access_token)
    sku   = _sanitize_sku(product.get("title","item"), product.get("draft_id","001"))

    # ── Step 1: Create/update inventory item ──────────────────────────────
    item_payload = _build_inventory_item(product)
    item_resp = requests.put(
        f"{base}/sell/inventory/v1/inventory_item/{sku}",
        headers=hdrs,
        json=item_payload,
        timeout=30,
    )
    if item_resp.status_code not in (200, 201, 204):
        return _upload_err(f"Inventory item failed ({item_resp.status_code}): {item_resp.text[:300]}")

    # ── Step 2: Create offer ──────────────────────────────────────────────
    price = product.get("price","") or "9.99"
    try: float(price)
    except Exception: price = "9.99"

    offer_payload = {
        "sku": sku,
        "marketplaceId": "EBAY_US",
        "format": "FIXED_PRICE",
        "availableQuantity": int(product.get("quantity", 1)),
        "categoryId": _guess_category_id(product.get("category","")),
        "listingDescription": product.get("ebay_html", product.get("description","")),
        "listingPolicies": _listing_policies(product),
        "pricingSummary": {
            "price": {"value": price, "currency": "USD"}
        },
        "merchantLocationKey": product.get("merchant_location_key", "default"),
        "tax": {"applyTax": True, "vatPercentage": 0},
    }

    # Check for existing offer first
    existing = _get_existing_offer(base, hdrs, sku)
    if existing:
        offer_id = existing
        upd_resp = requests.put(
            f"{base}/sell/inventory/v1/offer/{offer_id}",
            headers=hdrs, json=offer_payload, timeout=30,
        )
        if upd_resp.status_code not in (200,204):
            return _upload_err(f"Offer update failed ({upd_resp.status_code}): {upd_resp.text[:300]}")
    else:
        off_resp = requests.post(
            f"{base}/sell/inventory/v1/offer",
            headers=hdrs, json=offer_payload, timeout=30,
        )
        if off_resp.status_code not in (200,201):
            return _upload_err(f"Offer creation failed ({off_resp.status_code}): {off_resp.text[:300]}")
        offer_id = off_resp.json().get("offerId","")

    if not offer_id:
        return _upload_err("No offer ID returned from eBay.")

    # ── Step 3: Publish ───────────────────────────────────────────────────
    pub_resp = requests.post(
        f"{base}/sell/inventory/v1/offer/{offer_id}/publish",
        headers=hdrs, timeout=30,
    )
    if pub_resp.status_code not in (200,201):
        return _upload_err(f"Publish failed ({pub_resp.status_code}): {pub_resp.text[:300]}")

    listing_id = pub_resp.json().get("listingId","")
    env_prefix = "" if environment == "production" else "sandbox."
    listing_url = f"https://{env_prefix}ebay.com/itm/{listing_id}" if listing_id else ""

    return {
        "success": True,
        "listing_id": listing_id,
        "listing_url": listing_url,
        "offer_id": offer_id,
        "sku": sku,
        "error": None,
    }


def _build_inventory_item(product: dict) -> dict:
    images = product.get("images", [])[:12]
    img_urls = [{"imageUrl": u} for u in images if u.startswith("http")]

    item = {
        "product": {
            "title": product.get("title","")[:80],
            "description": product.get("description",""),
            "imageUrls": [u for u in images if u.startswith("http")],
            "aspects": _build_aspects(product),
        },
        "condition": _map_condition(product.get("condition","New")),
        "availability": {
            "shipToLocationAvailability": {
                "quantity": int(product.get("quantity", 1))
            }
        },
    }

    if product.get("brand"):
        item["product"]["brand"] = product["brand"]
    if product.get("sku"):
        item["product"]["mpn"] = product["sku"]

    return item


def _build_aspects(product: dict) -> dict:
    aspects = {}
    if product.get("brand"):       aspects["Brand"]     = [product["brand"]]
    if product.get("condition"):   aspects["Condition"] = [product["condition"]]
    if product.get("weight"):      aspects["Item Weight"] = [product["weight"]]
    if product.get("dimensions"):  aspects["Item Dimensions"] = [product["dimensions"]]
    for k, v in (product.get("specifications") or {}).items():
        clean_k = k.strip()[:65]
        clean_v = str(v).strip()[:65]
        if clean_k and clean_v and clean_k not in aspects:
            aspects[clean_k] = [clean_v]
        if len(aspects) >= 20: break
    return aspects


def _listing_policies(product: dict) -> dict:
    """
    Uses policy IDs from product if set, otherwise uses defaults from secrets.
    Sellers must set these up in their eBay account first.
    """
    return {
        "fulfillmentPolicyId": product.get("fulfillment_policy_id",""),
        "paymentPolicyId":     product.get("payment_policy_id",""),
        "returnPolicyId":      product.get("return_policy_id",""),
    }


def _map_condition(condition: str) -> str:
    mapping = {
        "new":                "NEW",
        "new with tags":      "NEW",
        "new without tags":   "NEW",
        "new with defects":   "NEW_WITH_DEFECTS",
        "pre-owned":          "USED_EXCELLENT",
        "good":               "USED_GOOD",
        "acceptable":         "USED_ACCEPTABLE",
        "for parts":          "FOR_PARTS_OR_NOT_WORKING",
    }
    return mapping.get(condition.lower(), "NEW")


def _get_existing_offer(base: str, hdrs: dict, sku: str) -> str:
    """Check if an offer already exists for this SKU."""
    try:
        resp = requests.get(f"{base}/sell/inventory/v1/offer?sku={sku}", headers=hdrs, timeout=15)
        if resp.status_code == 200:
            offers = resp.json().get("offers", [])
            if offers: return offers[0].get("offerId","")
    except Exception:
        pass
    return ""


def _upload_err(msg: str) -> dict:
    return {"success": False, "listing_id": "", "listing_url": "", "offer_id": "", "sku": "", "error": msg}


def get_seller_policies(access_token: str, environment: str = "production") -> dict:
    """
    Fetches the seller's fulfillment/payment/return policies from eBay.
    Returns dict of policy lists so the UI can let user pick the right ones.
    """
    base = _api_base(environment)
    hdrs = _auth_header(access_token)
    result = {"fulfillment": [], "payment": [], "return": []}

    for policy_type, key in [("fulfillment","fulfillmentPolicies"),
                               ("payment","paymentPolicies"),
                               ("return","returnPolicies")]:
        try:
            resp = requests.get(
                f"{base}/sell/account/v1/{policy_type}_policy?marketplace_id=EBAY_US",
                headers=hdrs, timeout=15,
            )
            if resp.status_code == 200:
                result[policy_type] = [
                    {"id": p.get("fulfillmentPolicyId") or p.get("paymentPolicyId") or p.get("returnPolicyId",""),
                     "name": p.get("name","")}
                    for p in resp.json().get(key, [])
                ]
        except Exception:
            pass

    return result
