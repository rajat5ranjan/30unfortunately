#!/usr/bin/env python3
"""
audio.py — the reel's soundtrack, synthesised from arithmetic.

Stdlib only: no sample library, no model, no network, nothing to install. The
whole track is sums of sine waves, which is also the reason it cannot sound
harsh — a sine has no harmonics to be harsh with. Everything sharp in audio is
a discontinuity, so every envelope here is a raised cosine and every note ends
at exactly zero.

Why it exists at all: reel.py was muxing literal silence (a stream is required,
a signal was not), and a silent reel reads as broken when autoplay has sound
on. Audio baked into the file is also attributed to the account as "Original
audio", which is a small surface of its own.

The brief was smooth and not monotone, and those pull against each other:
smooth means not much happens, monotone means nothing happens. The three
things that stop it flattening out:

  * The pad is a chord, and the chord CHANGES at every slide, crossfaded over
    most of a second. The voicings share notes, so the change is a colour
    shift rather than a move.
  * Every partial has its own slow amplitude drift, at a different rate, none
    of them in phase. The bed breathes instead of sitting still.
  * The typing is audible, so the busiest sound is tied to the busiest part of
    the picture, and it stops when the words stop.

Register matters more than taste here. A phone speaker gives you almost
nothing below ~300 Hz, so a "low drone" at 50 Hz is a drone nobody hears; the
pad sits at 170-590 Hz where a phone can reproduce it, with one sub partial
underneath for anyone on headphones.
"""
import array
import math

SR = 44100

# Chord voicings, cycled one per slide. Common tones (261.63, 329.63) run
# through most of them, so a change moves one or two voices and not the floor.
CHORDS = (
    (220.00, 261.63, 329.63, 493.88),   # A minor, add 9 above
    (174.61, 261.63, 329.63, 440.00),   # F major 7
    (196.00, 246.94, 329.63, 493.88),   # G over its own 7th
    (164.81, 246.94, 329.63, 440.00),   # E minor
)
PARTIAL_GAIN = (0.34, 0.26, 0.20, 0.13)   # quieter as they go up, as ears expect
SUB_GAIN = 0.15                            # the root an octave down, for headphones

PAD_PEAK = 0.30        # the bed, at its loudest. Everything else is louder.
CHORD_FADE = 0.85      # seconds of crossfade between voicings
LEAD_IN = 1.2          # the pad arrives, slowly, before the first word

TICK_HZ = 700.0        # a soft wooden blip, not a click
TICK_S = 0.024
TICK_GAIN = 0.075
TICK_EVERY = 7         # keystrokes per audible tick; every one is a machine gun

CHIME_GAIN = 0.09
CHIME_TAU = 1.6

LIMIT = 0.72           # where the soft knee starts; below this it is linear
PEAK = 0.62            # headroom: platforms normalise, clipping is forever


def arc(n: int, of: int) -> float:
    """How loud the bed is under slide `n` of `of`.

    Not flat. A bed at one level for twenty seconds is the definition of
    monotone however much the chord moves, so the reel gets a shape: quiet
    under the hook, where the job is reading and the sound should stay out of
    the way, opening up towards the landing where the joke goes.
    """
    return 0.50 + 0.50 * (n / float(max(1, of - 1)))


def _drift(i: int, rate: float, phase: float) -> float:
    """A slow amplitude wobble in [0.56, 1.0]. A different rate per partial, so
    no two of them peak together and the chord never sits perfectly still."""
    return 0.78 + 0.22 * math.sin(2 * math.pi * rate * i / SR + phase)


def _win(x: float) -> float:
    """Raised cosine, 0 -> 1. Used for every entrance and exit in the file."""
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, x)))


def _voices(buf, start: float, dur: float, freqs, gains, level: float,
            fade: float) -> None:
    """A chord, faded in and out over `fade` with an equal-power curve.

    sin/cos rather than the raised cosine used elsewhere: consecutive chords
    overlap, and sin^2 + cos^2 = 1 holds the loudness flat through the change
    where a linear crossfade would dip in the middle of it.
    """
    i0 = max(0, int(start * SR))
    n = int(dur * SR)
    nf = max(1, int(fade * SR))
    for f, g in zip(freqs, gains):
        step = 2 * math.pi * f / SR
        rate, phase = 0.041 + 0.017 * (f % 7), (f % 3.1)
        amp = level * g
        for i in range(n):
            j = i0 + i
            if j >= len(buf):
                break
            if i < nf:
                e = math.sin(0.5 * math.pi * i / nf)
            elif i > n - nf:
                e = math.cos(0.5 * math.pi * (i - (n - nf)) / nf)
            else:
                e = 1.0
            buf[j] += amp * e * _drift(j, rate, phase) * math.sin(step * j)


def _chord(buf, start: float, dur: float, chord, level: float,
           fade: float) -> None:
    """The chord plus its root an octave down, at one call site so the sub can
    never drift out of step with the voices above it."""
    _voices(buf, start, dur, chord, PARTIAL_GAIN, level, fade)
    _voices(buf, start, dur, (chord[0] / 2.0,), (SUB_GAIN,), level, fade)


def _tick(buf, at: float, hz: float) -> None:
    """A keystroke. A Hann window over a short sine: it starts and ends at
    zero, which is the whole difference between a blip and a click."""
    i0 = int(at * SR)
    n = int(TICK_S * SR)
    step = 2 * math.pi * hz / SR
    for i in range(n):
        j = i0 + i
        if 0 <= j < len(buf):
            w = 0.5 - 0.5 * math.cos(2 * math.pi * i / n)
            buf[j] += TICK_GAIN * w * math.sin(step * i)


def _chime(buf, at: float, hz: float, gain: float, tau: float = 0.9) -> None:
    """A soft bell on the slide change: fundamental plus its octave, an
    exponential tail, and a 12 ms rounded attack so the onset is not a step."""
    i0 = int(at * SR)
    n = int(min(tau * 4.0, 2.4) * SR)
    atk = int(0.012 * SR)
    for f, g in ((hz, 1.0), (hz * 2.0, 0.34)):
        step = 2 * math.pi * f / SR
        for i in range(n):
            j = i0 + i
            if j >= len(buf):
                break
            env = math.exp(-i / (tau * SR)) * (_win(i / atk) if i < atk else 1.0)
            buf[j] += gain * g * env * math.sin(step * i)


def render(score: dict) -> bytes:
    """Turn a reel's timing plan into mono 16-bit PCM.

    `score` is what reel.py's plan() already computed for the picture, so the
    ticks cannot drift off the typing no matter what the reading-speed
    constants do later.
    """
    dur = float(score["duration"])
    buf = array.array("d", bytes(8 * int(dur * SR)))
    n_slides = len(score["slides"])

    for n, seg in enumerate(score["slides"]):
        chord = CHORDS[n % len(CHORDS)]
        level = PAD_PEAK * arc(n, n_slides)
        start = seg["start"] - (LEAD_IN if n == 0 else CHORD_FADE * 0.5)
        span = seg["dur"] + CHORD_FADE + (LEAD_IN if n == 0 else 0.0)
        _chord(buf, start, span, chord, level, CHORD_FADE)
        if n:
            _chime(buf, seg["start"], chord[1] * 2.0, CHIME_GAIN, CHIME_TAU)

        # The typing, at the rate the picture actually types. The pitch walks a
        # few hertz per tick, so a run of them is a patter and not a siren.
        if seg["type_dur"] > 0 and seg["keys"] > 0:
            for k in range(0, seg["keys"], TICK_EVERY):
                at = seg["start"] + seg["type_dur"] * (k / float(seg["keys"]))
                _tick(buf, at, TICK_HZ + 26.0 * math.sin(k * 1.7))

    # The mark animation gets the first chord back, held under the sign-off so
    # the end sounds like the beginning — which it is, because the video
    # crossfades its last frames into frame 0.
    out = score["outro"]
    _chord(buf, out["start"] - CHORD_FADE * 0.5, out["dur"] + CHORD_FADE,
           CHORDS[0], PAD_PEAK * 0.78, CHORD_FADE)
    _chime(buf, out["start"] + out["dur"] * 0.55, CHORDS[0][1],
           CHIME_GAIN, CHIME_TAU * 1.3)

    _seam(buf, float(score.get("loop", 0.0)))
    return _pcm16(buf)


def _seam(buf, loop: float) -> None:
    """Fade the head in, crossfade the tail into it, land on zero.

    The order matters. The tail is crossfaded against the RAW head, so what
    plays under the last half second is what is about to play again; then the
    head is faded in, for the first play only; then the last 80 ms go to zero.
    Both ends of the file are therefore silent and equal, which is what makes
    a loop a loop — a step of any size at the join is a click, and a click
    on every replay is exactly what makes a reel feel cheap. Watch time counts
    replays, so the join is load-bearing.
    """
    n = int(loop * SR)
    if n < 2 or n * 2 >= len(buf):
        n = 0
    if n:
        tail = len(buf) - n
        for i in range(n):
            x = 0.5 * math.pi * i / n
            buf[tail + i] = buf[tail + i] * math.cos(x) + buf[i] * math.sin(x)

    nf = min(int(0.5 * SR), len(buf))
    for i in range(nf):
        buf[i] *= _win(i / float(nf))

    nf = min(int(0.08 * SR), len(buf))
    for i in range(nf):
        buf[len(buf) - 1 - i] *= _win(i / float(nf))


def _pcm16(buf) -> bytes:
    """Soft knee above LIMIT, then scale so the loudest moment lands on PEAK.

    The knee is only for the rare sample where a chime, a tick and four
    partials happen to align; below LIMIT the signal is untouched. The first
    version of this normalised to 1.0 and *then* drove tanh at 1.35, which
    put the whole track permanently inside the saturation region — the peak
    read 100% of full scale and the RMS never moved more than 1% across
    twenty seconds. That is a buzz, not a bed.
    """
    for i, v in enumerate(buf):
        a = abs(v)
        if a > LIMIT:
            a = LIMIT + (1.0 - LIMIT) * math.tanh((a - LIMIT) / (1.0 - LIMIT))
            buf[i] = math.copysign(a, v)

    peak = max((abs(v) for v in buf), default=0.0)
    k = (PEAK / peak) if peak > 1e-9 else 0.0
    out = array.array("h", bytes(2 * len(buf)))
    for i, v in enumerate(buf):
        out[i] = int(max(-32767, min(32767, 32767 * v * k)))
    return out.tobytes()
