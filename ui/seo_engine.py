"""
seo_engine.py — combinatorial listing copy, no AI and no API key.

What this is
------------
A phrase bank plus an assembly grammar. Every section of a description is
built by choosing one option from each of several independent slots, so the
number of distinct outputs is the product of the slot sizes rather than the
size of any single list. combination_count() reports the real figure.

What it will not do
-------------------
Vary a fact. Slots carry sentence *shape* and *connective* wording only; the
product's claims come from its own extracted data and pass through unchanged.
Rewording what a supplier wrote is not what makes a listing original or safe
— stating the same facts in our own structure is. So nothing here invents a
benefit, a measurement, or a material.

Selection is deterministic for a given (variant, slot), so a listing can be
reproduced, while consecutive variants differ in every slot.
"""

from __future__ import annotations

import hashlib
import re

# ── Slot banks ───────────────────────────────────────────────────────────────
# Each is a list of interchangeable wordings for one job. They describe how a
# sentence is framed, never what is true of the product.

OPENERS = [
    "{subject} — {detail}.",
    "{subject}: {detail}.",
    "{subject}, featuring {detail}.",
    "Meet the {subject_l} — {detail}.",
    "{detail_cap}, all in one {subject_l}.",
    "Here is the {subject_l} you have been looking for: {detail}.",
    "{subject} brings you {detail}.",
    "Shopping for a {subject_l}? This one offers {detail}.",
    "{subject} delivers {detail}.",
    "Built around {detail} — that is the {subject_l}.",
    "What makes this {subject_l} worth a look: {detail}.",
    "{subject} comes with {detail}.",
]

BULLET_LEADS = [
    "✔", "✓", "•", "▪", "➤", "★",
]

BENEFIT_FRAMES = [
    "{fact}",
    "{fact} — ready when you are",
    "{fact}, so you are covered",
    "{fact} for everyday use",
    "{fact}, straight out of the box",
    "{fact} — no guesswork",
    "{fact}, as described",
    "{fact} you can count on",
]

SECTION_TITLES = {
    "benefits": [
        "KEY FEATURES", "WHAT YOU GET", "HIGHLIGHTS", "AT A GLANCE",
        "WHY THIS ONE", "THE DETAILS", "FEATURES", "WORTH KNOWING",
    ],
    "specs": [
        "SPECIFICATIONS", "PRODUCT DETAILS", "THE SPECS", "TECHNICAL DETAILS",
        "ITEM DETAILS", "QUICK SPECS",
    ],
    "box": [
        "WHAT'S IN THE BOX", "INCLUDED", "IN THE PACKAGE", "YOU RECEIVE",
        "PACKAGE CONTENTS",
    ],
    "trust": [
        "BUY WITH CONFIDENCE", "WHY BUY FROM US", "OUR PROMISE",
        "SHOPPING WITH US", "WHAT WE GUARANTEE", "YOUR PURCHASE, PROTECTED",
    ],
    "shipping": [
        "SHIPPING & HANDLING", "DELIVERY", "SHIPPING", "DISPATCH & DELIVERY",
        "HOW IT SHIPS",
    ],
}

# Icons vary within a section, never across. A shared pool produced headings
# like "🚚 WORTH KNOWING" and "📋 DELIVERY" — a lorry over the feature list and
# a clipboard over shipping. Varied wording is the goal; a listing whose icons
# do not match their sections just looks machine-made, which is the opposite.
SECTION_ICON_BANKS = {
    "benefits": ["✅", "⭐", "✨", "🔥"],
    "specs": ["📋", "📑", "🧾", "📐"],
    "box": ["📦", "🎁", "🗃️"],
    "trust": ["🛒", "🛡️", "🤝", "💬"],
    "shipping": ["🚚", "📬", "✈️", "⏱️"],
}

# Flat view, for callers that need to recognise any icon this module emits
# (the description rebuilder strips them).
SECTION_ICONS = sorted({i for b in SECTION_ICON_BANKS.values() for i in b})

# Operational promises. These describe how the seller runs their own store,
# so they are safe to vary — unlike anything about the product itself.
TRUST_LINES = [
    ["Checked before it ships.", "Every item inspected before dispatch.",
     "Quality checked, then packed.", "Inspected and packed with care."],
    ["Tracking on every order.", "Tracked shipping as standard.",
     "A tracking number on every parcel.", "Full tracking included."],
    ["Questions answered within 24 hours.", "Message us any time — we reply fast.",
     "Friendly support, quick replies.", "Got a question? We are here."],
    ["Straightforward returns.", "Hassle-free returns if it is not right.",
     "Easy returns, no fuss.", "Returns accepted — just reach out."],
]

SHIPPING_LINES = [
    ["Packed and dispatched within 1–2 business days.",
     "Ships within 1–2 business days.",
     "Out the door in 1–2 business days.",
     "Dispatched quickly — usually next business day."],
    ["Combined shipping on multiple items — message first.",
     "Buying more than one? Ask about combined shipping.",
     "Combined postage available on multi-item orders.",
     "We can combine shipping — just get in touch."],
    ["Carefully packaged to arrive in one piece.",
     "Protective packaging on every order.",
     "Packed properly so it arrives as it left.",
     "Securely boxed for transit."],
]

CLOSERS = [
    "Add to cart while stock lasts.",
    "Order today and it ships straight away.",
    "Ready to ship now.",
    "In stock and ready to go.",
    "Buy with confidence — questions always welcome.",
    "Grab yours while it is available.",
]


def _pick(bank: list, variant: int, slot: str):
    """
    Deterministic choice for (variant, slot).

    Hashing the slot name alongside the variant means slots advance
    independently: bumping the variant changes every slot at once, rather
    than rotating one list while the rest stay put.
    """
    if not bank:
        return ""
    digest = hashlib.sha256(f"{slot}:{variant}".encode()).digest()
    return bank[int.from_bytes(digest[:4], "big") % len(bank)]


def combination_count() -> int:
    """Distinct outputs this grammar can produce, by construction."""
    total = len(OPENERS) * len(BULLET_LEADS) * len(BENEFIT_FRAMES)
    for bank in SECTION_TITLES.values():
        total *= len(bank)
    for bank in SECTION_ICON_BANKS.values():
        total *= len(bank)
    for group in TRUST_LINES:
        total *= len(group)
    for group in SHIPPING_LINES:
        total *= len(group)
    total *= len(CLOSERS)
    return total


def heading(kind: str, variant: int) -> str:
    icon = _pick(SECTION_ICON_BANKS.get(kind, ["•"]), variant, f"icon_{kind}")
    title = _pick(SECTION_TITLES.get(kind, ["DETAILS"]), variant, f"title_{kind}")
    return f"{icon} {title}"


def opener(subject: str, detail: str, variant: int) -> str:
    """Opening line. `detail` is the product's own fact, inserted verbatim."""
    if not detail:
        return ""
    subject = (subject or "This item").strip()
    shape = _pick(OPENERS, variant, "opener")
    out = shape.format(
        subject=subject,
        subject_l=subject[:1].lower() + subject[1:],
        detail=detail[:1].lower() + detail[1:],
        detail_cap=detail[:1].upper() + detail[1:],
    )
    return re.sub(r"\s{2,}", " ", out).strip()


def bullets(facts: list, variant: int) -> list:
    """
    One bullet per fact. The frame varies; the fact does not.

    Frames are offset per line so a list does not repeat one suffix down the
    page, which is what makes generated copy obvious.
    """
    lead = _pick(BULLET_LEADS, variant, "lead")
    out = []
    for i, fact in enumerate(facts):
        fact = str(fact).strip().rstrip(".")
        if not fact:
            continue
        frame = _pick(BENEFIT_FRAMES, variant + i * 7, f"frame_{i}")
        out.append(f"{lead} {frame.format(fact=fact)}")
    return out


def trust_block(variant: int) -> str:
    lines = [_pick(group, variant + i * 3, f"trust_{i}")
             for i, group in enumerate(TRUST_LINES)]
    return heading("trust", variant) + "\n" + "\n".join(f"• {l}" for l in lines)


def shipping_block(variant: int) -> str:
    lines = [_pick(group, variant + i * 5, f"ship_{i}")
             for i, group in enumerate(SHIPPING_LINES)]
    return heading("shipping", variant) + "\n" + "\n".join(f"• {l}" for l in lines)


def closer(variant: int) -> str:
    return _pick(CLOSERS, variant, "closer")


# ── Title phrasing ───────────────────────────────────────────────────────────
# Orderings only. No term is introduced that the product's own copy does not
# already support — the caller filters for that before passing them in.

TITLE_SHAPES = [
    "{brand} {core} {extras}",
    "{core} {extras} {brand}",
    "{brand} {core} - {extras}",
    "{core} - {brand} {extras}",
    "{brand} {extras} {core}",
    "{core} {brand} {extras}",
]


def title_variants(brand: str, core: str, extras: list, variant: int,
                   limit: int = 80) -> str:
    """
    Reorders brand, product name and supported keywords within eBay's
    80-character limit. Extras are rotated so different supported terms make
    the cut on different variants.
    """
    core = (core or "").strip()
    brand = (brand or "").strip()
    extras = [e for e in (extras or []) if e]

    if extras:
        shift = variant % len(extras)
        extras = extras[shift:] + extras[:shift]

    shape = _pick(TITLE_SHAPES, variant, "title_shape")
    built = ""
    for n in range(len(extras), -1, -1):
        candidate = shape.format(
            brand=brand, core=core,
            extras=" ".join(e.title() for e in extras[:n]),
        )
        candidate = re.sub(r"\s{2,}", " ", candidate).strip(" -")
        if len(candidate) <= limit:
            built = candidate
            break

    return built or core[:limit]
