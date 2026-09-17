#!/usr/bin/env python3
"""Generate the brand mark SVG from the real typeface glyph.

The '3' is extracted as an outline from the card font at its display weight, so
the SVG, the rendered PNGs and the headlines are all the same numeral. Run this
again whenever config.json's font changes.
"""
import json, os, sys
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.boundsPen import BoundsPen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cfg = json.load(open(os.path.join(ROOT, "config.json")))
FONT = os.path.join(ROOT, cfg.get("font", "fonts/SpaceGrotesk.ttf"))
WGHT = cfg.get("svg_glyph_weight", 800)

f = TTFont(FONT)
if "fvar" in f:
    axes = {a.axisTag: a.defaultValue for a in f["fvar"].axes}
    if "wght" in axes:
        axes["wght"] = WGHT
    f = instancer.instantiateVariableFont(f, axes, inplace=False)

gs = f.getGlyphSet()
name = "three"
bp = BoundsPen(gs); gs[name].draw(bp)
xmin, ymin, xmax, ymax = bp.bounds
pen = SVGPathPen(gs); gs[name].draw(pen)
d_attr = pen.getCommands()

H = 100.0
s = H / (ymax - ymin)
w3 = (xmax - xmin) * s
# same proportions as render.py's draw_mark, at h = 100
gap, bw, sw, nub_h = 10.0, 76.0, 11.5, 13.0
nub_w = bw * 0.42
bx = w3 + gap
pad = sw * 1.55
iy1, iy0 = H - pad, nub_h + pad
ch = (iy1 - iy0) * 0.30
total_w = w3 + gap + bw
# matrix flips the glyph's y-up coordinates into SVG's y-down box
matrix = "matrix(%.5f 0 0 %.5f %.3f %.3f)" % (s, -s, -s * xmin, s * ymin + H)

svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %.1f 100" fill="none" role="img" aria-label="30 with the zero as a battery at 30 percent">
  <!-- '3' is the real %s glyph; regenerate with tools/make_mark_svg.py -->
  <path d="%s" transform="%s" fill="currentColor"/>
  <rect x="%.2f" y="0" width="%.2f" height="%.2f" rx="%.2f" fill="currentColor"/>
  <rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="%.2f" stroke="currentColor" stroke-width="%.2f"/>
  <rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="%.2f" fill="#D8451F"/>
</svg>
''' % (total_w, os.path.basename(FONT), d_attr, matrix,
       bx + (bw - nub_w) / 2, nub_w, nub_h + sw * 0.4, nub_h * 0.35,
       bx + sw / 2, nub_h + sw / 2, bw - sw, H - nub_h - sw, bw * 0.22, sw,
       bx + pad, iy1 - ch, bw - 2 * pad, ch, sw * 0.5)

out = os.path.join(ROOT, "assets", "brand", "mark-30-battery.svg")
open(out, "w").write(svg)
print("%s  (viewBox 0 0 %.1f 100, glyph from %s @ wght %s)"
      % (os.path.relpath(out, ROOT), total_w, os.path.basename(FONT), WGHT))
