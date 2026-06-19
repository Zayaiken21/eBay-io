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
}

def _rules_for(domain: str) -> dict:
    for key, rules in SITE_RULES.items():
        if key in domain:
            return rules
    return {}


# ── Main entry point ───────────────────────────────────────────────────────

def fetch_product_page(url: str, timeout: int = 25, max_retries: int = 4) -> dict:
    """
    Fetches and parses a product page. Designed to work across any e-commerce
    site, not just the ones with dedicated SITE_RULES — unknown domains fall
    through to generic JSON-LD / Open Graph / heuristic extraction.

    Retries with exponential backoff + a fresh User-Agent and Referer on each
    attempt, since many sites only block a fraction of requests (rate-limit
    style) rather than every request outright. max_retries is generous but
    not infinite — an unreachable site still needs to fail eventually rather
    than hang forever.
    """
    domain = get_domain(url)
    last_error = None

    for attempt in range(1, max_retries + 1):
        # Backoff before retries (not before the first attempt)
        if attempt > 1:
            time.sleep(min(2 ** attempt, 8) + random.uniform(0, 1))
        else:
            time.sleep(random.uniform(0.3, 0.9))

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
                last_error = f"HTTP {resp.status_code} (temporary) on attempt {attempt}/{max_retries}"
                continue
            if resp.status_code == 403 and attempt < max_retries:
                last_error = f"HTTP 403 on attempt {attempt}/{max_retries} — retrying with new identity"
                continue

            resp.raise_for_status()
            html = resp.text
            break

        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response else 0
            if attempt >= max_retries:
                msg = {
                    403: "Access denied (403) after multiple attempts. This site actively blocks automated requests — paste the product details manually instead.",
                    404: "Page not found (404). Check the URL is correct.",
                }.get(code, f"HTTP {code}: could not fetch page after {max_retries} attempts.")
                return _err(msg)
            last_error = str(e)
            continue
        except requests.exceptions.Timeout:
            if attempt >= max_retries:
                return _err(f"Request timed out after {max_retries} attempts. The site may be very slow or blocking automated traffic.")
            last_error = "timeout"
            continue
        except requests.exceptions.ConnectionError as e:
            if attempt >= max_retries:
                return _err(f"Could not connect to {domain} after {max_retries} attempts: {e}")
            last_error = str(e)
            continue
        except Exception as e:
            return _err(str(e))
    else:
        return _err(f"Failed after {max_retries} attempts. Last error: {last_error}")

    if not BS4:
        return _err("BeautifulSoup not installed. Run: pip install beautifulsoup4 lxml")

    soup = BeautifulSoup(html, "lxml" if _lxml() else "html.parser")
    rules = _rules_for(domain)
    product = _extract_all(soup, html, url, domain, rules)
    _fill_defaults(product)

    note = None
    if any(s in domain for s in ["aliexpress", "temu", "shein"]) and not product.get("images"):
        note = f"{domain} is JS-heavy — some images may not load. Add image URLs manually in the editor."
    elif domain not in "".join(SITE_RULES.keys()) and product.get("confidence") == "low":
        note = (f"{domain} isn't a site we have dedicated extraction rules for — we used generic "
                f"JSON-LD/Open Graph/heuristic parsing. Results may be partial; fill in any gaps manually.")

    return {"success": True, "product": product, "note": note, "error": None}


def _extract_all(soup, html: str, url: str, domain: str, rules: dict) -> dict:
    p = {}
    p["domain"]     = domain
    p["source_url"] = url
    p["status"]     = "draft"
    p["condition"]  = "New"
    p["currency"]   = "USD"

    p["title"]         = _title(soup, html, rules)
    p["price"]         = _price(soup, html, rules)
    p["brand"]         = _brand(soup, html, rules)
    p["category"]      = _category(soup, html, rules)
    p["description"]   = _description(soup, html, domain, rules)
    p["features"]      = _features(soup, html, rules)
    p["specifications"]= _specifications(soup, html, domain, rules)
    p["images"]        = _images(soup, html, url, rules)
    p["sku"]           = _sku(soup, html)
    p["weight"]        = _spec_val(p["specifications"], ["weight","item weight","net weight","shipping weight"])
    p["dimensions"]    = _spec_val(p["specifications"], ["dimensions","size","item dimensions","product dimensions","package dimensions"])
    p["variants"]      = []
    p["tags"]          = _tags(p["title"], p["features"], p["category"], p["brand"])
    p["confidence"]    = _confidence(p)
    return p


# ── Field extractors ───────────────────────────────────────────────────────

def _title(soup, html, rules):
    # 1. OG
    v = _og(soup, "og:title")
    if v and len(v) > 5: return _clean_title(v)
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
def _lxml():
    try: import lxml; return True
    except ImportError: return False
