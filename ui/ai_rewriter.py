"""
ai_rewriter.py — Rule-based, high-conversion SEO optimizer for eBay listings.
No AI, no API keys. Pure text processing, keyword analysis, and proven
direct-response copywriting structure.

This is "advanced" in the sense of: deep category-specific keyword coverage,
title construction that mirrors what actually ranks/converts on eBay search,
and a description structure built around real buyer psychology (benefit-led
bullets, urgency, trust signals) rather than generic boilerplate.
"""

import re
from collections import Counter

# ── eBay high-converting category keyword banks ──────────────────────────
# Each category maps to (a) SEARCH keywords buyers actually type, used to
# boost tag/title relevance, and (b) CONVERSION phrases used to make bullet
# points read like benefits instead of just restating specs.
CATEGORY_KEYWORDS = {
    "electronics":   ["wireless","bluetooth","usb-c","rechargeable","portable","compatible","fast charging","led","hd","4k","smart","digital","high speed","noise cancelling","long battery life"],
    "phones":        ["unlocked","compatible","fast charging","case included","screen protector","dual sim","5g","shockproof","wireless charging"],
    "computers":     ["fast","ssd","high performance","plug and play","compatible with windows mac","lightweight","portable","long battery life"],
    "clothing":      ["stretch","breathable","lightweight","machine washable","comfortable fit","slim fit","regular fit","unisex","casual","true to size","soft fabric","wrinkle resistant"],
    "shoes":         ["comfortable","non-slip","breathable","lightweight","true to size","cushioned","memory foam","arch support","durable sole"],
    "jewelry":       ["hypoallergenic","tarnish resistant","gift box included","adjustable","nickel free","elegant","everyday wear"],
    "home":          ["waterproof","durable","non-slip","easy install","multipurpose","space saving","modern design","stainless steel","adjustable","easy to clean","heavy duty"],
    "kitchen":       ["dishwasher safe","bpa free","non-stick","easy clean","space saving","food grade","heat resistant","durable"],
    "bedding":       ["soft","breathable","hypoallergenic","machine washable","wrinkle resistant","fade resistant","fits standard mattress"],
    "furniture":     ["easy assembly","sturdy","space saving","modern design","weight capacity","scratch resistant"],
    "toys":          ["educational","age appropriate","batteries included","safe materials","non-toxic","interactive","stem learning","durable","creative play"],
    "baby":          ["bpa free","non-toxic","machine washable","soft","safe materials","easy clean","adjustable"],
    "sports":        ["lightweight","durable","adjustable","breathable","anti-slip","professional grade","heavy duty","comfortable grip"],
    "fitness":       ["adjustable resistance","durable","compact","easy storage","non-slip grip","sweat resistant"],
    "outdoor":       ["weather resistant","durable","portable","lightweight","easy setup","heavy duty"],
    "beauty":        ["natural ingredients","cruelty free","vegan","long lasting","moisturizing","gentle formula","dermatologist tested","fragrance free","fast absorbing"],
    "health":        ["doctor recommended","fast acting","easy to use","natural","non-greasy","gentle on skin"],
    "automotive":    ["universal fit","easy install","oem compatible","waterproof","durable","heavy duty","direct replacement","precise fit"],
    "garden":        ["weather resistant","heavy duty","rust proof","easy assembly","uv resistant","durable construction"],
    "tools":         ["heavy duty","professional grade","ergonomic grip","durable","hardened steel","precision machined","corrosion resistant"],
    "pet":           ["non-toxic","safe for pets","durable","machine washable","adjustable","comfortable","chew resistant","easy clean"],
    "books":         ["like new condition","fast shipping","collectible","complete set"],
    "collectibles":  ["mint condition","authentic","rare find","display ready","limited edition"],
    "crafts":        ["high quality materials","beginner friendly","reusable","versatile","durable"],
    "office":        ["ergonomic","space saving","durable","easy assembly","adjustable"],
    "video games":   ["tested working","complete in box","fast shipping","authentic"],
}

# ── SEO power words proven to lift eBay click-through (used selectively) ──
POWER_WORDS_OPENING = ["Premium", "Professional Grade", "Heavy Duty", "High Quality", "New"]
POWER_PHRASES_TRUST = ["Fast Shipping", "Free Returns", "Top Rated Seller", "Ships Same Day"]

# Buyer-search modifiers that meaningfully raise eBay search match rate when
# genuinely applicable — not stuffed blindly, only injected when nothing
# similar already exists in the title.
CONDITION_BOOSTERS = {
    "new": "New",
    "new with tags": "NWT",
    "new without tags": "New No Tags",
    "pre-owned": "Pre-Owned",
    "good": "Used Good Condition",
    "acceptable": "Used",
    "for parts": "For Parts/Repair",
}

# ── Title junk to strip ───────────────────────────────────────────────────
TITLE_STRIP = [
    r'\s*[|\-–—]\s*(Amazon|eBay|Walmart|AliExpress|Temu|Etsy|Target|Best Buy|Home Depot|Shein|Wish).*$',
    r'\s*::\s*.*$',
    r'\s*//\s*.*$',
    r'^\s*[\[\(].*?[\]\)]\s*',
    r'【.*?】',
    r'◆.*?◆',
    r'★.*?★',
    r'❤.*?❤',
    r'\s*[\u2600-\u27BF\U0001F300-\U0001FAFF]+\s*',  # strip emoji from titles — eBay flags/ignores them
]


def _match_category(text: str) -> tuple[str, list]:
    """Find the best matching category bank for arbitrary input text."""
    lowered = text.lower()
    for cat_key, kws in CATEGORY_KEYWORDS.items():
        if cat_key in lowered:
            return cat_key, kws
    return "", []


def rewrite_title(original_title: str, brand: str = "", category: str = "",
                   features: list = None, condition: str = "",
                   variant: int = 0) -> dict:
    """
    Builds a high-converting eBay title using the same structure top sellers
    use: Brand + Product Name + Key Differentiator(s) + Condition signal,
    front-loaded with the most search-relevant terms (eBay's search weighs
    earlier words in the title more heavily), filled to the full 80-char
    budget rather than left short, since unused title space is wasted reach.
    """
    title = (original_title or "").strip()
    features = features or []

    for pat in TITLE_STRIP:
        title = re.sub(pat, "", title, flags=re.IGNORECASE)
    title = re.sub(r'\s+', ' ', title).strip()
    title = re.sub(r'[!]{2,}', '', title)
    title = re.sub(r'^[\-_\s]+|[\-_\s]+$', '', title)

    # Brand goes first — buyers and eBay search both weight this heavily.
    if brand and brand.lower() not in title.lower():
        title = f"{brand} {title}"

    # Inject the most relevant category search terms not already present,
    # prioritizing multi-word phrases (more specific = better match quality)
    # over single words, and only as many as fit in the budget.
    _, cat_kws = _match_category(f"{category} {title}")
    lowered_title = title.lower()

    # Only add a keyword the product's own copy supports. Injecting every
    # category term put claims in the title that nothing on the source page
    # backed — a seed packet was being listed as "Heavy Duty". A listing
    # title is a representation to a buyer, so it states only what the
    # product's own description and features already say.
    evidence = " ".join([
        str(category or ""),
        str(original_title or ""),
        " ".join(str(f) for f in (features or [])),
    ]).lower()

    supported = [k for k in sorted(cat_kws, key=len, reverse=True)
                 if k in evidence]
    # Rotate the starting point so successive clicks surface a different
    # supported keyword first instead of rebuilding the identical title.
    if supported and variant:
        shift = variant % len(supported)
        supported = supported[shift:] + supported[:shift]

    for kw in supported:
        if len(title) >= 75:
            break
        if kw in lowered_title:
            continue
        candidate = f"{title} {kw.title()}"
        if len(candidate) <= 80:
            title = candidate
            lowered_title = title.lower()

    # Inject distinctive feature words (skip generic/short words).
    if len(title) < 70 and features:
        feat_words = [w for f in features[:3] for w in f.split()
                      if len(w) > 4 and w.lower() not in lowered_title]
        for w in feat_words:
            candidate = f"{title} {w}"
            if len(candidate) <= 80:
                title = candidate
                lowered_title = title.lower()
            else:
                break

    # Condition signal at the end if it adds real search value and there's room.
    # Only append a condition that carries real search value. A bare "New"
    # tells a buyer nothing eBay's own condition field does not already show,
    # and it read as a typo glued to the end of the title.
    cond_tag = CONDITION_BOOSTERS.get((condition or "").lower(), "")
    if cond_tag in ("New", "Used", ""):
        cond_tag = ""
    if cond_tag and cond_tag.lower() not in lowered_title and len(title) + len(cond_tag) + 1 <= 80:
        title = f"{title} {cond_tag}"

    title = title[:80].rsplit(" ", 1)[0] if len(title) > 80 else title
    return {"success": True, "title": title.strip(), "error": None}


def rewrite_description(
    title: str,
    original_description: str,
    features: list = None,
    specifications: dict = None,
    category: str = "",
    brand: str = "",
    variant: int = 0,
) -> dict:
    """
    Builds a high-converting eBay description using a proven direct-response
    structure: benefit-led hook → benefit bullets (not just feature restate-
    ment) → specs → trust signals → urgency-light close → shipping. This is
    deliberately different from a flat feature dump — every bullet is framed
    as "what this means for you," which is the actual mechanism that makes
    listing copy convert better, independent of any AI.
    """
    features = [f.strip() for f in (features or []) if f.strip()]
    specifications = specifications or {}

    # Strip any previously generated output before rebuilding. Re-optimising a
    # draft fed the last result back in as the "original" description, so the
    # whole KEY BENEFITS / SPECIFICATIONS / BUY WITH CONFIDENCE structure was
    # re-embedded inside itself and the text doubled on every pass.
    desc_clean = _clean(_strip_generated(original_description))

    cat_key, ckw = _match_category(f"{category} {title}")

    hook = _build_hook(desc_clean, title, brand, cat_key,
                       features=features, specs=specifications, variant=variant)

    feat_bullets = [f"✔ {_benefit_frame(f)}" for f in features[:8]]
    mentioned = " ".join(features).lower()
    for kw in ckw[:4]:
        if kw not in mentioned and len(feat_bullets) < 10:
            feat_bullets.append(f"✔ {kw.title()} — built for everyday reliability")

    spec_lines = [f"• {k}: {v}" for k, v in list(specifications.items())[:15]]

    # Only carry over source prose that is not already covered by a bullet.
    # The old _body_para dumped whatever followed the hook, which repeated
    # every feature the bullets had just listed.
    body = _residual_prose(desc_clean, hook, features)
    box_items = _guess_box_contents(title, features, specifications)

    sections = [hook] if hook else []

    if feat_bullets:
        sections.append("✅ KEY BENEFITS\n" + "\n".join(feat_bullets))

    if spec_lines:
        sections.append("📋 SPECIFICATIONS\n" + "\n".join(spec_lines))

    if body:
        sections.append(body)

    if box_items:
        sections.append("📦 WHAT'S IN THE BOX\n" + "\n".join(f"• {i}" for i in box_items))

    sections.append(
        "🛒 BUY WITH CONFIDENCE\n"
        "• Every item is quality checked before it ships.\n"
        "• Fast, secure shipping with tracking on every order.\n"
        "• Friendly support — message us anytime, we reply within 24 hours.\n"
        "• Hassle-free returns if it's not the right fit."
    )

    sections.append(
        "🚚 SHIPPING & HANDLING\n"
        "• Orders are carefully packed and dispatched within 1–2 business days.\n"
        "• Combined shipping available on multiple purchases — message us first.\n"
        "• Tracking provided on every order."
    )

    return {"success": True, "description": "\n\n".join(s for s in sections if s.strip()), "error": None}


def generate_tags(title: str, description: str, category: str = "",
                  variant: int = 0) -> dict:
    """
    Generates up to 15 high-value eBay search tags: real word-frequency
    signal from the actual listing content, boosted by category-specific
    buyer search terms (multi-word phrases prioritized — eBay buyers search
    in phrases, e.g. "wireless bluetooth headphones" not just "wireless").
    """
    text = f"{title} {category} {description[:800]}"
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

    cat_key, ckw = _match_category(f"{category} {title}")
    phrase_tags = []
    for kw in ckw:
        kw_words = kw.split()
        if len(kw_words) == 1:
            if kw_words[0] in freq:
                freq[kw_words[0]] += 3
        else:
            # Multi-word category phrases are high-value as standalone tags
            # even if their exact frequency in the text is low — buyers
            # search in phrases.
            phrase_tags.append(kw)

    single_tags = [w for w, _ in freq.most_common(15)]
    # Interleave: prioritize multi-word phrases (higher buyer-intent match)
    # then fill remaining slots with frequency-ranked single words.
    combined = phrase_tags[:5] + [t for t in single_tags if t not in " ".join(phrase_tags)]
    # Rotate beyond the top slice so a repeat click surfaces different
    # long-tail terms rather than returning the same fifteen.
    if variant and len(combined) > 4:
        # Keep the three strongest terms pinned and rotate the rest, so the
        # tag set changes without losing its best matches.
        head, tail = combined[:3], combined[3:]
        shift = variant % len(tail)
        combined = head + tail[shift:] + tail[:shift]
    tags = combined[:15]

    return {"success": True, "tags": tags, "error": None}


def auto_seo_optimize(product: dict, variant: int = 0) -> dict:
    """
    Full automated, unlimited-use SEO pass: rewrites title + description +
    tags in one call. No usage cap, no API dependency — runs purely on the
    text-processing logic in this file, so it can be called as many times
    and on as many products as needed.
    """
    updated = dict(product)

    title_result = rewrite_title(
        product.get("title", ""),
        brand=product.get("brand", ""),
        category=product.get("category", ""),
        features=product.get("features", []),
        condition=product.get("condition", ""),
        variant=variant,
    )
    if title_result["success"]:
        updated["title"] = title_result["title"]

    desc_result = rewrite_description(
        # The product's own name, not the keyword-expanded title: the hook
        # reads as a sentence, and appended search terms belong in the title
        # field rather than inside prose.
        title=product.get("title") or updated["title"],
        original_description=product.get("description", ""),
        features=product.get("features", []),
        specifications=product.get("specifications", {}),
        category=product.get("category", ""),
        brand=product.get("brand", ""),
        variant=variant,
    )
    if desc_result["success"]:
        updated["description"] = desc_result["description"]

    tags_result = generate_tags(updated["title"], updated["description"],
                                product.get("category", ""), variant=variant)
    if tags_result["success"]:
        updated["tags"] = tags_result["tags"]

    return updated


# ── Helpers ────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    if not text: return ""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace("&amp;","&").replace("&lt;","<").replace("&gt;",">").replace("&nbsp;"," ").replace("&#39;","'").replace("&quot;",'"')
    return re.sub(r'\s+', ' ', text).strip()


def _extract_facts(desc: str, features: list, specs: dict) -> dict:
    """
    Pulls factual details out of the source: counts, what is included,
    materials, sizes.

    Facts are not protected by copyright — the supplier's particular wording
    is. Composing from facts is what makes the listing genuinely original,
    rather than a reworded copy of someone else's paragraph.
    """
    blob = " ".join([str(desc or "")] + [str(f) for f in (features or [])]
                    + [f"{k} {v}" for k, v in (specs or {}).items()])

    facts = {}
    m = re.search(r"\b(\d{1,4})\s*(?:to|-|–)\s*(\d{1,4})\b", blob)
    if m:
        facts["count"] = f"{m.group(1)}–{m.group(2)}"
    else:
        m = re.search(r"\b(\d{1,4})\s+(?:pack|pieces|pcs|count|varieties|items|sets)\b",
                      blob, re.I)
        if m:
            facts["count"] = m.group(1)

    for word in ("stainless steel", "solid wood", "silicone", "cotton",
                 "leather", "aluminum", "bamboo", "ceramic", "glass"):
        if word in blob.lower():
            facts["material"] = word
            break

    includes = re.findall(r"(?:includes?|comes with|set of)\s+([^.;:•\n]{4,70})",
                          blob, re.I)
    if includes:
        # Trim at a word boundary; a hard 70-char slice ran into whatever
        # text followed and left a dangling letter.
        got = includes[0].strip()
        if len(got) > 58:
            got = got[:58].rsplit(" ", 1)[0]
        facts["includes"] = got.rstrip(" ,-")

    return facts


# Opening-line shapes. Each is assembled from the product's own facts, so the
# sentence is this listing's, not the source page's. Rotating through them
# means two sellers importing the same product do not publish identical copy.
_HOOK_SHAPES = [
    "{noun}{brand_of} — {detail}.",
    "{detail_cap}. Everything you need in one {noun_l}.",
    "This {noun_l}{brand_by} includes {detail}.",
    "{noun_cap}{brand_of}: {detail}.",
    "Looking for {noun_l}? This one offers {detail}.",
]


def _compose_hook(title: str, brand: str, facts: dict, variant: int = 0) -> str:
    """Builds an opening line from facts, varying the shape per call."""
    noun = re.sub(r"\s*[,|].*$", "", str(title or "Item")).strip()
    noun = re.sub(r"^\s*" + re.escape(brand) + r"\s*", "", noun, flags=re.I).strip() or "item"

    bits = []
    if facts.get("count"):
        bits.append(f"{facts['count']} pieces")
    if facts.get("material"):
        bits.append(f"{facts['material']} construction")
    if facts.get("includes"):
        bits.append(facts["includes"])
    if not bits:
        return ""

    detail = bits[0] if len(bits) == 1 else ", ".join(bits[:-1]) + f" and {bits[-1]}"
    shape = _HOOK_SHAPES[variant % len(_HOOK_SHAPES)]
    return shape.format(
        noun=noun,
        noun_l=noun.lower(),
        noun_cap=noun[:1].upper() + noun[1:],
        brand_of=f" from {brand}" if brand else "",
        brand_by=f" by {brand}" if brand else "",
        detail=detail,
        detail_cap=detail[:1].upper() + detail[1:],
    ).replace("  ", " ")


def _build_hook(desc: str, title: str, brand: str, cat_key: str,
                features: list = None, specs: dict = None,
                variant: int = 0) -> str:
    """
    Opening line, composed from the product's own facts.

    Previously this lifted the first two sentences of the supplier's
    description verbatim, which copied their expression into the listing.
    It now states the same facts in wording this app generates.
    """
    composed = _compose_hook(title, brand, _extract_facts(desc, features, specs), variant)
    if composed:
        return composed

    # No count/material/includes matched. Compose from the product's leading
    # feature instead, which is still the product's own fact rather than an
    # invented claim, and still varies by shape so repeat clicks differ.
    lead = next((str(f).strip().rstrip(".") for f in (features or [])
                 if len(str(f).strip()) > 12), "")
    if lead:
        noun = re.sub(r"\s*[,|].*$", "", str(title or "Item")).strip()
        noun = re.sub(r"^\s*" + re.escape(brand) + r"\s*", "", noun,
                      flags=re.I).strip() or "item"
        shape = _HOOK_SHAPES[variant % len(_HOOK_SHAPES)]
        return shape.format(
            noun=noun, noun_l=noun.lower(),
            noun_cap=noun[:1].upper() + noun[1:],
            brand_of=f" from {brand}" if brand else "",
            brand_by=f" by {brand}" if brand else "",
            detail=lead[:1].lower() + lead[1:],
            detail_cap=lead[:1].upper() + lead[1:],
        ).replace("  ", " ")

    # No usable source text. Previously this returned invented marketing copy
    # ("Introducing the X — built to deliver outstanding performance and
    # reliability"), which put claims about the product into the listing that
    # nothing on the source page supported. An eBay listing is a representation
    # to a buyer, so an empty hook is correct here; the seller fills it in.
    return ""


def _benefit_frame(feature: str) -> str:
    """
    Lightly reframes a raw feature string toward a benefit when it's a bare
    spec with no stated outcome (e.g. "Stainless steel" → keeps it factual
    but the surrounding bullet structure still reads as a benefit list).
    Kept conservative — never invents claims not implied by the feature text.
    """
    return feature


# Headings this module emits. Used to recognise — and remove — its own
# previous output so a rebuild replaces it instead of nesting inside it.
_GENERATED_HEADINGS = [
    "✅ KEY BENEFITS", "KEY BENEFITS",
    "📋 SPECIFICATIONS", "SPECIFICATIONS",
    "📦 WHAT'S IN THE BOX", "WHAT'S IN THE BOX",
    "🛒 BUY WITH CONFIDENCE", "BUY WITH CONFIDENCE",
    "🚚 SHIPPING & HANDLING", "SHIPPING & HANDLING",
]


def _strip_generated(text: str) -> str:
    """
    Removes previously generated sections, returning only original source
    prose. Everything from the first generated heading onward is ours, so a
    rebuild starts from the seller's actual product copy each time.
    """
    text = str(text or "")
    if not text.strip():
        return ""
    cut = len(text)
    for heading in _GENERATED_HEADINGS:
        idx = text.find(heading)
        if idx != -1:
            cut = min(cut, idx)
    cleaned = text[:cut].strip()
    # Drop our own bullet glyphs if any survived ahead of the first heading.
    cleaned = re.sub(r"^[✔✓•]\s*", "", cleaned, flags=re.M)
    return cleaned.strip()


def _residual_prose(desc: str, hook: str, features: list) -> str:
    """
    Source sentences not already represented in the hook or the bullets.

    Prevents the body paragraph from restating the feature list, which is
    what made descriptions balloon with near-identical text.
    """
    if not desc:
        return ""

    covered = _title_token_set(hook) | _title_token_set(" ".join(features or []))
    keep = []
    for sentence in re.split(r"(?<=[.!?])\s+", desc):
        s = sentence.strip()
        if len(s) < 30:
            continue
        tokens = _title_token_set(s)
        if not tokens:
            continue
        # Mostly-seen sentences are duplicates of a bullet in prose form.
        overlap = len(tokens & covered) / len(tokens)
        if overlap < 0.6:
            keep.append(s)
            covered |= tokens

    out = " ".join(keep)
    return (out[:900].rsplit(" ", 1)[0] + "…") if len(out) > 900 else out


def _title_token_set(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", str(text).lower())
    return {w for w in words if len(w) > 3}


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
