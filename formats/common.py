"""Drawing and timing helpers shared by the reel formats."""
import math
import os
import re
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFilter

from render import load_font  # noqa: F401  (re-exported for the formats)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1080, 1920
SAFE_TOP, SAFE_BOTTOM = 190, 340       # Instagram's chrome covers the rest
NOT_IN_FONT = re.compile(r"[^\u0000-ɏ‐-⁯₹™←-⇿]")
_PROBE = ImageDraw.Draw(Image.new("L", (1, 1)))


def clean(text: str) -> str:
    """Emoji and scripts the font has no glyph for would draw as tofu."""
    return re.sub(r"[ \t]{2,}", " ", NOT_IN_FONT.sub("", text or "")).strip()


def tw(text: str, font) -> float:
    return _PROBE.textlength(text, font=font)


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def back(t: float) -> float:
    """Ease-out with a small overshoot: things land, they do not slide."""
    t = max(0.0, min(1.0, t))
    c = 1.70158
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def read_secs(text: str, cps: float = 17.0) -> float:
    return 0.35 + len(text) / cps


def wrap(text: str, font, max_w: float) -> List[str]:
    out = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            t = (line + " " + word).strip()
            if tw(t, font) <= max_w or not line:
                line = t
            else:
                out.append(line)
                line = word
        out.append(line)
    return out


def hexrgb(h: str) -> Tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def circle_crop(img: Image.Image, size: int) -> Image.Image:
    src = img.convert("RGB").resize((size * 3, size * 3), Image.LANCZOS)
    mask = Image.new("L", (size * 3, size * 3), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * 3 - 1, size * 3 - 1], fill=255)
    out = Image.new("RGBA", (size * 3, size * 3), (0, 0, 0, 0))
    out.paste(src, (0, 0), mask)
    return out.resize((size, size), Image.LANCZOS)


def brand_avatar(size: int) -> Image.Image:
    return circle_crop(Image.open(os.path.join(ROOT, "assets", "brand", "pfp-dark.png")),
                       size)


def initials_avatar(name: str, size: int, bg: str, fg: str) -> Image.Image:
    img = Image.new("RGBA", (size * 3, size * 3), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size * 3 - 1, size * 3 - 1], fill=bg)
    ini = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    d.text((size * 1.5, size * 1.5), ini, font=load_font(int(size * 1.15), "Bold"),
           fill=fg, anchor="mm")
    return img.resize((size, size), Image.LANCZOS)


def with_alpha(sprite: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return sprite
    s = sprite.copy()
    s.putalpha(sprite.split()[3].point(lambda v: int(v * alpha)))
    return s


def paste_scaled(base: Image.Image, sprite: Image.Image, cx: float, cy: float,
                 scale: float = 1.0, alpha: float = 1.0) -> None:
    if alpha <= 0.01:
        return
    if abs(scale - 1.0) > 0.002:
        sprite = sprite.resize((max(1, int(sprite.width * scale)),
                                max(1, int(sprite.height * scale))), Image.BILINEAR)
    base.alpha_composite(with_alpha(sprite, alpha),
                         (int(cx - sprite.width / 2), int(cy - sprite.height / 2)))


def soft_shadow(sprite: Image.Image, blur: int = 24, alpha: int = 70,
                dy: int = 10) -> Tuple[Image.Image, int]:
    pad = blur * 2
    out = Image.new("RGBA", (sprite.width + 2 * pad, sprite.height + 2 * pad + dy),
                    (0, 0, 0, 0))
    sh = Image.new("RGBA", sprite.size, (0, 0, 0, 255))
    sh.putalpha(sprite.split()[3].point(lambda v: alpha if v > 0 else 0))
    out.alpha_composite(sh, (pad, pad + dy))
    out = out.filter(ImageFilter.GaussianBlur(blur))
    out.alpha_composite(sprite, (pad, pad))
    return out, pad


def score(beats: List[float], duration: float, loop: float,
          pops: List[Tuple[float, float]],
          outro_start: float = None) -> Dict[str, Any]:
    """A timing plan in the shape audio.render() reads: one pad chord per beat,
    and the outro chord under the sign-off when there is one."""
    end = outro_start if outro_start is not None else max(0.1, duration - 0.6)
    edges = [0.0] + sorted(b for b in beats if 0.05 < b < end - 0.05) + [end]
    slides = [{"start": a, "dur": b - a, "type_dur": 0.0, "keys": 0}
              for a, b in zip(edges, edges[1:]) if b - a > 0.05]
    return {"slides": slides, "outro": {"start": end, "dur": duration - end},
            "duration": duration, "loop": loop, "pops": pops}


def loop_frames(draw, duration: float, fps: int, loop: float) -> List[Image.Image]:
    """Draw every frame, crossfading the tail into frame 0 so a replay is seamless."""
    first = draw(0.0).convert("RGB")
    out = []
    for i in range(int(duration * fps)):
        t = i / float(fps)
        f = first if i == 0 else draw(t).convert("RGB")
        if t > duration - loop:
            f = Image.blend(f, first, ease((t - (duration - loop)) / loop))
        out.append(f)
    return out


HANDOFF_S = 0.3


def signed_off(draw, content_secs: float, fps: int,
               loop: float) -> Tuple[List[Image.Image], float, float]:
    """The content, then the account's sign-off, then a seamless loop.

    Every reel ends the same way — the 30 mark charging and draining to 30%,
    then the name and @30unfortunately (reel.outro_frames, shared with the
    text cards). The chat format shipped without it on 2026-11-26 and the
    first one also hit a hard length cap that cut its punchline off before
    it was sent. So: content runs as long as it needs, the sign-off follows,
    and only the sign-off's tail fades into frame 0.

    Returns (frames, total seconds, second the sign-off starts).
    """
    import reel
    first = draw(0.0).convert("RGB")
    frames = [first] + [draw(i / float(fps)).convert("RGB")
                        for i in range(1, int(content_secs * fps))]
    outro = [f.convert("RGB") for f in reel.outro_frames(int(reel.OUTRO_S * fps))]
    n = int(HANDOFF_S * fps)
    last = frames[-1]
    for i in range(n):           # hand over from the last frame into the sign-off
        frames.append(Image.blend(last, outro[0], ease((i + 1) / float(n))))
    outro_start = len(frames) / float(fps)
    nl = int(loop * fps)
    for i, f in enumerate(outro):
        k = i - (len(outro) - nl)
        if k >= 0:               # and out of it, back into frame 0
            f = Image.blend(f, first, ease((k + 1) / float(nl)))
        frames.append(f)
    return frames, len(frames) / float(fps), outro_start


def bezier(p0, p1, p2, n: int = 16):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1])
            for t in (k / float(n) for k in range(n + 1))]


def heart(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, fill=None,
          outline=None, width: int = 4) -> None:
    pts = []
    for k in range(60):
        a = 2 * math.pi * k / 60
        x = 16 * math.sin(a) ** 3
        y = -(13 * math.cos(a) - 5 * math.cos(2 * a) - 2 * math.cos(3 * a) - math.cos(4 * a))
        pts.append((cx + x * s / 34.0, cy + y * s / 34.0))
    if fill:
        d.polygon(pts, fill=fill)
    if outline:
        d.line(pts + [pts[0]], fill=outline, width=width, joint="curve")
