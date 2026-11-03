"""
keyword_demand.py — measure keywords against live eBay data instead of
guessing them.

The problem with a keyword list
-------------------------------
ai_rewriter ships a hand-written CATEGORY_KEYWORDS dict. It is a guess about
what buyers type, frozen at the moment it was written, and it cannot tell
which of its terms are worth the title characters they cost.

What replaces it
----------------
Two measurements, both from eBay's own documented APIs:

1. Competition and demand per term. Browse search reports how many active
   listings match a query; Marketplace Insights reports how many sold. The
   ratio of the two is the only honest read on whether a term is worth
   targeting — a term with 40,000 competing listings and 12 sales is a term
   to avoid, however relevant it sounds.

2. The term gap. Pull the listings eBay currently ranks for a query and count
   which words recur across them. A word in most of the top listings and
   absent from yours is a measured gap, not an invention — which is the whole
   difference between this and keyword stuffing.

Everything degrades to empty on an API error or a missing credential. A
listing tool that silently invents demand figures is worse than one that
says it does not know.
"""

from __future__ import annotations

import re
from collections import Counter

import streamlit as st

from core.ebay_oauth import call_ebay_app_api

BROWSE_SEARCH = "/buy/browse/v1/item_summary/search"
INSIGHTS_SEARCH = "/buy/marketplace_insights/v1_beta/item_sales/search"

# Words that carry no search intent. Kept separate from the rewriter's own
# stop list because this one filters *competitor titles*, where seller
# boilerplate ("free", "ship", "new") dominates the counts.
_NOISE = {
    "the", "and", "for", "with", "from", "this", "that", "your", "you",
    "new", "free", "ship", "shipping", "fast", "best", "top", "hot", "sale",
    "lot", "set", "pack", "pcs", "pc", "piece", "pieces", "size", "inch",
    "usa", "oem", "genuine", "authentic", "brand", "quality", "premium",
    "item", "items", "各", "more", "all", "one", "two", "x",
}


def _tokens(text: str) -> list:
    words = re.findall(r"[a-z0-9][a-z0-9\-']{2,}", str(text or "").lower())
    return [w for w in words if w not in _NOISE and not w.isdigit()]


@st.cache_data(ttl=3600, show_spinner=False)
def term_competition(term: str, marketplace: str = "EBAY_US") -> int | None:
    """
    Active listings matching a query, or None if it could not be measured.

    None and 0 are different answers and must not be conflated: 0 means an
    untapped term, None means we do not know. Showing None as 0 would present
    an API failure as a gap in the market.
    """
    term = (term or "").strip()
    if not term:
        return None
    try:
        resp = call_ebay_app_api(
            BROWSE_SEARCH,
            params={"q": term, "limit": 1},
            marketplace_id=marketplace,
        )
        if resp.status_code != 200:
            return None
        return int(resp.json().get("total") or 0)
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def term_sales(term: str, marketplace: str = "EBAY_US") -> int | None:
    """
    Sold listings matching a query over the Insights window, or None.

    Marketplace Insights is a limited-release API. A seller without access
    gets None here and the UI falls back to competition alone rather than
    pretending demand is zero.
    """
    term = (term or "").strip()
    if not term:
        return None
    try:
        resp = call_ebay_app_api(
            INSIGHTS_SEARCH,
            params={"q": term, "limit": 1},
            marketplace_id=marketplace,
        )
        if resp.status_code != 200:
            return None
        return int(resp.json().get("total") or 0)
    except Exception:
        return None


def rank_terms(terms: list, marketplace: str = "EBAY_US") -> list:
    """
    Score candidate keywords by measured demand against measured competition.

    Returns one row per term: {term, active, sold, ratio, verdict}. Sorted
    best-first. Terms that could not be measured sort last and are labelled
    as unmeasured rather than dropped — the seller should see that the
    lookup failed.
    """
    rows = []
    for term in dict.fromkeys(t for t in (terms or []) if str(t).strip()):
        active = term_competition(term, marketplace)
        sold = term_sales(term, marketplace)

        if active is None:
            rows.append({"term": term, "active": None, "sold": sold,
                         "ratio": None, "verdict": "Not measured"})
            continue

        if sold is None:
            verdict = "Low competition" if active < 500 else (
                "Crowded" if active > 10000 else "Moderate")
            rows.append({"term": term, "active": active, "sold": None,
                         "ratio": None, "verdict": verdict})
            continue

        # Sales per thousand active listings. Normalising by competition is
        # what stops a huge, saturated term from looking good purely because
        # its absolute sales are large.
        ratio = (sold / active * 1000) if active else float(sold)
        if ratio >= 100:
            verdict = "Strong"
        elif ratio >= 25:
            verdict = "Worth targeting"
        elif ratio >= 5:
            verdict = "Marginal"
        else:
            verdict = "Saturated"

        rows.append({"term": term, "active": active, "sold": sold,
                     "ratio": round(ratio, 1), "verdict": verdict})

    rows.sort(key=lambda r: (r["ratio"] is None, -(r["ratio"] or 0)))
    return rows


@st.cache_data(ttl=1800, show_spinner=False)
def _top_titles(query: str, limit: int = 50,
                marketplace: str = "EBAY_US") -> list:
    """Titles eBay currently ranks for a query."""
    query = (query or "").strip()
    if not query:
        return []
    try:
        resp = call_ebay_app_api(
            BROWSE_SEARCH,
            params={"q": query, "limit": min(limit, 200),
                    "sort": "-watchCount"},
            marketplace_id=marketplace,
        )
        if resp.status_code != 200:
            return []
        items = resp.json().get("itemSummaries") or []
        return [str(i.get("title") or "") for i in items]
    except Exception:
        return []


def term_gap(our_title: str, query: str = "", min_share: float = 0.3,
             marketplace: str = "EBAY_US") -> dict:
    """
    Words common among the listings eBay ranks, that ours is missing.

    `query` defaults to our own title's first few words — the head term the
    listing is implicitly competing on.

    `min_share` is the fraction of top listings a word must appear in to
    count. At 0.3 a word has to be in nearly a third of what is ranking
    before it is suggested, which keeps one competitor's branding out of the
    results.

    Returns {query, sampled, missing, present}. `missing` rows carry the
    share so the seller can judge each one — none of them is applied
    automatically, because a word common among competitors is still only
    usable if it is true of this item.
    """
    our_title = str(our_title or "")
    if not query:
        query = " ".join(our_title.split()[:4])

    titles = _top_titles(query, marketplace=marketplace)
    if not titles:
        return {"query": query, "sampled": 0, "missing": [], "present": []}

    counts = Counter()
    for title in titles:
        # Count each word once per listing, not once per occurrence, so a
        # title that repeats a word cannot inflate its apparent popularity.
        counts.update(set(_tokens(title)))

    ours = set(_tokens(our_title))
    threshold = max(2, int(len(titles) * min_share))

    missing, present = [], []
    for word, n in counts.most_common(60):
        if n < threshold:
            continue
        row = {"term": word, "share": round(n / len(titles), 2), "count": n}
        (present if word in ours else missing).append(row)

    return {
        "query": query,
        "sampled": len(titles),
        "missing": missing[:20],
        "present": present[:20],
    }


def supported_gap_terms(our_title: str, product: dict,
                        marketplace: str = "EBAY_US") -> list:
    """
    Gap terms this product's own copy actually supports.

    The filter is the point. A word being common among competitors is a
    reason to consider it, never a reason to assert it — putting
    "waterproof" in a title because rivals use it is a false claim about the
    item, which is both an eBay policy problem and a returns problem. So a
    suggestion only survives if it already appears in the product's own
    description, features or specifics.
    """
    gap = term_gap(our_title, marketplace=marketplace)

    evidence = " ".join([
        str(product.get("description") or ""),
        " ".join(str(f) for f in (product.get("features") or [])),
        " ".join(f"{k} {v}" for k, v in
                 (product.get("specifications") or {}).items()),
        str(product.get("category") or ""),
    ]).lower()

    return [row["term"] for row in gap.get("missing", [])
            if row["term"] in evidence]
