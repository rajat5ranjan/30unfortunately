#!/usr/bin/env python3
"""
reel.py — turns an approved post into a 1080x1920 Reel.

    .venv/bin/python reel.py --id g004          one post
    .venv/bin/python reel.py --from content/approved/g004.json
    .venv/bin/python reel.py --id g004 --stills only the storyboard PNGs

Same words as the carousel, different surface. Reels are the only Instagram
format with a real non-follower feed, so this is the reach experiment; the
carousel is the control. See `publish.py ab`.

Everything visual is Pillow, drawn parametrically, exactly like render.py — the
figure's slump and the battery's drain are just numbers. The one thing Pillow
cannot do is H.264, so PyAV does the encode: a pip wheel with libav inside it,
no system binary and nothing to install on a runner beyond `pip install av`.

Design decisions that are load-bearing, not taste:

  * Frame 0 is the hook, whole. Nothing types on the first slide, because
    the first frame is the audition: Instagram decides inside a second or two
    and the cover has to work as a still. The hook is read, not watched.
  * Every slide after it types itself out with a caret. There the typing is
    the pacing — you read at the speed it arrives — and there is no
    audition left to lose.
  * The mark animation is the last three and a half seconds, not the first
    five. It scales out of the corner, the battery charges and drains, and the
    number it lands on IS the logo.
  * The draining battery is the progress bar. It starts the content full and
    empties as the reel runs out. One element, two jobs, in the brand's own
    metaphor.
  * The cover is rendered here, at 9:16. Handing Instagram the carousel's
    slide 1 does not work: that is 1080x1350 against a 1080x1920 cover frame,
    and the difference is made up by scaling to fill and cropping — the text
    comes out oversized and running off the frame. The cover also has to
    survive a second crop, because the profile grid keeps a 4:5 slice of the
    middle, so the hook is centred in the whole frame rather than in the band
    the video uses.
  * Short and loopable. Watch time now counts replays, so a short reel watched
    three times beats a long one watched once. The tail crossfades back into
    frame 0 so a replay has no seam.
  * Audio, synthesised in audio.py from the same plan() that draws the
    picture — so the keystrokes are heard exactly where they are seen. It
    used to be silence (a stream is required, a signal was not), which reads
    as broken when autoplay has sound on. Reels published through the API
    cannot attach Instagram's trending audio, so the only options were nothing
    or our own, and baked-in audio at least becomes the account's "Original
    audio".
"""
import argparse
import glob
import json
import math
import os
import sys
from datetime import datetime
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

import audio
import render
from render import INK, PAPER, RED, MUTED_ON_INK, MUTED_ON_PAPER, load_font, wrap

ROOT = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(ROOT, "config.json")) as _f:
    CFG = json.load(_f)
OUT = os.path.join(ROOT, "build", "reels")

# ---------------------------------------------------------------- design tokens
W, H = 1080, 1920                      # 9:16, Reels' native frame
FPS = int(CFG.get("reel_fps", 30))
OUTRO_S = float(CFG.get("reel_outro_seconds", 3.5))
LOOP_S = float(CFG.get("reel_loop_fade_seconds", 0.5))
FADE_S = float(CFG.get("reel_text_fade_seconds", 0.45))
READ_CPS = float(CFG.get("reel_chars_per_second", 15.0))
TAGLINE = CFG.get("reel_tagline", "Thirty, unfortunately.")
TYPE_CPS = float(CFG.get("reel_type_cps", 30.0))
LINE_PAUSE = 4          # characters' worth of pause at each line break
CRF = str(CFG.get("reel_crf", 21))
AUDIO = bool(CFG.get("reel_audio", True))

MARGIN_X = 104
TEXT_MAX_W = W - 2 * MARGIN_X
# Instagram draws its own chrome over a Reel — caption and action rail at the
# bottom, the status bar at the top. Anything outside this band is covered.
SAFE_TOP, SAFE_BOTTOM = 190, 340
MARK_H = 74                            # corner watermark, where the intro parks
MARK_TOP = SAFE_TOP
HERO_H = 300                           # the mark at full size, mid-intro


def secs_for(text: str) -> float:
    """Reading time, plus both fades. Long enough to actually finish the line.

    Comfortable silent reading is ~18-20 characters a second, so 15 leaves
    time to go back over the line. The fades are dead time for reading, so they
    are added rather than absorbed, and the cap keeps a three-slide post near
    twenty seconds, past which replays stop being likely.

    The cap bites on long slides: 186 characters in 5.6s is 33 a second, which
    nobody reads. That is a content problem, not a timing one — the fix is a
    slide-length rule in brand.yaml, not more seconds here.
    """
    return max(3.2, min(5.6, len(text) / READ_CPS)) + FADE_S * 2


# --------------------------------------------------------------------- pieces
def _ease(t: float) -> float:
    """Cubic ease-out: fast in, settles. Linear motion looks mechanical."""
    return 1 - (1 - t) ** 3


def _ease_io(t: float) -> float:
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def _lerp_hex(a: str, b: str, t: float) -> str:
    """Blend two brand hexes. The battery goes ink -> red as it empties, so the
    colour carries the same information as the level."""
    ca = tuple(int(a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    cb = tuple(int(b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02X%02X%02X" % tuple(int(round(ca[i] + (cb[i] - ca[i]) * t))
                                   for i in range(3))


_ASPECT = None


def mark_aspect() -> float:
    """Width of the mark per unit of height. Measured, not assumed, so the
    layout survives a font change."""
    global _ASPECT
    if _ASPECT is None:
        probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        _ASPECT = render.draw_mark(probe, 0, 0, 100.0, (0, 0, 0, 0),
                                   (0, 0, 0, 0)) / 100.0
    return _ASPECT


def stamp_mark(img: Image.Image, x: float, y: float, h: float, charge: float,
               stroke: str, accent: str, alpha: float = 1.0) -> None:
    """The mark, composited. Drawn into its own layer because ImageDraw with a
    translucent fill replaces the pixel's alpha instead of blending it."""
    if alpha <= 0.002 or h < 2:
        return
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    render.draw_mark(ImageDraw.Draw(layer), x, y, h,
                     render._hex_rgba(stroke, alpha),
                     render._hex_rgba(accent, alpha), charge)
    img.alpha_composite(layer)


def _ghost(img: Image.Image, dark: bool, charge: float) -> None:
    """The oversized mark bled off the bottom — and the progress indicator.

    Its charge is what is left of the reel. The outline sits at the same weight
    as the cards' ghost; the level is drawn stronger, because a progress bar
    nobody can see is not one.
    """
    gh = (W * 1.06) / mark_aspect()
    x, y = W * 0.10, H - SAFE_BOTTOM + 20 - gh
    base = PAPER if dark else INK
    stamp_mark(img, x, y, gh, 0.0, base, base, 0.085 if dark else 0.055)
    stamp_mark(img, x, y, gh, charge, base, RED, 0.0)   # outline already down
    if charge > 0.004:                                   # the level, readable
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        render.draw_mark(ImageDraw.Draw(layer), x, y, gh,
                         (0, 0, 0, 0), render._hex_rgba(RED, 0.20), charge)
        img.alpha_composite(layer)


def _surface(dark: bool, charge: float = 0.0) -> Tuple[Image.Image, Any, Any]:
    bg, fg = (INK, PAPER) if dark else (PAPER, INK)
    img = Image.new("RGBA", (W, H), bg)
    _ghost(img, dark, charge)
    return img, fg, (MUTED_ON_INK if dark else MUTED_ON_PAPER)


def _fit(text: str, role: str, max_h: int):
    """render.py's fitter, retargeted at the taller frame."""
    return render.fit_text(text, role, TEXT_MAX_W, max_h)


def type_length(lines) -> int:
    """Total 'keystrokes' in a wrapped block, counting a pause at each break."""
    return sum(len(l) for l in lines) + LINE_PAUSE * max(0, len(lines) - 1)


def _typed(lines, budget: int):
    """Reveal `budget` keystrokes across already-wrapped lines.

    The wrap is computed on the full text and never recomputed, so the layout
    cannot reflow mid-type — revealing a growing prefix and re-wrapping it makes
    finished lines jump as the next word arrives.

    Returns (visible lines, index of the line holding the caret or None).
    """
    out, caret, left = [], None, budget
    for i, line in enumerate(lines):
        if left <= 0:
            out.append("")
            continue
        take = min(len(line), left)
        out.append(line[:take])
        left -= take
        if take < len(line):
            caret, left = i, 0
        else:
            if caret is None and left <= LINE_PAUSE and i < len(lines) - 1:
                caret = i
            left -= LINE_PAUSE
    return out, caret


def _text_block(img: Image.Image, lines, font, lh: int, top: int, fg,
                alpha: float, rise: float = 0.0, budget: Optional[int] = None,
                caret_on: bool = False) -> None:
    """One alpha for the whole block. Per-line staggering reads as restless at
    this pace; the words are the content, not the transition.

    With a budget, the block types itself out instead — which doubles as the
    pacing, since you read at the speed it arrives.
    """
    if alpha <= 0.004:
        return
    shown, caret = (lines, None) if budget is None else _typed(lines, budget)
    col = render._hex_rgba(PAPER if fg == PAPER else INK, alpha)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for i, line in enumerate(shown):
        if line:
            ld.text((MARGIN_X, top + i * lh + rise), line, font=font, fill=col)
    if caret_on and caret is not None:
        cw, ch = font.size * 0.46, font.size * 0.74
        cx = MARGIN_X + font.getlength(shown[caret])
        cy = top + caret * lh + rise + font.size * 0.30
        ld.rounded_rectangle([cx + 6, cy, cx + 6 + cw, cy + ch],
                             radius=cw * 0.22, fill=render._hex_rgba(RED, alpha))
    img.alpha_composite(layer)


def _fade(t: float, dur: float) -> Tuple[float, float]:
    """(alpha, rise) for a block that fades in, holds, and fades out."""
    if t < FADE_S:
        e = _ease(t / FADE_S)
        return e, (1 - e) * 22
    if t > dur - FADE_S:
        return max(0.0, _ease((dur - t) / FADE_S)), 0.0
    return 1.0, 0.0


# ----------------------------------------------------------------------- frames
def _tagline(img: Image.Image, cy: float, alpha: float) -> None:
    """thirty, unfortunately. — with the comma in brand red, because the comma
    is the account's end-mark and this is where the name gets said out loud."""
    if alpha <= 0.004:
        return
    f = load_font(46, "Bold")
    head, _, tail = TAGLINE.partition(",")
    widths = [f.getlength(head), f.getlength(","), f.getlength(tail)]
    x = (W - sum(widths)) / 2.0
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for part, col, w in zip((head, ",", tail), (INK, RED, INK), widths):
        ld.text((x, cy), part, font=f, fill=render._hex_rgba(col, alpha))
        x += w
    img.alpha_composite(layer)


def outro_frames(n_frames: int) -> List[Image.Image]:
    """The five seconds that CLOSE every reel, identically.

    This used to open them, and that was the single most expensive mistake in
    the file. Measured across the three published reels it was 23-24% of the
    runtime, and it was the first 23% — the exact window Instagram uses to
    decide whether to show the reel to anyone else, spent on a logo. Watch
    time is one of three confirmed ranking signals and nobody has ever
    watched a logo.

    So the motion is reversed and moved to the end. The mark grows out of the
    corner watermark it has been sitting in, the battery charges and drains,
    and the number it lands on IS the logo. Frame 0 of the video is now the
    hook.
    """
    asp = mark_aspect()
    cx, cy = W / 2.0, H * 0.46
    end_x, end_y = MARGIN_X, MARK_TOP
    pf = load_font(64, "Bold")
    hf = load_font(34, "Medium")
    frames = []
    for i in range(n_frames):
        t = i / max(1, n_frames - 1)
        img, fg, muted = _surface(False)

        if t < 0.16:                      # grow out of the corner watermark
            e = _ease(t / 0.16)
            h, alpha, charge = MARK_H + (HERO_H - MARK_H) * e, 1.0, 0.0
        elif t < 0.34:                    # charge to full
            e = _ease((t - 0.16) / 0.18)
            h, alpha, charge = HERO_H, 1.0, e
        elif t < 0.70:                    # and drain, which is the whole point
            e = _ease_io((t - 0.34) / 0.36)
            h, alpha, charge = HERO_H, 1.0, 1.0 - 0.70 * e
        else:                             # hold on thirty, and say the name
            h, alpha, charge = HERO_H, 1.0, 0.30

        w = h * asp
        if t < 0.16:
            e = _ease(t / 0.16)
            x = end_x + (cx - w / 2.0 - end_x) * e
            y = end_y + (cy - h / 2.0 - end_y) * e
        else:
            x, y = cx - w / 2.0, cy - h / 2.0

        # full brand red by the time it lands on 30%: the last frame of the
        # drain has to BE the logo, not an approximation of it
        accent = _lerp_hex(INK, RED, min(1.0, (1.0 - charge) / 0.70))
        stamp_mark(img, x, y, h, charge, INK, accent, alpha)

        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        if 0.18 <= t < 0.78:              # the counter, landing on the logo
            lab = "%d%%" % int(round(charge * 100))
            a = min(1.0, (t - 0.18) / 0.05) * min(1.0, (0.78 - t) / 0.05)
            ld.text((cx - pf.getlength(lab) / 2, cy + HERO_H * 0.62), lab,
                    font=pf, fill=render._hex_rgba(accent, a))
        if t > 0.86:                      # the handle, last thing on screen
            a = _ease(min(1.0, (t - 0.86) / 0.08))
            ld.text((cx - hf.getlength("@30unfortunately") / 2,
                     cy + HERO_H * 0.62 + 96), "@30unfortunately",
                    font=hf, fill=render._hex_rgba(INK, a * 0.55))
        img.alpha_composite(layer)

        # the counter hands over to the name, in the same place on screen
        if t > 0.74:
            _tagline(img, cy + HERO_H * 0.62 + 8,
                     _ease(min(1.0, (t - 0.74) / 0.08)) * 0.92)
        frames.append(img)
    return frames


def plan(post: Dict[str, Any]) -> Dict[str, Any]:
    """Every number the picture and the sound both need, decided once.

    The ticks have to land on the keystrokes, and the only way to guarantee
    that is for one function to own the timing. Frame counts rather than
    seconds, because the video is quantised to FPS and the audio is not —
    deriving the sound from the float durations would let the two drift apart
    by a frame per slide and nobody would know why the end sounded loose.
    """
    slides = post["slides"]
    durs = [secs_for(t) for t in slides]
    span = sum(durs)

    segs, at, elapsed = [], 0, 0.0
    for n, text in enumerate(slides):
        last = n == len(slides) - 1
        role = "hook" if n == 0 else "landing" if last else "list"
        lines, font, lh = _fit(text, role, 980)
        nf = int(durs[n] * FPS)
        keys = type_length(lines)
        # The hook does not type. It used to, and budget = total * t/type_dur
        # meant frame 0 held zero characters — an empty card with a caret, on
        # the one frame that is also the still. A second in, half the sentence
        # had arrived; the hook was not finished until 2.0-2.9s, by which time
        # the reel has already been rated. Typing stays on the slides after it,
        # where it is pacing rather than an audition.
        type_dur = 0.0 if role == "hook" else min(
            max(0.4, (durs[n] - FADE_S) * 0.62), keys / TYPE_CPS)
        segs.append({
            "role": role, "dark": last, "lines": lines, "font": font, "lh": lh,
            "frames": nf, "dur": nf / float(FPS), "start": at / float(FPS),
            "keys": keys, "type_dur": type_dur,
            "charge0": 1.0 - elapsed / span,
            "charge1": 1.0 - (elapsed + durs[n]) / span,
        })
        at += nf
        elapsed += durs[n]

    outro_nf = int(OUTRO_S * FPS)
    loop_nf = int(LOOP_S * FPS)
    return {
        "slides": segs,
        "outro": {"frames": outro_nf, "start": at / float(FPS),
                  "dur": outro_nf / float(FPS)},
        "loop_frames": loop_nf,
        "loop": loop_nf / float(FPS),
        "duration": (at + outro_nf + loop_nf) / float(FPS),
    }


def slide_frames(seg: Dict[str, Any], handle: str) -> List[Image.Image]:
    lines, font, lh = seg["lines"], seg["font"], seg["lh"]
    dark, n_frames, dur = seg["dark"], seg["frames"], seg["dur"]
    total, type_dur = seg["keys"], seg["type_dur"]
    band_top, band_bot = SAFE_TOP + MARK_H + 90, H - SAFE_BOTTOM - 60
    top = band_top + (band_bot - band_top - len(lines) * lh) // 2
    frames = []
    for i in range(n_frames):
        t = i / float(FPS)
        charge = seg["charge0"] + (seg["charge1"] - seg["charge0"]) * (
            i / max(1, n_frames - 1))
        img, fg, muted = _surface(dark, charge)
        render.draw_mark(ImageDraw.Draw(img), MARGIN_X, MARK_TOP, MARK_H,
                         fg, RED)
        # no fade in: the typing is the entrance, and doing both is muddy
        alpha = 1.0 if t < dur - FADE_S else max(0.0, _ease((dur - t) / FADE_S))
        typing = t < type_dur
        # type_dur of 0 is the hook: whole line, frame 0, no reveal at all.
        budget = int(total * min(1.0, t / type_dur)) if type_dur else total
        caret = typing or (int(t * 2.2) % 2 == 0 and t < dur - FADE_S)
        _text_block(img, lines, font, lh, top, fg, alpha, 0.0, budget, caret)
        if dark:
            d = ImageDraw.Draw(img)
            fy = H - SAFE_BOTTOM - 40
            d.text((MARGIN_X, fy), handle, font=load_font(30, "Medium"),
                   fill=muted)
            cw = 18.0 * (110 / 33.0)
            render.draw_comma(img, W - MARGIN_X - cw, fy - 110 * 0.52, 110, RED)
        frames.append(img)
    return frames


def cover_image(post: Dict[str, Any]) -> Image.Image:
    """The grid thumbnail: the hook, at 9:16, centred to survive a 4:5 crop.

    Close to frame 0 of the video, which is the hook in full, but not the same
    image: the video's watermark sits above the grid's 4:5 crop, so this one
    is centred in the whole frame rather than in the video's safe band.
    """
    lines, font, lh = _fit(post["slides"][0], "hook", 1000)
    img, fg, _ = _surface(False, 0.30)
    # lower than the video's watermark: the grid crop starts at y=285 and the
    # mark at MARK_TOP would be sliced off the top of every tile
    grid_top = (H - W * 5 // 4) // 2        # the 4:5 slice the profile keeps
    render.draw_mark(ImageDraw.Draw(img), MARGIN_X, grid_top + 62, MARK_H, fg, RED)
    # centred in the FULL frame, not the safe band: the grid keeps the middle
    # 4:5 of this and throws the rest away
    _text_block(img, lines, font, lh, (H - len(lines) * lh) // 2 - 30, fg, 1.0)
    return img.convert("RGB")


def build_frames(post: Dict[str, Any], handle: str,
                 sc: Optional[Dict[str, Any]] = None) -> List[Image.Image]:
    """The hook on frame 0, every slide, then the mark."""
    sc = sc or plan(post)
    frames = []
    for seg in sc["slides"]:
        frames += slide_frames(seg, handle)
    frames += outro_frames(sc["outro"]["frames"])

    nf = sc["loop_frames"]
    if nf > 1 and frames:
        first, last = frames[0], frames[-1]
        for i in range(nf):
            frames.append(Image.blend(last, first, _ease((i + 1) / float(nf))))
    return frames


# ---------------------------------------------------------------------- encode
def encode(frames: List[Image.Image], out: str,
           samples: Optional[bytes] = None) -> str:
    """H.264 in an MP4, plus an AAC track.

    Instagram wants yuv420p H.264; a missing audio stream has historically been
    rejected outright, so there is always a track — audio.py's if there is
    one, silence if audio is switched off. Either way it goes in as raw s16
    bytes rather than through numpy, which is the only thing that array would
    have been for.
    """
    import av

    os.makedirs(os.path.dirname(out), exist_ok=True)
    container = av.open(out, mode="w")
    v = container.add_stream("libx264", rate=FPS)
    v.width, v.height = W, H
    v.pix_fmt = "yuv420p"
    v.options = {"crf": CRF, "preset": "medium", "movflags": "+faststart"}

    a = container.add_stream("aac", rate=44100)
    a.layout = "mono"

    for img in frames:
        frame = av.VideoFrame.from_image(img.convert("RGB"))
        for packet in v.encode(frame):
            container.mux(packet)

    # Trimmed or zero-padded to the video's own length. plan() and the frame
    # list agree, but rounding a float duration twice need not.
    n_samples = int(44100 * len(frames) / float(FPS))
    need = 2 * n_samples
    pcm = (samples or b"")[:need]
    pcm += bytes(need - len(pcm))

    chunk = 1024
    pts = 0
    while pts < n_samples:
        size = min(chunk, n_samples - pts)
        af = av.AudioFrame(format="s16", layout="mono", samples=size)
        af.planes[0].update(pcm[2 * pts:2 * (pts + size)])
        af.sample_rate = 44100
        af.pts = pts
        af.time_base = Fraction(1, 44100)
        pts += size
        for packet in a.encode(af):
            container.mux(packet)

    for packet in v.encode():
        container.mux(packet)
    for packet in a.encode():
        container.mux(packet)
    container.close()
    return out


def render_reel(post: Dict[str, Any], handle: str, stills_only: bool = False,
                preview: bool = False) -> Optional[str]:
    sc = plan(post)
    frames = build_frames(post, handle, sc)
    pid = post["id"]
    if preview:
        # A fresh filename every time. QuickTime holds a file open and re-focuses
        # the existing window instead of reloading it, so re-encoding to the same
        # path silently shows you the previous build.
        pid = "%s-%s" % (pid, datetime.now().strftime("%H%M%S"))
    if stills_only:
        d = os.path.join(OUT, pid)
        os.makedirs(d, exist_ok=True)
        # Frame 0 first, because frame 0 is now the whole hook and it is the
        # frame the reel is judged on. Then the content, then the outro.
        n, tail = len(frames) - sc["loop_frames"], sc["outro"]["frames"]
        marks = [0, int((n - tail) * 0.45), (n - tail) - 3,
                 (n - tail) + int(tail * 0.25), (n - tail) + int(tail * 0.55),
                 (n - tail) + int(tail * 0.80), n - 1]
        for n, f in enumerate(marks, 1):
            frames[min(f, len(frames) - 1)].convert("RGB").save(
                os.path.join(d, "storyboard-%d.png" % n))
        print("%s storyboard: %s (%d frames, %.1fs)"
              % (pid, os.path.relpath(d, ROOT), len(frames), len(frames) / float(FPS)))
        return None

    out = os.path.join(OUT, "%s.mp4" % pid)
    encode(frames, out, audio.render(sc) if AUDIO else None)
    cover_image(post).save(os.path.join(OUT, "%s-cover.jpg" % pid),
                           quality=92, optimize=True)
    mb = os.path.getsize(out) / 1e6
    print("%s -> %s  %.1fs  %.1f MB  (%s, + cover)"
          % (pid, os.path.relpath(out, ROOT), len(frames) / float(FPS), mb,
             "sound" if AUDIO else "silent"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="post id from content/approved/")
    ap.add_argument("--from", dest="src", help="a candidates or approved json")
    ap.add_argument("--stills", action="store_true",
                    help="storyboard PNGs only, no encode")
    ap.add_argument("--preview", action="store_true",
                    help="write to a timestamped file so a player cannot show "
                         "you a stale one")
    args = ap.parse_args()

    if args.src:
        files = [args.src]
    elif args.id:
        files = [os.path.join(ROOT, "content", "approved", "%s.json" % args.id)]
    else:
        sys.exit("need --id or --from")

    for f in files:
        blob = json.load(open(f))
        handle = blob.get("handle", "@30unfortunately")
        for p in blob.get("posts") or blob.get("accepted") or []:
            render_reel(p, handle, args.stills, args.preview)


if __name__ == "__main__":
    main()
