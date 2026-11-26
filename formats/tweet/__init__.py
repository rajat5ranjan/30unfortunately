"""Tweet: one post from our own handle, as the dark-mode post view, punchline late.

Fields (`tweet`):
  setup   the first line(s), on screen from frame 0
  punch   the last line, which lands once the setup has been read

Drawn as the post-detail screen of the app everyone screenshots — avatar,
name, handle, the text, the timestamp and views line, the action row — but
it is our handle and our joke, and there is no app logo anywhere. Things keep
moving so the frame is never dead: the views count ticks up throughout, the
punch rises in, and once it has been read the like is tapped. Then it loops.
"""
import hashlib
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw

from formats.common import (H, SAFE_BOTTOM, SAFE_TOP, W, back, brand_avatar, clean,
                            ease, heart, load_font, loop_frames, read_secs, score,
                            tw, wrap)

FIELDS = {"tweet": {"setup": "str, <= 120 chars", "punch": "str, <= 140 chars"}}

BG, TEXT, MUTED, RULE = "#000000", "#E7E9EA", "#71767B", "#2F3336"
PINK = "#F91880"
NAME = "Thirty, unfortunately"
SIZES = (82, 76, 70, 64, 58, 52)
X0 = 54
TEXT_W = W - 2 * X0


def _parts(post: Dict[str, Any]) -> Tuple[str, str]:
    t = post.get("tweet") or {}
    if isinstance(t, str):
        paras = [p.strip() for p in clean(t).split("\n\n") if p.strip()]
        return ("\n\n".join(paras[:-1]), paras[-1]) if len(paras) > 1 else (paras[0], "")
    return clean(t.get("setup", "")), clean(t.get("punch", ""))


def _numbers(pid: str) -> Dict[str, int]:
    """Plausible, modest, and the same every time a post is re-rendered."""
    h = int(hashlib.sha1((pid or "x").encode()).hexdigest(), 16)
    likes = 1800 + h % 7200
    return {"views": 90000 + (h >> 8) % 400000, "likes": likes,
            "reposts": likes // (4 + (h >> 20) % 4), "replies": likes // (30 + (h >> 24) % 30),
            "bookmarks": likes // (6 + (h >> 28) % 6)}


def _k(n: float) -> str:
    if n >= 1_000_000:
        return "%.1fM" % (n / 1e6)
    if n >= 10_000:
        return "%.1fK" % (n / 1e3)
    return "{:,}".format(int(n))


# -------------------------------------------------------------- the icons
def _reply(d, x, y, c):
    d.rounded_rectangle([x, y, x + 46, y + 36], 16, outline=c, width=4)
    d.polygon([(x + 8, y + 32), (x + 8, y + 46), (x + 22, y + 34)], fill=c)


def _repost(d, x, y, c):
    d.line([(x + 6, y + 30), (x + 6, y + 8), (x + 38, y + 8)], fill=c, width=4)
    d.polygon([(x - 2, y + 22), (x + 14, y + 22), (x + 6, y + 34)], fill=c)
    d.line([(x + 46, y + 14), (x + 46, y + 36), (x + 14, y + 36)], fill=c, width=4)
    d.polygon([(x + 38, y + 22), (x + 54, y + 22), (x + 46, y + 10)], fill=c)


def _views(d, x, y, c):
    for k, hh in enumerate((18, 34, 26)):
        d.rectangle([x + 6 + k * 15, y + 42 - hh, x + 11 + k * 15, y + 42], fill=c)


def _bookmark(d, x, y, c):
    d.line([(x + 8, y + 44), (x + 8, y + 2), (x + 38, y + 2), (x + 38, y + 44),
            (x + 23, y + 32), (x + 8, y + 44)], fill=c, width=4, joint="curve")


def _share(d, x, y, c):
    d.line([(x + 4, y + 26), (x + 4, y + 44), (x + 42, y + 44), (x + 42, y + 26)],
           fill=c, width=4)
    d.line([(x + 23, y + 34), (x + 23, y + 2)], fill=c, width=4)
    d.line([(x + 11, y + 14), (x + 23, y + 2), (x + 35, y + 14)], fill=c, width=4)


# --------------------------------------------------------------- layout
def _layout(setup: str, punch: str) -> Dict[str, Any]:
    for size in SIZES:
        f = load_font(size, "Regular")
        sl = wrap(setup, f, TEXT_W)
        pl = wrap(punch, f, TEXT_W) if punch else []
        lh = int(size * 1.28)
        gap = int(lh * 0.5) if pl else 0
        body = (len(sl) + len(pl)) * lh + gap
        if body <= 900:
            break
    head_h, meta_h, row_h = 150, 96, 120
    total = head_h + 36 + body + 40 + meta_h + row_h
    top = SAFE_TOP + 80 + max(0, (H - SAFE_TOP - 80 - SAFE_BOTTOM - total) // 2)
    return {"f": f, "lh": lh, "sl": sl, "pl": pl, "gap": gap, "top": top,
            "text_y": top + head_h + 36, "meta_y": top + head_h + 36 + body + 40,
            "row_y": top + head_h + 36 + body + 40 + meta_h}


def _static(L: Dict[str, Any], handle: str) -> Image.Image:
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)
    # the screen's own top bar
    by = max(SAFE_TOP - 40, L["top"] - 150)
    d.line([(X0 + 2, by), (X0 + 26, by - 22)], fill=TEXT, width=5)
    d.line([(X0 + 2, by), (X0 + 26, by + 22)], fill=TEXT, width=5)
    d.line([(X0 + 2, by), (X0 + 46, by)], fill=TEXT, width=5)
    d.text((X0 + 96, by), "Post", font=load_font(48, "Bold"), fill=TEXT, anchor="lm")
    top = L["top"]
    img.alpha_composite(brand_avatar(124), (X0, top))
    d.text((X0 + 148, top + 16), NAME, font=load_font(48, "Bold"), fill=TEXT)
    d.text((X0 + 148, top + 74), handle, font=load_font(42, "Regular"), fill=MUTED)
    for k in range(3):
        d.ellipse([W - X0 - 46 + k * 16, top + 40, W - X0 - 38 + k * 16, top + 48],
                  fill=MUTED)
    y = L["text_y"]
    for ln in L["sl"]:
        d.text((X0, y), ln, font=L["f"], fill=TEXT)
        y += L["lh"]
    return img


def _meta(d, L, views: float) -> None:
    y = L["meta_y"]
    mf = load_font(38, "Regular")
    left = "9:47 PM · 26 Nov 26 · "
    d.text((X0, y), left, font=mf, fill=MUTED)
    x = X0 + tw(left, mf)
    vf = load_font(38, "Bold")
    v = _k(views)
    d.text((x, y), v, font=vf, fill=TEXT)
    d.text((x + tw(v, vf) + 10, y), "Views", font=mf, fill=MUTED)
    d.line([(X0, y + 74), (W - X0, y + 74)], fill=RULE, width=2)


def _row(img, L, n: Dict[str, int], liked: float, tap: float) -> None:
    d = ImageDraw.Draw(img)
    y = L["row_y"] + 30
    cf = load_font(34, "Regular")
    slots = [X0, X0 + 210, X0 + 430, X0 + 650, W - X0 - 46]
    _reply(d, slots[0], y, MUTED)
    d.text((slots[0] + 62, y + 22), _k(n["replies"]), font=cf, fill=MUTED, anchor="lm")
    _repost(d, slots[1], y, MUTED)
    d.text((slots[1] + 66, y + 22), _k(n["reposts"]), font=cf, fill=MUTED, anchor="lm")
    # the like: outline until tapped, then a pink pop with a ring
    hx, hy = slots[2] + 23, y + 24
    if liked > 0:
        s = 30 * (0.6 + 0.4 * back(liked)) if liked < 1 else 30
        if 0 < tap < 1:
            r = 20 + 46 * ease(tap)
            ring = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(ring).ellipse([hx - r, hy - r, hx + r, hy + r],
                                         outline=PINK + "%02x" % int(255 * (1 - tap)),
                                         width=5)
            img.alpha_composite(ring)
        heart(d, hx, hy, s, fill=PINK)
        d.text((slots[2] + 62, y + 22), _k(n["likes"] + 1), font=cf, fill=PINK,
               anchor="lm")
    else:
        heart(d, hx, hy, 30, outline=MUTED, width=4)
        d.text((slots[2] + 62, y + 22), _k(n["likes"]), font=cf, fill=MUTED,
               anchor="lm")
    _bookmark(d, slots[3], y, MUTED)
    d.text((slots[3] + 58, y + 22), _k(n["bookmarks"]), font=cf, fill=MUTED, anchor="lm")
    _share(d, slots[4], y, MUTED)
    d.line([(X0, L["row_y"] + 110), (W - X0, L["row_y"] + 110)], fill=RULE, width=2)


def _punch(L: Dict[str, Any]) -> Image.Image:
    lines = L["pl"]
    img = Image.new("RGBA", (TEXT_W, max(1, len(lines) * L["lh"] + 20)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        d.text((0, i * L["lh"]), ln, font=L["f"], fill=TEXT)
    return img


# ---------------------------------------------------------------- build
def build(post: Dict[str, Any], handle: str, fps: int
          ) -> Tuple[List[Image.Image], Dict[str, Any], Image.Image]:
    setup, punch = _parts(post)
    if not setup:
        raise ValueError("%s: tweet needs tweet.setup" % post.get("id"))
    L = _layout(setup, punch)
    n = _numbers(post.get("id", ""))
    base = _static(L, handle)
    psprite = _punch(L) if punch else None
    punch_y = L["text_y"] + len(L["sl"]) * L["lh"] + L["gap"]

    t_punch = min(2.4, max(0.9, read_secs(setup, 20.0))) if punch else 0.0
    t_like = t_punch + (read_secs(punch, 16.0) if punch else 1.2)
    duration = min(9.5, max(5.5, t_like + 1.6))
    loop = 0.35

    def draw(t: float, like: bool = True) -> Image.Image:
        img = base.copy()
        if psprite is not None and t >= t_punch:
            k = ease((t - t_punch) / 0.3)
            spr = psprite if k >= 0.999 else psprite.copy()
            if k < 0.999:
                spr.putalpha(psprite.split()[3].point(lambda v: int(v * k)))
            img.alpha_composite(spr, (X0, int(punch_y + (1 - k) * 26)))
        d = ImageDraw.Draw(img)
        _meta(d, L, n["views"] * (0.97 + 0.03 * t / duration))
        liked = min(1.0, (t - t_like) / 0.25) if (like and t >= t_like) else 0.0
        tap = (t - t_like) / 0.45 if (like and t >= t_like) else 0.0
        _row(img, L, n, liked, tap)
        return img

    frames = loop_frames(draw, duration, fps, loop)
    pops = ([(t_punch, 1318.5)] if punch else []) + [(t_like, 1760.0)]
    sc = score([b for b in (t_punch, t_like) if b], duration, loop, pops)
    cover = draw(t_like - 0.05, like=False).convert("RGB")
    return frames, sc, cover
