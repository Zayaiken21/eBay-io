"""
ai_extractor.py — Thin wrapper that calls scraper directly.
No AI. Exists so products.py import path stays unchanged.
"""

from ui.scraper import fetch_product_page


def extract_product_data(page_data: dict) -> dict:
    """
    page_data is already the full parsed product from scraper.fetch_product_page().
    This just re-wraps it in the expected return shape.
    """
    product = page_data.get("product")
    if product:
        return {"success": True, "product": product, "error": None}
    return {"success": False, "product": None, "error": page_data.get("error", "Unknown error")}
