#!/usr/bin/env python3
"""Render the Instagram profile picture.

Instagram crops to a circle, so the mark sits well inside the inscribed circle
rather than filling the square. Uses the same draw_mark as the cards, so the
profile picture and the corner watermark can never drift apart.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image, ImageDraw
import render

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "brand")
SIZE = 1080
MARK_W = SIZE * 0.56          # inside the circle with room to spare


def build(bg, stroke, name, guide=False):
    img = Image.new("RGBA", (SIZE, SIZE), bg)
    d = ImageDraw.Draw(img)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    aspect = render.draw_mark(probe, 0, 0, 100.0, (0, 0, 0, 0), (0, 0, 0, 0)) / 100.0
    h = MARK_W / aspect
    render.draw_mark(d, (SIZE - MARK_W) / 2, (SIZE - h) / 2, h, stroke, render.RED)
    if guide:  # visualise Instagram's circular crop
        d.ellipse([2, 2, SIZE - 2, SIZE - 2], outline=(216, 69, 31, 90), width=4)
    path = os.path.join(OUT, name)
    img.convert("RGB").save(path, "PNG", optimize=True)
    print("  %-26s %dx%d  %.0f KB" % (name, SIZE, SIZE, os.path.getsize(path) / 1024))


build(render.INK, render.PAPER, "pfp-dark.png")

if "--all" in sys.argv:          # variants, for checking rather than shipping
    build(render.PAPER, render.INK, "pfp-light.png")
    build(render.INK, render.PAPER, "pfp-dark-cropguide.png", guide=True)
