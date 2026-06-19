"""
ebay_formatter.py — Generates professional eBay listing HTML and structured export data.
The HTML template follows eBay best practices for high-conversion listings.
"""

import re

def _sanitize_ebay_sku(value: str, fallback_title: str = "") -> str:
    raw = str(value or "").strip() or str(fallback_title or "ITEM")
    cleaned = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    return (cleaned or "ITEM")[:50]


def generate_ebay_html(product: dict) -> str:
    """
    Generate professional eBay listing HTML from a product draft.

    Args:
        product: The full product draft dict from session state

    Returns:
        Complete HTML string ready to paste into eBay's HTML description editor
    """
    title = product.get("title", "")
    description = product.get("description", "")
    features = product.get("features", [])
    specifications = product.get("specifications", {})
    images = product.get("images", [])
    brand = product.get("brand", "")
    condition = product.get("condition", "New")
    weight = product.get("weight", "")
    dimensions = product.get("dimensions", "")

    # Build features HTML
    features_html = ""
    if features:
        features_html = "<ul class='eb-features'>" + "".join(
            f"<li>{f}</li>" for f in features
        ) + "</ul>"

    # Build specs HTML
    specs_html = ""
    if specifications:
        rows = "".join(
            f"<tr><td class='spec-key'>{k}</td><td class='spec-val'>{v}</td></tr>"
            for k, v in specifications.items()
        )
        if weight:
            rows += f"<tr><td class='spec-key'>Weight</td><td class='spec-val'>{weight}</td></tr>"
        if dimensions:
            rows += f"<tr><td class='spec-key'>Dimensions</td><td class='spec-val'>{dimensions}</td></tr>"
        specs_html = f"<table class='eb-specs'><tbody>{rows}</tbody></table>"

    # Build image gallery HTML
    main_img = images[0] if images else ""
    thumbs_html = ""
    if len(images) > 1:
        thumbs_html = "<div class='eb-thumbs'>" + "".join(
            f"<img src='{img}' class='eb-thumb' onclick='showMain(this.src)' />"
            for img in images[:8]
        ) + "</div>"

    # Convert description newlines to HTML
    desc_html = description.replace("\n\n", "</p><p>").replace("\n", "<br/>")
    desc_html = f"<p>{desc_html}</p>"

    brand_row = f"<span class='eb-badge'>Brand: {brand}</span>" if brand else ""
    condition_row = f"<span class='eb-badge eb-badge-green'>Condition: {condition}</span>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
    color: #222;
    background: #fff;
    max-width: 900px;
    margin: 0 auto;
    padding: 20px;
    font-size: 15px;
    line-height: 1.65;
  }}
  .eb-header {{
    background: linear-gradient(135deg, #0053a0 0%, #003f7d 100%);
    color: white;
    padding: 24px 28px;
    border-radius: 8px;
    margin-bottom: 24px;
  }}
  .eb-header h1 {{
    font-size: 22px;
    font-weight: 700;
    line-height: 1.3;
    margin-bottom: 10px;
  }}
  .eb-badges {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }}
  .eb-badge {{
    background: rgba(255,255,255,0.15);
    color: white;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 500;
  }}
  .eb-badge-green {{ background: #2ecc71; color: white; }}
  .eb-main {{ display: flex; gap: 28px; margin-bottom: 28px; flex-wrap: wrap; }}
  .eb-gallery {{ flex: 0 0 380px; max-width: 380px; }}
  .eb-main-img {{
    width: 100%;
    height: 380px;
    object-fit: contain;
    border: 1px solid #e5e5e5;
    border-radius: 8px;
    background: #fafafa;
    display: block;
  }}
  .eb-thumbs {{
    display: flex;
    gap: 6px;
    margin-top: 8px;
    flex-wrap: wrap;
  }}
  .eb-thumb {{
    width: 60px;
    height: 60px;
    object-fit: cover;
    border: 1px solid #ddd;
    border-radius: 4px;
    cursor: pointer;
    transition: border-color 0.2s;
  }}
  .eb-thumb:hover {{ border-color: #0053a0; }}
  .eb-info {{ flex: 1; min-width: 260px; }}
  .eb-section {{
    margin-bottom: 28px;
    border: 1px solid #e8e8e8;
    border-radius: 8px;
    overflow: hidden;
  }}
  .eb-section-header {{
    background: #f5f7fa;
    border-bottom: 1px solid #e8e8e8;
    padding: 12px 18px;
    font-weight: 600;
    font-size: 15px;
    color: #0053a0;
  }}
  .eb-section-body {{ padding: 18px; }}
  .eb-features li {{
    list-style: none;
    padding: 6px 0;
    padding-left: 22px;
    position: relative;
    border-bottom: 1px solid #f0f0f0;
    font-size: 14px;
  }}
  .eb-features li:last-child {{ border-bottom: none; }}
  .eb-features li::before {{
    content: '✓';
    position: absolute;
    left: 0;
    color: #2ecc71;
    font-weight: 700;
  }}
  .eb-specs {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  .eb-specs tr:nth-child(even) {{ background: #f9f9f9; }}
  .spec-key {{
    padding: 8px 12px;
    font-weight: 600;
    color: #555;
    width: 40%;
    border-right: 1px solid #eee;
  }}
  .spec-val {{ padding: 8px 12px; color: #222; }}
  .eb-desc p {{
    margin-bottom: 12px;
    font-size: 14px;
    color: #333;
    line-height: 1.7;
  }}
  .eb-trust {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    padding: 20px;
    background: #f0f7ff;
    border-radius: 8px;
    margin-bottom: 20px;
    text-align: center;
  }}
  .eb-trust-item {{ font-size: 13px; color: #333; }}
  .eb-trust-icon {{ font-size: 24px; margin-bottom: 4px; display: block; }}
  .eb-trust-label {{ font-weight: 600; font-size: 13px; }}
  .eb-footer {{
    text-align: center;
    padding: 20px;
    background: #0053a0;
    color: white;
    border-radius: 8px;
    font-size: 13px;
  }}
</style>
</head>
<body>

<!-- HEADER -->
<div class="eb-header">
  <h1>{title}</h1>
  <div class="eb-badges">
    {brand_row}
    {condition_row}
  </div>
</div>

<!-- MAIN: IMAGE + QUICK INFO -->
<div class="eb-main">
  <div class="eb-gallery">
    {f'<img src="{main_img}" id="mainImg" class="eb-main-img" alt="{title}" />' if main_img else '<div class="eb-main-img" style="display:flex;align-items:center;justify-content:center;color:#aaa;font-size:14px;">No image available</div>'}
    {thumbs_html}
  </div>
  <div class="eb-info">
    <div class="eb-section">
      <div class="eb-section-header">🌟 Why Buy From Us</div>
      <div class="eb-section-body">
        <div class="eb-trust">
          <div class="eb-trust-item">
            <span class="eb-trust-icon">🚚</span>
            <div class="eb-trust-label">Fast Shipping</div>
            <div>Quick dispatch</div>
          </div>
          <div class="eb-trust-item">
            <span class="eb-trust-icon">🔒</span>
            <div class="eb-trust-label">Secure Purchase</div>
            <div>eBay protected</div>
          </div>
          <div class="eb-trust-item">
            <span class="eb-trust-icon">✅</span>
            <div class="eb-trust-label">Quality Checked</div>
            <div>Every item inspected</div>
          </div>
          <div class="eb-trust-item">
            <span class="eb-trust-icon">💬</span>
            <div class="eb-trust-label">Top Support</div>
            <div>Quick responses</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- DESCRIPTION -->
<div class="eb-section">
  <div class="eb-section-header">📝 Product Description</div>
  <div class="eb-section-body eb-desc">
    {desc_html}
  </div>
</div>

<!-- KEY FEATURES -->
{f'''<div class="eb-section">
  <div class="eb-section-header">✅ Key Features</div>
  <div class="eb-section-body">
    {features_html}
  </div>
</div>''' if features_html else ''}

<!-- SPECIFICATIONS -->
{f'''<div class="eb-section">
  <div class="eb-section-header">📋 Specifications</div>
  <div class="eb-section-body">
    {specs_html}
  </div>
</div>''' if specs_html else ''}

<!-- FOOTER -->
<div class="eb-footer">
  <p><strong>Questions? Message us before purchasing — we're happy to help!</strong></p>
  <p style="margin-top:6px; opacity:0.85;">✅ Secure checkout &nbsp;|&nbsp; 🚚 Fast dispatch &nbsp;|&nbsp; 💬 Friendly support</p>
</div>

<script>
function showMain(src) {{
  document.getElementById('mainImg').src = src;
}}
</script>
</body>
</html>"""
    return html


def generate_ebay_export(product: dict) -> dict:
    """
    Generate the full eBay listing export package.

    Returns a dict with all the data needed to manually create an eBay listing
    or feed into the eBay API.
    """
    return {
        "title": product.get("title", "")[:80],  # eBay max title length
        "description_html": generate_ebay_html(product),
        "category": product.get("category", ""),
        "condition": product.get("condition", "New"),
        "price": product.get("price", ""),
        "brand": product.get("brand", ""),
        "sku": _sanitize_ebay_sku(product.get("sku", ""), product.get("title", "")),
        "images": product.get("images", [])[:12],  # eBay max 12 images
        "item_specifics": _build_item_specifics(product),
        "tags": product.get("tags", []),
    }


def _build_item_specifics(product: dict) -> dict:
    """Build eBay item specifics dict from product data."""
    specifics = {}
    if product.get("brand"):
        specifics["Brand"] = product["brand"]
    if product.get("condition"):
        specifics["Condition"] = product["condition"]
    if product.get("weight"):
        specifics["Item Weight"] = product["weight"]
    if product.get("dimensions"):
        specifics["Item Dimensions"] = product["dimensions"]
    # Add any specs as item specifics
    for k, v in (product.get("specifications") or {}).items():
        if len(specifics) < 20:  # eBay item specifics limit
            specifics[k] = str(v)[:65]  # eBay value max 65 chars
    return specifics
