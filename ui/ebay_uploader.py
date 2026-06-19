"""
ebay_uploader.py — Production eBay upload helpers.

- Uses connected seller OAuth token for Taxonomy API category suggestions.
- Resolves a real eBay category before publishing.
- Uses/creates a real Inventory API merchantLocationKey. Never sends "default".
- Sanitizes SKU and description at the final API boundary.
- Removes eBay-blocked active content from listing HTML before publish.
- Updates old active Trading listings via ReviseFixedPriceItem when they do not have an Inventory-safe SKU.
"""

import html
import re
import xml.etree.ElementTree as ET
from typing import Any

import requests
import streamlit as st

from core.ebay_account_store import get_valid_ebay_access_token, get_latest_ebay_account

SAFE_CATEGORY_FALLBACKS = {
    "lego": "19006",
    "building toy": "19006",
    "lawn": "29518",
    "scarifier": "29518",
    "dethatcher": "29518",
    "shoes": "63889",
}


def _get_owner_name() -> str:
    role = st.session_state.get("role") or ""
    client_name = st.session_state.get("client_name") or ""
    if client_name:
        return client_name.strip()
    if str(role).lower() == "ceo":
        return "ceo"
    return "default"


def _api_context():
    owner = _get_owner_name()
    access_token, account = get_valid_ebay_access_token(owner)
    env = (account.get("environment") or "production").lower()
    api_base = "https://api.sandbox.ebay.com" if env != "production" else "https://api.ebay.com"
    trading_url = "https://api.sandbox.ebay.com/ws/api.dll" if env != "production" else "https://api.ebay.com/ws/api.dll"
    marketplace = account.get("marketplace_id", "EBAY_US")
    return owner, access_token, account, env, api_base, trading_url, marketplace


def _json_headers(token: str, marketplace: str | None = None) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Language": "en-US",
        "Content-Language": "en-US",
    }
    if marketplace:
        h["X-EBAY-C-MARKETPLACE-ID"] = marketplace
    return h


def _safe_text(resp) -> str:
    try:
        data = resp.json()
        errs = data.get("errors", [])
        if errs:
            return " | ".join(
                f"{e.get('errorId','')}: {e.get('longMessage') or e.get('message') or ''}".strip()
                for e in errs[:5]
            )
        return data.get("error_description") or data.get("message") or resp.text[:500]
    except Exception:
        return resp.text[:500]


def _err(msg: str, **extra) -> dict:
    out = {"success": False, "listing_id": "", "listing_url": "", "offer_id": "", "sku": "", "environment": "", "error": msg}
    out.update(extra)
    return out


def get_account_info() -> dict | None:
    owner = _get_owner_name()
    account = get_latest_ebay_account(owner)
    if account:
        account["_resolved_owner"] = owner
    return account


def _sanitize_sku(value: str, fallback: str = "ITEM") -> str:
    raw = str(value or "").strip() or str(fallback or "ITEM")
    cleaned = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    return (cleaned or "ITEM")[:50]


def _is_inventory_safe_sku(sku: str) -> bool:
    return bool(sku) and len(str(sku)) <= 50 and re.fullmatch(r"[A-Za-z0-9]+", str(sku)) is not None


def _strip_html(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"(?is)<(script|style|iframe|meta|base|object|embed|form|input|button).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<(script|style|iframe|meta|base|object|embed|form|input|button)[^>]*?/?>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_inventory_description(product: dict) -> str:
    parts = [product.get("description", ""), " ".join(product.get("features") or []), product.get("title", "")]
    text = _strip_html("\n".join(str(p) for p in parts if p))
    if not text:
        text = f"Quality product: {product.get('title') or 'Item'}"
    return text[:3990]


def _safe_listing_description(product: dict) -> str:
    raw = str(product.get("ebay_html") or product.get("description") or product.get("title") or "Quality item.")
    raw = re.sub(r"(?is)<script.*?>.*?</script>", "", raw)
    raw = re.sub(r"(?is)<iframe.*?>.*?</iframe>", "", raw)
    raw = re.sub(r"(?is)<(meta|base|object|embed|form|input|button|link)[^>]*>", "", raw)
    raw = re.sub(r"\s+on\w+\s*=\s*(['\"]).*?\1", "", raw, flags=re.I | re.S)
    raw = re.sub(r"javascript\s*:", "", raw, flags=re.I)
    raw = re.sub(r"(?i)\.cookie|cookie\s*\(|replace\s*\(|includes\s*\(", "", raw)
    raw = raw.strip()
    if not raw:
        raw = f"<p>{html.escape(_safe_inventory_description(product))}</p>"
    return raw[:490000]



def _num_from_text(value: Any, default: float = 0.0) -> float:
    """Extract the first positive number from seller/scraped text like '1 lb', '12 oz', '8 x 6 x 4'."""
    try:
        if isinstance(value, (int, float)):
            return float(value) if float(value) > 0 else default
        text = str(value or "")
        m = re.search(r"(\d+(?:\.\d+)?)", text)
        if m:
            num = float(m.group(1))
            return num if num > 0 else default
    except Exception:
        pass
    return default


def _parse_weight_ounces(product: dict) -> float:
    """Return a safe package weight in ounces. Never returns 0 because eBay rejects missing/invalid weight."""
    raw = " ".join(str(product.get(k) or "") for k in ["package_weight", "shipping_weight", "weight"])
    num = _num_from_text(raw, 0.0)
    lower = raw.lower()
    if num <= 0:
        # Safe seller-default for lightweight retail items. User can override in Weight field.
        return 16.0
    if any(x in lower for x in ["kg", "kilogram"]):
        return max(1.0, num * 35.274)
    if any(x in lower for x in ["g", "gram"]) and "lb" not in lower:
        return max(1.0, num * 0.035274)
    if any(x in lower for x in ["oz", "ounce"]):
        return max(1.0, num)
    # Default bare numbers and 'lb/lbs/pound' to pounds.
    return max(1.0, num * 16.0)


def _parse_dimensions_inches(product: dict) -> tuple[float, float, float]:
    """Return length,width,height inches. Defaults are valid, conservative, and editable by seller."""
    raw = " ".join(str(product.get(k) or "") for k in ["package_dimensions", "dimensions"])
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", raw)[:3]]
    if len(nums) >= 3 and all(x > 0 for x in nums[:3]):
        length, width, height = nums[:3]
    else:
        # Valid generic package used when scraper/product has no shipping dimensions.
        length, width, height = 8.0, 6.0, 4.0
    lower = raw.lower()
    if "cm" in lower or "centimeter" in lower:
        length, width, height = length / 2.54, width / 2.54, height / 2.54
    return max(1.0, round(length, 2)), max(1.0, round(width, 2)), max(1.0, round(height, 2))


def _package_weight_and_size(product: dict) -> dict:
    """Build eBay Inventory API packageWeightAndSize. Weight is required by calculated/weight-surcharge shipping policies."""
    ounces = _parse_weight_ounces(product)
    length, width, height = _parse_dimensions_inches(product)
    # Avoid packageType because some marketplace/carrier combinations reject otherwise valid enum values.
    return {
        "weight": {"value": round(max(1.0, ounces), 2), "unit": "OUNCE"},
        "dimensions": {"length": length, "width": width, "height": height, "unit": "INCH"},
    }


def _looks_like_package_weight_error(msg: str) -> bool:
    m = str(msg or "").lower()
    return ("package weight" in m or "packageweightandsize" in m or "package weight is not valid" in m or "weight is not valid" in m)

def _map_condition(condition: str) -> str:
    return {
        "new": "NEW",
        "new with tags": "NEW",
        "new without tags": "NEW",
        "new with defects": "NEW_WITH_DEFECTS",
        "pre-owned": "USED_EXCELLENT",
        "good": "USED_GOOD",
        "acceptable": "USED_ACCEPTABLE",
        "for parts": "FOR_PARTS_OR_NOT_WORKING",
    }.get((condition or "new").lower(), "NEW")


def _build_aspects(product: dict) -> dict:
    aspects = {}
    if product.get("brand"):
        aspects["Brand"] = [str(product["brand"])[:65]]
    if product.get("condition"):
        aspects["Condition"] = [str(product["condition"])[:65]]
    if product.get("weight"):
        aspects["Item Weight"] = [str(product["weight"])[:65]]
    if product.get("dimensions"):
        aspects["Item Dimensions"] = [str(product["dimensions"])[:65]]
    for k, v in (product.get("specifications") or {}).items():
        ck, cv = str(k).strip()[:65], str(v).strip()[:65]
        if ck and cv and ck not in aspects:
            aspects[ck] = [cv]
        if len(aspects) >= 20:
            break
    return aspects


def _get_default_category_tree_id(api_base: str, token: str, marketplace: str) -> str:
    resp = requests.get(f"{api_base}/commerce/taxonomy/v1/get_default_category_tree_id", headers=_json_headers(token), params={"marketplace_id": marketplace}, timeout=20)
    if resp.status_code >= 400:
        raise RuntimeError(_safe_text(resp))
    return str(resp.json().get("categoryTreeId") or "")


def _category_suggestions(api_base: str, token: str, marketplace: str, query: str) -> list[dict]:
    tree_id = _get_default_category_tree_id(api_base, token, marketplace)
    if not tree_id:
        return []
    resp = requests.get(f"{api_base}/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_suggestions", headers=_json_headers(token), params={"q": query[:350]}, timeout=25)
    if resp.status_code >= 400:
        raise RuntimeError(_safe_text(resp))
    suggestions = []
    for row in resp.json().get("categorySuggestions", []) or []:
        cat = row.get("category") or {}
        cid = str(cat.get("categoryId") or "")
        name = cat.get("categoryName") or ""
        ancestors = row.get("categoryTreeNodeAncestors") or []
        path_names = [a.get("categoryName", "") for a in ancestors if a.get("categoryName")]
        if name:
            path_names.append(name)
        if cid:
            suggestions.append({
                "id": cid,
                "name": name or cid,
                "path": " > ".join(path_names) or name or cid,
                "source": "taxonomy_api",
                "is_leaf": bool(cat.get("leafCategoryTreeNode") or row.get("leafCategoryTreeNode")),
            })
    return suggestions



def _is_leaf_category(api_base: str, token: str, marketplace: str, category_id: str) -> bool:
    """Verify category is a leaf before offer creation/publish."""
    try:
        tree_id = _get_default_category_tree_id(api_base, token, marketplace)
        if not tree_id or not str(category_id).isdigit():
            return False
        resp = requests.get(
            f"{api_base}/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_subtree",
            headers=_json_headers(token),
            params={"category_id": str(category_id)},
            timeout=25,
        )
        if resp.status_code >= 400:
            return False
        data = resp.json() or {}
        node = data.get("categorySubtreeNode") or data.get("rootCategoryNode") or data
        if node.get("leafCategoryTreeNode") is True:
            return True
        children = node.get("childCategoryTreeNodes") or []
        return len(children) == 0
    except Exception:
        # Do not trust unknown categories as leaf; this prevents 25005 loops.
        return False


def _get_required_aspects(api_base: str, token: str, marketplace: str, category_id: str) -> list[dict]:
    """Return eBay-required item specifics for the selected leaf category."""
    try:
        tree_id = _get_default_category_tree_id(api_base, token, marketplace)
        if not tree_id or not str(category_id).isdigit():
            return []
        resp = requests.get(
            f"{api_base}/commerce/taxonomy/v1/category_tree/{tree_id}/get_item_aspects_for_category",
            headers=_json_headers(token),
            params={"category_id": str(category_id)},
            timeout=30,
        )
        if resp.status_code >= 400:
            return []
        out = []
        for a in (resp.json() or {}).get("aspects", []) or []:
            name = a.get("localizedAspectName") or a.get("aspectName") or ""
            constraint = a.get("aspectConstraint") or {}
            required = constraint.get("aspectRequired") is True or str(constraint.get("aspectRequired", "")).lower() == "true"
            if name and required:
                values = []
                for v in a.get("aspectValues", []) or []:
                    val = v.get("localizedValue") or v.get("value") or ""
                    if val:
                        values.append(str(val))
                out.append({"name": str(name), "values": values})
        return out
    except Exception:
        return []


def _infer_required_aspect_value(name: str, product: dict, values: list[str] | None = None) -> str:
    """Best-effort safe defaults for required specifics when scraper data is missing."""
    values = values or []
    lname = str(name or "").strip().lower()
    title = str(product.get("title") or "").lower()
    category = str(product.get("category") or "").lower()
    brand = str(product.get("brand") or "").strip()

    # Common required field that caused: "The item specific Type is missing."
    if lname == "type":
        guesses = [
            ("toothpaste", "Toothpaste"),
            ("fluoride", "Toothpaste"),
            ("crest", "Toothpaste"),
            ("scarifier", "Scarifier"),
            ("dethatcher", "Dethatcher"),
            ("lawn", "Lawn Dethatcher"),
            ("lego", "Building Toy Complete Set"),
            ("shoe", "Athletic"),
            ("sneaker", "Athletic"),
        ]
        for needle, guess in guesses:
            if needle in title or needle in category:
                return _pick_allowed_value(guess, values)
        return _pick_allowed_value("Product", values)
    if lname == "brand":
        return _pick_allowed_value(brand or "Unbranded", values)
    if lname in {"model", "product line"}:
        return _pick_allowed_value(str(product.get("title") or "Not Specified")[:65], values)
    if lname in {"color", "colour"}:
        return _pick_allowed_value("Multicolor", values)
    if lname == "size":
        return _pick_allowed_value("One Size", values)
    if lname == "material":
        return _pick_allowed_value("Not Specified", values)
    if lname in {"department", "gender"}:
        return _pick_allowed_value("Unisex Adults", values)
    if lname in {"upc", "ean", "isbn"}:
        return _pick_allowed_value("Does Not Apply", values)
    return _pick_allowed_value("Not Specified", values)


def _pick_allowed_value(guess: str, values: list[str]) -> str:
    if not values:
        return str(guess or "Not Specified")[:65]
    g = str(guess or "").lower()
    for val in values:
        if val.lower() == g:
            return val[:65]
    for val in values:
        if g and (g in val.lower() or val.lower() in g):
            return val[:65]
    # Prefer non-empty allowed value over invalid free text when eBay gives a closed list.
    return str(values[0])[:65]


def _ensure_required_aspects(product: dict, aspects: dict, api_base: str, token: str, marketplace: str, category_id: str) -> tuple[dict, list[str]]:
    """Fill required eBay aspects before PUT inventory_item to prevent publish 25002 specifics errors."""
    aspects = dict(aspects or {})
    existing_lower = {str(k).lower(): k for k in aspects.keys()}
    added = []
    for req in _get_required_aspects(api_base, token, marketplace, category_id):
        name = req.get("name") or ""
        if not name:
            continue
        current_key = existing_lower.get(name.lower())
        if current_key and aspects.get(current_key):
            continue
        value = _infer_required_aspect_value(name, product, req.get("values") or [])
        if value:
            aspects[name] = [str(value)[:65]]
            added.append(f"{name}: {value}")
    return aspects, added


def _get_current_policy_sets(api_base: str, token: str, marketplace: str) -> dict:
    result = {"fulfillment": [], "payment": [], "return": []}
    hdrs = _json_headers(token, marketplace)
    for policy_type, resp_key, id_field in [
        ("fulfillment", "fulfillmentPolicies", "fulfillmentPolicyId"),
        ("payment", "paymentPolicies", "paymentPolicyId"),
        ("return", "returnPolicies", "returnPolicyId"),
    ]:
        try:
            r = requests.get(
                f"{api_base}/sell/account/v1/{policy_type}_policy",
                headers=hdrs,
                params={"marketplace_id": marketplace},
                timeout=20,
            )
            if r.status_code == 200:
                result[policy_type] = [
                    {"id": p.get(id_field, ""), "name": p.get("name", "")} 
                    for p in r.json().get(resp_key, []) if p.get(id_field)
                ]
        except Exception:
            pass
    return result


def _resolve_policy_id(kind: str, requested: str, policies: dict) -> str:
    rows = policies.get(kind, []) or []
    if not rows:
        labels = {"fulfillment": "shipping/fulfillment", "payment": "payment", "return": "return"}
        raise RuntimeError(
            f"No {labels.get(kind, kind)} policy was found for this signed-in eBay account. "
            "Open eBay Seller Hub → Business Policies, create/enable the missing policy for this marketplace, then click 'Load my eBay policies' again."
        )
    requested = str(requested or "").strip()
    if requested and any(str(x.get("id")) == requested for x in rows):
        return requested
    # Draft had stale policy IDs from another user/account; choose the first real policy for the current signed-in account.
    return str(rows[0]["id"])

def suggest_categories(title: str, category: str = "") -> dict:
    try:
        _, token, _, _, api_base, _, marketplace = _api_context()
        query = " ".join(x for x in [title, category] if x).strip() or "item"
        suggestions = _category_suggestions(api_base, token, marketplace, query)
        return {"success": True, "suggestions": suggestions[:10], "error": None}
    except Exception as e:
        return {"success": False, "suggestions": [], "error": f"Could not resolve eBay categories with the connected seller token: {e}"}


def _fallback_category_id(title: str, category: str) -> str:
    text = f"{title} {category}".lower()
    for key, cid in SAFE_CATEGORY_FALLBACKS.items():
        if key in text:
            return cid
    return ""


def _resolve_category_id(product: dict, api_base: str, token: str, marketplace: str) -> tuple[str, str, str]:
    explicit = str(product.get("category_id_override") or product.get("ebay_category_id") or "").strip()
    title = product.get("title", "")
    category = product.get("category", "")
    query = " ".join(x for x in [title, category] if x).strip()
    fallback_err = "Missing product title/category for category lookup."

    if explicit.isdigit():
        if _is_leaf_category(api_base, token, marketplace, explicit):
            return explicit, "user_selected", ""
        # Explicit ID was a parent; resolve a leaf from the same product text instead.
        fallback_err = f"Selected category ID {explicit} is not a leaf category."

    if query:
        try:
            suggestions = _category_suggestions(api_base, token, marketplace, query)
            for suggestion in suggestions:
                sid = suggestion.get("id", "")
                if sid and (_is_leaf_category(api_base, token, marketplace, sid) or suggestion.get("is_leaf")):
                    return sid, "taxonomy_api", suggestion.get("path", "")
            fallback_err = "No leaf category suggestions returned."
        except Exception as e:
            fallback_err = str(e)

    fallback = _fallback_category_id(title, category)
    if fallback and _is_leaf_category(api_base, token, marketplace, fallback):
        return fallback, "fallback", fallback_err
    raise RuntimeError("No valid eBay leaf category could be resolved. Click 'Find eBay Category' and choose the best eBay category before publishing. Category lookup detail: " + fallback_err)


def _normalize_location(row: dict) -> dict:
    key = row.get("merchantLocationKey") or row.get("locationKey") or row.get("key") or ""
    status = str(row.get("merchantLocationStatus") or row.get("status") or "ENABLED").upper()
    return {"key": key, "name": row.get("name") or key, "enabled": status != "DISABLED", "status": status, "raw": row}


def _get_inventory_locations(api_base: str, token: str, marketplace: str) -> list[dict]:
    resp = requests.get(f"{api_base}/sell/inventory/v1/location", headers=_json_headers(token, marketplace), params={"limit": "100", "offset": "0"}, timeout=25)
    if resp.status_code >= 400:
        return []
    data = resp.json()
    raw_locs = data.get("locations") or data.get("location") or []
    return [_normalize_location(x) for x in raw_locs if isinstance(x, dict)]


def _create_or_enable_main_warehouse(api_base: str, token: str, marketplace: str, product: dict) -> str:
    key = _sanitize_sku(product.get("warehouse_key") or "MAINWAREHOUSE", "MAINWAREHOUSE")[:50] or "MAINWAREHOUSE"
    payload = {
        "name": product.get("warehouse_name") or "Main Warehouse",
        "locationTypes": ["WAREHOUSE"],
        "merchantLocationStatus": "ENABLED",
        "location": {"address": {
            "addressLine1": product.get("warehouse_address") or "2083 e 19th St",
            "city": product.get("warehouse_city") or "Brooklyn",
            "stateOrProvince": product.get("warehouse_state") or "NY",
            "postalCode": product.get("warehouse_postal") or "11229",
            "country": product.get("warehouse_country") or "US",
        }},
    }
    resp = requests.post(f"{api_base}/sell/inventory/v1/location/{key}", headers=_json_headers(token, marketplace), json=payload, timeout=30)
    if resp.status_code not in (200, 201, 204, 409):
        msg = _safe_text(resp)
        if "already" not in msg.lower() and "duplicate" not in msg.lower():
            raise RuntimeError(f"Could not create eBay warehouse location {key}: {msg}")
    try:
        requests.post(f"{api_base}/sell/inventory/v1/location/{key}/enable", headers=_json_headers(token, marketplace), timeout=20)
    except Exception:
        pass
    return key


def _resolve_location_key(product: dict, api_base: str, token: str, marketplace: str) -> str:
    requested = str(product.get("merchant_location_key") or "").strip()
    locs = _get_inventory_locations(api_base, token, marketplace)
    enabled = [x for x in locs if x.get("enabled") and x.get("key")]
    if requested and requested.lower() != "default" and any(x["key"] == requested for x in enabled):
        return requested
    if enabled:
        return enabled[0]["key"]
    return _create_or_enable_main_warehouse(api_base, token, marketplace, product)


def get_seller_policies() -> dict:
    owner = _get_owner_name()
    result = {"fulfillment": [], "payment": [], "return": [], "locations": [], "error": None}
    try:
        access_token, account = get_valid_ebay_access_token(owner)
        env = (account.get("environment") or "production").lower()
        api_base = "https://api.sandbox.ebay.com" if env != "production" else "https://api.ebay.com"
        marketplace = account.get("marketplace_id", "EBAY_US")
        hdrs = _json_headers(access_token, marketplace)
        for policy_type, resp_key, id_field in [("fulfillment", "fulfillmentPolicies", "fulfillmentPolicyId"), ("payment", "paymentPolicies", "paymentPolicyId"), ("return", "returnPolicies", "returnPolicyId")]:
            r = requests.get(f"{api_base}/sell/account/v1/{policy_type}_policy", headers=hdrs, params={"marketplace_id": marketplace}, timeout=20)
            if r.status_code == 200:
                result[policy_type] = [{"id": p.get(id_field, ""), "name": p.get("name", "")} for p in r.json().get(resp_key, []) if p.get(id_field)]
        result["locations"] = _get_inventory_locations(api_base, access_token, marketplace)
    except Exception as e:
        result["error"] = str(e)
    return result


def _trading_headers(token: str, call_name: str) -> dict[str, str]:
    return {"X-EBAY-API-CALL-NAME": call_name, "X-EBAY-API-SITEID": "0", "X-EBAY-API-COMPATIBILITY-LEVEL": "1193", "X-EBAY-API-IAF-TOKEN": token, "Content-Type": "text/xml"}


def _xml_escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _revise_fixed_price_item(product: dict, token: str, trading_url: str, env: str) -> dict:
    item_id = str(product.get("ebay_listing_id") or product.get("listing_id") or "").strip()
    if not item_id:
        return _err("This live listing cannot be updated because it has no eBay ItemID.", environment=env)
    try:
        price = f"{float(product.get('price') or 9.99):.2f}"
    except Exception:
        price = "9.99"
    qty = max(1, int(product.get("quantity", 1)))
    title = str(product.get("title") or "Untitled Listing")[:80]
    desc = _safe_listing_description(product)
    category_xml = ""
    if str(product.get("category_id_override") or "").isdigit():
        category_xml = f"<PrimaryCategory><CategoryID>{_xml_escape(product.get('category_id_override'))}</CategoryID></PrimaryCategory>"
    body = f'''<?xml version="1.0" encoding="utf-8"?>
<ReviseFixedPriceItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials/>
  <Item>
    <ItemID>{_xml_escape(item_id)}</ItemID>
    <Title>{_xml_escape(title)}</Title>
    <Description>{_xml_escape(desc)}</Description>
    <StartPrice currencyID="USD">{_xml_escape(price)}</StartPrice>
    <Quantity>{qty}</Quantity>
    {category_xml}
  </Item>
  <ErrorLanguage>en_US</ErrorLanguage>
  <WarningLevel>High</WarningLevel>
</ReviseFixedPriceItemRequest>'''
    resp = requests.post(trading_url, headers=_trading_headers(token, "ReviseFixedPriceItem"), data=body.encode("utf-8"), timeout=45)
    if resp.status_code >= 400:
        return _err(f"Trading update failed ({resp.status_code}): {resp.text[:500]}", environment=env)
    try:
        root = ET.fromstring(resp.text)
        ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
        ack = (root.findtext("e:Ack", default="", namespaces=ns) or "").lower()
        if ack == "failure":
            msgs = []
            for err in root.findall(".//e:Errors", ns):
                code = err.findtext("e:ErrorCode", default="", namespaces=ns)
                msg = err.findtext("e:LongMessage", default="", namespaces=ns) or err.findtext("e:ShortMessage", default="", namespaces=ns)
                if msg:
                    msgs.append(f"{code}: {msg}" if code else msg)
            return _err("Trading update failed: " + "; ".join(msgs), environment=env)
    except Exception:
        pass
    url = f"https://www.ebay.com/itm/{item_id}" if env == "production" else f"https://sandbox.ebay.com/itm/{item_id}"
    return {"success": True, "listing_id": item_id, "listing_url": url, "offer_id": "", "sku": product.get("sku", ""), "environment": env, "error": None, "updated_via": "trading_api"}


def _get_existing_offer(api_base: str, token: str, sku: str, marketplace_id: str) -> str:
    try:
        resp = requests.get(f"{api_base}/sell/inventory/v1/offer", headers=_json_headers(token, marketplace_id), params={"sku": sku}, timeout=20)
        if resp.status_code == 200:
            offers = resp.json().get("offers", [])
            if offers:
                return offers[0].get("offerId", "")
    except Exception:
        pass
    return ""


def upload_to_ebay(product: dict) -> dict:
    try:
        _, access_token, _, env, api_base, trading_url, marketplace = _api_context()
    except RuntimeError as e:
        return _err(str(e))
    original_sku = str(product.get("_from_ebay_sku") or product.get("sku") or "").strip()
    if product.get("_edit_via_trading") or (product.get("ebay_listing_id") and original_sku and not _is_inventory_safe_sku(original_sku)):
        return _revise_fixed_price_item(product, access_token, trading_url, env)
    hdrs = _json_headers(access_token, marketplace)
    sku_seed = product.get("_from_ebay_sku") if _is_inventory_safe_sku(product.get("_from_ebay_sku", "")) else product.get("sku") or product.get("title") or "ITEM"
    sku = _sanitize_sku(sku_seed, product.get("draft_id", "001"))
    try:
        price = f"{float(product.get('price') or 9.99):.2f}"
    except Exception:
        price = "9.99"
    try:
        category_id, category_source, category_detail = _resolve_category_id(product, api_base, access_token, marketplace)
    except Exception as e:
        return _err(str(e), environment=env)
    try:
        merchant_location_key = _resolve_location_key(product, api_base, access_token, marketplace)
    except Exception as e:
        return _err(f"Could not resolve/create eBay inventory location: {e}", environment=env)
    images = [u for u in (product.get("images") or []) if isinstance(u, str) and u.startswith("http")][:12]
    aspects, added_aspects = _ensure_required_aspects(
        product, _build_aspects(product), api_base, access_token, marketplace, category_id
    )
    inventory_payload = {
        "product": {
            "title": str(product.get("title", "Untitled Listing"))[:80],
            "description": _safe_inventory_description(product),
            "imageUrls": images,
            "aspects": aspects,
        },
        "condition": _map_condition(product.get("condition", "New")),
        "availability": {"shipToLocationAvailability": {"quantity": max(1, int(product.get("quantity", 1)))}},
        "packageWeightAndSize": _package_weight_and_size(product),
    }
    if product.get("brand"):
        inventory_payload["product"]["brand"] = str(product["brand"])[:65]
    inventory_payload["product"]["mpn"] = sku
    inv_resp = requests.put(f"{api_base}/sell/inventory/v1/inventory_item/{sku}", headers=hdrs, json=inventory_payload, timeout=35)
    if inv_resp.status_code not in (200, 201, 204):
        return _err(f"Inventory item failed ({inv_resp.status_code}): {_safe_text(inv_resp)}", environment=env, sku=sku)
    try:
        current_policies = _get_current_policy_sets(api_base, access_token, marketplace)
        fulfillment_policy_id = _resolve_policy_id("fulfillment", product.get("fulfillment_policy_id", ""), current_policies)
        payment_policy_id = _resolve_policy_id("payment", product.get("payment_policy_id", ""), current_policies)
        return_policy_id = _resolve_policy_id("return", product.get("return_policy_id", ""), current_policies)
    except Exception as e:
        return _err(str(e), environment=env, sku=sku, merchant_location_key=merchant_location_key, category_id=category_id)

    offer_payload = {
        "sku": sku,
        "marketplaceId": marketplace,
        "format": "FIXED_PRICE",
        "availableQuantity": max(1, int(product.get("quantity", 1))),
        "categoryId": category_id,
        "listingDescription": _safe_listing_description(product),
        "pricingSummary": {"price": {"value": price, "currency": "USD"}},
        "listingPolicies": {
            "fulfillmentPolicyId": fulfillment_policy_id,
            "paymentPolicyId": payment_policy_id,
            "returnPolicyId": return_policy_id,
        },
        "merchantLocationKey": merchant_location_key,
    }
    if product.get("apply_tax"):
        offer_payload["tax"] = {"applyTax": True, "vatPercentage": 0}
    existing_offer_id = _get_existing_offer(api_base, access_token, sku, marketplace)
    if existing_offer_id:
        off_resp = requests.put(f"{api_base}/sell/inventory/v1/offer/{existing_offer_id}", headers=hdrs, json=offer_payload, timeout=35)
        if off_resp.status_code not in (200, 204):
            return _err(f"Offer update failed ({off_resp.status_code}): {_safe_text(off_resp)}", environment=env, sku=sku, merchant_location_key=merchant_location_key)
        offer_id = existing_offer_id
    else:
        off_resp = requests.post(f"{api_base}/sell/inventory/v1/offer", headers=hdrs, json=offer_payload, timeout=35)
        if off_resp.status_code not in (200, 201):
            return _err(f"Offer creation failed ({off_resp.status_code}): {_safe_text(off_resp)}", environment=env, sku=sku, merchant_location_key=merchant_location_key, category_id=category_id)
        offer_id = off_resp.json().get("offerId", "")
    if not offer_id:
        return _err("eBay did not return an offer ID. Check policy IDs, merchant location, and category.", environment=env, sku=sku, merchant_location_key=merchant_location_key, category_id=category_id)
    pub_resp = requests.post(f"{api_base}/sell/inventory/v1/offer/{offer_id}/publish", headers=hdrs, timeout=35)
    if pub_resp.status_code not in (200, 201):
        msg = _safe_text(pub_resp)
        # If eBay reports missing/invalid package weight, re-save inventory with safe package details and retry once.
        if _looks_like_package_weight_error(msg):
            inventory_payload["packageWeightAndSize"] = _package_weight_and_size(product)
            retry_inv = requests.put(f"{api_base}/sell/inventory/v1/inventory_item/{sku}", headers=hdrs, json=inventory_payload, timeout=35)
            if retry_inv.status_code in (200, 201, 204):
                pub_resp = requests.post(f"{api_base}/sell/inventory/v1/offer/{offer_id}/publish", headers=hdrs, timeout=35)
                if pub_resp.status_code not in (200, 201):
                    return _err(
                        f"Publish failed ({pub_resp.status_code}): {_safe_text(pub_resp)}",
                        environment=env, sku=sku, offer_id=offer_id,
                        merchant_location_key=merchant_location_key, category_id=category_id,
                        package_weight_and_size=inventory_payload.get("packageWeightAndSize"),
                    )
            else:
                return _err(
                    f"Inventory item failed while adding package weight/details ({retry_inv.status_code}): {_safe_text(retry_inv)}",
                    environment=env, sku=sku, offer_id=offer_id,
                    merchant_location_key=merchant_location_key, category_id=category_id,
                    package_weight_and_size=inventory_payload.get("packageWeightAndSize"),
                )
        # If eBay reports a missing required specific, add it and retry once.
        m = re.search(r"item specific ([A-Za-z0-9 /&-]+) is missing", msg, flags=re.I)
        if m:
            missing_name = m.group(1).strip()
            aspects[missing_name] = [_infer_required_aspect_value(missing_name, product, [])]
            inventory_payload["product"]["aspects"] = aspects
            retry_inv = requests.put(f"{api_base}/sell/inventory/v1/inventory_item/{sku}", headers=hdrs, json=inventory_payload, timeout=35)
            if retry_inv.status_code in (200, 201, 204):
                pub_resp = requests.post(f"{api_base}/sell/inventory/v1/offer/{offer_id}/publish", headers=hdrs, timeout=35)
                if pub_resp.status_code in (200, 201):
                    pass
                else:
                    return _err(f"Publish failed ({pub_resp.status_code}): {_safe_text(pub_resp)}", environment=env, sku=sku, offer_id=offer_id, merchant_location_key=merchant_location_key, category_id=category_id)
            else:
                return _err(f"Inventory item failed while adding missing specific {missing_name} ({retry_inv.status_code}): {_safe_text(retry_inv)}", environment=env, sku=sku, offer_id=offer_id, merchant_location_key=merchant_location_key, category_id=category_id)
        elif "invalid shipping policy" in msg.lower() or "fulfillment policy" in msg.lower():
            return _err(
                "Publish failed because this signed-in eBay account has no valid shipping/fulfillment policy for this listing. "
                "Open eBay Seller Hub → Business Policies and create/enable a domestic fulfillment policy for this marketplace, then click 'Load my eBay policies' again. "
                f"eBay detail: {msg}",
                environment=env, sku=sku, offer_id=offer_id, merchant_location_key=merchant_location_key, category_id=category_id,
            )
        else:
            return _err(f"Publish failed ({pub_resp.status_code}): {msg}", environment=env, sku=sku, offer_id=offer_id, merchant_location_key=merchant_location_key, category_id=category_id)
    listing_id = pub_resp.json().get("listingId", "")
    listing_url = (f"https://www.ebay.com/itm/{listing_id}" if env == "production" else f"https://sandbox.ebay.com/itm/{listing_id}") if listing_id else ""
    return {"success": True, "listing_id": listing_id, "listing_url": listing_url, "offer_id": offer_id, "sku": sku, "environment": env, "merchant_location_key": merchant_location_key, "category_id": category_id, "category_source": category_source, "category_path": category_detail, "added_required_aspects": added_aspects, "error": None}
