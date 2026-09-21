#!/usr/bin/env python3
"""metrics.py — what the numbers say. No HTML, no network, no Pillow.

`publish.py ab` in the terminal and the dashboard on the Pages site were about
to become two readings of the same table, which is how two readings drift
apart. Both come from here instead: this module does the arithmetic, its
callers only choose whether to print it or draw it.

stdlib only — publish.py imports it and that job has no install step.
"""
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import store

ARMS = ("carousel", "reel")

# Below this many posts in the smaller arm, a median is mostly noise. Stated
# once here so the printer and the page carry the same warning.
CALL_IT_AT = 15


def median(xs: List[float]) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def bootstrap(a: List[float], b: List[float], rounds: int = 4000
              ) -> Optional[Tuple[float, float]]:
    """A confidence interval on median(b)/median(a), by resampling.

    Not a t-test: reach is violently right-skewed and the samples are tiny, so
    the assumptions behind a p-value are not met. Resampling makes no
    distributional claim — it just asks how much the ratio moves when you draw
    these same posts again with replacement.
    """
    import random
    if len(a) < 3 or len(b) < 3:
        return None
    rng = random.Random(30)
    out = []
    for _ in range(rounds):
        ma = median([rng.choice(a) for _ in a])
        mb = median([rng.choice(b) for _ in b])
        if ma:
            out.append(mb / ma)
    if not out:
        return None
    out.sort()
    return out[int(0.05 * len(out))], out[int(0.95 * len(out)) - 1]


# --------------------------------------------------------------- freshness
def last_capture(conn: sqlite3.Connection) -> Optional[datetime]:
    row = conn.execute("SELECT MAX(captured_at) t FROM metrics").fetchone()
    if not row or not row["t"]:
        return None
    return datetime.fromisoformat(row["t"])


def ago(then: Optional[datetime], now: Optional[datetime] = None) -> str:
    """'4 hours ago'. Coarse on purpose — the question is stale or not."""
    if then is None:
        return "never"
    now = now or datetime.now(timezone.utc)
    mins = int((now - then).total_seconds() // 60)
    if mins < 2:
        return "just now"
    if mins < 90:
        return "%d minutes ago" % mins
    hours = mins / 60.0
    if hours < 36:
        return "%d hours ago" % round(hours)
    return "%d days ago" % round(hours / 24.0)


def hours_since(then: Optional[datetime], now: Optional[datetime] = None) -> float:
    if then is None:
        return float("inf")
    now = now or datetime.now(timezone.utc)
    return (now - then).total_seconds() / 3600.0


# ------------------------------------------------------------------ totals
COUNTED = ("reach", "views", "likes", "comments", "saved", "shares",
           "total_interactions")


def overview(conn: sqlite3.Connection) -> Dict[str, Any]:
    """One row of headline numbers, from each post's most recent capture.

    Summed across posts, not across captures: metrics rows are cumulative
    per post, so adding every row would count the same reach once per pull.
    """
    rows = store.published_with_metrics(conn)
    out = dict((k, 0) for k in COUNTED)
    for r in rows:
        for k in COUNTED:
            out[k] += r[k] or 0
    out["posts"] = len(rows)
    out["best"] = max(rows, key=lambda r: r["reach"] or 0) if rows else None
    out["counts"] = store.counts(conn)
    out["captured"] = last_capture(conn)
    return out


# --------------------------------------------------------------------- a/b
def _vals(rows, key) -> List[float]:
    return [r[key] for r in rows if r[key] is not None]


def _rates(rows, key) -> List[float]:
    return [(r[key] or 0) / float(r["reach"]) for r in rows if r["reach"]]


def compare(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Carousel versus reel, on the metrics both formats actually report.

    Reel watch time is absent because a carousel cannot report it, and a
    comparison that uses a number only one arm produces is not a comparison.
    """
    groups = store.by_format(conn)
    arm = dict((a, groups.get(a, [])) for a in ARMS)
    a, b = arm["carousel"], arm["reel"]

    lines = []
    for label, va, vb, pct in (
            ("reach (median)", _vals(a, "reach"), _vals(b, "reach"), False),
            ("views (median)", _vals(a, "views"), _vals(b, "views"), False),
            ("shares / reach", _rates(a, "shares"), _rates(b, "shares"), True),
            ("saves / reach", _rates(a, "saved"), _rates(b, "saved"), True),
            ("likes / reach", _rates(a, "likes"), _rates(b, "likes"), True)):
        ma, mb = median(va), median(vb)
        lines.append({"label": label, "carousel": ma, "reel": mb, "pct": pct,
                      "ratio": (mb / ma) if (ma and mb) else None})

    ci = bootstrap(_vals(a, "reach"), _vals(b, "reach"))
    if ci is None:
        verdict = "Too few posts in one arm to resample a difference yet."
    elif ci[0] > 1.0:
        verdict = ("Reels reach further: the 90%% interval is %.1fx–%.1fx and "
                   "clears 1.0." % ci)
    elif ci[1] < 1.0:
        verdict = ("Carousels reach further: the 90%% interval is %.1fx–%.1fx "
                   "and sits below 1.0." % ci)
    else:
        verdict = ("Not a difference yet: the 90%% interval is %.1fx–%.1fx and "
                   "spans 1.0." % ci)

    smallest = min(len(a), len(b))
    caveat = None
    if smallest < CALL_IT_AT:
        caveat = ("Only %d in the smaller arm. Medians move a lot at this size "
                  "— read this as a shape, not a result. Look again at %d each, "
                  "decide at %d." % (smallest, CALL_IT_AT, CALL_IT_AT * 2))

    return {"n": dict((k, len(v)) for k, v in arm.items()),
            "rows": arm, "lines": lines, "ci": ci,
            "verdict": verdict, "caveat": caveat}
