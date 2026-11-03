"""
listing_audit.py — score a draft against eBay's own ranking and conversion
factors, and say what to fix first.

Why this and not website-SEO checks
-----------------------------------
eBay search (Cassini) is not Google. Backlinks, page speed, canonicals,
hreflang and robots.txt have no bearing on where a listing places. What
Cassini actually weighs is: keyword match against the *title* and the
structured item specifics, then the listing's own conversion history, then
seller and logistics signals. So every check here maps to one of those.

The score is a weighted completeness measure, not a prediction. A 100 does
not promise a placement — it means nothing eBay looks at has been left
empty, which is the part a seller controls. Findings are what matter; the
number is there to rank drafts against each other.

Severity drives the order of work:
  critical — the listing will underperform badly or may not list at all
  high     — a ranking or filter signal eBay uses is missing
  medium   — a conversion leak
  low      — polish
"""

from __future__ import annotations

import re

TITLE_LIMIT = 80

# Words buyers do not search for. They consume title characters that could
# hold a real search term, and eBay's own title guidance calls them out.
FILLER_WORDS = {
    "beautiful", "amazing", "awesome", "nice", "lovely", "stunning",
    "gorgeous", "perfect", "great", "excellent", "quality", "best",
    "wow", "look", "rare", "hot", "sale", "cheap", "bargain", "must",
    "l@@k", "nr", "wholesale",
}

# Active content eBay strips from listings on mobile, where most of its
# traffic is. A description built from these renders blank to the buyer.
BLOCKED_HTML = [
    (r"<\s*iframe", "iframe"),
    (r"<\s*script", "script"),
    (r"<\s*form", "form"),
    (r"<\s*object", "object"),
    (r"<\s*embed", "embed"),
    (r"<\s*applet", "applet"),
    (r"<\s*link[^>]+stylesheet", "external stylesheet"),
    (r"<\s*table", "table layout"),
]

WEIGHTS = {
    "title": 30,
    "aspects": 25,
    "photos": 20,
    "description": 15,
    "logistics": 10,
}

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _finding(area: str, severity: str, message: str, fix: str) -> dict:
    return {"area": area, "severity": severity, "message": message, "fix": fix}


def _score_title(p: dict, out: list) -> float:
    title = str(p.get("title") or "").strip()
    earned = 1.0

    if not title:
        out.append(_finding(
            "title", "critical", "No title.",
            "A listing cannot go live without one, and the title is the "
            "single strongest ranking signal eBay has.",
        ))
        return 0.0

    # Length. Unused title space is unused reach — there is no penalty for a
    # full title, only a cost to a short one.
    n = len(title)
    if n < 40:
        earned -= 0.45
        out.append(_finding(
            "title", "critical",
            f"Title is {n} of {TITLE_LIMIT} characters.",
            f"You are leaving {TITLE_LIMIT - n} characters of search reach "
            "unused. Add the terms a buyer would actually type — size, "
            "colour, model, quantity, compatibility.",
        ))
    elif n < 60:
        earned -= 0.2
        out.append(_finding(
            "title", "high",
            f"Title is {n} of {TITLE_LIMIT} characters.",
            f"Room for roughly {TITLE_LIMIT - n} more characters of search "
            "terms. Run Rewrite Title to fill it.",
        ))
    elif n > TITLE_LIMIT:
        earned -= 0.3
        out.append(_finding(
            "title", "critical",
            f"Title is {n} characters — over eBay's {TITLE_LIMIT} limit.",
            "eBay will reject it. Trim to 80.",
        ))

    lowered = title.lower()
    words = re.findall(r"[a-z0-9@]+", lowered)

    # Repeats. eBay indexes a title term once; a second copy is wasted budget.
    repeats = {w for w in words if len(w) > 3 and words.count(w) > 1}
    if repeats:
        earned -= 0.12
        out.append(_finding(
            "title", "medium",
            f"Repeated word(s): {', '.join(sorted(repeats))}.",
            "A repeat adds no ranking weight. Replace it with another "
            "search term.",
        ))

    found_filler = sorted(set(words) & FILLER_WORDS)
    if found_filler:
        earned -= 0.12
        out.append(_finding(
            "title", "medium",
            f"Filler word(s) buyers do not search: {', '.join(found_filler)}.",
            "Swap them for words from a buyer's query — a model number, a "
            "size, a material.",
        ))

    # Shouting. eBay's listing policy discourages it and it reads as spam.
    letters = [c for c in title if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        earned -= 0.1
        out.append(_finding(
            "title", "medium", "Title is mostly capital letters.",
            "Use Title Case. All-caps reads as spam to buyers and breaches "
            "eBay's title guidance.",
        ))

    if title.count("!") or "*" in title:
        earned -= 0.05
        out.append(_finding(
            "title", "low", "Decorative punctuation in the title.",
            "Remove ! and *. They occupy characters a search term could use.",
        ))

    brand = str(p.get("brand") or "").strip()
    if brand and brand.lower() not in lowered:
        earned -= 0.15
        out.append(_finding(
            "title", "high", f"Brand '{brand}' is missing from the title.",
            "Brand is one of the most searched and most filtered terms on "
            "eBay. Put it at the front.",
        ))

    return max(0.0, earned)


def _score_aspects(p: dict, out: list) -> float:
    specs = p.get("specifications") or {}
    if not isinstance(specs, dict):
        specs = {}
    filled = {k: v for k, v in specs.items() if str(v or "").strip()}
    count = len(filled)
    earned = 1.0

    # Item specifics are how eBay's left-hand filters work. A buyer who
    # filters by a value the listing never supplied does not see the listing
    # at all — this is the most common invisible cause of no views.
    if count == 0:
        out.append(_finding(
            "aspects", "critical", "No item specifics.",
            "Buyers narrowing by brand, size, type or colour will never see "
            "this listing. Fill the Specifications panel.",
        ))
        return 0.0
    if count < 5:
        earned -= 0.4
        out.append(_finding(
            "aspects", "high", f"Only {count} item specific(s).",
            "eBay surfaces listings that answer its recommended aspects. "
            "Aim for 8 or more.",
        ))
    elif count < 8:
        earned -= 0.15
        out.append(_finding(
            "aspects", "medium", f"{count} item specifics.",
            "A few more would widen the filters you appear in.",
        ))

    lower_keys = {str(k).strip().lower() for k in filled}

    for key, severity, why in (
        ("brand", "high", "Brand is a top-used buyer filter."),
        ("type", "medium", "Type places the listing in the right facet."),
    ):
        if key not in lower_keys:
            earned -= 0.12
            out.append(_finding(
                "aspects", severity, f"Missing '{key.title()}' specific.", why,
            ))

    if not ({"upc", "ean", "mpn", "gtin", "isbn"} & lower_keys):
        earned -= 0.12
        out.append(_finding(
            "aspects", "medium", "No product identifier (UPC, EAN, MPN).",
            "An identifier lets eBay match the listing to its catalogue, "
            "which adds structured data and can surface it in Google "
            "Shopping as well.",
        ))

    return max(0.0, earned)


def _score_photos(p: dict, out: list) -> float:
    images = [i for i in (p.get("images") or []) if str(i or "").strip()]
    n = len(images)
    earned = 1.0

    if n == 0:
        out.append(_finding(
            "photos", "critical", "No photos.",
            "eBay will not publish without at least one, and photo count "
            "correlates with conversion more strongly than any copy change.",
        ))
        return 0.0
    if n < 3:
        earned -= 0.5
        out.append(_finding(
            "photos", "critical", f"Only {n} photo(s).",
            "eBay gives you 24 free. Buyers who cannot see the item from "
            "several angles do not buy it.",
        ))
    elif n < 8:
        earned -= 0.25
        out.append(_finding(
            "photos", "high", f"{n} photos.",
            "eBay's own guidance is 8–12. Add angles, scale, packaging and "
            "any flaw — a shown flaw prevents a return.",
        ))
    elif n < 12:
        earned -= 0.08
        out.append(_finding(
            "photos", "low", f"{n} photos.",
            "A couple more would reach eBay's recommended 12.",
        ))

    # eBay's zoom only engages at 1600px on the longest side, and zoom is
    # one of its stated conversion factors.
    small = [i for i in images if re.search(r"(?:_|/)(?:\d{2,3})x(?:\d{2,3})", str(i))]
    if small:
        earned -= 0.1
        out.append(_finding(
            "photos", "medium",
            f"{len(small)} image URL(s) look like small thumbnails.",
            "Re-import at full size. eBay enables zoom only at 1600px on the "
            "longest side.",
        ))

    return max(0.0, earned)


def _score_description(p: dict, out: list) -> float:
    desc = str(p.get("description") or "")
    plain = re.sub(r"<[^>]+>", " ", desc)
    plain = re.sub(r"\s+", " ", plain).strip()
    earned = 1.0

    if not plain:
        out.append(_finding(
            "description", "critical", "No description.",
            "Run Rewrite Description to build one from the item's own facts.",
        ))
        return 0.0

    if len(plain) < 200:
        earned -= 0.35
        out.append(_finding(
            "description", "high",
            f"Description is only {len(plain)} characters.",
            "Thin descriptions convert poorly and give buyers reasons to "
            "message instead of buy. Cover what it is, what is included, "
            "condition and sizing.",
        ))
    elif len(plain) < 400:
        earned -= 0.12
        out.append(_finding(
            "description", "low",
            f"Description is {len(plain)} characters.",
            "There is room to answer more pre-sale questions.",
        ))

    for pattern, label in BLOCKED_HTML:
        if re.search(pattern, desc, re.I):
            earned -= 0.3
            out.append(_finding(
                "description", "critical",
                f"Description contains a {label}.",
                "eBay strips active content and table layouts on mobile, "
                "where most of its traffic is — the buyer sees a blank "
                "description. Use plain paragraphs and bullets.",
            ))
            break

    if "\n" not in desc.strip() and len(plain) > 400:
        earned -= 0.1
        out.append(_finding(
            "description", "medium", "Description is one unbroken block.",
            "Break it into short sections with headings. Buyers scan on a "
            "phone; they do not read a wall.",
        ))

    return max(0.0, earned)


def _score_logistics(p: dict, out: list) -> float:
    earned = 1.0

    try:
        price = float(str(p.get("price") or 0).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        earned -= 0.4
        out.append(_finding(
            "logistics", "critical", "No price.",
            "Set one before publishing.",
        ))

    if not str(p.get("condition") or "").strip():
        earned -= 0.25
        out.append(_finding(
            "logistics", "high", "No condition set.",
            "Condition is a required field and a heavily used buyer filter.",
        ))

    weight = p.get("weight") or (p.get("package") or {}).get("weight")
    if not str(weight or "").strip():
        earned -= 0.2
        out.append(_finding(
            "logistics", "medium", "No package weight or dimensions.",
            "Without them eBay cannot calculate shipping, which costs you "
            "the buyers who filter for it — and you absorb any shortfall.",
        ))

    if not str(p.get("sku") or "").strip():
        earned -= 0.1
        out.append(_finding(
            "logistics", "low", "No SKU.",
            "A SKU keeps inventory straight once you are running many "
            "listings.",
        ))

    return max(0.0, earned)


_SCORERS = {
    "title": _score_title,
    "aspects": _score_aspects,
    "photos": _score_photos,
    "description": _score_description,
    "logistics": _score_logistics,
}


def audit(product: dict) -> dict:
    """
    Score a draft and list what to fix.

    Returns {score, grade, breakdown, findings, counts}. Findings are sorted
    worst-first, so the top of the list is always the next thing to do.
    """
    product = product or {}
    findings: list = []
    breakdown = {}
    total = 0.0

    for area, scorer in _SCORERS.items():
        before = len(findings)
        fraction = scorer(product, findings)
        points = fraction * WEIGHTS[area]
        total += points
        breakdown[area] = {
            "points": round(points, 1),
            "max": WEIGHTS[area],
            "issues": len(findings) - before,
        }

    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f["severity"], 9))

    score = int(round(total))
    counts = {s: sum(1 for f in findings if f["severity"] == s)
              for s in ("critical", "high", "medium", "low")}

    return {
        "score": score,
        "grade": grade_for(score),
        "breakdown": breakdown,
        "findings": findings,
        "counts": counts,
    }


def grade_for(score: int) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 60:
        return "Needs work"
    if score >= 40:
        return "Weak"
    return "Incomplete"


def cannibalization(products: list) -> list:
    """
    Find drafts competing against each other on the same head terms.

    Two of your own listings matching one query split their click and sales
    history between them, and eBay rarely shows both. The repo this idea
    came from solves it for web pages; the same thing happens on eBay when a
    seller lists near-identical variations separately instead of as one
    multi-variation listing.

    Returns groups of indices that overlap heavily, with the shared terms.
    """
    stop = {"the", "and", "for", "with", "new", "set", "pack", "size", "pcs",
            "piece", "pieces", "lot", "free", "shipping", "inch", "from"}

    signatures = []
    for i, p in enumerate(products or []):
        words = re.findall(r"[a-z0-9]{3,}", str(p.get("title") or "").lower())
        signatures.append((i, {w for w in words if w not in stop}))

    groups = []
    seen = set()
    for i, (idx_a, sig_a) in enumerate(signatures):
        if not sig_a or idx_a in seen:
            continue
        cluster, shared = [idx_a], set(sig_a)
        for idx_b, sig_b in signatures[i + 1:]:
            if not sig_b or idx_b in seen:
                continue
            overlap = len(sig_a & sig_b) / max(1, len(sig_a | sig_b))
            if overlap >= 0.7:
                cluster.append(idx_b)
                shared &= sig_b
        if len(cluster) > 1:
            seen.update(cluster)
            groups.append({"indices": cluster, "shared_terms": sorted(shared)})

    return groups
