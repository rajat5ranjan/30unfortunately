"""Comments: a short post at the top, then the comment section, one at a time.

Fields (`comments`):
  source  the invented page that posted it: "Office Updates", "Bengaluru Rent Watch"
  post    the post's text, <= 140 chars, on screen from frame 0 — the hook
  items   3-6 of {user, text, reply?}. `user` is an invented lowercase handle
          ("priya.k_94"); `reply: true` indents it under the comment before.
          The LAST one is the punchline: it gets the most likes and the like
          is tapped on it once it has been read.

The post-detail view everyone has read a thousand comment sections in: the
post as the first entry, a rule, then comments with avatars, handles, "Reply"
and a heart with a count. No app name or logo, and every handle is invented.
The list scrolls when it outgrows the screen, so the newest comment is always
in view and the last thing on screen is the punchline with its heart going red.
"""
import hashlib
import re
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw

from formats.common import (H, SAFE_BOTTOM, SAFE_TOP, W, back, clean, ease, heart,
                            initials_avatar, load_font, paste_scaled, read_secs,
                            score, signed_off, tw, wrap)

FIELDS = {"comments": {"source": "str", "post": "str <= 140",
                       "items": "list[{user, text, reply?}] 3-6 items"}}

BG, TEXT, MUTED, RULE = "#101010", "#F2F2F2", "#8E8E8E", "#262626"
RED = "#FF3040"
AVATAR_BG = ["#C2185B", "#1E88E5", "#43A047", "#F4511E", "#8E24AA", "#00897B",
             "#6D4C41", "#3949AB"]
X0 = 40
AV, AV_REPLY = 88, 64
INDENT = 112
POST_F, USER_F, TEXT_F, META_F = 52, 36, 44, 32
LH = int(TEXT_F * 1.3)
HEAD_Y = SAFE_TOP + 6
LIST_TOP = HEAD_Y + 110
BAR_H = 120
BAR_Y = H - SAFE_BOTTOM - BAR_H
FLOOR = BAR_Y - 20
MAX_CONTENT_S = 17.0


def _handle(u: str) -> str:
    u = re.sub(r"[^a-z0-9._]", "", (u or "").lower().replace(" ", "_"))
    return (u or "someone")[:24]


def _parts(post: Dict[str, Any]) -> Dict[str, Any]:
    c = post.get("comments") or {}
    items = [{"user": _handle(it.get("user", "")), "text": clean(it.get("text", "")),
              "reply": bool(it.get("reply"))} for it in c.get("items", [])]
    if items:
        items[0]["reply"] = False          # nothing to reply to
    return {"source": clean(c.get("source", "")) or "Daily Updates",
            "post": clean(c.get("post", "")), "items": items}


def _k(n: int) -> str:
    if n >= 10000:
        return "%.1fK" % (n / 1000.0)
    if n >= 1000:
        return "{:,}".format(n)
    return str(n)


def _likes(pid: str, items: List[Dict[str, Any]]) -> List[int]:
    """Plausible, stable across re-renders, and the punchline always wins."""
    h = int(hashlib.sha1((pid or "x").encode()).hexdigest(), 16)
    out = []
    for i, it in enumerate(items):
        base = (40 + (h >> (i * 6)) % 1900) if not it["reply"] else (5 + (h >> (i * 5)) % 300)
        out.append(base)
    out[-1] = max(out) * 6 + 2000 + (h >> 40) % 9000
    return out


def _colour(user: str) -> str:
    return AVATAR_BG[int(hashlib.md5(user.encode()).hexdigest(), 16) % len(AVATAR_BG)]


# ----------------------------------------------------------------- sprites
def _post_block(source: str, text: str) -> Image.Image:
    pf = load_font(POST_F, "Regular")
    x = X0 + AV + 26
    lines = wrap(text, pf, W - x - X0)
    h = 70 + len(lines) * int(POST_F * 1.28) + 70
    img = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    img.alpha_composite(initials_avatar(source, AV, "#E8E2D4", "#15140F"), (X0, 4))
    uf = load_font(USER_F + 2, "Bold")
    d.text((x, 6), source, font=uf, fill=TEXT)
    d.text((x + tw(source, uf) + 16, 10), "2h", font=load_font(META_F, "Regular"),
           fill=MUTED)
    y = 62
    for ln in lines:
        d.text((x, y), ln, font=pf, fill=TEXT)
        y += int(POST_F * 1.28)
    d.line([(0, h - 22), (W, h - 22)], fill=RULE, width=2)
    return img


def _comment(it: Dict[str, Any], stamp: str) -> Tuple[Image.Image, Tuple[int, int]]:
    """The row without its heart; returns the sprite and the heart's centre."""
    reply = it["reply"]
    av = AV_REPLY if reply else AV
    x0 = X0 + (INDENT if reply else 0)
    tx = x0 + av + 24
    text_w = W - tx - 130
    xf = load_font(TEXT_F, "Regular")
    lines = wrap(it["text"], xf, text_w)
    h = 46 + len(lines) * LH + 54 + 22
    img = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    img.alpha_composite(initials_avatar(it["user"].replace(".", " ").replace("_", " "),
                                        av, _colour(it["user"]), "#FFFFFF"), (x0, 2))
    uf = load_font(USER_F, "SemiBold")
    d.text((tx, 0), it["user"], font=uf, fill=TEXT)
    d.text((tx + tw(it["user"], uf) + 14, 3), stamp, font=load_font(META_F, "Regular"),
           fill=MUTED)
    y = 46
    for ln in lines:
        d.text((tx, y), ln, font=xf, fill=TEXT)
        y += LH
    d.text((tx, y + 10), "Reply", font=load_font(META_F, "SemiBold"), fill=MUTED)
    return img, (W - X0 - 30, 40)


def _bar(handle: str) -> Image.Image:
    img = Image.new("RGBA", (W, BAR_H), BG)
    d = ImageDraw.Draw(img)
    d.line([(0, 0), (W, 0)], fill=RULE, width=2)
    img.alpha_composite(initials_avatar("3 0", 70, "#D8451F", "#FFFFFF"), (X0, 25))
    d.rounded_rectangle([X0 + 92, 22, W - X0, 98], 38, outline="#3A3A3A", width=2)
    d.text((X0 + 124, 60), "Add a comment...", font=load_font(38, "Regular"),
           fill=MUTED, anchor="lm")
    return img


def _header() -> Image.Image:
    img = Image.new("RGBA", (W, 100), BG)
    d = ImageDraw.Draw(img)
    y = 50
    d.line([(X0 + 2, y), (X0 + 26, y - 22)], fill=TEXT, width=5)
    d.line([(X0 + 2, y), (X0 + 26, y + 22)], fill=TEXT, width=5)
    d.line([(X0 + 2, y), (X0 + 46, y)], fill=TEXT, width=5)
    d.text((W / 2, y), "Comments", font=load_font(44, "Bold"), fill=TEXT, anchor="mm")
    d.line([(0, 98), (W, 98)], fill=RULE, width=2)
    return img


# ----------------------------------------------------------------- build
def build(post: Dict[str, Any], handle: str, fps: int
          ) -> Tuple[List[Image.Image], Dict[str, Any], Image.Image]:
    c = _parts(post)
    items = c["items"]
    if not c["post"] or len(items) < 2:
        raise ValueError("%s: comments needs a post and two comments" % post.get("id"))
    k = len(items)
    likes = _likes(post.get("id", ""), items)
    stamps = ["%dh" % max(1, 2 - i // 3) if i < k - 1 else "1h" for i in range(k)]
    rows = [_comment(it, st) for it, st in zip(items, stamps)]
    head = _post_block(c["source"], c["post"])
    bar, top = _bar(handle), _header()

    # layout in list coordinates
    ys, y = [], LIST_TOP + head.height + 10
    for spr, _ in rows:
        ys.append(y)
        y += spr.height + 8

    def need(i: int) -> float:
        return max(0.0, ys[i] + rows[i][0].height - FLOOR)

    def timeline(speed: float):
        ts, t = [], min(2.0, max(1.2, read_secs(c["post"], 22.0 * speed)))
        for i in range(k):
            if i:
                t += min(2.8, max(1.0, read_secs(items[i - 1]["text"], 18.0 * speed)))
            ts.append(t)
        like = ts[-1] + min(1.6, max(0.9, read_secs(items[-1]["text"], 30.0)))
        return ts, like, like + 1.8
    for speed in (1.0, 1.15, 1.3, 1.45):
        arrive, t_like, content = timeline(speed)
        if content <= MAX_CONTENT_S:
            break
    loop = 0.4
    cf = load_font(32, "SemiBold")

    def draw(t: float, like: bool = True) -> Image.Image:
        frame = Image.new("RGBA", (W, H), BG)
        landed = [i for i in range(k) if arrive[i] <= t]
        # scroll toward keeping the newest comment in view
        if landed:
            n = landed[-1]
            prev = need(n - 1) if n else 0.0
            off = prev + (need(n) - prev) * ease((t - arrive[n]) / 0.3)
        else:
            off = 0.0
        frame.alpha_composite(head, (0, int(LIST_TOP - off)))
        d = ImageDraw.Draw(frame)
        for i in landed:
            spr, (hx, hy) = rows[i]
            yy = ys[i] - off
            if yy > FLOOR or yy + spr.height < LIST_TOP - 120:
                continue
            age = t - arrive[i]
            if age < 0.3:
                paste_scaled(frame, spr, W / 2, yy + spr.height / 2 + (1 - back(age / 0.3)) * 40,
                             1.0, min(1.0, age / 0.15))
            else:
                frame.alpha_composite(spr, (0, int(yy)))
            # the heart and its count, live: the punchline's gets tapped
            liked = i == k - 1 and like and t >= t_like
            n_likes = likes[i] + (1 if liked else 0)
            cy = yy + hy
            if liked:
                p = min(1.0, (t - t_like) / 0.25)
                tap = (t - t_like) / 0.45
                if tap < 1:
                    r = 24 + 48 * ease(tap)
                    ring = Image.new("RGBA", frame.size, (0, 0, 0, 0))
                    ImageDraw.Draw(ring).ellipse([hx - r, cy - r, hx + r, cy + r],
                                                 outline=RED + "%02x" % int(255 * (1 - tap)),
                                                 width=5)
                    frame.alpha_composite(ring)
                    d = ImageDraw.Draw(frame)
                heart(d, hx, cy, 40 * (0.6 + 0.4 * back(p)) if p < 1 else 40, fill=RED)
                d.text((hx, cy + 30), _k(n_likes), font=cf, fill=RED, anchor="mt")
            else:
                heart(d, hx, cy, 40, outline=MUTED, width=4)
                d.text((hx, cy + 30), _k(n_likes), font=cf, fill=MUTED, anchor="mt")
        frame.alpha_composite(top, (0, HEAD_Y))
        frame.alpha_composite(bar, (0, BAR_Y))
        # cover the gap between the list and Instagram's top chrome
        ImageDraw.Draw(frame).rectangle([0, 0, W, HEAD_Y], fill=BG)
        return frame

    frames, duration, outro_at = signed_off(draw, content, fps, loop)
    pops = [(a, 1318.5 if i == k - 1 else 880.0) for i, a in enumerate(arrive)]
    pops.append((t_like, 1760.0))
    sc = score(arrive + [t_like], duration, loop, pops, outro_at)
    cover = draw(arrive[-1] - 0.05, like=False).convert("RGB")
    return frames, sc, cover
