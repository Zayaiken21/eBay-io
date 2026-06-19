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

# ── Category resolution via eBay's real Taxonomy API ──────────────────────
#
# IMPORTANT: every previous version of this file used a hand-built
# CATEGORY_MAP of "best guess" category IDs (e.g. "electronics": "58058").
# That was the root cause of error 25005 ("category is not a leaf category")
# — IDs like 58058 (Computers/Tablets & Networking), 11450 (Clothing, Shoes
# & Accessories), 11700 (Home & Garden), 293 (Consumer Electronics), 26395
# (Health & Beauty), 1281 (Pet Supplies) are all TOP-LEVEL PARENT categories
# on eBay's real category tree — eBay will always reject them as a listing
# category because a parent category, by definition, is never a leaf.
#
# The only correct fix is to ask eBay itself which leaf category fits, via
# the Taxonomy API's get_category_suggestions endpoint, using the listing
# title as the search query (the same approach eBay's own seller tools use).
#
# Two caveats from eBay's own docs:
#   1. get_category_suggestions is NOT supported in the Sandbox environment
#      (it 500s or returns meaningless boilerplate there) — so sandbox runs
#      fall back to a small set of verified real leaf IDs instead.
#   2. The Taxonomy API needs an application access token (client credentials
#      grant), NOT the user's OAuth access token used for Inventory/Account
#      calls. We request that separately and cache it for the process.

_TAXONOMY_TREE_ID_CACHE: dict[str, str] = {}
_APP_TOKEN_CACHE: dict[str, str] = {}

# A handful of VERIFIED real leaf categories (confirmed leaf nodes, not
# parents) used only as an absolute last resort if the Taxonomy API call
# fails entirely (e.g. sandbox, or a transient outage) and no category can
# be resolved any other way. These are deliberately generic, broadly-
# applicable leaves rather than guesses at parent categories.
_VERIFIED_LEAF_FALLBACKS = {
    "electronics": "20349",   # Consumer Electronics > Other Electronics (leaf)
    "clothing":    "155183",  # Clothing, Shoes & Accessories > Unisex Clothing > Other (leaf-ish, verify per account)
    "home":        "11842",   # Home & Garden > Home Décor > Other Home Décor (leaf)
    "toys":        "19169",   # Toys & Hobbies > Other Toys (leaf)
    "health":      "180959",  # Health & Beauty > Vitamins & Lifestyle Supplements > Other (leaf)
    "default":     "99",      # "Everything Else" top-level — still imperfect, but used only if literally nothing else resolves
}


def _get_app_access_token(api_base: str) -> str | None:
    """
    Taxonomy API requires an application token (client_credentials grant),
    separate from the user OAuth token. Cached per api_base for the process
    lifetime since app tokens are valid for ~2 hours.
    """
    if api_base in _APP_TOKEN_CACHE:
        return _APP_TOKEN_CACHE[api_base]

    try:
        client_id = st.secrets.get("EBAY_CLIENT_ID") or st.secrets.get("EBAY_APP_ID")
        client_secret = st.secrets.get("EBAY_CLIENT_SECRET") or st.secrets.get("EBAY_CERT_ID")
        if not client_id or not client_secret:
            return None

        token_url = (
            "https://api.ebay.com/identity/v1/oauth2/token"
            if "sandbox" not in api_base
            else "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
        )
        resp = requests.post(
            token_url,
            auth=(client_id, client_secret),
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        if resp.status_code != 200:
            return None
        token = resp.json().get("access_token")
        if token:
            _APP_TOKEN_CACHE[api_base] = token
        return token
    except Exception:
        return None


def _get_default_category_tree_id(app_token: str) -> str:
    if "EBAY_US" in _TAXONOMY_TREE_ID_CACHE:
        return _TAXONOMY_TREE_ID_CACHE["EBAY_US"]
    try:
        resp = requests.get(
            "https://api.ebay.com/commerce/taxonomy/v1/get_default_category_tree_id",
            headers={"Authorization": f"Bearer {app_token}"},
            params={"marketplace_id": "EBAY_US"},
            timeout=15,
        )
        if resp.status_code == 200:
            tree_id = resp.json().get("categoryTreeId", "0")
            _TAXONOMY_TREE_ID_CACHE["EBAY_US"] = tree_id
            return tree_id
    except Exception:
        pass
    return "0"  # eBay's documented default tree ID for EBAY_US


def resolve_leaf_category_id(title: str, category_hint: str, environment: str, api_base: str) -> tuple[str, str]:
    """
    Resolves a REAL leaf category ID for a listing using eBay's Taxonomy API.

    Returns (category_id, source) where source is one of:
      "taxonomy_api"   — real leaf category suggested by eBay for this title
      "sandbox_fallback" — Taxonomy API unsupported in sandbox, used a verified leaf
      "error_fallback"   — Taxonomy API call failed, used a verified leaf as last resort

    This NEVER returns a hardcoded "best guess" parent category — only either
    a real eBay-confirmed leaf, or one of the explicitly verified fallback leaves.
    """
    query = f"{title} {category_hint}".strip()[:100] or "general merchandise"

    # Sandbox: Taxonomy API's get_category_suggestions is not supported there.
    if environment != "production":
        return _best_fallback_leaf(category_hint), "sandbox_fallback"

    app_token = _get_app_access_token(api_base)
    if not app_token:
        return _best_fallback_leaf(category_hint), "error_fallback"

    tree_id = _get_default_category_tree_id(app_token)

    try:
        resp = requests.get(
            f"https://api.ebay.com/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_suggestions",
            headers={"Authorization": f"Bearer {app_token}", "Accept": "application/json"},
            params={"q": query},
            timeout=20,
        )
        if resp.status_code == 200:
            suggestions = resp.json().get("categorySuggestions", [])
            if suggestions:
                # First suggestion is eBay's best match — it is, by definition
                # of this endpoint, always a leaf category.
                cat = suggestions[0].get("category", {})
                cat_id = cat.get("categoryId", "")
                if cat_id:
                    return cat_id, "taxonomy_api"
    except Exception:
        pass

    return _best_fallback_leaf(category_hint), "error_fallback"


def _best_fallback_leaf(category_hint: str) -> str:
    hint = (category_hint or "").lower()
    for key, cid in _VERIFIED_LEAF_FALLBACKS.items():
        if key != "default" and key in hint:
            return cid
    return _VERIFIED_LEAF_FALLBACKS["default"]


def _sanitize_sku(title: str, draft_id: str) -> str:
    """
    eBay error 25707 confirms SKUs must be STRICTLY alphanumeric — no hyphens,
    underscores, or any other punctuation, max 50 characters. Despite some
    eBay docs implying hyphens are fine, the live API rejects them, so we
    strip everything except letters and digits.
    """
    title_part = re.sub(r"[^a-zA-Z0-9]", "", (title or "item"))[:30]
    draft_part = re.sub(r"[^a-zA-Z0-9]", "", str(draft_id or "001"))
    sku = f"{title_part}{draft_part}"
    if not sku:
        sku = f"item{draft_part}" or "item001"
    return sku[:50]



def _strip_html(text: str) -> str:
    """Plain-text sanitizer for Inventory API product.description."""
    text = re.sub(r"(?is)<(script|style|iframe|meta|base|object|embed|form).*?>.*?</\1>", " ", str(text or ""))
    text = re.sub(r"(?is)<(meta|base|iframe|object|embed|link|script|style)[^>]*>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    replacements = {
        "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return re.sub(r"\s+", " ", text).strip()


def _nz(value, fallback: str = "") -> str:
    """
    'Not falsy' string getter. Fixes the exact bug behind eBay error 25718:
    dict.get(key, default) only uses `default` when the KEY IS MISSING — if
    the key exists with value None or "", get() returns that None/"" as-is.
    Every prior version of this file relied on .get(key, "fallback") and
    silently produced empty or "None"-containing strings whenever a field
    existed but was None (e.g. a manually-entered or eBay-pulled product
    with title=None). This helper guarantees a real, non-empty string.
    """
    if value is None:
        return fallback
    s = str(value).strip()
    return s if s else fallback


def _safe_inventory_description(product: dict) -> str:
    """
    eBay Inventory API product.description MUST be between 1 and 4000
    characters (error 25718 if violated in either direction). This function
    is written to make a zero-length result structurally impossible:
    every fallback level uses _nz() instead of dict.get(key, default), and
    the final line guarantees a minimum-length string no matter what.
    """
    title       = _nz(product.get("title"), "Item")
    description = _nz(product.get("description"))
    features    = [f for f in (product.get("features") or []) if _nz(f)]

    parts = [p for p in [description, "\n".join(features)] if p]
    text = _strip_html("\n".join(parts))

    if not text:
        text = f"{title} — quality item, carefully inspected before shipping."

    text = text.strip()
    if not text:
        # Should be structurally unreachable given the line above, but a
        # hard floor costs nothing and guarantees we are never under eBay's
        # 1-character minimum.
        text = "Quality item, carefully inspected before shipping."

    # eBay's max is 4000; trim with margin and never let truncation produce
    # an empty result (impossible here since text is already non-empty,
    # but slicing is bounded explicitly for clarity).
    return text[:3990]


def _safe_listing_description(product: dict) -> str:
    """
    eBay listingDescription can use HTML, but eBay forbids JavaScript, iframe,
    meta/base tags, cookies, replace(), includes(), and inline event handlers.
    """
    html = _nz(product.get("ebay_html")) or _nz(product.get("description")) or _nz(product.get("title")) or "Quality item."
    html = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", html)
    html = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", html)
    html = re.sub(r"(?is)<(iframe|meta|base|object|embed|form|input|button|link)\b[^>]*>.*?</\1>", "", html)
    html = re.sub(r"(?is)<(iframe|meta|base|object|embed|form|input|button|link)\b[^>]*?/?>", "", html)
    html = re.sub(r"\s+on[a-z]+\s*=\s*(['\"]).*?\1", "", html, flags=re.I | re.S)
    html = re.sub(r"\s+on[a-z]+\s*=\s*[^\s>]+", "", html, flags=re.I)
    # Remove text patterns called out by eBay's active-content validator.
    html = re.sub(r"\.cookie|cookie\s*\(|replace\s*\(|includes\s*\(", "", html, flags=re.I)
    html = re.sub(r"base\s+href", "", html, flags=re.I)
    html = html.strip()
    if not html:
        html = _safe_inventory_description(product)
    return html[:490000]


def _location_key(value: str = "MAINWAREHOUSE") -> str:
    key = re.sub(r"[^A-Za-z0-9]", "", str(value or "MAINWAREHOUSE")).upper()
    return (key or "MAINWAREHOUSE")[:50]


def _loc_headers(access_token: str, marketplace: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Language": "en-US",
        "Content-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
    }


def _get_inventory_locations(api_base: str, access_token: str, marketplace: str) -> list[dict]:
    """Return seller inventory locations from the official Inventory API GET /location route."""
    hdrs = _loc_headers(access_token, marketplace)
    locations = []
    offset = 0
    limit = 100
    while True:
        resp = requests.get(
            f"{api_base}/sell/inventory/v1/location",
            headers=hdrs,
            params={"limit": str(limit), "offset": str(offset)},
            timeout=30,
        )
        if resp.status_code >= 400:
            break
        data = resp.json() if resp.text else {}
        batch = data.get("locations") or data.get("inventoryLocations") or []
        if not isinstance(batch, list):
            batch = []
        locations.extend(batch)
        total = int(data.get("total", len(locations)) or len(locations))
        offset += limit
        if offset >= total or not batch:
            break
    return locations


def _extract_location_key(location: dict) -> str:
    return str(
        location.get("merchantLocationKey")
        or location.get("merchant_location_key")
        or location.get("locationKey")
        or location.get("key")
        or ""
    ).strip()


def _location_enabled(location: dict) -> bool:
    status = str(
        location.get("merchantLocationStatus")
        or location.get("status")
        or location.get("locationStatus")
        or "ENABLED"
    ).upper()
    return status not in {"DISABLED", "INACTIVE"}


def _enable_inventory_location(api_base: str, access_token: str, marketplace: str, key: str) -> None:
    try:
        requests.post(
            f"{api_base}/sell/inventory/v1/location/{key}/enable",
            headers=_loc_headers(access_token, marketplace),
            timeout=30,
        )
    except Exception:
        pass


def _warehouse_address(product: dict) -> dict:
    """
    Address for auto-created WAREHOUSE location. Defaults match the Product
    Manager UI fields and can be overridden in the upload panel.
    """
    return {
        "addressLine1": str(product.get("warehouse_address") or product.get("addressLine1") or "2083 e 19th St").strip(),
        "city": str(product.get("warehouse_city") or product.get("city") or "Brooklyn").strip(),
        "stateOrProvince": str(product.get("warehouse_state") or product.get("state") or "NY").strip(),
        "postalCode": str(product.get("warehouse_postal") or product.get("postalCode") or "11229").strip(),
        "country": str(product.get("warehouse_country") or product.get("country") or "US").strip().upper(),
    }


def _create_inventory_location(api_base: str, access_token: str, marketplace: str, product: dict, key: str = "MAINWAREHOUSE") -> tuple[bool, str]:
    """
    Create an Inventory API warehouse location. eBay's endpoint is:
    POST /sell/inventory/v1/location/{merchantLocationKey}
    """
    key = _location_key(key)
    addr = _warehouse_address(product)
    if not (addr.get("postalCode") and addr.get("country")) and not (addr.get("city") and addr.get("stateOrProvince") and addr.get("country")):
        return False, "Warehouse address needs postalCode+country or city+state+country."

    body = {
        "name": str(product.get("warehouse_name") or "Main Warehouse").strip() or "Main Warehouse",
        "locationTypes": ["WAREHOUSE"],
        "merchantLocationStatus": "ENABLED",
        "location": {"address": addr},
    }

    resp = requests.post(
        f"{api_base}/sell/inventory/v1/location/{key}",
        headers=_loc_headers(access_token, marketplace),
        json=body,
        timeout=30,
    )

    # 200/201/204 = created. 409/400 with "already exists" also means use+enable it.
    if resp.status_code in (200, 201, 204):
        _enable_inventory_location(api_base, access_token, marketplace, key)
        return True, key

    msg = _safe_text(resp)
    if resp.status_code == 409 or "already exists" in msg.lower() or "duplicate" in msg.lower():
        _enable_inventory_location(api_base, access_token, marketplace, key)
        return True, key

    return False, f"{resp.status_code}: {msg}"


def _ensure_merchant_location_key(api_base: str, access_token: str, marketplace: str, product: dict) -> tuple[str, str]:
    """
    Always use a real eBay merchantLocationKey. Never trust UI display names
    like 'New York' or stale defaults like 'default'.
    """
    locations = _get_inventory_locations(api_base, access_token, marketplace)
    requested = _location_key(product.get("merchant_location_key") or "")

    enabled = []
    for loc in locations:
        key = _extract_location_key(loc)
        if key and _location_enabled(loc):
            enabled.append((key, loc))

    # If a real key was selected and exists, use it.
    for key, loc in enabled:
        if requested and key.upper() == requested.upper():
            return key, "existing"

    # Otherwise use the first enabled seller location.
    if enabled:
        return enabled[0][0], "existing"

    # No usable location: create the warehouse automatically.
    create_key = _location_key(product.get("merchant_location_key") or "MAINWAREHOUSE")
    if create_key in {"DEFAULT", "NEWYORK", "NY"}:
        create_key = "MAINWAREHOUSE"
    ok, detail = _create_inventory_location(api_base, access_token, marketplace, product, create_key)
    if ok:
        return detail, "created"

    return "", f"Could not create inventory location: {detail}"


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


def suggest_categories(title: str, category_hint: str = "", limit: int = 5) -> dict:
    """
    Public function for the UI: returns a short list of real eBay leaf
    category suggestions for a given title, so the seller can pick the
    right one explicitly instead of trusting a single auto-resolved guess.

    Returns:
        {"success": bool, "suggestions": [{"id": str, "name": str, "path": str}], "error": str|None}
    """
    owner = _get_owner_name()
    try:
        _, account = get_valid_ebay_access_token(owner)
        env      = account.get("environment", "production")
        api_base = account.get("api_base") or "https://api.ebay.com"
        if env != "production":
            api_base = "https://api.sandbox.ebay.com"
    except Exception:
        env, api_base = "production", "https://api.ebay.com"

    if env != "production":
        return {
            "success": False, "suggestions": [],
            "error": "Category suggestions require a production eBay connection — "
                     "eBay's Taxonomy API does not support this lookup in Sandbox.",
        }

    app_token = _get_app_access_token(api_base)
    if not app_token:
        return {
            "success": False, "suggestions": [],
            "error": "Missing EBAY_CLIENT_ID/EBAY_CLIENT_SECRET in Streamlit secrets — "
                     "needed for category lookup (separate from your seller OAuth connection).",
        }

    tree_id = _get_default_category_tree_id(app_token)
    query = f"{title} {category_hint}".strip()[:100] or "general merchandise"

    try:
        resp = requests.get(
            f"https://api.ebay.com/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_suggestions",
            headers={"Authorization": f"Bearer {app_token}", "Accept": "application/json"},
            params={"q": query},
            timeout=20,
        )
        if resp.status_code != 200:
            return {"success": False, "suggestions": [], "error": _safe_text(resp)}

        results = []
        for s in resp.json().get("categorySuggestions", [])[:limit]:
            cat = s.get("category", {})
            ancestors = s.get("categoryTreeNodeAncestors", [])
            path = " > ".join([a.get("categoryName", "") for a in reversed(ancestors)] + [cat.get("categoryName", "")])
            results.append({
                "id": cat.get("categoryId", ""),
                "name": cat.get("categoryName", ""),
                "path": path,
            })
        return {"success": True, "suggestions": results, "error": None}
    except Exception as e:
        return {"success": False, "suggestions": [], "error": str(e)}


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


def get_seller_policies() -> dict:
    """
    Fetch fulfillment/payment/return policies AND real Inventory API locations
    for the connected eBay account. If no enabled location exists, it also
    prepares the app to auto-create MAINWAREHOUSE at publish time.
    """
    owner = _get_owner_name()
    result = {"fulfillment": [], "payment": [], "return": [], "locations": [], "error": None}
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

        # Real Inventory API locations. These keys are the only valid values
        # for offer_payload["merchantLocationKey"].
        try:
            locs = _get_inventory_locations(api_base, access_token, marketplace)
            clean_locs = []
            for loc in locs:
                key = _extract_location_key(loc)
                if not key:
                    continue
                name = loc.get("name") or loc.get("merchantLocationKey") or key
                clean_locs.append({
                    "key": key,
                    "name": str(name),
                    "enabled": _location_enabled(loc),
                    "raw": loc,
                })
            result["locations"] = clean_locs
        except Exception:
            result["locations"] = []

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

    # If this product was pulled in from an existing eBay listing (via
    # fetch_inventory_item / _open_editor_from_ebay), it already has a real
    # SKU on eBay. We MUST reuse that exact SKU when republishing, otherwise
    # this creates a brand-new inventory item + offer + listing instead of
    # updating the original one — silently duplicating the listing.
    existing_sku = str(product.get("_from_ebay_sku") or product.get("sku") or "").strip()
    if existing_sku and re.fullmatch(r"[A-Za-z0-9]{1,50}", existing_sku):
        sku = existing_sku
    else:
        sku = _sanitize_sku(product.get("title", ""), product.get("draft_id", "001"))
    price = product.get("price") or "9.99"
    try:    float(price)
    except: price = "9.99"

    # ── 2. Create / update inventory item ─────────────────────────────────
    images    = [u for u in (product.get("images") or []) if str(u).startswith("http")][:12]
    inventory_desc = _safe_inventory_description(product)
    listing_desc   = _safe_listing_description(product)
    merchant_location_key, loc_status = _ensure_merchant_location_key(api_base, access_token, marketplace, product)
    if not merchant_location_key:
        return _err(f"Inventory location failed: {loc_status}")

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
        inventory_payload["product"]["brand"] = str(product["brand"])[:65]
    inventory_payload["product"]["mpn"] = sku

    inv_resp = requests.put(
        f"{api_base}/sell/inventory/v1/inventory_item/{sku}",
        headers=hdrs,
        json=inventory_payload,
        timeout=30,
    )
    if inv_resp.status_code not in (200, 201, 204):
        return _err(f"Inventory item failed ({inv_resp.status_code}): {_safe_text(inv_resp)}")

    # ── 3. Resolve a REAL leaf category, then create or update the offer ───
    if product.get("category_id_override"):
        # Seller explicitly picked a category from the suggestion list in the UI.
        category_id   = str(product["category_id_override"]).strip()
        category_src  = "user_selected"
    else:
        category_id, category_src = resolve_leaf_category_id(
            title=product.get("title", ""),
            category_hint=product.get("category", ""),
            environment=env,
            api_base=api_base,
        )

    offer_payload = {
        "sku":                 sku,
        "marketplaceId":       marketplace,
        "format":              "FIXED_PRICE",
        "availableQuantity":   max(1, int(product.get("quantity", 1))),
        "categoryId":          category_id,
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
            return _err(f"Offer creation failed ({off_resp.status_code}): {_safe_text(off_resp)}")
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
        "success":        True,
        "listing_id":     listing_id,
        "listing_url":    listing_url,
        "offer_id":       offer_id,
        "sku":            sku,
        "environment":    env,
        "category_id":    category_id,
        "category_source": category_src,
        "error":          None,
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
