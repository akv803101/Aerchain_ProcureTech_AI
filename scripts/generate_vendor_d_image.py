"""
Generate vendor_d_response.jpg — a blurry, slightly-rotated rate card image
in USD pricing, with 28 of 30 lines visible (2 cut off at bottom).
Run once: python scripts/generate_vendor_d_image.py
"""
import os, math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# USD prices (28 visible lines; 29-30 cut off)
USD_PRICES = [
    0.215, 0.188, 0.280, 0.147, 0.234,   # lines 1-5  (3-ply)
    0.489, 0.754, 0.993, 0.441, 0.604,   # lines 6-10 (5-ply)
    1.098, 1.393, 1.681, 2.024, 2.464,   # lines 11-15 (7-ply)
    0.326, 0.441, 0.165, 0.209, 0.374,   # lines 16-20 (specialty)
    0.258, 0.327, 0.408, 0.525, 0.101,   # lines 21-25 (inner)
    0.639, 0.789, 0.843,                  # lines 26-28 (custom, visible)
]

DESCRIPTIONS = [
    "3-ply box 30x20x10cm",        "3-ply box 25x20x15cm",
    "3-ply box 40x25x20cm",        "3-ply box 20x15x10cm",
    "3-ply box 35x25x15cm",        "5-ply box 40x30x20cm",
    "5-ply box 50x40x30cm",        "5-ply box 60x45x35cm",
    "5-ply box 35x25x20cm",        "5-ply box 45x35x25cm",
    "7-ply h/d 60x40x30cm",        "7-ply h/d 70x50x40cm",
    "7-ply h/d 80x60x40cm",        "7-ply h/d 90x60x50cm",
    "7-ply h/d 100x70x50cm",       "Archive 400x300x250mm",
    "Archive 500x380x280mm",       "Lit. mailer A4",
    "Lit. mailer A3",              "Gift box 300x200x100mm",
    "Divider set 400x300mm",       "Divider set 500x400mm",
    "Honeycomb 600x400mm",         "Honeycomb 800x600mm",
    "Corner protector 50mm",       "Die-cut tray 400x300mm",
    "Die-cut tray 500x400mm",      "Display box 300x200x150mm",
]

QUANTITIES = [5000,8000,3000,10000,6000,3000,2000,1500,4000,2500,
              1000,800,500,600,500,2000,1500,5000,3000,2000,
              1500,1000,2000,1500,5000,1000,800,1000]

W, H = 900, 1300

def make_rate_card():
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    # Try to load a font; fall back to default
    try:
        font_hdr  = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
        font_sub  = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
        font_body = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
        font_note = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 11)
    except Exception:
        font_hdr = font_sub = font_body = font_note = ImageFont.load_default()

    # Header
    draw.rectangle([0, 0, W, 60], fill="#1F4E79")
    draw.text((W//2, 15), "VENDOR D GLOBAL PACKAGING CO.", fill="white", font=font_hdr, anchor="mt")
    draw.text((W//2, 40), "RATE CARD — RFX-001 | All prices in USD | Date: 2026-09-07", fill="#BDD7EE", font=font_sub, anchor="mt")

    draw.text((30, 72), "ISO 9001:2015 Certified — No: ISO9001-D-2022-9012", fill="#1F4E79", font=font_sub)
    draw.text((30, 90), "Currency: USD  |  Exchange rate: 83.5 INR/USD (for reference only)", fill="gray", font=font_note)

    # Column headers
    y_hdr = 115
    cols = [(30, "Line"), (80, "Description"), (330, "Qty"), (420, "Unit"), (520, "USD/box")]
    draw.rectangle([25, y_hdr, W-25, y_hdr+22], fill="#D6E4F0")
    for x, label in cols:
        draw.text((x, y_hdr+4), label, fill="#1F4E79", font=font_sub)

    draw.line([25, y_hdr+22, W-25, y_hdr+22], fill="#1F4E79", width=1)

    # Data rows — only 28 rows rendered (29-30 cut off by image boundary)
    y = y_hdr + 26
    row_h = 36
    for i, (desc, usd, q) in enumerate(zip(DESCRIPTIONS, USD_PRICES, QUANTITIES)):
        if i % 2 == 0:
            draw.rectangle([25, y-2, W-25, y+row_h-4], fill="#F0F8FF")
        draw.text((30,  y+2), str(i+1),    fill="black", font=font_body)
        draw.text((80,  y+2), desc[:38],   fill="black", font=font_body)
        draw.text((330, y+2), f"{q:,}",    fill="black", font=font_body)
        draw.text((420, y+2), "per box",   fill="black", font=font_body)
        draw.text((520, y+2), f"${usd:.3f}", fill="#1a5276", font=font_body)
        draw.line([25, y+row_h-5, W-25, y+row_h-5], fill="#CCCCCC", width=1)
        y += row_h
        # Lines 29-30 would appear here but image ends

    # Footer note (partially visible — simulating cutoff)
    draw.text((30, H-40), "* All prices USD. Lead time: 21 days from PO. Payment: 50% advance.", fill="gray", font=font_note)

    # Apply slight rotation (2.5 degrees)
    img = img.rotate(2.5, expand=True, fillcolor="white")

    # Apply moderate Gaussian blur
    img = img.filter(ImageFilter.GaussianBlur(radius=1.2))

    # Crop slightly to simulate phone-photo framing
    w2, h2 = img.size
    margin = 20
    img = img.crop((margin, margin, w2-margin, h2-margin))

    out = "data/vendor_responses/vendor_d_response.jpg"
    img.save(out, "JPEG", quality=78)
    print(f"Created: {out} ({img.size[0]}x{img.size[1]}px)")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    make_rate_card()
