"""Notify: the story told as lock-screen notifications, arriving one at a time.

Fields (`notify`):
  time    optional clock on the lock screen, e.g. "7:42" (default "9:41")
  date    optional date line, e.g. "Monday, 1 December" (default: none drawn)
  items   3-6 of {app, title, text, time?}. Oldest first; the LAST one is the
          punchline. `app` is one of APPS below and picks the icon. `title` is
          the sender or headline ("Mummy", "Salary", "HR - Priya"); `time` is
          the small stamp on the card ("1 Dec", "9:02 AM"), default "now" for
          the newest and "Nm ago" for the rest.

Drawn as the lock screen everyone has: big clock, frosted cards, a coloured
icon per app. No app names or logos — the icons are generic glyphs drawn here.
Each notification drops in at the top of the stack and pushes the older ones
down, the way the real thing does, so when the story ends the punchline is the
card at the top, the first thing the eye lands on.
"""
import hashlib
import math
import re
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFilter

from formats.common import (H, SAFE_BOTTOM, SAFE_TOP, W, back, clean, ease,
                            load_font, paste_scaled, read_secs, score,
                            signed_off, tw, wrap)

FIELDS = {"notify": {"time": "str?", "date": "str?",
                     "items": "list[{app, title, text, time?}] 3-6 items"}}

# app -> (icon colour, glyph)
APPS = {
    "bank":     ("#1F8A4C", "rupee"),
    "upi":      ("#5B3FD1", "arrow"),
    "messages": ("#2FB344", "bubble"),
    "family":   ("#2FB344", "bubble"),
    "mail":     ("#2B7BE4", "envelope"),
    "work":     ("#4A4FB5", "briefcase"),
    "calendar": ("#E5483B", "calendar"),
    "reminder": ("#F08A24", "bell"),
    "news":     ("#D63A3A", "paper"),
    "delivery": ("#E8742C", "box"),
    "food":     ("#E0453A", "bowl"),
    "health":   ("#E2466D", "heart"),
    "landlord": ("#7A5A3A", "house"),
    "system":   ("#6B7280", "gear"),
}
DEFAULT_APP = "messages"

MARGIN = 44
CARD_W = W - 2 * MARGIN
ICON = 76
PAD = 26
TITLE_F, TEXT_F, TIME_F = 44, 44, 31
LH = int(TEXT_F * 1.3)
GAP = 16
STACK_TOP = 640                      # below the clock
STACK_FLOOR = H - SAFE_BOTTOM - 20
MAX_CONTENT_S = 16.0


# ------------------------------------------------------------------ fields
def _parts(post: Dict[str, Any]) -> Dict[str, Any]:
    n = post.get("notify") or {}
    items = []
    for it in n.get("items", []):
        app = (it.get("app") or DEFAULT_APP).strip().lower()
        items.append({"app": app if app in APPS else DEFAULT_APP,
                      "title": clean(it.get("title", ""))[:40],
                      "text": clean(it.get("text", "")),
                      "time": clean(it.get("time", ""))[:14]})
    clock = re.sub(r"^0(\d:)", r"\1", clean(n.get("time", "")))
    clock = re.sub(r"\s*(AM|PM|am|pm)$", "", clock)     # the lock screen has neither
    return {"time": clock or "9:41",
            "date": clean(n.get("date", "")), "items": items}


# --------------------------------------------------------------- the icons
def _glyph(d: ImageDraw.ImageDraw, kind: str, s: int) -> None:
    """White glyph on an s x s icon, drawn at 3x and scaled down."""
    c, w = "#FFFFFF", max(2, s // 16)
    m = s * 0.26
    if kind == "rupee":
        d.text((s / 2, s / 2 + s * 0.02), "₹", font=load_font(int(s * 0.62), "Bold"),
               fill=c, anchor="mm")
    elif kind == "arrow":
        d.line([(m, s / 2), (s - m, s / 2)], fill=c, width=w)
        d.polygon([(s - m + 2, s / 2), (s - m - s * 0.16, s / 2 - s * 0.14),
                   (s - m - s * 0.16, s / 2 + s * 0.14)], fill=c)
    elif kind == "bubble":
        d.ellipse([m - 4, m, s - m + 4, s - m - 2], fill=c)
        d.polygon([(m + s * 0.08, s - m - s * 0.12), (m - s * 0.04, s - m + s * 0.08),
                   (m + s * 0.24, s - m - s * 0.06)], fill=c)
    elif kind == "envelope":
        d.rounded_rectangle([m - 4, m + 6, s - m + 4, s - m - 4], s // 20, outline=c,
                            width=w)
        d.line([(m - 2, m + 10), (s / 2, s / 2 + 2), (s - m + 2, m + 10)], fill=c,
               width=w)
    elif kind == "briefcase":
        d.rounded_rectangle([m - 4, m + s * 0.12, s - m + 4, s - m], s // 22, fill=c)
        d.rounded_rectangle([s * 0.38, m - 2, s * 0.62, m + s * 0.14], s // 30,
                            outline=c, width=w)
    elif kind == "calendar":
        d.rounded_rectangle([m - 4, m, s - m + 4, s - m], s // 20, fill=c)
        d.rectangle([m - 4, m + s * 0.14, s - m + 4, m + s * 0.2], fill="#E5483B")
        d.text((s / 2, s * 0.6), "1", font=load_font(int(s * 0.34), "Bold"),
               fill="#E5483B", anchor="mm")
    elif kind == "bell":
        d.pieslice([m, m - 2, s - m, s - m + s * 0.1], 180, 360, fill=c)
        d.rectangle([m, s / 2 - 2, s - m, s - m - 4], fill=c)
        d.rectangle([m - 6, s - m - 6, s - m + 6, s - m + 2], fill=c)
        d.ellipse([s / 2 - s * 0.07, s - m + 2, s / 2 + s * 0.07, s - m + s * 0.14],
                  fill=c)
    elif kind == "paper":
        d.rounded_rectangle([m - 2, m, s - m + 2, s - m], s // 24, fill=c)
        for k in range(3):
            y = m + s * 0.12 + k * s * 0.12
            d.line([(m + s * 0.08, y), (s - m - s * 0.08, y)], fill="#D63A3A",
                   width=max(2, w - 1))
    elif kind == "box":
        d.polygon([(s / 2, m - 2), (s - m + 4, m + s * 0.12), (s - m + 4, s - m - s * 0.08),
                   (s / 2, s - m + 4), (m - 4, s - m - s * 0.08), (m - 4, m + s * 0.12)],
                  fill=c)
        d.line([(m - 4, m + s * 0.12), (s / 2, m + s * 0.24), (s - m + 4, m + s * 0.12)],
               fill="#E8742C", width=w)
        d.line([(s / 2, m + s * 0.24), (s / 2, s - m + 4)], fill="#E8742C", width=w)
    elif kind == "bowl":
        d.pieslice([m - 6, m, s - m + 6, s - m + s * 0.16], 0, 180, fill=c)
        for k in range(3):
            x = s * 0.36 + k * s * 0.14
            d.arc([x - 6, m - 4, x + 6, m + s * 0.2], 100, 260, fill=c, width=w)
    elif kind == "heart":
        from formats.common import heart
        heart(d, s / 2, s / 2 + 2, s * 0.5, fill=c)
    elif kind == "house":
        d.polygon([(s / 2, m - 4), (s - m + 6, s / 2), (m - 6, s / 2)], fill=c)
        d.rectangle([m + 4, s / 2 - 2, s - m - 4, s - m], fill=c)
        d.rectangle([s / 2 - s * 0.07, s - m - s * 0.2, s / 2 + s * 0.07, s - m],
                    fill="#7A5A3A")
    else:  # gear
        r1, r2 = s * 0.3, s * 0.2
        pts = []
        for k in range(16):
            a = math.pi * 2 * k / 16
            r = r1 if k % 2 == 0 else r1 * 0.78
            pts.append((s / 2 + r * math.cos(a), s / 2 + r * math.sin(a)))
        d.polygon(pts, fill=c)
        d.ellipse([s / 2 - r2 / 2, s / 2 - r2 / 2, s / 2 + r2 / 2, s / 2 + r2 / 2],
                  fill="#6B7280")


def _icon(app: str) -> Image.Image:
    colour, kind = APPS.get(app, APPS[DEFAULT_APP])
    big = ICON * 3
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, big - 1, big - 1], int(big * 0.23), fill=colour)
    _glyph(d, kind, big)
    return img.resize((ICON, ICON), Image.LANCZOS)


# ----------------------------------------------------------------- the cards
def _card(it: Dict[str, Any], stamp: str, top: bool) -> Image.Image:
    tf, xf, mf = (load_font(TITLE_F, "Bold"), load_font(TEXT_F, "Regular"),
                  load_font(TIME_F, "Regular"))
    text_x = PAD + ICON + 24
    text_w = CARD_W - text_x - PAD
    lines = wrap(it["text"], xf, text_w)[:4]
    h = PAD + int(TITLE_F * 1.25) + len(lines) * LH + PAD - 4
    h = max(h, ICON + 2 * PAD)
    img = Image.new("RGBA", (CARD_W, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # frosted: light and a little see-through, the real thing's look
    d.rounded_rectangle([0, 0, CARD_W - 1, h - 1], 40,
                        fill=(246, 244, 240, 236 if top else 214))
    img.alpha_composite(_icon(it["app"]), (PAD, PAD + 2))
    sw = tw(stamp, mf)
    title = it["title"] or it["app"].capitalize()
    while tw(title, tf) > text_w - sw - 20 and len(title) > 4:
        title = title[:-2].rstrip() + "…" if not title.endswith("…") else title[:-2] + "…"
    d.text((text_x, PAD - 2), title, font=tf, fill="#121212")
    d.text((CARD_W - PAD, PAD + 4), stamp, font=mf, fill="#7A7570", anchor="ra")
    y = PAD + int(TITLE_F * 1.25)
    for ln in lines:
        d.text((text_x, y), ln, font=xf, fill="#232323")
        y += LH
    return img


def _wallpaper(seed: str) -> Image.Image:
    """Dusk gradient with soft light blobs, a little larger than the frame so
    it can drift — the lock screen is alive from frame 0 without a clock tick."""
    h = int(hashlib.sha1(seed.encode()).hexdigest(), 16)
    ww, hh = W + 120, H + 120
    top, bot = (28, 32, 64), (196, 98, 70)
    img = Image.new("RGB", (ww, hh))
    d = ImageDraw.Draw(img)
    for y in range(hh):
        k = y / float(hh - 1)
        k = k * k
        d.line([(0, y), (ww, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(top, bot)))
    blobs = Image.new("RGBA", (ww, hh), (0, 0, 0, 0))
    bd = ImageDraw.Draw(blobs)
    for i, col in enumerate([(255, 170, 110, 90), (120, 110, 220, 80), (255, 120, 140, 70)]):
        cx = 150 + (h >> (i * 7)) % (ww - 300)
        cy = 300 + (h >> (i * 5 + 3)) % (hh - 600)
        r = 260 + (h >> (i * 3)) % 180
        bd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    img = Image.alpha_composite(img.convert("RGBA"), blobs.filter(ImageFilter.GaussianBlur(120)))
    return img


def _clock(time: str, date: str) -> Image.Image:
    img = Image.new("RGBA", (W, 460), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # the lock glyph
    lx, ly = W / 2, 22
    d.rounded_rectangle([lx - 18, ly + 14, lx + 18, ly + 44], 6, fill="#FFFFFFE6")
    d.arc([lx - 12, ly - 2, lx + 12, ly + 26], 180, 360, fill="#FFFFFFE6", width=5)
    d.line([(lx - 12, ly + 12), (lx - 12, ly + 16)], fill="#FFFFFFE6", width=5)
    d.line([(lx + 12, ly + 12), (lx + 12, ly + 16)], fill="#FFFFFFE6", width=5)
    y = 92
    if date:
        d.text((W / 2, y), date, font=load_font(44, "SemiBold"), fill="#FFFFFFE6",
               anchor="mt")
    d.text((W / 2, y + 48), time, font=load_font(230, "SemiBold"), fill="#FFFFFFF2",
           anchor="mt")
    return img


# ---------------------------------------------------------------- build
def build(post: Dict[str, Any], handle: str, fps: int
          ) -> Tuple[List[Image.Image], Dict[str, Any], Image.Image]:
    n = _parts(post)
    items = n["items"]
    if len(items) < 2:
        raise ValueError("%s: notify needs at least two items" % post.get("id"))
    k = len(items)

    def stamp(i: int, newest: int) -> str:
        if items[i]["time"]:
            return items[i]["time"]
        return "now" if i == newest else "%dm ago" % (2 + 3 * (newest - i))

    # one sprite per (item, its stamp at each stage) — stamps age as new ones land
    cards: Dict[Tuple[int, int], Image.Image] = {}

    def card(i: int, newest: int) -> Image.Image:
        key = (i, newest)
        if key not in cards:
            cards[key] = _card(items[i], stamp(i, newest), top=(i == newest))
        return cards[key]

    # timing: each one is read before the next arrives; the last is held
    def timeline(speed: float):
        ts, t = [], 0.2
        for i, it in enumerate(items):
            if i:
                t += min(2.8, max(1.1, read_secs(items[i - 1]["title"] + " " +
                                                 items[i - 1]["text"], 17.0 * speed)))
            ts.append(t)
        return ts, t + max(2.6, read_secs(items[-1]["text"], 12.0))
    for speed in (1.0, 1.15, 1.3, 1.45):
        arrive, content = timeline(speed)
        if content <= MAX_CONTENT_S:
            break
    loop = 0.4
    SLIDE = 0.38

    wall = _wallpaper(post.get("id") or "x")
    clock = _clock(n["time"], n["date"])
    hf = load_font(28, "SemiBold")

    def draw(t: float) -> Image.Image:
        dx = 60 + 26 * math.sin(t * 0.5)
        dy = 60 + 18 * math.cos(t * 0.4)
        frame = wall.crop((int(dx), int(dy), int(dx) + W, int(dy) + H))
        frame.alpha_composite(clock, (0, SAFE_TOP + 20))
        landed = [i for i in range(k) if arrive[i] <= t]
        if landed:
            newest = landed[-1]
            age = t - arrive[newest]
            p = ease(age / SLIDE)
            # newest on top; the rest pushed down by its height as it arrives
            y = STACK_TOP
            new_h = card(newest, newest).height + GAP
            for j, i in enumerate(reversed(landed)):
                spr = card(i, newest)
                if j == 0:
                    yy = STACK_TOP - (1 - back(min(1.0, age / SLIDE))) * 70
                    a = min(1.0, age / 0.16)
                    paste_scaled(frame, spr, W / 2, yy + spr.height / 2,
                                 0.94 + 0.06 * back(min(1.0, age / SLIDE)), a)
                    y = STACK_TOP + new_h
                    continue
                yy = y - (1 - p) * new_h
                if yy + spr.height > STACK_FLOOR:
                    # what no longer fits collapses into a stack edge, the way
                    # older notifications fold away — never a card cut mid-line
                    peek = ImageDraw.Draw(frame)
                    for e in range(min(2, len(landed) - j)):
                        inset = 24 + 22 * e
                        top_ = yy + 4 + 18 * e - (1 - p) * 0
                        if top_ + 18 > STACK_FLOOR + 30:
                            break
                        peek.rounded_rectangle(
                            [MARGIN + inset, top_, W - MARGIN - inset, top_ + 22], 11,
                            fill=(246, 244, 240, 150 - 50 * e))
                    break
                frame.alpha_composite(spr, (MARGIN, int(yy)))
                y += spr.height + GAP
        d = ImageDraw.Draw(frame)
        d.text((W - 30, H - SAFE_BOTTOM + 2), handle, font=hf, fill="#FFFFFFAA",
               anchor="ra")
        return frame

    frames, duration, outro_at = signed_off(draw, content, fps, loop)
    pops = [(a, 1318.5 if i == k - 1 else 987.8) for i, a in enumerate(arrive)]
    sc = score(arrive[1:], duration, loop, pops, outro_at)
    # cover: everything but the punchline, which is what makes someone tap
    cover = draw(arrive[-1] - 0.05).convert("RGB")
    return frames, sc, cover
