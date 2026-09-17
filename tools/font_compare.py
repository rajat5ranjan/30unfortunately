#!/usr/bin/env python3
"""Render the same slides in every candidate typeface, tiled into one sheet."""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image, ImageDraw, ImageFont
import render

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POST = json.load(open(os.path.join(ROOT, "content", "seed_posts.json")))["posts"][4]
CANDIDATES = [
    ("Space Grotesk", "fonts/SpaceGrotesk.ttf", "Bold"),
    ("Bricolage Grotesque", "fonts/BricolageGrotesque.ttf", "ExtraBold"),
    ("Archivo", "fonts/Archivo.ttf", "Bold"),
    ("Familjen Grotesk", "fonts/FamiljenGrotesk.ttf", "Bold"),
    ("Instrument Sans", "fonts/InstrumentSans.ttf", "Bold"),
]

TW, TH, PAD, LABEL = 360, 450, 22, 42
sheet_w = len(CANDIDATES) * (TW + PAD) + PAD
sheet_h = LABEL + 2 * (TH + PAD) + PAD
sheet = Image.new("RGB", (sheet_w, sheet_h), "#E7E2D6")
d = ImageDraw.Draw(sheet)
label_font = ImageFont.truetype(os.path.join(ROOT, "fonts", "SpaceGrotesk.ttf"), 20)
try:
    label_font.set_variation_by_name("Bold")
except Exception:
    pass

for i, (name, path, weight) in enumerate(CANDIDATES):
    render.FONT = os.path.join(ROOT, path)
    render.WEIGHT = weight
    x = PAD + i * (TW + PAD)
    d.text((x, 12), name, font=label_font, fill="#15140F")
    for row, idx in enumerate((0, len(POST["slides"]) - 1)):
        role = "hook" if idx == 0 else "landing"
        img = render.render_slide(POST["slides"][idx], role, idx,
                                  len(POST["slides"]), "@30unfortunately")
        sheet.paste(img.resize((TW, TH), Image.LANCZOS),
                    (x, LABEL + row * (TH + PAD)))

out = os.path.join(ROOT, "docs", "font-compare.png")
sheet.save(out, "PNG", optimize=True)
print("->", os.path.relpath(out, ROOT), sheet.size)
