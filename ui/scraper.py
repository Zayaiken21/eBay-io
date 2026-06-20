"""
scraper.py — Universal product scraper. No AI, no API keys.
Covers: Amazon, eBay, Walmart, AliExpress, Temu, Etsy, Target,
        Best Buy, Home Depot, Shopify stores, and generic sites.
Strategy per site: JSON-LD → Open Graph → domain-specific selectors → heuristics.
"""

import re, time, random, json
import requests
from urllib.parse import urlparse, urljoin

try:
    from bs4 import BeautifulSoup
    BS4 = True
except ImportError:
    BS4 = False

# ── Rotate user agents to avoid simple blocks ─────────────────────────────
_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

def _headers():
    return {
        "User-Agent": random.choice(_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }

def get_domain(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


# ── Embedded data-island extraction ────────────────────────────────────────
# This is the single highest-leverage addition for "heavy JS" sites: most
# React/Vue/Next.js/Nuxt storefronts still render an initial JSON payload
# server-side and embed it in a <script> tag, even though the VISIBLE page
# is built client-side from that data. A plain requests.get() can't run the
# JS that builds the visible DOM, but it CAN read this embedded JSON
# directly — often more complete and more reliable than scraping rendered
# HTML even when rendering does work.
#
# This covers a large share of sites that look "JS-only" but actually still
# ship real product data in the raw response.
_DATA_ISLAND_PATTERNS = [
    # Next.js (extremely common: Target, many modern storefronts)
    (r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', "json"),
    # Nuxt.js (Vue-based storefronts)
    (r'window\.__NUXT__\s*=\s*(\{.*?\});?\s*</script>', "js_object"),
    # Generic Redux/Apollo initial state patterns
    (r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>', "js_object"),
    (r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*</script>', "js_object"),
    (r'window\.__APOLLO_STATE__\s*=\s*(\{.*?\});?\s*</script>', "js_object"),
    # Shopify-native analytics object — present on nearly every Shopify
    # storefront regardless of theme, contains clean product data.
    (r'window\.ShopifyAnalytics\.meta\.product\s*=\s*(\{.*?\});', "js_object"),
    (r'var meta\s*=\s*(\{.*?"product".*?\});', "js_object"),
    # BigCommerce
    (r'window\.BCData\s*=\s*(\{.*?\});?\s*</script>', "js_object"),
]


def _extract_data_islands(html: str) -> list:
    """
    Returns a list of parsed dicts found in embedded JS data islands.
    Best-effort: malformed/truncated JSON is silently skipped rather than
    raising, since these regexes are inherently approximate.
    """
    found = []
    for pattern, _kind in _DATA_ISLAND_PATTERNS:
        for m in re.finditer(pattern, html, re.DOTALL):
            raw = m.group(1)
            parsed = _try_parse_json_loose(raw)
            if isinstance(parsed, dict):
                found.append(parsed)
    return found


def _try_parse_json_loose(raw: str):
    """Attempt strict JSON parse; on failure, trim to the last balanced brace and retry once."""
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Trim trailing garbage after the JSON object commonly left by the regex
    # capturing too much (e.g. a trailing `;</script>` fragment).
    depth, end = 0, -1
    for i, ch in enumerate(raw):
        if ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end > 0:
        try:
            return json.loads(raw[:end])
        except Exception:
            return None
    return None


def _search_nested(obj, keys: set, max_depth: int = 8) -> dict:
    """
    Recursively search a nested dict/list structure for the first dict that
    contains ANY of the target keys (e.g. {"title","name"} for a product
    title). Used to pull product fields out of arbitrary Next.js/Nuxt state
    blobs whose exact shape varies site to site.
    """
    found = {}

    def walk(node, depth):
        if depth > max_depth or (found and len(found) >= len(keys)):
            return
        if isinstance(node, dict):
            for k in keys:
                if k in node and node[k] and k not in found:
                    found[k] = node[k]
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for item in node[:30]:  # cap breadth to avoid pathological scans
                walk(item, depth + 1)

    walk(obj, 0)
    return found


def _fields_from_data_islands(html: str) -> dict:
    """
    Pulls title/price/description/images/brand/sku candidates out of any
    embedded data islands found on the page. Returns whatever it finds —
    callers treat this as one more source to merge, not an all-or-nothing
    replacement for HTML-based extraction.
    """
    islands = _extract_data_islands(html)
    if not islands:
        return {}

    target_keys = {
        "title", "name", "productTitle",
        "price", "currentPrice", "salePrice",
        "description", "body_html", "descriptionHtml",
        "images", "image", "media",
        "brand", "vendor", "manufacturer",
        "sku", "id", "productId",
    }

    merged = {}
    for island in islands:
        result = _search_nested(island, target_keys)
        for k, v in result.items():
            merged.setdefault(k, v)

    return merged


def _try_shopify_json_endpoint(url: str, session: requests.Session, headers: dict, timeout: int):
    """
    Most Shopify stores expose a clean JSON product endpoint at
    {product-url}.json — this returns fully structured product data
    (title, full HTML description, all variants/prices, all images) with
    zero scraping required, completely sidestepping any JS-rendering issue.
    Only attempted for URLs that look like Shopify product pages
    (contain /products/); silently returns None if unavailable.
    """
    if "/products/" not in url:
        return None
    base = url.split("?")[0].rstrip("/")
    json_url = base + ".json"
    try:
        resp = session.get(json_url, headers=headers, timeout=timeout)
        if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""):
            data = resp.json()
            if isinstance(data, dict) and "product" in data:
                return data["product"]
    except Exception:
        pass
    return None


# ── Bot-wall / CAPTCHA detection ──────────────────────────────────────────
# Plain HTTP requests can't run JavaScript or pass interactive challenges
# ("press and hold" buttons, image grids, etc). When a major retailer's
# anti-bot system intercepts the request, it returns a *real* HTTP 200 page
# that just happens to be a challenge page instead of product content. If we
# don't detect this, the parser will confidently extract garbage like
# "Robot or human?" as the product title. This check stops that.
_BOT_WALL_SIGNALS = [
    "robot or human", "press and hold", "verify you are human", "are you a robot",
    "captcha", "/distil_r_captcha", "perimeterx", "_px-captcha", "px-captcha",
    "akamai bot manager", "datadome", "cf-challenge", "challenge-platform",
    "checking your browser", "just a moment...", "enable javascript and cookies",
    "unusual traffic from your computer", "automated access", "bot detection",
    "human verification", "please verify you are a human", "ddos protection by",
]

def _looks_like_bot_wall(html: str, resp_status: int) -> bool:
    if not html:
        return False
    lowered = html.lower()
    hits = sum(1 for sig in _BOT_WALL_SIGNALS if sig in lowered)
    # A real product page is large; bot-wall pages are typically tiny and
    # almost entirely chrome/script with no real product markup.
    is_suspiciously_small = len(html) < 15000
    has_no_product_signals = ("og:title" not in lowered and "application/ld+json" not in lowered)
    return hits >= 1 and (is_suspiciously_small or has_no_product_signals)


# ── "JS rendering required" detection ──────────────────────────────────────
# Distinct from a bot wall: this is a normal 200 response, no CAPTCHA, but
# the page body is just an empty React/Vue mount point with no real content
# anywhere — meaning the product data genuinely doesn't exist until client
# JS runs. Diagnosing this separately from a bot-wall means we can tell the
# seller the TRUE reason their import returned almost nothing, instead of a
# generic "low confidence" result that looks like a parsing bug.
_JS_SHELL_SIGNALS = [
    '<div id="root"></div>', '<div id="app"></div>', '<div id="__next"></div>',
    'id="root">​</div>', 'you need to enable javascript to run this app',
    'noscript', '<div id="react-root"></div>',
]


def _looks_like_js_shell(html: str, has_data_islands: bool) -> bool:
    if not html or has_data_islands:
        return False
    lowered = html.lower()
    body_match = re.search(r'<body[^>]*>(.*?)</body>', lowered, re.DOTALL)
    body = body_match.group(1) if body_match else lowered
    visible_text = re.sub(r'<script.*?</script>', '', body, flags=re.DOTALL)
    visible_text = re.sub(r'<[^>]+>', '', visible_text).strip()
    shell_signal_hit = any(sig in lowered for sig in _JS_SHELL_SIGNALS)
    almost_no_visible_text = len(visible_text) < 200
    no_structured_data = "application/ld+json" not in lowered and "og:title" not in lowered
    return (shell_signal_hit or almost_no_visible_text) and no_structured_data

# ── Site-specific selectors ────────────────────────────────────────────────
SITE_RULES = {
    "amazon": {
        "title":       ["#productTitle", "#title span"],
        "price":       [".a-price .a-offscreen", "#priceblock_ourprice", "#priceblock_dealprice", ".a-price-whole"],
        "description": ["#productDescription p", "#feature-bullets", "#aplus"],
        "features":    ["#feature-bullets li span.a-list-item"],
        "specs_table": ["#productDetails_techSpec_section_1 tr", "#productDetails_detailBullets_sections1 tr",
                        ".prodDetTable tr", "#detailBulletsWrapper_feature_div li"],
        "images":      ["#imgTagWrapperId img", "#landingImage", "#altImages img"],
        "brand":       ["#bylineInfo", "#brand", 'a#bylineInfo'],
        "breadcrumb":  ["#wayfinding-breadcrumbs_container a", ".a-breadcrumb a"],
    },
    "ebay": {
        "title":       ["h1.x-item-title__mainTitle span", ".x-item-title span", "h1#itemTitle"],
        "price":       [".x-price-primary span", "#prcIsum", ".notranslate"],
        "description": [".ux-layout-section__item--table-view", "#ds_div", ".itemAttr"],
        "features":    [".ux-layout-section .ux-labels-values__labels"],
        "specs_table": [".ux-layout-section__item--table-view tr", ".itemAttr tr"],
        "images":      ["#icImg", ".ux-image-carousel-item img", ".vi-image-gallery__image img"],
        "brand":       [".ux-seller-section__item--seller"],
        "breadcrumb":  ["nav.breadcrumbs a", "#vi-VR-brumb-lnkLst a"],
    },
    "walmart": {
        "title":       ['[itemprop="name"]', 'h1[itemprop="name"]', ".prod-ProductTitle"],
        "price":       ['[itemprop="price"]', ".price-characteristic", '[data-automation="buybox-price"]'],
        "description": ['[data-testid="product-description"]', ".about-product-description", ".dangerous-html"],
        "features":    ['[data-testid="product-highlights"] li', ".product-specification li"],
        "specs_table": ['[data-testid="specifications"] tr', ".specifications-table tr"],
        "images":      ['[data-testid="media-thumbnail"] img', ".hover-zoom-hero-image", ".prod-hero-image img"],
        "brand":       ['[itemprop="brand"]', ".prod-brandName"],
        "breadcrumb":  ['[data-testid="breadcrumb"] a', ".breadcrumb-list a"],
    },
    "aliexpress": {
        "title":       [".product-title-text", "h1.product-title", ".title--wrap--UUHae_e h1"],
        "price":       [".product-price-value", ".uniform-banner-box-price", ".snow-price_SnowPrice__mainS__jAHput"],
        "description": [".product-description", ".detail-desc-decorate-richtext", ".pc-dynamic-render"],
        "features":    [".product-prop li", ".specification--list--LevYqHO li"],
        "specs_table": [".specification--list--LevYqHO li", ".product-prop-list li"],
        "images":      [".magnifier-image", ".slider-item img", ".img-view-item img"],
        "brand":       [".product-brand a"],
        "breadcrumb":  [".bread-crumb a", ".breadcrumb a"],
    },
    "temu": {
        "title":       ["h1._1y3Gq", "h1.title", '[data-testid="product-title"]'],
        "price":       ["._2Nj1f", ".price", '[data-testid="product-price"]'],
        "description": [".description-content", '[data-testid="product-description"]'],
        "features":    [".product-info li"],
        "specs_table": [".product-spec li", ".spec-list li"],
        "images":      [".product-img img", ".swiper-slide img", '[data-testid="product-image"] img'],
        "brand":       [],
        "breadcrumb":  [".breadcrumb a"],
    },
    "etsy": {
        "title":       ["h1[data-buy-box-listing-title]", ".wt-text-body-03", "h1.wt-text-body-01"],
        "price":       [".wt-text-title-largest", '[data-testid="price"]', ".currency-value"],
        "description": ["#wt-content-toggle--description", ".wt-content-toggle__body"],
        "features":    ["#product-attributes-content li"],
        "specs_table": ["#product-attributes-content .wt-pb-xs-1"],
        "images":      ["img.wt-max-width-full", "[data-img-loaded] img", ".carousel-pane img"],
        "brand":       [".wt-text-link-no-underline"],
        "breadcrumb":  ["nav[aria-label='breadcrumb'] a"],
    },
    "target": {
        "title":       ['[data-test="product-title"]', "h1"],
        "price":       ['[data-test="product-price"]', ".sx-price-display"],
        "description": ['[data-test="item-description"]', ".ProductDetailsPaneInfo"],
        "features":    ['[data-test="item-details-description"] li'],
        "specs_table": ['[data-test="item-details-specifications"] li'],
        "images":      ['[data-test="product-image"] img', ".GalleryThumbnail--image"],
        "brand":       ['[data-test="product-brand"]'],
        "breadcrumb":  ['[data-test="breadcrumb"] a'],
    },
    "bestbuy": {
        "title":       [".sku-title h1", "h1.heading-5"],
        "price":       [".priceView-customer-price span", ".priceView-price span"],
        "description": [".product-description", ".html-fragment"],
        "features":    [".feature-list li", ".product-features li"],
        "specs_table": [".specification-list li", "#specsDrawer tr"],
        "images":      [".primary-image img", ".carousel__slide img"],
        "brand":       [".brand-link"],
        "breadcrumb":  [".breadcrumb-list a"],
    },
    "homedepot": {
        "title":       ["h1.product-title__title", "span.product-title__title"],
        "price":       ['[data-testid="price-format__main-price"]', ".price-format__main-price"],
        "description": ["[class*='product-description']", ".desktop-content-wrapper__main-description"],
        "features":    ["ul.product-section-list li"],
        "specs_table": [".specifications-and-dimensions tr", "[class*='specifications'] tr"],
        "images":      [".mediagallery__image img", ".ProductImage__image img"],
        "brand":       [".product-title__brand a"],
        "breadcrumb":  ["[class*='breadcrumb'] a"],
    },
    "shein": {
        "title":       ["h1.product-intro__head-name", ".product-intro__head-name"],
        "price":       [".product-intro__head-mainprice", ".original"],
        "description": [".product-intro__description", ".product-intro-description"],
        "features":    [".product-intro__size-radio", ".attr-list li"],
        "specs_table": [".product-intro__attr li", ".attr-list li"],
        "images":      [".product-intro__main-img img", ".sw-product-img__main img", ".crop-image-container img"],
        "brand":       [],
        "breadcrumb":  [".bread-crumb a"],
    },
    "wish": {
        "title":       ["h1[data-testid='product-name']", "h1.product-name"],
        "price":       ["[data-testid='primary-price']", ".product-price"],
        "description": ["[data-testid='product-description']", ".product-description"],
        "features":    [".product-details li"],
        "specs_table": [".product-specifications li"],
        "images":      ["[data-testid='product-image'] img", ".product-image img"],
        "brand":       [],
        "breadcrumb":  [".breadcrumb a"],
    },
    "newegg": {
        "title":       ["h1.product-title"],
        "price":       [".price-current"],
        "description": [".product-bullets", "#Specs-Block"],
        "features":    [".product-bullets li"],
        "specs_table": [".table-horizontal tr", ".spec-table tr"],
        "images":      [".product-view-img-original", ".thumbnail-list img"],
        "brand":       [".product-title-brand a"],
        "breadcrumb":  [".breadcrumb a"],
    },
    "costco": {
        "title":       ["h1.product-h1", "h1[automation-id='productTitle']"],
        "price":       [".value", "[automation-id='productPriceOutput']"],
        "description": ["#product-details", ".product-info-description"],
        "features":    ["#product-details li", ".product-info-description li"],
        "specs_table": [".product-specifications tr"],
        "images":      [".product-image-main img", ".thumb-image img"],
        "brand":       [],
        "breadcrumb":  [".breadcrumb a"],
    },
    "wayfair": {
        "title":       ["h1[data-enzyme-id='ProductTitle']", "h1.pl-Heading"],
        "price":       ["[data-enzyme-id='PriceBlock'] span", ".SFPrice"],
        "description": ["[data-enzyme-id='ProductOverview']", ".ProductDetailOverview"],
        "features":    ["[data-enzyme-id='ProductOverview'] li"],
        "specs_table": ["[data-enzyme-id='SpecificationsSection'] tr", ".DimensionsAndSpecifications tr"],
        "images":      ["[data-enzyme-id='ProductDetailImage'] img", ".ImageComponent img"],
        "brand":       ["[data-enzyme-id='ProductOverviewBrandName']"],
        "breadcrumb":  [".Breadcrumbs a"],
    },
    "macys": {
        "title":       ["h1.product-name", "h1[data-auto='product-title']"],
        "price":       [".price-reg", "[data-auto='product-price']"],
        "description": [".bullets", "#productDetailsTabs"],
        "features":    [".bullets li"],
        "specs_table": ["#productDetailsTabs li"],
        "images":      [".main-image img", ".thumbnail-image img"],
        "brand":       [".brand-name a"],
        "breadcrumb":  [".breadcrumbs a"],
    },
    "lowes": {
        "title":       ["h1[data-selectortype='title']", "h1.title"],
        "price":       ["[data-testid='mn-pdp-price']", ".main-price"],
        "description": ["[data-testid='description']", "#descriptionSection"],
        "features":    ["[data-testid='key-features'] li", ".feature-bullets li"],
        "specs_table": ["#specsSection tr", ".specs-table tr"],
        "images":      [".pdp-image img", "[data-testid='product-image'] img"],
        "brand":       [".brand-image img"],
        "breadcrumb":  [".breadcrumb a"],
    },
}

def _rules_for(domain: str) -> dict:
    for key, rules in SITE_RULES.items():
        if key in domain:
            return rules
    return {}


# ── Main entry point ───────────────────────────────────────────────────────

# Domains known to run enterprise-grade bot management (PerimeterX, Akamai
# Bot Manager, etc.) where a different User-Agent on retry has essentially
# zero chance of getting through — these systems fingerprint TLS/HTTP2
# behavior, not just headers. Retrying 4 times against these just makes the
# person wait ~30+ seconds to be told what we could tell them in 2 seconds.
# These still get exactly ONE real attempt (sites do occasionally change
# their protection), just no slow multi-retry dance.
_HARD_BOT_WALL_DOMAINS = [
    "walmart.com", "amazon.com", "target.com", "bestbuy.com",
]


def fetch_product_page(url: str, timeout: int = 15, max_retries: int = 4) -> dict:
    """
    Fetches and parses a product page. Designed to work across any e-commerce
    site, not just the ones with dedicated SITE_RULES — unknown domains fall
    through to generic JSON-LD / Open Graph / heuristic extraction.

    Fails fast (1 attempt, ~2-4 seconds) for domains known to run enterprise
    bot management where retrying with a new User-Agent has no realistic
    chance of success. Otherwise retries with exponential backoff, capped
    much lower than before so a failing import reports back in seconds, not
    over a minute, while still giving soft rate-limits a real chance to clear.
    """
    domain = get_domain(url)
    is_hard_wall_domain = any(d in domain for d in _HARD_BOT_WALL_DOMAINS)
    effective_retries = 1 if is_hard_wall_domain else max_retries
    last_error = None

    for attempt in range(1, effective_retries + 1):
        # Backoff before retries (not before the first attempt). Capped at
        # 3s instead of 8s — a person waiting on an import should see a
        # result within a few seconds, not nearly a minute on worst case.
        if attempt > 1:
            time.sleep(min(1.5 * attempt, 3) + random.uniform(0, 0.5))
        else:
            time.sleep(random.uniform(0.15, 0.4))

        try:
            s = requests.Session()
            hdrs = _headers()
            # A referer makes the request look like organic navigation —
            # helps on Amazon, Walmart, and several Shopify storefronts.
            hdrs["Referer"] = random.choice([
                "https://www.google.com/", "https://www.bing.com/", "https://duckduckgo.com/",
            ])
            resp = s.get(url, headers=hdrs, timeout=timeout, allow_redirects=True)

            # Retry on transient/blocking status codes; fail fast on permanent ones.
            if resp.status_code in (429, 503, 502, 504):
                last_error = f"HTTP {resp.status_code} (temporary) on attempt {attempt}/{effective_retries}"
                continue
            if resp.status_code == 403 and attempt < effective_retries:
                last_error = f"HTTP 403 on attempt {attempt}/{effective_retries} — retrying with new identity"
                continue
            if resp.status_code == 403 and is_hard_wall_domain:
                # Known hard-wall domain, single attempt already failed —
                # don't bother parsing, go straight to the bot-wall result.
                return _bot_wall_result(domain)

            resp.raise_for_status()
            html = resp.text

            if _looks_like_bot_wall(html, resp.status_code):
                if attempt < effective_retries:
                    last_error = "bot-check page detected — retrying with a different identity"
                    continue
                else:
                    return _bot_wall_result(domain)

            break

        except requests.exceptions.HTTPError as e:
            # BUG FIX: requests.Response defines __bool__ as self.ok, which is
            # False for any 4xx/5xx status. "if e.response else 0" silently
            # discarded the real status code on every single HTTP error,
            # always reporting code=0 regardless of the actual response.
            # Must check "is not None" explicitly, never truthiness.
            code = e.response.status_code if e.response is not None else 0
            if attempt >= effective_retries:
                if code == 403 and is_hard_wall_domain:
                    return _bot_wall_result(domain)
                msg = {
                    403: "Access denied (403) after multiple attempts. This site actively blocks automated requests — paste the product details manually instead.",
                    404: "Page not found (404). Check the URL is correct.",
                }.get(code, f"HTTP {code}: could not fetch page after {effective_retries} attempt(s).")
                return _err(msg)
            last_error = str(e)
            continue
        except requests.exceptions.Timeout:
            if attempt >= effective_retries:
                return _err(f"Request timed out after {effective_retries} attempt(s). The site may be very slow or blocking automated traffic.")
            last_error = "timeout"
            continue
        except requests.exceptions.ConnectionError as e:
            if attempt >= effective_retries:
                return _err(f"Could not connect to {domain} after {effective_retries} attempt(s): {e}")
            last_error = str(e)
            continue
        except Exception as e:
            return _err(str(e))
    else:
        return _err(f"Failed after {effective_retries} attempt(s). Last error: {last_error}")

    if not BS4:
        return _err("BeautifulSoup not installed. Run: pip install beautifulsoup4 lxml")

    # ── Try the Shopify-native JSON endpoint first ─────────────────────────
    # If this succeeds, it's the cleanest possible data source — fully
    # structured, no scraping/parsing guesswork at all. Works regardless of
    # whether the visible page is JS-rendered, since it's a separate
    # dedicated data endpoint, not the page itself.
    shopify_product = _try_shopify_json_endpoint(url, s, hdrs, timeout)
    if shopify_product:
        product = _product_from_shopify_json(shopify_product, url, domain)
        _fill_defaults(product)
        return {"success": True, "product": product, "note": None, "error": None}

    soup = BeautifulSoup(html, "lxml" if _lxml() else "html.parser")
    rules = _rules_for(domain)

    # ── Pull anything available from embedded JS data islands ─────────────
    # Done BEFORE HTML-based extraction so island data can fill gaps that
    # selector-based scraping misses on JS-heavy pages.
    island_fields = _fields_from_data_islands(html)

    product = _extract_all(soup, html, url, domain, rules, island_fields)
    _fill_defaults(product)

    note = None
    has_islands = bool(island_fields)
    is_js_shell = _looks_like_js_shell(html, has_islands)

    if is_js_shell and product.get("confidence") == "low":
        note = (
            f"{domain} renders its product page almost entirely with JavaScript, and this page had no "
            f"embedded data we could recover either. A plain page fetch cannot see content that only "
            f"appears after JavaScript runs — this is a real technical limit, not a parsing bug. "
            f"Please use Manual Entry to paste in the product details."
        )
    elif has_islands and product.get("confidence") in ("medium", "high"):
        note = f"Recovered product data from {domain}'s embedded page data — results should be solid."
    elif any(s in domain for s in ["aliexpress", "temu", "shein"]) and not product.get("images"):
        note = f"{domain} is JS-heavy — some images may not load. Add image URLs manually in the editor."
    elif domain not in "".join(SITE_RULES.keys()) and product.get("confidence") == "low":
        note = (f"{domain} isn't a site we have dedicated extraction rules for — we used generic "
                f"JSON-LD/Open Graph/microdata/heuristic parsing. Results may be partial; fill in any gaps manually.")

    return {"success": True, "product": product, "note": note, "error": None}


def _product_from_shopify_json(sp: dict, url: str, domain: str) -> dict:
    """Builds our standard product dict directly from Shopify's clean .json endpoint response."""
    variants = sp.get("variants") or []
    price = ""
    if variants:
        price = _clean_price(str(variants[0].get("price", "")))

    images = []
    for img in (sp.get("images") or []):
        src = img.get("src") if isinstance(img, dict) else img
        if src:
            images.append(src if str(src).startswith("http") else f"https:{src}")

    options = sp.get("options") or []
    variant_labels = [o.get("name", "") for o in options if isinstance(o, dict) and o.get("name")]

    specs = {}
    if sp.get("product_type"):
        specs["Product Type"] = sp["product_type"]
    if sp.get("tags"):
        specs["Tags"] = ", ".join(sp["tags"][:10]) if isinstance(sp["tags"], list) else str(sp["tags"])

    return {
        "domain": domain, "source_url": url, "status": "draft",
        "condition": "New", "currency": "USD",
        "title": _clean_title(sp.get("title", "")),
        "price": price,
        "brand": sp.get("vendor", ""),
        "category": sp.get("product_type", ""),
        "description": _clean_html(sp.get("body_html", "")),
        "features": [],
        "specifications": specs,
        "images": images[:12],
        "sku": (variants[0].get("sku", "") if variants else ""),
        "weight": (f"{variants[0].get('grams', 0)/1000:.2f} kg" if variants and variants[0].get("grams") else ""),
        "dimensions": "",
        "variants": variant_labels,
        "tags": _tags(sp.get("title", ""), [], sp.get("product_type", ""), sp.get("vendor", "")),
        "confidence": "high",
    }




def _extract_all(soup, html: str, url: str, domain: str, rules: dict, island_fields: dict = None) -> dict:
    island_fields = island_fields or {}
    p = {}
    p["domain"]     = domain
    p["source_url"] = url
    p["status"]     = "draft"
    p["condition"]  = "New"
    p["currency"]   = "USD"

    p["title"]         = _title(soup, html, rules) or _island_str(island_fields, ["title", "name", "productTitle"])
    p["price"]         = _price(soup, html, rules) or _island_price(island_fields)
    p["brand"]         = _brand(soup, html, rules) or _island_str(island_fields, ["brand", "vendor", "manufacturer"])
    p["category"]      = _category(soup, html, rules)
    p["description"]   = _description(soup, html, domain, rules) or _island_str(island_fields, ["description", "body_html", "descriptionHtml"])
    p["features"]      = _features(soup, html, rules)
    p["specifications"]= _specifications(soup, html, domain, rules)
    p["images"]        = _images(soup, html, url, rules) or _island_images(island_fields, url)
    p["sku"]           = _sku(soup, html) or _island_str(island_fields, ["sku", "id", "productId"])
    p["weight"]        = _spec_val(p["specifications"], ["weight","item weight","net weight","shipping weight"])
    p["dimensions"]    = _spec_val(p["specifications"], ["dimensions","size","item dimensions","product dimensions","package dimensions"])
    p["variants"]      = []
    p["tags"]          = _tags(p["title"], p["features"], p["category"], p["brand"])
    p["confidence"]    = _confidence(p)
    return p


def _island_str(fields: dict, keys: list) -> str:
    for k in keys:
        v = fields.get(k)
        if v:
            if isinstance(v, str):
                return _clean_html(v) if "<" in v else v.strip()
            if isinstance(v, (int, float)):
                return str(v)
    return ""


def _island_price(fields: dict) -> str:
    for k in ["price", "currentPrice", "salePrice"]:
        v = fields.get(k)
        if v is not None:
            c = _clean_price(str(v))
            if c:
                return c
    return ""


def _island_images(fields: dict, base_url: str) -> list:
    out = []
    for k in ["images", "image", "media"]:
        v = fields.get(k)
        if isinstance(v, list):
            for item in v:
                url_val = None
                if isinstance(item, str):
                    url_val = item
                elif isinstance(item, dict):
                    url_val = item.get("src") or item.get("url") or item.get("originalSrc")
                if url_val:
                    out.append(url_val if str(url_val).startswith("http") else urljoin(base_url, str(url_val)))
        elif isinstance(v, str) and v.startswith("http"):
            out.append(v)
    return out[:12]


# ── Field extractors ───────────────────────────────────────────────────────

def _title(soup, html, rules):
    # 1. OG
    v = _og(soup, "og:title")
    if v and len(v) > 5: return _clean_title(v)
    # 1b. Microdata
    tag = soup.find(attrs={"itemprop": "name"})
    if tag:
        t = tag.get("content") or tag.get_text(strip=True)
        if t and len(t) > 5: return _clean_title(t)
    # 2. Site selectors
    for sel in rules.get("title", []):
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if len(t) > 5: return _clean_title(t)
    # 3. JSON-LD
    v = _jld(html, "name")
    if v: return _clean_title(str(v))
    # 4. H1
    h1 = soup.find("h1")
    if h1: return _clean_title(h1.get_text(strip=True))
    # 5. <title> tag
    if soup.title: return _clean_title(soup.title.get_text(strip=True))
    return ""

def _clean_title(t: str) -> str:
    # Strip store suffixes
    for pat in [r'\s*[|\-–—]\s*(Amazon|eBay|Walmart|AliExpress|Temu|Etsy|Target|Best Buy|Home Depot).*$',
                r'\s*::\s*.*$', r'\s*\|\s*.*$']:
        t = re.sub(pat, "", t, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', t).strip()


def _price(soup, html, rules):
    # 1. OG
    tag = soup.find("meta", property="product:price:amount")
    if tag and tag.get("content"):
        return _clean_price(tag["content"])
    # 2. JSON-LD
    v = _jld(html, "price")
    if v: return _clean_price(str(v))
    # 3. itemprop
    tag = soup.find(attrs={"itemprop": "price"})
    if tag:
        v = tag.get("content") or tag.get_text(strip=True)
        c = _clean_price(v)
        if c: return c
    # 3b. Common data-* price attributes used by many storefront platforms
    for attr in ["data-price-amount", "data-price", "data-product-price", "data-amount"]:
        tag = soup.find(attrs={attr: True})
        if tag:
            c = _clean_price(tag.get(attr, ""))
            if c: return c
    # 4. Site selectors
    for sel in rules.get("price", []):
        el = soup.select_one(sel)
        if el:
            c = _clean_price(el.get("content") or el.get_text(strip=True))
            if c: return c
    # 4b. Generic class-based fallback for unknown sites — most storefronts
    # use some variant of "price" in a class name on the main price element.
    for el in soup.select('[class*="price" i]'):
        txt = el.get_text(strip=True)
        c = _clean_price(txt)
        if c and 0 < float(c) < 100000:
            return c
    # 5. Regex — currency-agnostic, falls back to any plausible decimal price
    for pat in [r'"price"\s*:\s*"?([\d.]+)"?', r'[\$£€]\s?([\d,]+\.\d{2})',
                r'USD\s*([\d.]+)', r'price["\']?\s*[:=]\s*["\']?([\d]+\.\d{2})']:
        m = re.search(pat, html)
        if m:
            c = _clean_price(m.group(1))
            if c: return c
    return ""

def _clean_price(v: str) -> str:
    v = re.sub(r'[^\d.,]', '', str(v)).replace(",", "")
    try:
        f = float(v)
        return f"{f:.2f}" if f > 0 else ""
    except Exception:
        return ""


def _brand(soup, html, rules):
    # OG / meta
    for prop in ["product:brand", "og:brand"]:
        v = _og(soup, prop)
        if v: return v
    v = _meta_name(soup, "brand")
    if v: return v
    # Microdata
    tag = soup.find(attrs={"itemprop": "brand"})
    if tag:
        t = tag.get("content") or tag.get_text(strip=True)
        if t: return t
    # JSON-LD
    raw = _jld(html, "brand")
    if raw:
        if isinstance(raw, dict): raw = raw.get("name", "")
        if raw: return str(raw).strip()
    # Site selectors
    for sel in rules.get("brand", []):
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t and "visit" not in t.lower(): return t
    return ""


def _category(soup, html, rules):
    # Breadcrumb (most reliable)
    for sel in rules.get("breadcrumb", []) + ["nav[aria-label*='breadcrumb'] a", ".breadcrumb a", ".breadcrumbs a"]:
        crumbs = soup.select(sel)
        if len(crumbs) >= 2:
            return crumbs[-2].get_text(strip=True)
        elif len(crumbs) == 1:
            return crumbs[0].get_text(strip=True)
    # OG / meta / JSON-LD
    for v in [_og(soup, "product:category"), _meta_name(soup, "category"), str(_jld(html, "category") or "")]:
        if v and v != "None": return v.strip()
    return ""


def _description(soup, html, domain, rules):
    # 1. JSON-LD
    v = _jld(html, "description")
    if v and len(str(v)) > 60: return _clean_html(str(v))
    # 1b. Microdata (schema.org itemprop) — common on sites that don't use
    # JSON-LD but still mark up Product schema directly in HTML attributes.
    tag = soup.find(attrs={"itemprop": "description"})
    if tag:
        t = tag.get("content") or tag.get_text(separator="\n", strip=True)
        if t and len(t) > 60: return t
    # 2. Site-specific selectors
    for sel in rules.get("description", []):
        el = soup.select_one(sel)
        if el:
            t = el.get_text(separator="\n", strip=True)
            if len(t) > 80: return t
    # 3. OG / meta description
    for v in [_og(soup, "og:description"), _meta_name(soup, "description")]:
        if v and len(v) > 60: return v
    # 4. Largest meaningful text block
    best, best_len = "", 0
    for tag in soup.find_all(["div", "section", "article"]):
        t = tag.get_text(separator=" ", strip=True)
        if 150 < len(t) < 6000 and len(t) > best_len:
            best, best_len = t, len(t)
    return best


def _features(soup, html, rules):
    feats = []
    # Site selectors first
    for sel in rules.get("features", []):
        items = soup.select(sel)
        if items:
            feats = [el.get_text(strip=True) for el in items if len(el.get_text(strip=True)) > 8]
            if feats: return feats[:10]
    # Generic: find lists near "feature/highlight/benefit" headings
    for heading in soup.find_all(["h2","h3","h4","strong","b"]):
        txt = heading.get_text(strip=True).lower()
        if any(kw in txt for kw in ["feature","highlight","benefit","about this","key point"]):
            ul = heading.find_next("ul")
            if ul:
                items = [li.get_text(strip=True) for li in ul.find_all("li") if len(li.get_text(strip=True)) > 8]
                if items: return items[:10]
    # JSON-LD additionalProperty
    for m in re.finditer(r'"additionalProperty"\s*:\s*\[([^\]]+)\]', html, re.DOTALL):
        for nm in re.finditer(r'"name"\s*:\s*"([^"]+)"', m.group(1)):
            feats.append(nm.group(1))
    return feats[:10]


def _specifications(soup, html, domain, rules):
    specs = {}
    # 1. JSON-LD additionalProperty key/value pairs
    for m in re.finditer(r'"additionalProperty"\s*:\s*\[([^\]]+)\]', html, re.DOTALL):
        for pair in re.finditer(r'"name"\s*:\s*"([^"]+)"[^}]*"value"\s*:\s*"([^"]+)"', m.group(1), re.DOTALL):
            specs[pair.group(1)] = pair.group(2)
    # 2. Site selector rows
    for sel in rules.get("specs_table", []):
        for row in soup.select(sel):
            cells = row.find_all(["th","td","dt","dd","span","div"])
            if len(cells) >= 2:
                k = cells[0].get_text(strip=True).rstrip(":")
                v = cells[1].get_text(strip=True)
                if k and v and 1 < len(k) < 60 and len(v) < 200:
                    specs[k] = v
            elif len(cells) == 1:
                txt = cells[0].get_text(strip=True)
                if ":" in txt:
                    k, _, v = txt.partition(":")
                    if k.strip() and v.strip():
                        specs[k.strip()] = v.strip()
    # 3. Generic dl/table
    for dl in soup.find_all("dl"):
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            k, v = dt.get_text(strip=True).rstrip(":"), dd.get_text(strip=True)
            if k and v: specs[k] = v
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th","td"])
            if len(cells) == 2:
                k, v = cells[0].get_text(strip=True).rstrip(":"), cells[1].get_text(strip=True)
                if k and v and len(k) < 60: specs[k] = v
    return dict(list(specs.items())[:25])


def _images(soup, html, base_url, rules):
    seen, imgs = set(), []

    def add(u):
        if not u: return
        u = u.strip()
        if u.startswith("data:"): return
        if any(s in u.lower() for s in ["icon","logo","sprite","pixel","1x1","blank","avatar","placeholder","transparent"]): return
        if not u.startswith("http"): u = urljoin(base_url, u)
        if u not in seen:
            seen.add(u)
            imgs.append(u)

    # 1. OG image
    og = soup.find("meta", property="og:image")
    if og and og.get("content"): add(og["content"])
    # All OG images
    for tag in soup.find_all("meta", property="og:image"):
        if tag.get("content"): add(tag["content"])

    # 2. JSON-LD
    for m in re.finditer(r'"image"\s*:\s*"(https?://[^"]+)"', html):
        add(m.group(1))
    for m in re.finditer(r'"image"\s*:\s*\[([^\]]+)\]', html):
        for u in re.findall(r'"(https?://[^"]+)"', m.group(1)): add(u)
    # imageUrl patterns in JS
    for m in re.finditer(r'"(?:imageUrl|imgUrl|largeImageUrl|hiRes|mainImage)"\s*:\s*"(https?://[^"]+)"', html):
        add(m.group(1))

    # 3. Site selectors
    for sel in rules.get("images", []):
        for el in soup.select(sel):
            for attr in ["data-zoom-src","data-large","data-src","data-original","data-lazy","src","data-hi-res"]:
                v = el.get(attr,"")
                if v and re.search(r'\.(jpg|jpeg|png|webp)', v, re.I):
                    add(v); break

    # 4. All img tags — prefer large-image data attrs
    for img in soup.find_all("img"):
        for attr in ["data-zoom-src","data-large","data-src","data-original","data-lazy","src"]:
            v = img.get(attr,"")
            if v and re.search(r'\.(jpg|jpeg|png|webp)', v, re.I):
                add(v); break

    # 4b. srcset / picture>source — modern responsive sites often hide the
    # highest-resolution image here instead of in src=. Take the largest
    # candidate (last one is usually highest-res in a srcset list).
    for el in soup.find_all(["img", "source"]):
        srcset = el.get("srcset", "")
        if srcset:
            candidates = [c.strip().split(" ")[0] for c in srcset.split(",") if c.strip()]
            if candidates:
                add(candidates[-1])

    # 5. Background images
    for m in re.finditer(r'url\(["\']?(https?://[^"\')\s]+\.(?:jpg|jpeg|png|webp))["\']?\)', html, re.I):
        add(m.group(1))

    # Filter: remove very small images (thumbnails often have _SX38_ etc)
    filtered = [u for u in imgs if not re.search(r'_SX\d{2}[^0-9]|_SY\d{2}[^0-9]|/\d{2}x\d{2}/', u)]
    candidates = filtered or imgs
    # No hard cap on what we extract from the page — eBay listings allow up
    # to 12 images, so we trim to that limit here since this is the bridge
    # into a draft destined for eBay, not a general-purpose scraping limit.
    return candidates[:12]


def _sku(soup, html):
    for v in [
        _meta_name(soup, "product:retailer_item_id"),
        _jld(html, "sku"), _jld(html, "mpn"), _jld(html, "productID"),
    ]:
        if v: return str(v).strip()
    m = re.search(r'"(?:sku|itemId|productId|asin)"\s*:\s*"([A-Z0-9\-]{4,20})"', html, re.IGNORECASE)
    return m.group(1) if m else ""


# ── Utility ────────────────────────────────────────────────────────────────

def _og(soup, prop):
    tag = soup.find("meta", property=prop)
    return tag["content"].strip() if tag and tag.get("content") else ""

def _meta_name(soup, name):
    tag = soup.find("meta", attrs={"name": name})
    return tag["content"].strip() if tag and tag.get("content") else ""

def _jld(html: str, field: str):
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL|re.IGNORECASE):
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list): data = data[0]
            if not isinstance(data, dict): continue
            # Direct field
            val = data.get(field)
            if val: return val
            # Nested in offers
            offers = data.get("offers", {})
            if isinstance(offers, list): offers = offers[0] if offers else {}
            val = offers.get(field)
            if val: return val
        except Exception:
            pass
    # Regex fallback
    m = re.search(rf'"{re.escape(field)}"\s*:\s*"([^"]+)"', html)
    return m.group(1) if m else None

def _clean_html(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace("&amp;","&").replace("&lt;","<").replace("&gt;",">").replace("&nbsp;"," ").replace("&#39;","'").replace("&quot;",'"')
    return re.sub(r'\s+', ' ', text).strip()

def _spec_val(specs: dict, keys: list) -> str:
    for k in specs:
        if any(key in k.lower() for key in keys):
            return specs[k]
    return ""

def _tags(title, features, category, brand) -> list:
    text = f"{title} {category} {brand} " + " ".join(features[:3])
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
    stops = {"the","and","for","with","new","free","fast","best","from","this","that","item",
             "are","has","can","not","its","all","any","was","will","get","you","have",
             "been","more","also","buy","shop","use","lot","set","per","may","made"}
    freq = {}
    for w in words:
        if w not in stops: freq[w] = freq.get(w,0)+1
    return sorted(freq, key=freq.get, reverse=True)[:10]

def _confidence(p: dict) -> str:
    score = sum([
        len(p.get("title","")) > 10,
        bool(p.get("price")),
        len(p.get("description","")) > 100,
        bool(p.get("images")),
        bool(p.get("features")),
        bool(p.get("specifications")),
        bool(p.get("brand")),
    ])
    return "high" if score >= 5 else "medium" if score >= 3 else "low"

def _fill_defaults(p: dict):
    for k, v in {
        "title":"","brand":"","price":"","currency":"USD","description":"",
        "features":[],"specifications":{},"category":"Other","condition":"New",
        "images":[],"sku":"","weight":"","dimensions":"","variants":[],"tags":[],"confidence":"low"
    }.items():
        if k not in p: p[k] = v

def _err(msg): return {"success": False, "product": None, "note": None, "error": msg}

def _bot_wall_result(domain: str) -> dict:
    """
    Returned when the site served a bot-check/CAPTCHA page instead of real
    product content. Distinct from a generic fetch error so the UI can show
    a clear, specific message and route the seller straight to manual entry
    instead of confusing them with garbage extracted "product data."
    """
    return {
        "success": False,
        "product": None,
        "note": None,
        "bot_wall": True,
        "error": (
            f"{domain} blocked this request with a bot-check page (e.g. a "
            f"\"press and hold to verify you're human\" challenge). This site "
            f"uses anti-automation protection that a plain page fetch cannot "
            f"pass — no amount of retrying will get through it. "
            f"Please use Manual Entry below to paste in the product details yourself."
        ),
    }

def _lxml():
    try: import lxml; return True
    except ImportError: return False
