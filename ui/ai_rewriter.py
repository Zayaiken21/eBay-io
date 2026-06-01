"""
ai_rewriter.py — Rule-based SEO optimizer and eBay description formatter.
No AI, no API keys. Pure text processing and keyword analysis.
"""

import re
from collections import Counter

# ── eBay high-frequency search keywords by category ──────────────────────
CATEGORY_KEYWORDS = {
    "electronics":   ["wireless","bluetooth","usb","rechargeable","portable","compatible","fast charging","led","hd","4k","smart","digital"],
    "clothing":      ["stretch","breathable","lightweight","machine washable","comfortable","slim fit","regular fit","unisex","casual","outdoor"],
    "home":          ["waterproof","durable","non-slip","easy install","multipurpose","space saving","modern","stainless steel","adjustable","set of"],
    "toys":          ["educational","age 3+","batteries included","safe","non-toxic","interactive","stem","creative","kids","children"],
    "sports":        ["lightweight","durable","adjustable","breathable","anti-slip","professional","outdoor","training","gym","fitness"],
    "beauty":        ["natural","cruelty-free","vegan","long lasting","moisturizing","gentle","dermatologist tested","fragrance-free","organic"],
    "automotive":    ["universal fit","easy install","oem compatible","waterproof","durable","heavy duty","professional grade"],
    "garden":        ["weather resistant","heavy duty","rust proof","easy assembly","outdoor","durable","uv resistant"],
    "tools":         ["heavy duty","professional","ergonomic","durable","hardened steel","precision","multi-purpose"],
    "pet":           ["non-toxic","safe","durable","washable","adjustable","comfortable","waterproof","chew-proof"],
}

# ── SEO power words that boost eBay visibility ────────────────────────────
POWER_WORDS = [
    "Premium","Professional","Heavy Duty","High Quality","Ultra","Super",
    "Advanced","Deluxe","Complete","Genuine","Original","Brand New",
    "Fast Shipping","Free Returns","Top Rated","Best Seller",
]

# ── Title junk to strip ───────────────────────────────────────────────────
TITLE_STRIP = [
    r'\s*[|\-–—]\s*(Amazon|eBay|Walmart|AliExpress|Temu|Etsy|Target|Best Buy|Home Depot|Shein|Wish).*$',
    r'\s*::\s*.*$',
    r'\s*//\s*.*$',
    r'^\s*[\[\(].*?[\]\)]\s*',   # remove leading [brackets]
    r'【.*?】',                    # remove 【Chinese style brackets】
    r'◆.*?◆',
    r'★.*?★',
    r'❤.*?❤',
]


def rewrite_title(original_title: str, brand: str = "", category: str = "", features: list = None) -> dict:
    """
    Cleans & restructures product title for eBay SEO.
    Format: Brand + Core Product Name + Key Differentiators | trimmed to 80 chars
    """
    title = original_title.strip()

    # Strip junk
    for pat in TITLE_STRIP:
        title = re.sub(pat, "", title, flags=re.IGNORECASE)
    title = re.sub(r'\s+', ' ', title).strip()

    # Remove excessive punctuation and emoji-style chars
    title = re.sub(r'[!]{2,}', '', title)
    title = re.sub(r'^[\-_\s]+|[\-_\s]+$', '', title)

    # Prepend brand if not already present
    if brand and brand.lower() not in title.lower():
        title = f"{brand} {title}"

    # Try to inject top feature keyword if title is short
    if len(title) < 50 and features:
        feat_words = [w for f in features[:2] for w in f.split()
                      if len(w) > 4 and w.lower() not in title.lower()]
        if feat_words:
            title = f"{title} {feat_words[0]}"

    # Trim to eBay 80-char limit at word boundary
    title = title[:80].rsplit(" ", 1)[0] if len(title) > 80 else title

    return {"success": True, "title": title.strip(), "error": None}


def rewrite_description(
    title: str,
    original_description: str,
    features: list = None,
    specifications: dict = None,
    category: str = "",
    brand: str = "",
) -> dict:
    """
    Builds a structured, professional eBay listing description.
    Format: Hook → Features → Specs → What's in the box → Why buy → Shipping
    """
    features = [f.strip() for f in (features or []) if f.strip()]
    specifications = specifications or {}
    desc_clean = _clean(original_description)
    cat_lower  = category.lower()

    # ── Hook ──────────────────────────────────────────────────────────────
    hook = _build_hook(desc_clean, title, brand)

    # ── Category keywords to sprinkle ─────────────────────────────────────
    ckw = []
    for cat_key, kws in CATEGORY_KEYWORDS.items():
        if cat_key in cat_lower or cat_key in title.lower():
            ckw = kws; break

    # ── Build feature bullets (merge scraped + inferred) ──────────────────
    feat_bullets = []
    for f in features[:8]:
        feat_bullets.append(f"✔ {f}")
    # Inject category keywords not already mentioned
    mentioned = " ".join(features).lower()
    for kw in ckw[:4]:
        if kw not in mentioned and len(feat_bullets) < 10:
            feat_bullets.append(f"✔ {kw.title()} design for everyday use")

    # ── Spec table ────────────────────────────────────────────────────────
    spec_lines = [f"• {k}: {v}" for k, v in list(specifications.items())[:15]]

    # ── Body paragraphs (from original description, cleaned & trimmed) ─────
    body = _body_para(desc_clean, hook)

    # ── Assemble ──────────────────────────────────────────────────────────
    sections = [hook]

    if feat_bullets:
        sections.append("✅ KEY FEATURES\n" + "\n".join(feat_bullets))

    if spec_lines:
        sections.append("📋 SPECIFICATIONS\n" + "\n".join(spec_lines))

    if body:
        sections.append(body)

    # What's in the box (guess from specs/features)
    box_items = _guess_box_contents(title, features, specifications)
    if box_items:
        sections.append("📦 WHAT'S IN THE BOX\n" + "\n".join(f"• {i}" for i in box_items))

    sections.append(
        "🚚 SHIPPING & HANDLING\n"
        "• Orders are carefully packed and dispatched within 1-2 business days.\n"
        "• Combined shipping available — message us before checkout.\n"
        "• All items are tracked. Contact us with any questions or concerns."
    )

    sections.append(
        "💡 WHY BUY FROM US\n"
        "• Every item is quality checked before it ships.\n"
        "• We respond to all messages within 24 hours.\n"
        "• Hassle-free returns — your satisfaction is our priority."
    )

    return {"success": True, "description": "\n\n".join(s for s in sections if s.strip()), "error": None}


def generate_tags(title: str, description: str, category: str = "") -> dict:
    """
    Generates 10 high-value eBay search tags using keyword frequency + category boosting.
    """
    text = f"{title} {category} {description[:500]}"
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())

    stops = {
        "the","and","for","with","new","free","fast","best","from","this","that",
        "item","are","has","can","not","its","all","any","was","will","get","you",
        "have","been","more","also","buy","shop","use","lot","set","per","may",
        "made","our","your","they","them","their","very","just","also","even",
        "great","good","high","easy","long","wide","flat","fits","well","like",
        "come","comes","each","both","over","under","into","than","when","which",
    }

    freq = Counter(w for w in words if w not in stops)

    # Boost category keywords
    cat_lower = category.lower()
    for cat_key, kws in CATEGORY_KEYWORDS.items():
        if cat_key in cat_lower or cat_key in title.lower():
            for kw in kws:
                kw_words = kw.split()
                if len(kw_words) == 1 and kw_words[0] in freq:
                    freq[kw_words[0]] += 3  # boost

    tags = [w for w, _ in freq.most_common(12)][:10]
    return {"success": True, "tags": tags, "error": None}


def auto_seo_optimize(product: dict) -> dict:
    """
    Full automated SEO pass: rewrites title + description + tags in one call.
    Returns updated product dict. Called from the editor's 'Auto SEO' button.
    """
    updated = dict(product)

    title_result = rewrite_title(
        product.get("title", ""),
        brand=product.get("brand", ""),
        category=product.get("category", ""),
        features=product.get("features", []),
    )
    if title_result["success"]:
        updated["title"] = title_result["title"]

    desc_result = rewrite_description(
        title=updated["title"],
        original_description=product.get("description", ""),
        features=product.get("features", []),
        specifications=product.get("specifications", {}),
        category=product.get("category", ""),
        brand=product.get("brand", ""),
    )
    if desc_result["success"]:
        updated["description"] = desc_result["description"]

    tags_result = generate_tags(updated["title"], updated["description"], product.get("category", ""))
    if tags_result["success"]:
        updated["tags"] = tags_result["tags"]

    return updated


# ── Helpers ────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    if not text: return ""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace("&amp;","&").replace("&lt;","<").replace("&gt;",">").replace("&nbsp;"," ").replace("&#39;","'").replace("&quot;",'"')
    return re.sub(r'\s+', ' ', text).strip()

def _build_hook(desc: str, title: str, brand: str) -> str:
    sents = re.split(r'(?<=[.!?])\s+', desc)
    good  = [s.strip() for s in sents if 30 < len(s.strip()) < 300]
    if good: return " ".join(good[:2])
    b = f" by {brand}" if brand else ""
    return f"Introducing the {title}{b} — built to deliver outstanding performance and reliability."

def _body_para(desc: str, hook: str) -> str:
    if not desc: return ""
    rest = desc[desc.find(hook) + len(hook):].strip() if hook in desc else desc
    rest = rest[:1200].rsplit(" ", 1)[0] + "..." if len(rest) > 1200 else rest
    return rest

def _guess_box_contents(title: str, features: list, specs: dict) -> list:
    items = [f"1x {title}"]
    feat_text = " ".join(features).lower()
    spec_text = " ".join(f"{k} {v}" for k,v in specs.items()).lower()
    combined  = feat_text + " " + spec_text
    if "cable" in combined or "usb" in combined:     items.append("1x USB Cable")
    if "adapter" in combined:                         items.append("1x Power Adapter")
    if "remote" in combined:                          items.append("1x Remote Control")
    if "manual" in combined or "instruction" in combined: items.append("1x User Manual")
    if "battery" in combined and "include" in combined:   items.append("Batteries (Included)")
    if "case" in combined or "pouch" in combined:    items.append("1x Carrying Case/Pouch")
    if "screw" in combined or "mount" in combined:   items.append("Mounting Hardware")
    return items[:5]
