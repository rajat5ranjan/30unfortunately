#!/usr/bin/env python3
"""
render.py — turns approved posts into 1080x1350 carousel slides.

    .venv/bin/python render.py                 render every approved post
    .venv/bin/python render.py --id p001       one post
    .venv/bin/python render.py --from content/candidates/x.json

Output goes to docs/media/<post_id>/1.png, 2.png ... — docs/ is the GitHub Pages
root, so each rendered slide already sits at a public HTTPS URL. Meta's
publishing API pulls images from a URL; there is no byte upload.

No SVG library: the battery mark is drawn with Pillow primitives, so the only
binary dependency is the font.
"""
import argparse
import glob
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(ROOT, "config.json")) as _f:
    _CFG = json.load(_f)
FONT = os.path.join(ROOT, _CFG.get("font", "fonts/SpaceGrotesk.ttf"))
WEIGHT = _CFG.get("font_weight", "Bold")
OUT = os.path.join(ROOT, "docs", "media")

# ---------------------------------------------------------------- design tokens
W, H = 1080, 1350
PAPER, INK, RED = "#F2EEE4", "#15140F", "#D8451F"
MUTED_ON_PAPER, MUTED_ON_INK = "#9A9384", "#7A7365"

MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 104, 92, 150
TEXT_MAX_W = W - 2 * MARGIN_X          # 872
WATERMARK_H = 68
GHOST_WIDTH_RATIO = 1.08               # ghost width vs card width (slight bleed)
GHOST_BOTTOM = 40                      # gap from the card bottom
GHOST_SHIFT_X = 130                    # off-centre to the right, clearing the
                                       # left-aligned text column
COMMA_SIZE = 100                       # end-mark height, drawn not typeset
GHOST_ALPHA_PAPER, GHOST_ALPHA_INK = 0.055, 0.075

ROLE = {                               # size, line-height multiplier
    "hook":    (100, 1.18),
    "list":    (80, 1.42),
    "landing": (83, 1.30),
}


# --------------------------------------------------------------------- the mark
def _three_font(h: float) -> Tuple[ImageFont.FreeTypeFont, Tuple[int, int, int, int]]:
    """A font sized so the '3' glyph is exactly h pixels tall."""
    probe = load_font(200)
    bb = probe.getbbox("3")
    size = max(8, int(round(200 * h / (bb[3] - bb[1]))))
    f = load_font(size)
    return f, f.getbbox("3")


def draw_mark(d: ImageDraw.ImageDraw, x: float, y: float, h: float,
              stroke: Any, accent: Any) -> float:
    """Battery-at-30%: a real '3' from the typeface, then a battery as the zero.

    (x, y) is the mark's top-left; h its cap height. Returns total width.
    The '3' is set in the card's own typeface rather than drawn from arcs, so it
    is a properly designed numeral and stays consistent with the headline.
    """
    f, bb = _three_font(h)
    d.text((x - bb[0], y - bb[1]), "3", font=f, fill=stroke)
    w3 = bb[2] - bb[0]

    gap = h * 0.10
    bx = x + w3 + gap
    bw = h * 0.76   # the zero should sit as wide as the three
    sw = max(1, round(h * 0.115))
    nub_h = h * 0.13
    body_t = y + nub_h
    body_b = y + h

    # nub
    nub_w = bw * 0.42
    d.rounded_rectangle([bx + (bw - nub_w) / 2, y, bx + (bw + nub_w) / 2, body_t + sw * 0.4],
                        radius=nub_h * 0.35, fill=stroke)
    # body
    d.rounded_rectangle([bx, body_t, bx + bw, body_b], radius=bw * 0.22,
                        outline=stroke, width=sw)
    # 30% charge
    pad = sw * 1.55
    iy1, iy0 = body_b - pad, body_t + pad
    ch = (iy1 - iy0) * 0.30
    d.rounded_rectangle([bx + pad, iy1 - ch, bx + bw - pad, iy1],
                        radius=sw * 0.5, fill=accent)
    return w3 + gap + bw


def draw_comma(img: Image.Image, x: float, y: float, h: float,
               colour: str) -> float:
    """The end-mark, drawn from the same curve as endmark-comma.svg.

    Not the typeface's comma: Bricolage sets it as a straight slab, which at
    end-mark size reads as a slash. Drawn at 4x and downscaled, because Pillow
    renders thick polylines with visible facets at this weight.
    (x, y) is the top-left; h the height. Returns width.
    """
    P = [(28.0, 14.0), (28.0, 26.0), (23.0, 38.0), (10.0, 47.0)]   # cubic bezier
    sc = h / 33.0                                                   # spans y 14..47
    sw = 13.0 * sc
    w = 18.0 * sc
    SS = 4
    pad = sw
    lw, lh = int((w + 2 * pad) * SS), int((h + 2 * pad) * SS)
    layer = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)

    pts = []
    for i in range(61):
        t = i / 60.0
        u = 1 - t
        bx = (u ** 3 * P[0][0] + 3 * u * u * t * P[1][0]
              + 3 * u * t * t * P[2][0] + t ** 3 * P[3][0])
        by = (u ** 3 * P[0][1] + 3 * u * u * t * P[1][1]
              + 3 * u * t * t * P[2][1] + t ** 3 * P[3][1])
        pts.append(((bx - 10.0) * sc * SS + pad * SS,
                    (by - 14.0) * sc * SS + pad * SS))

    r = sw * SS / 2.0
    ld.line(pts, fill=colour, width=int(sw * SS))
    for px, py in pts:            # round the joins the line leaves open
        ld.ellipse([px - r, py - r, px + r, py + r], fill=colour)

    layer = layer.resize((int(w + 2 * pad), int(h + 2 * pad)), Image.LANCZOS)
    img.alpha_composite(layer, (int(x - pad), int(y - pad)))
    return w


def _hex_rgba(hex_color: str, alpha: float) -> Tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(alpha * 255))


def paste_ghost(img: Image.Image, alpha: float) -> None:
    """The oversized red 30, bled off the bottom-right corner."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    col = _hex_rgba(RED, alpha)
    # size from the width so the whole 30 stays readable whatever the typeface
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    aspect = draw_mark(probe, 0, 0, 100.0, (0, 0, 0, 0), (0, 0, 0, 0)) / 100.0
    h = (W * GHOST_WIDTH_RATIO) / aspect
    total_w = h * aspect
    # centred, bleeding off both edges: a background graphic, not a corner stamp
    draw_mark(d, (W - total_w) / 2 + GHOST_SHIFT_X, H - GHOST_BOTTOM - h, h, col, col)
    img.alpha_composite(layer)


# --------------------------------------------------------------------- typeset
def load_font(size: int, weight: Optional[str] = None) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(FONT, size)
    try:
        f.set_variation_by_name(weight or WEIGHT)
    except Exception:
        pass
    return f


def wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> List[str]:
    """Wrap to max_w, preserving the author's own line and paragraph breaks."""
    out = []  # type: List[str]
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        line = ""
        for word in para.split(" "):
            trial = (line + " " + word).strip()
            if font.getlength(trial) <= max_w or not line:
                line = trial
            else:
                out.append(line)
                line = word
        out.append(line)
    return out


def fit_text(text: str, role: str, max_w: int, max_h: int
             ) -> Tuple[List[str], ImageFont.FreeTypeFont, int]:
    """Shrink until the block fits. Over-tall beats clipped, so we never crop.

    For a list slide the escalation only reads as a list if every item sits on
    one line, so we also shrink until nothing wraps.
    """
    size, lh_mult = ROLE[role]
    source_lines = len(text.split("\n"))
    while size > 40:
        font = load_font(size)
        lines = wrap(text, font, max_w)
        lh = int(size * lh_mult)
        fits = len(lines) * lh <= max_h
        unwrapped = (role != "list") or len(lines) == source_lines
        if fits and unwrapped:
            return lines, font, lh
        size -= 3
    font = load_font(size)
    return wrap(text, font, max_w), font, int(size * lh_mult)


# ---------------------------------------------------------------------- slides
def render_slide(text: str, role: str, index: int, total: int,
                 handle: str) -> Image.Image:
    is_last = index == total - 1
    bg, fg = (INK, PAPER) if is_last else (PAPER, INK)
    muted = MUTED_ON_INK if is_last else MUTED_ON_PAPER

    img = Image.new("RGBA", (W, H), bg)
    paste_ghost(img, GHOST_ALPHA_INK if is_last else GHOST_ALPHA_PAPER)
    d = ImageDraw.Draw(img)

    # corner watermark, top-left, flush with the text margin
    draw_mark(d, MARGIN_X, MARGIN_TOP, WATERMARK_H, fg, RED)

    body_top = MARGIN_TOP + WATERMARK_H + 70
    body_bottom = H - MARGIN_BOTTOM
    lines, font, lh = fit_text(text, role, TEXT_MAX_W, body_bottom - body_top)

    y = body_top + (body_bottom - body_top - len(lines) * lh) // 2
    for line in lines:
        d.text((MARGIN_X, y), line, font=font, fill=fg)
        y += lh

    foot_y = H - MARGIN_BOTTOM + 44
    if is_last:
        hf = load_font(27, "Medium")
        d.text((MARGIN_X, foot_y), handle, font=hf, fill=muted)
        cw = 18.0 * (COMMA_SIZE / 33.0)
        draw_comma(img, W - MARGIN_X - cw, foot_y - COMMA_SIZE * 0.52, COMMA_SIZE, RED)
    else:
        r, gap = 9, 26
        cx = MARGIN_X + r
        for i in range(total):
            fill = fg if i == index else (INK + "40" if not is_last else PAPER)
            d.ellipse([cx - r, foot_y + 4, cx + r, foot_y + 4 + 2 * r],
                      fill=fg if i == index else muted)
            cx += gap
        sf = load_font(26, "Bold")
        label = "SWIPE →"
        d.text((W - MARGIN_X - sf.getlength(label), foot_y), label, font=sf, fill=RED)

    return img.convert("RGB")


def render_post(post: Dict[str, Any], handle: str, outdir: str) -> List[str]:
    slides = post["slides"]
    total = len(slides)
    os.makedirs(outdir, exist_ok=True)
    paths = []
    for i, text in enumerate(slides):
        role = "hook" if i == 0 else ("landing" if i == total - 1 else "list")
        img = render_slide(text, role, i, total, handle)
        p = os.path.join(outdir, "%d.png" % (i + 1))
        img.save(p, "PNG", optimize=True)
        paths.append(p)
    return paths


# ----------------------------------------------------------------- contact sheet
SHEET_CSS = """
*{box-sizing:border-box}body{margin:0;background:#E7E2D6;color:#15140F;
font-family:'Bricolage Grotesque',system-ui,sans-serif;padding:40px}
h1{font-size:30px;letter-spacing:-1px;margin:0 0 4px}
p.sub{margin:0 0 28px;color:#5E5849;font-size:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:26px}
.post{background:#FBF9F4;border-radius:12px;padding:14px;box-shadow:0 3px 12px rgba(21,20,15,.1)}
.strip{display:flex;gap:7px;overflow-x:auto}
.strip img{width:31%;flex-shrink:0;border-radius:4px;display:block}
.meta{font-size:11px;color:#6E675A;margin-top:10px;line-height:1.5}
.meta b{color:#D8451F}
a{color:#15140F}
"""


def all_known_posts() -> List[Dict[str, Any]]:
    """Every post with rendered slides — seeds plus approved — newest first.

    The sheet is the approval UI, so it must show everything regardless of what
    this particular run happened to render.
    """
    out = []
    for path in ([os.path.join(ROOT, "content", "seed_posts.json")]
                 + sorted(glob.glob(os.path.join(ROOT, "content", "approved", "*.json")))):
        if not os.path.exists(path):
            continue
        with open(path) as f:
            out.extend(json.load(f).get("posts", []))
    return [p for p in out
            if os.path.isdir(os.path.join(OUT, p.get("id", "")))][::-1]


def write_contact_sheet(posts: List[Dict[str, Any]], handle: str) -> None:
    """A static approval page. Cheapest possible review UI: look, then delete."""
    posts = all_known_posts() or posts
    cards = []
    for i, p in enumerate(posts):
        pid = p.get("id") or "c%03d" % (i + 1)
        imgs = "".join('<img src="media/%s/%d.png" alt="slide %d">' % (pid, n + 1, n + 1)
                       for n in range(len(p["slides"])))
        cards.append(
            '<div class="post"><div class="strip">%s</div>'
            '<div class="meta"><b>%s</b> &middot; %s &middot; satire %s%s<br>%s</div></div>'
            % (imgs, pid, p.get("structure", ""), p.get("satire_level", ""),
               " &middot; hinglish" if p.get("hinglish") else "",
               p.get("angle", "")))
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>%s — contact sheet</title>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        'family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,700&display=swap">'
        '<style>%s</style></head><body>'
        '<h1>Thirty Unfortunately</h1>'
        '<p class="sub">%d posts rendered &middot; %s &middot; these files are what '
        'Meta pulls at publish time</p><div class="grid">%s</div></body></html>'
        % (handle, SHEET_CSS, len(posts), handle, "".join(cards)))
    with open(os.path.join(ROOT, "docs", "index.html"), "w") as f:
        f.write(html)


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src",
                    default=os.path.join(ROOT, "content", "seed_posts.json"))
    ap.add_argument("--id", help="render a single post id")
    args = ap.parse_args()

    if not os.path.exists(FONT):
        sys.exit("fonts/SpaceGrotesk.ttf missing — see README")

    with open(args.src) as f:
        blob = json.load(f)
    handle = blob.get("handle", "@30unfortunately")
    posts = blob.get("posts") or blob.get("accepted") or []
    if args.id:
        posts = [p for p in posts if p.get("id") == args.id]
        if not posts:
            sys.exit("no post with id %s in %s" % (args.id, args.src))

    with open(os.path.join(ROOT, "config.json")) as f:
        base = json.load(f).get("pages_base_url", "").rstrip("/")

    total_bytes = 0
    for i, post in enumerate(posts):
        pid = post.get("id") or "c%03d" % (i + 1)
        paths = render_post(post, handle, os.path.join(OUT, pid))
        total_bytes += sum(os.path.getsize(p) for p in paths)
        print("%s  %d slides" % (pid, len(paths)))
        for p in paths:
            rel = os.path.relpath(p, os.path.join(ROOT, "docs"))
            print("    %s/%s" % (base, rel) if base else "    %s" % os.path.relpath(p, ROOT))

    write_contact_sheet(posts, blob.get("handle", ""))
    print("\n%d posts, %.1f MB total" % (len(posts), total_bytes / 1e6))
    print("contact sheet: docs/index.html  (open it to approve)")


if __name__ == "__main__":
    main()
