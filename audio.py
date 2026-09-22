#!/usr/bin/env python3
"""
audio.py — the reel's soundtrack, synthesised from arithmetic.

Stdlib only: no sample library, no model, no network, nothing to install.

The typing is the soundtrack. A melody runs underneath it, quietly, and its
job is to stop the keystrokes sounding like they were recorded in a cupboard
— not to be listened to. If you find yourself following the tune, it is too
loud and PLUCK_GAIN is the number to turn down.

That ordering is the whole design, and it took four tries to get to. Music
that leads has to be about something, and a reel's music can only be about
the post it is under, which a renderer cannot know. The typing has no such
problem: it is caused by the picture, it stops when the words stop, and it is
right for every post automatically because it is not commenting on any of
them.

  * Keys are a short filtered-noise transient over a low decaying body —
    plastic on top, desk beneath. A sine blip is a beep; the noise is what
    makes it a key. Three bodies, cycled, so a run of them is typing rather
    than a metronome. One audible key per four keystrokes: the picture
    reveals at thirty characters a second and nobody types at thirty.
  * The melody is two phrases in a simple AABA, written as scale degrees
    rather than frequencies, so when the scale changes under it at a slide
    boundary the same shape arrives in the new key. Every scale is a major
    pentatonic — five notes with no semitone between any of them, so no two
    notes in this file can sound sour however they overlap.
  * A triad sits under that as glue, quieter still.
  * A whisper of filtered room tone under everything, so it sounds recorded
    rather than assembled. ROOM_GAIN to nothing if you disagree.

Two constraints that shape the rest. A phone speaker gives almost nothing
below ~300 Hz, so nothing here is voiced down where it would only muddy the
encode. And everything sharp in audio is a discontinuity, so every envelope
is a raised cosine and every sound begins and ends at zero.
"""
import array
import math

SR = 44100

# ------------------------------------------------------------------ the keys
# A key is a BAND, not a thump. The first version put most of its energy in a
# 258 Hz body with a 45 ms tail, which at seven keys a second overlaps into a
# rumble: boomy on headphones and, because a phone or laptop speaker gives
# almost nothing under 300 Hz, close to inaudible everywhere else. Both
# complaints, one cause.
#
# The second attempt band-passed noise instead, and a one-pole either end is
# far too gentle to be a band — what came out was broadband hiss that was
# harsh AND failed the no-click test on slope alone. So the click is not noise
# at all now: three short decaying resonances between 1.2 and 2.4 kHz, which
# is where a keyboard lives and where a small speaker is most efficient. Four
# milliseconds of decay reads as a click rather than as a tone, and being made
# of sines it is band-limited by construction rather than by a filter.
KEY_EVERY = 4          # keystrokes per audible one; thirty a second is a buzz
KEY_CLICK = 0.135      # the transient: the plastic
KEY_MODES = ((1240.0, 1.00), (1830.0, 0.62), (2380.0, 0.38))
KEY_CLICK_TAU = 0.004
KEY_DETUNE = (1.0, 1.035, 0.968)   # three keys, not one key three times
KEY_THUD = 0.030       # the body under it
KEY_THUD_HZ = (360.0, 392.0, 340.0)
KEY_THUD_TAU = 0.014   # short. Any longer and consecutive keys smear.

# ----------------------------------------------------------------- the room
ROOM_GAIN = 0.008
ROOM_HZ = 1800.0       # one-pole cutoff on the room tone
TOP_HZ = 2600.0        # the brightest deliberate thing in the file, with a
                       # little margin. tests.py measures the no-click ceiling
                       # against it: what that catches is a join nobody faded,
                       # which steps far harder than any waveform here.

# ---------------------------------------------------------------- the melody
# One scale per slide, cycled. Major pentatonics sharing notes (523.25, 587.33,
# 783.99 recur), so a change of scale is a change of light, not of subject.
SCALES = (
    (392.00, 523.25, 587.33, 659.25, 783.99),   # C major pentatonic
    (349.23, 523.25, 587.33, 698.46, 880.00),   # F, the warm one
    (392.00, 493.88, 587.33, 783.99, 880.00),   # G, the bright one
    (440.00, 523.25, 659.25, 783.99, 880.00),   # A, read as a major 6
)
# The triad under each. Kept above 195 Hz: a root a phone cannot reproduce is
# a root that only costs bitrate.
PADS = (
    (196.00, 261.63, 329.63),
    (220.00, 261.63, 349.23),
    (246.94, 293.66, 392.00),
    (220.00, 329.63, 440.00),
)
PAD_GAIN = (0.017, 0.013, 0.010)

BEAT = 0.34            # one eighth. Phrase lengths are all in these.
# (degree, beats); None is a rest. Degrees index whichever scale is in force
# when the note is struck, which is what lets the tune change key with the
# picture without being rewritten.
PHRASE_A = ((1, 2), (2, 1), (3, 1), (4, 3), (None, 1), (3, 2), (2, 2))
PHRASE_B = ((4, 2), (3, 1), (2, 1), (1, 3), (None, 1), (0, 2), (1, 2))
FORM = (PHRASE_A, PHRASE_A, PHRASE_B, PHRASE_A)
PLUCK_GAIN = 0.030     # well under the keys. This is the volume knob.
PLUCK_TAU = 0.30
PLUCK_ATK = 0.006
ROOT_GAIN = 0.011      # a low note on the first beat of each phrase, for floor

CHORD_FADE = 0.85      # seconds of crossfade between voicings
LEAD_IN = 1.2          # the bed arrives, slowly, before the first word

LIMIT = 0.72           # where the soft knee starts; below this it is linear
PEAK = 0.62            # headroom: platforms normalise, clipping is forever


def arc(n: int, of: int) -> float:
    """How loud the bed is under slide `n` of `of`. Held back under the hook,
    where the job is reading, opening up towards the landing."""
    return 0.62 + 0.38 * (n / float(max(1, of - 1)))


def _win(x: float) -> float:
    """Raised cosine, 0 -> 1. Every entrance and exit in this file."""
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, x)))


def _drift(i: int, rate: float, phase: float) -> float:
    """A slow wobble in [0.56, 1.0]. A different rate per partial, so no two
    of them peak together and the triad never sits perfectly still."""
    return 0.78 + 0.22 * math.sin(2 * math.pi * rate * i / SR + phase)


def _rand(seed: int = 30):
    """A deterministic noise source.

    Deterministic because CI re-renders every reel whenever the renderer
    changes, and two builds of the same post have to be the same file.
    """
    x = seed
    while True:
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        yield x / 1073741823.5 - 1.0


def _room(buf) -> None:
    """Noise, one-pole lowpassed. A room rather than a vacuum.

    The filter is not decoration: unfiltered white noise steps the full
    amplitude between neighbouring samples, and a file full of maximal steps
    measures as nothing but clicks.
    """
    rnd = _rand()
    a = math.exp(-2 * math.pi * ROOM_HZ / SR)
    y = 0.0
    for i in range(len(buf)):
        y = (1 - a) * next(rnd) + a * y
        buf[i] += ROOM_GAIN * y


def _key(buf, at: float, n: int) -> None:
    """One keystroke: three short resonances over a short body."""
    i0 = int(at * SR)
    if i0 >= len(buf):
        return
    det = KEY_DETUNE[n % len(KEY_DETUNE)]
    nc = int(KEY_CLICK_TAU * 6 * SR)
    atk = max(1, int(0.0004 * SR))
    for hz, g in KEY_MODES:
        step = 2 * math.pi * hz * det / SR
        for i in range(nc):
            j = i0 + i
            if j >= len(buf):
                break
            env = math.exp(-i / (KEY_CLICK_TAU * SR))
            if i < atk:
                env *= _win(i / float(atk))
            buf[j] += KEY_CLICK * g * env * math.sin(step * i)

    hz = KEY_THUD_HZ[n % len(KEY_THUD_HZ)]
    step = 2 * math.pi * hz / SR
    atk = max(1, int(0.0015 * SR))
    for i in range(int(KEY_THUD_TAU * 5 * SR)):
        j = i0 + i
        if j >= len(buf):
            break
        env = math.exp(-i / (KEY_THUD_TAU * SR))
        if i < atk:
            env *= _win(i / float(atk))
        buf[j] += KEY_THUD * env * math.sin(step * i)


def _voices(buf, start: float, dur: float, freqs, gains, level: float,
            fade: float) -> None:
    """A chord, faded in and out with an equal-power curve.

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


def _pluck(buf, at: float, hz: float, gain: float,
           tau: float = PLUCK_TAU) -> None:
    """One struck note: a fast rounded attack, a short exponential tail, and a
    quieter octave above it that decays twice as fast.

    The octave is the difference between this and a sine beep. Real struck
    things lose their top partials first, so a shimmer that fades before the
    fundamental does is what reads as struck rather than as played back.

    `tau` is per note, because a note written to last three beats has to still
    be sounding when the next one arrives or the phrase has a hole in it.
    """
    i0 = int(at * SR)
    if i0 >= len(buf):
        return
    n = int(min(tau * 5.0, 2.4) * SR)
    atk = max(1, int(PLUCK_ATK * SR))
    for f, g, tv in ((hz, 1.0, tau), (hz * 2.0, 0.25, tau * 0.5)):
        step = 2 * math.pi * f / SR
        for i in range(n):
            j = i0 + i
            if j >= len(buf):
                break
            env = math.exp(-i / (tv * SR))
            if i < atk:
                env *= _win(i / float(atk))
            buf[j] += gain * g * env * math.sin(step * i)


def _where(spans, t: float):
    """Which scale and which level are in force at `t`."""
    scale, level = SCALES[0], 1.0
    for s_at, s_i, s_lv in spans:
        if t >= s_at:
            scale, level = SCALES[s_i], s_lv
    return scale, level


def render(score: dict) -> bytes:
    """Turn a reel's timing plan into mono 16-bit PCM.

    `score` is what reel.py's plan() computed for the picture, so the keys
    cannot drift off the typing no matter what the reading-speed constants do
    later.
    """
    dur = float(score["duration"])
    buf = array.array("d", bytes(8 * int(dur * SR)))
    n_slides = len(score["slides"])

    spans, n = [], 0
    for i, seg in enumerate(score["slides"]):
        level = arc(i, n_slides)
        start = seg["start"] - (LEAD_IN if i == 0 else CHORD_FADE * 0.5)
        span = seg["dur"] + CHORD_FADE + (LEAD_IN if i == 0 else 0.0)
        _voices(buf, start, span, PADS[i % len(PADS)], PAD_GAIN, level,
                CHORD_FADE)
        spans.append((seg["start"], i % len(SCALES), level))

        # The reason any of this exists. One key per KEY_EVERY keystrokes, at
        # the rate the picture actually types, from the same plan().
        if seg["type_dur"] > 0 and seg["keys"] > 0:
            for k in range(0, seg["keys"], KEY_EVERY):
                _key(buf, seg["start"] + seg["type_dur"] * (k / float(seg["keys"])),
                     n)
                n += 1

    out = score["outro"]
    _voices(buf, out["start"] - CHORD_FADE * 0.5, out["dur"] + CHORD_FADE,
            PADS[0], PAD_GAIN, 0.86, CHORD_FADE)
    spans.append((out["start"], 0, 0.86))

    # The melody, unbroken start to finish, underneath everything.
    end = dur - 0.1
    at, ph = 0.0, 0
    while at < end:
        phrase = FORM[ph % len(FORM)]
        scale, level = _where(spans, at)
        _pluck(buf, at, scale[0] / 2.0, ROOT_GAIN * level, 0.60)
        for i, (deg, beats) in enumerate(phrase):
            if at >= end:
                break
            if deg is not None:
                scale, level = _where(spans, at)
                # A long note is held, a short one is clipped. Without this
                # every note decays in a third of a second and the phrase is
                # as even as an arpeggio.
                tau = min(0.80, max(0.26, 0.18 + 0.18 * beats))
                vel = 1.0 if i == 0 else (0.88 if beats > 1 else 0.74)
                _pluck(buf, at, scale[deg], PLUCK_GAIN * level * vel, tau)
            at += beats * BEAT
        ph += 1

    _room(buf)
    _seam(buf, float(score.get("loop", 0.0)))
    return _pcm16(buf)


def _seam(buf, loop: float) -> None:
    """Fade the head in, crossfade the tail into it, land on zero.

    The order matters. The tail is crossfaded against the RAW head, so what
    plays under the last half second is what is about to play again; then the
    head is faded in, for the first play only; then the last 80 ms go to zero.
    Both ends of the file are therefore silent and equal, which is what makes
    a loop a loop — a step of any size at the join is a click, and a click on
    every replay is what makes a reel feel cheap. Watch time counts replays.
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

    The knee is only for the rare sample where a key and several partials
    align; below LIMIT the signal is untouched. An earlier version normalised
    to 1.0 and THEN drove tanh at 1.35, which parked the whole track inside
    the saturation region: peak at 100% of full scale and an RMS that never
    moved 1% across twenty seconds. That is a buzz.
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
