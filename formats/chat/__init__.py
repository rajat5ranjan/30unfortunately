"""Chat: a phone chat thread, messages arriving one by one. Family group, landlord, HR.

Fields (`chat`):
  title      group or contact name, e.g. "Sharma Parivaar", "Landlord Ji"
  group      optional bool; default true when more than one person besides you
  messages   list of {from, text, kind?}. `from` is a name, or "You" for the
             viewer. kind: "text" (default) | "deleted" | "voice" | "forwarded".
             "voice" uses `text` as its duration, e.g. "1:47".

Looks like the chat app everyone has — wallpaper, tails, ticks, "is typing…",
the input bar — without any app's name or logo, and every name is invented.
Nothing is on screen at frame 0 but the open chat and someone already typing:
the question the eye asks is who, and the first message answers it inside half
a second. "You" messages are typed into the input bar before they are sent.
"""
import math
import random
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from formats.common import (H, SAFE_BOTTOM, W, back, clean, ease, initials_avatar,
                            load_font, paste_scaled, read_secs,
                            score, signed_off, tw, wrap)

FIELDS = {"chat": {"title": "str", "group": "bool?",
                   "messages": "list[{from, text, kind?}] 3-7 items"}}

# palette: the familiar light theme, nudged so it copies nobody exactly
HEAD = "#0F6B5B"
HEAD_TEXT, HEAD_SUB, HEAD_TYPING = "#FFFFFF", "#CFE7E1", "#FFFFFF"
WALL, DOODLE = "#EDE5DB", "#DDD3C5"
IN_BG, OUT_BG = "#FFFFFF", "#D8F8CF"
TEXT, META, TICK = "#111B21", "#667781", "#3FA9E0"
SEND = "#13A884"
NAME_COLOURS = ["#D2492A", "#1C7FA6", "#9B3DB8", "#B8860B", "#2E8B57", "#C2185B",
                "#5C6BC0"]
ME = {"you", "me", "main", "mai", "self"}

FS = 50                     # message text
LH = int(FS * 1.27)
MAX_W = 760
GAP_SAME, GAP_NEW = 8, 22
STATUS_H, HEAD_H = 92, 196  # the header sits under the status bar
THREAD_TOP = STATUS_H + HEAD_H + 34
BAR_H = 112
BAR_Y = H - SAFE_BOTTOM - BAR_H - 6
TYPE_CPS = 26.0
MAX_CONTENT_S = 17.0   # beyond this the thread is paced faster, never cut


def _is_me(sender: str) -> bool:
    return sender.strip().lower() in ME


def _parts(post: Dict[str, Any]) -> Dict[str, Any]:
    c = post.get("chat") or {}
    msgs = []
    for m in c.get("messages", []):
        kind = (m.get("kind") or "text").lower()
        text = clean(m.get("text", ""))
        if kind == "deleted":
            text = text or "This message was deleted"
        if not text:
            continue
        msgs.append({"from": clean(m.get("from", "")) or "?", "text": text,
                     "kind": kind if kind in ("text", "deleted", "voice", "forwarded")
                     else "text"})
    others = []
    for m in msgs:
        if not _is_me(m["from"]) and m["from"] not in others:
            others.append(m["from"])
    group = bool(c.get("group", len(others) > 1))
    return {"title": clean(c.get("title", "")) or (others[0] if others else "Chat"),
            "group": group, "others": others, "messages": msgs}


# ------------------------------------------------------------------- pieces
def _wallpaper() -> Image.Image:
    """Faint doodles, seeded so every chat reel has the same wall."""
    img = Image.new("RGBA", (W, H), WALL)
    d = ImageDraw.Draw(img)
    rnd = random.Random(30)
    for gy in range(0, H, 120):
        for gx in range(0, W, 120):
            x, y = gx + rnd.randint(10, 80), gy + rnd.randint(10, 80)
            k = rnd.randint(0, 5)
            if k == 0:
                d.ellipse([x, y, x + 26, y + 26], outline=DOODLE, width=3)
            elif k == 1:
                d.line([x, y + 20, x + 12, y, x + 24, y + 20, x + 36, y], fill=DOODLE, width=3)
            elif k == 2:
                d.rounded_rectangle([x, y, x + 30, y + 22], 6, outline=DOODLE, width=3)
            elif k == 3:
                d.arc([x, y, x + 34, y + 34], 200, 340, fill=DOODLE, width=3)
            elif k == 4:   # a tiny battery at 30%, the account's own doodle
                d.rounded_rectangle([x, y, x + 18, y + 30], 4, outline=DOODLE, width=3)
                d.rectangle([x + 5, y + 20, x + 13, y + 26], fill=DOODLE)
            else:
                for j in range(3):
                    d.ellipse([x + j * 12, y, x + j * 12 + 6, y + 6], fill=DOODLE)
    return img


def _icon_video(d, x, y, c):
    d.rounded_rectangle([x, y + 6, x + 34, y + 32], 6, fill=c)
    d.polygon([(x + 36, y + 19), (x + 50, y + 8), (x + 50, y + 30)], fill=c)


def _icon_phone(d, x, y, c):
    """A handset: two ear/mouth pads joined by a curved grip."""
    d.arc([x - 4, y - 4, x + 52, y + 52], 120, 240, fill=c, width=11)
    d.rounded_rectangle([x + 2, y - 2, x + 22, y + 14], 5, fill=c)
    d.rounded_rectangle([x + 2, y + 34, x + 22, y + 50], 5, fill=c)


def _header(title: str, sub: str, typing: bool, group: bool) -> Image.Image:
    img = Image.new("RGBA", (W, STATUS_H + HEAD_H), HEAD)
    d = ImageDraw.Draw(img)
    # status bar
    sf = load_font(34, "SemiBold")
    d.text((46, 30), "9:41", font=sf, fill=HEAD_TEXT)
    bx = W - 110
    d.rounded_rectangle([bx, 34, bx + 52, 58], 6, outline=HEAD_TEXT, width=3)
    d.rectangle([bx + 6, 40, bx + 6 + 12, 52], fill=HEAD_TEXT)   # ~30%, of course
    d.rectangle([bx + 54, 41, bx + 58, 51], fill=HEAD_TEXT)
    for k in range(4):
        h = 8 + k * 6
        d.rectangle([bx - 70 + k * 12, 58 - h, bx - 62 + k * 12, 58], fill=HEAD_TEXT)
    # back, avatar, title
    cy = STATUS_H + HEAD_H // 2
    d.line([(52, cy), (84, cy - 30)], fill=HEAD_TEXT, width=7)
    d.line([(52, cy), (84, cy + 30)], fill=HEAD_TEXT, width=7)
    av = initials_avatar(title, 104, "#CFC3B0" if group else "#B9C8D6", "#3B3A36")
    img.alpha_composite(av, (100, cy - 52))
    tf = load_font(48, "Bold")
    t = title
    while tw(t, tf) > W - 470 and len(t) > 3:
        t = t[:-2] + "…"
    d.text((226, cy - 52), t, font=tf, fill=HEAD_TEXT)
    sf2 = load_font(33, "Regular")
    s = sub
    while tw(s, sf2) > W - 470 and len(s) > 3:
        s = s[:-2].rstrip(", ") + "…"
    d.text((226, cy + 8), s, font=sf2, fill=HEAD_TYPING if typing else HEAD_SUB)
    _icon_video(d, W - 236, cy - 20, HEAD_TEXT)
    _icon_phone(d, W - 156, cy - 20, HEAD_TEXT)
    for k in range(3):
        d.ellipse([W - 62, cy - 22 + k * 18, W - 52, cy - 12 + k * 18], fill=HEAD_TEXT)
    return img


def _ticks(d, x, y, colour):
    for off in (0, 13):
        d.line([(x + off, y + 10), (x + off + 7, y + 17), (x + off + 20, y + 2)],
               fill=colour, width=4, joint="curve")


def _bubble(m: Dict[str, Any], name_colour: Optional[str], tail: bool,
            stamp: str) -> Image.Image:
    mine = _is_me(m["from"])
    kind = m["kind"]
    f = load_font(FS, "Regular")
    nf = load_font(36, "SemiBold")
    mf = load_font(27, "Regular")
    pad_x, pad_top = 30, 20

    meta_w = tw(stamp, mf) + (40 if mine else 0) + 18
    name_h = 50 if name_colour else 0
    fwd_h = 44 if kind == "forwarded" else 0

    if kind == "voice":
        body_w, body_h, lines = 560, 96, []
    else:
        ff = load_font(FS, "Light") if kind == "deleted" else f
        lines = wrap(m["text"], ff, MAX_W)
        body_w = max(tw(l, ff) for l in lines)
        if kind == "deleted":
            body_w += 56
        body_h = len(lines) * LH
    last_w = (tw(lines[-1], f) + (56 if kind == "deleted" else 0)) if lines else body_w
    tuck = last_w + meta_w <= MAX_W and kind != "voice"
    inner_w = max(body_w, (last_w + meta_w) if tuck else body_w,
                  tw(m["from"], nf) if name_colour else 0, 230 if fwd_h else 0)
    bw = int(inner_w + 2 * pad_x)
    bh = int(pad_top + name_h + fwd_h + body_h + (14 if tuck else 48) + 10)

    tail_w = 18 if tail else 0
    img = Image.new("RGBA", (bw + tail_w, bh + 3), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = OUT_BG if mine else IN_BG
    x0 = 0 if mine else tail_w
    # the hairline shadow every chat bubble has
    d.rounded_rectangle([x0, 3, x0 + bw - 1, bh + 2], 24, fill=(0, 0, 0, 22))
    d.rounded_rectangle([x0, 0, x0 + bw - 1, bh - 1], 24, fill=bg)
    if tail:
        if mine:
            d.polygon([(bw - 30, 0), (bw + tail_w, 0), (bw - 4, 30)], fill=bg)
        else:
            d.polygon([(30 + tail_w, 0), (0, 0), (tail_w + 4, 30)], fill=bg)

    y = pad_top
    lx = x0 + pad_x
    if name_colour:
        d.text((lx, y - 4), m["from"], font=nf, fill=name_colour)
        y += name_h
    if fwd_h:
        ix = lx
        d.line([(ix, y + 24), (ix + 18, y + 10), (ix + 18, y + 18), (ix + 30, y + 18)],
               fill=META, width=3)
        d.text((ix + 40, y + 2), "Forwarded", font=load_font(32, "Regular"), fill=META)
        y += fwd_h
    if kind == "voice":
        cy = y + 46
        d.polygon([(lx + 6, cy - 22), (lx + 6, cy + 22), (lx + 42, cy)], fill="#54656F")
        rnd = random.Random(m["text"])
        for k in range(30):
            hh = 8 + rnd.random() * 44 * (0.5 + 0.5 * math.sin(k * 0.6))
            bx = lx + 74 + k * 13
            d.rounded_rectangle([bx, cy - hh / 2, bx + 6, cy + hh / 2], 3,
                                fill="#8696A0")
        d.ellipse([lx + 64, cy - 10, lx + 84, cy + 10], fill=SEND)
        d.text((lx + 74, cy + 34), m["text"], font=mf, fill=META)
    elif kind == "deleted":
        cy = y + LH // 2
        d.ellipse([lx, cy - 18, lx + 36, cy + 18], outline=META, width=4)
        d.line([(lx + 8, cy + 10), (lx + 28, cy - 10)], fill=META, width=4)
        d.text((lx + 56, y), lines[0], font=load_font(FS, "Light"), fill=META)
    else:
        for ln in lines:
            d.text((lx, y), ln, font=f, fill=TEXT)
            y += LH
    # meta: time, and ticks on yours
    my = bh - 40
    mx = x0 + bw - 22
    if mine:
        _ticks(d, mx - 36, my + 4, TICK)
        mx -= 46
    d.text((mx, my), stamp, font=mf, fill=META, anchor="ra")
    return img


def _dots(t: float) -> Image.Image:
    img = Image.new("RGBA", (170, 86), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([18, 0, 169, 85], 24, fill=IN_BG)
    d.polygon([(48, 0), (0, 0), (22, 30)], fill=IN_BG)
    for k in range(3):
        lift = max(0.0, math.sin((t * 2.4 - k * 0.2) * 2 * math.pi)) * 9
        x = 62 + k * 34
        d.ellipse([x - 10, 43 - 10 - lift, x + 10, 43 + 10 - lift],
                  fill="#8D9BA4" if lift > 3 else "#B5BFC6")
    return img


def _input_bar(typed: str, caret: bool) -> Image.Image:
    img = Image.new("RGBA", (W, BAR_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fx1 = W - 150
    d.rounded_rectangle([22, 6, fx1, BAR_H - 6], 50, fill="#FFFFFF")
    # smiley
    d.ellipse([52, 34, 96, 78], outline=META, width=4)
    d.ellipse([64, 46, 70, 52], fill=META)
    d.ellipse([78, 46, 84, 52], fill=META)
    d.arc([62, 50, 86, 70], 20, 160, fill=META, width=3)
    f = load_font(46, "Regular")
    tx = 122
    room = fx1 - tx - (150 if not typed else 40)
    if typed:
        shown = typed
        while tw(shown, f) > room and len(shown) > 1:
            shown = shown[1:]
        d.text((tx, BAR_H // 2), shown, font=f, fill=TEXT, anchor="lm")
        if caret:
            cx = tx + tw(shown, f) + 4
            d.line([(cx, 32), (cx, BAR_H - 32)], fill=SEND, width=4)
    else:
        d.text((tx, BAR_H // 2), "Message", font=f, fill="#8A979F", anchor="lm")
        # camera
        d.rounded_rectangle([fx1 - 82, 40, fx1 - 34, 76], 8, outline=META, width=4)
        d.ellipse([fx1 - 66, 50, fx1 - 50, 66], outline=META, width=3)
    # round button: mic, or send while typing
    bx0 = W - 128
    d.ellipse([bx0, 2, bx0 + BAR_H - 4, BAR_H - 2], fill=SEND)
    cx, cy = bx0 + (BAR_H - 4) / 2, BAR_H / 2
    if typed:
        d.polygon([(cx - 20, cy - 22), (cx + 24, cy), (cx - 20, cy + 22), (cx - 12, cy)],
                  fill="#FFFFFF")
    else:
        d.rounded_rectangle([cx - 10, cy - 26, cx + 10, cy + 6], 10, fill="#FFFFFF")
        d.arc([cx - 18, cy - 10, cx + 18, cy + 16], 0, 180, fill="#FFFFFF", width=4)
        d.line([(cx, cy + 16), (cx, cy + 26)], fill="#FFFFFF", width=4)
    return img


# ------------------------------------------------------------------- build
def build(post: Dict[str, Any], handle: str, fps: int
          ) -> Tuple[List[Image.Image], Dict[str, Any], Image.Image]:
    c = _parts(post)
    msgs = c["messages"]
    if len(msgs) < 2:
        raise ValueError("%s: chat needs at least two messages" % post.get("id"))
    colour = {s: NAME_COLOURS[i % len(NAME_COLOURS)] for i, s in enumerate(c["others"])}
    members = ", ".join(c["others"] + ["You"])
    idle_sub = members if c["group"] else "online"

    # sprites, once
    bubbles, prev = [], None
    for i, m in enumerate(msgs):
        first_of_run = m["from"] != prev
        name = colour.get(m["from"]) if (c["group"] and first_of_run and
                                         not _is_me(m["from"])) else None
        stamp = "9:%02d PM" % (41 + i // 2)
        bubbles.append(_bubble(m, name, first_of_run, stamp))
        prev = m["from"]

    # timing: typing (theirs: "is typing…"; yours: the input bar), then landing
    # Never truncated. The first chat that shipped hit a 14s cap while the
    # punchline was still being typed, and the reel looped before it was sent.
    # A long thread is read a little faster instead; the last message always
    # lands and is held long enough to read twice.
    def timeline(speed: float):
        ev, t = [], 0.0
        for i, m in enumerate(msgs):
            if i > 0:
                t += read_secs(msgs[i - 1]["text"] if msgs[i - 1]["kind"] != "voice"
                               else "a voice note", 22.0 * speed)
            if _is_me(m["from"]):
                dur = min(1.3, max(0.45, len(m["text"]) / (TYPE_CPS * speed)))
            else:
                dur = 0.45 if i == 0 else 0.5
            ev.append((t, t + dur))
            t += dur
        return ev, t + max(2.4, read_secs(msgs[-1]["text"], 13.0))
    for speed in (1.0, 1.15, 1.3, 1.45, 1.6):
        events, content = timeline(speed)
        if content <= MAX_CONTENT_S:
            break
    loop = 0.4

    # layout and scrolling
    ys, y, prev = [], THREAD_TOP + 70, None
    for m, b in zip(msgs, bubbles):
        y += (GAP_SAME if m["from"] == prev else GAP_NEW) if prev else 0
        ys.append(y)
        y += b.height
        prev = m["from"]
    floor = BAR_Y - 24

    def need(k: int, dots: bool) -> float:
        bottom = (ys[k] + 92) if dots else ys[k] + bubbles[k].height
        return max(0.0, bottom - floor)

    wall = _wallpaper()
    heads: Dict[Tuple[str, bool], Image.Image] = {}

    def head(sub: str, typing: bool) -> Image.Image:
        if (sub, typing) not in heads:
            heads[(sub, typing)] = _header(c["title"], sub, typing, c["group"])
        return heads[(sub, typing)]

    chip_f = load_font(28, "SemiBold")

    def draw(t: float) -> Image.Image:
        frame = wall.copy()
        d = ImageDraw.Draw(frame)
        landed = [i for i, (_, land) in enumerate(events) if land <= t]
        nxt = len(landed)
        pending = nxt < len(msgs) and events[nxt][0] <= t
        theirs_typing = pending and not _is_me(msgs[nxt]["from"])
        mine_typing = pending and _is_me(msgs[nxt]["from"])

        # scroll: ease toward whatever must be visible now
        tgt = need(nxt, True) if theirs_typing else (need(landed[-1], False)
                                                    if landed else 0.0)
        src = need(landed[-1], False) if landed else 0.0
        if theirs_typing:
            off = src + (tgt - src) * ease((t - events[nxt][0]) / 0.25)
        elif landed:
            prev_need = need(landed[-2], False) if len(landed) > 1 else 0.0
            off = prev_need + (src - prev_need) * ease((t - events[landed[-1]][1]) / 0.25)
        else:
            off = 0.0

        # date chip
        cy = THREAD_TOP + 26 - off
        if cy > STATUS_H + HEAD_H - 40:
            chip = "TODAY"
            cw = tw(chip, chip_f) + 44
            d.rounded_rectangle([(W - cw) / 2, cy - 24, (W + cw) / 2, cy + 24], 14,
                                fill="#FFFFFFE0")
            d.text((W / 2, cy), chip, font=chip_f, fill="#54656F", anchor="mm")

        for i in landed:
            b = bubbles[i]
            mine = _is_me(msgs[i]["from"])
            x = W - 24 - b.width if mine else 24
            y0 = ys[i] - off
            if y0 + b.height < STATUS_H + HEAD_H or y0 > BAR_Y:
                continue
            age = t - events[i][1]
            if age >= 0.24:
                frame.alpha_composite(b, (int(x), int(y0)))
            else:
                k = back(age / 0.24)
                paste_scaled(frame, b, x + (b.width if mine else 0) * 1.0 +
                             (-b.width / 2 if mine else b.width / 2), y0 + b.height / 2,
                             0.88 + 0.12 * k, min(1.0, age / 0.1))
        if theirs_typing:
            frame.alpha_composite(_dots(t), (24, int(ys[nxt] - off)))

        # input bar: yours are typed here first
        typed = ""
        if mine_typing:
            s, e = events[nxt]
            typed = msgs[nxt]["text"][:int(len(msgs[nxt]["text"]) *
                                           min(1.0, (t - s) / max(0.01, e - s - 0.12)))]
        frame.alpha_composite(_input_bar(typed, int(t * 3) % 2 == 0),
                              (0, BAR_Y))

        sub = ("%s is typing…" % msgs[nxt]["from"] if c["group"] else "typing…") \
            if theirs_typing else idle_sub
        frame.alpha_composite(head(sub, theirs_typing), (0, 0))
        d.text((W - 30, H - SAFE_BOTTOM + 2), handle, font=load_font(28, "SemiBold"),
               fill="#9C9384", anchor="ra")
        return frame

    frames, duration, outro_at = signed_off(draw, content, fps, loop)

    lands = [e[1] for e in events]
    pops = [(e[1], 1174.7 if _is_me(m["from"]) else 880.0)
            for e, m in zip(events, msgs)]
    sc = score(lands, duration, loop, pops, outro_at)
    # your messages click as they are typed into the input bar
    sc["typing"] = [(s, e - s - 0.12, len(m["text"]))
                    for (s, e), m in zip(events, msgs) if _is_me(m["from"])]

    # cover: everything but the last message, with its sender caught typing
    cover = draw(events[-1][0] + 0.2).convert("RGB")
    return frames, sc, cover
