#!/usr/bin/env python3
"""metrics.py — what the numbers say. No HTML, no network, no Pillow.

`publish.py ab` in the terminal and the dashboard on the Pages site were about
to become two readings of the same table, which is how two readings drift
apart. Both come from here instead: this module does the arithmetic, its
callers only choose whether to print it or draw it.

stdlib only — publish.py imports it and that job has no install step.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import store

ARMS = ("carousel", "reel")


def relaunch() -> str:
    """The day v2 began (config `relaunch_date`): reels only, five reel styles,
    funny-first tone, news topics. Everything before it is v1, the 70-day test."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "config.json")) as f:
            return json.load(f).get("relaunch_date", "2026-11-26")
    except Exception:
        return "2026-11-26"

# Below this many posts in the smaller arm, a median is mostly noise. Stated
# once here so the printer and the page carry the same warning.
CALL_IT_AT = 15


def _dt(raw: str) -> datetime:
    """Parse a stored timestamp, assuming UTC when it carries no zone.

    Everything this pipeline writes is timezone-aware, but the two columns
    being subtracted here come from different writers, and one naive row
    anywhere turns an arithmetic error into a crashed dashboard.
    """
    t = datetime.fromisoformat(raw)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


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
    return _dt(row["t"])


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


def cumulative(conn: sqlite3.Connection, since: Optional[str] = None,
               until: Optional[str] = None) -> List[Dict[str, Any]]:
    """Running total of reach and views, in publication order.

    Built from the same rows and the same per-post figure that overview()
    sums, so the last point on the chart equals the KPI above it. A chart
    that disagrees with the number beside it is worse than no chart.

    Lifetime reach per post rather than day-one: this answers "how much reach
    has the account had", which is a total and not a comparison, and day-one
    would under-report it by excluding everything published in the last
    twelve hours.
    """
    out, reach, views = [], 0, 0
    for r in store.published_with_metrics(conn):
        if not r["published_at"]:
            continue
        if (since and r["published_at"] < since) or (until and r["published_at"] >= until):
            continue
        reach += r["reach"] or 0
        views += r["views"] or 0
        out.append({"id": r["id"], "at": r["published_at"],
                    "reach": reach, "views": views})
    return out


def running(conn: sqlite3.Connection) -> Dict[str, Any]:
    """How long the account has been live, from its first published post.

    Counted from the first publish rather than the first commit: the repo
    existed for a while before anything went out, and "day 3" has to mean
    three days of the experiment, not three days of writing the thing that
    runs it.
    """
    row = conn.execute(
        "SELECT MIN(published_at) FROM posts WHERE status='published'"
    ).fetchone()
    if not row or not row[0]:
        return {"days": 0, "since": None}
    since = _dt(row[0])
    return {"days": (datetime.now(timezone.utc) - since).days + 1,
            "since": since}


def watch(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Reel watch time, and whether it predicts anything.

    Deliberately reports the correlation alongside the average, because the
    average on its own invites the wrong conclusion. avg_watch_time is per
    PLAY: a reel pushed to a cold non-follower audience collects fast swipes
    and the mean falls, so a low number can mean wide distribution rather
    than a bad reel. Measured over the first 21 reels the correlation with
    reach was -0.39 — the opposite sign to the one you would assume.

    So this is here to be watched over time, not to be optimised yet.
    """
    rows = [r for r in store.published_with_metrics(conn)
            if (r["format"] or "") == "reel" and r["avg_watch_ms"] is not None]
    if not rows:
        return {"n": 0}
    w = [r["avg_watch_ms"] / 1000.0 for r in rows]
    reach = [r["reach"] or 0 for r in rows]
    n = len(rows)
    mean_w = sum(w) / n
    mean_r = sum(reach) / n
    num = sum((a - mean_w) * (b - mean_r) for a, b in zip(w, reach))
    den = (sum((a - mean_w) ** 2 for a in w)
           * sum((b - mean_r) ** 2 for b in reach)) ** 0.5
    return {"n": n, "mean": mean_w, "median": sorted(w)[n // 2],
            "corr_reach": (num / den) if den else None}


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


DAY_ONE_H = 24     # the age every post is compared at
MIN_AGE_H = 12     # too young to have a fair number yet


def day_one(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Each post's reach at the same age, rather than whatever it is now.

    Reach accrues for days, so a lifetime number compares a post published
    this morning against one published last week and calls the difference
    performance. Everything that ranks formats or trends runs off this
    instead: the last capture taken before the post turned 24 hours old.
    Posts younger than 12 hours have no fair number yet and are left out —
    they come back into the figures tomorrow.
    """
    hist = {}  # type: Dict[str, List[sqlite3.Row]]
    for m in conn.execute("SELECT * FROM metrics ORDER BY captured_at"):
        hist.setdefault(m["ig_media_id"], []).append(m)

    out = []
    for r in store.published_with_metrics(conn):
        if not r["published_at"]:
            continue
        pub = _dt(r["published_at"])
        pick = None
        for m in hist.get(r["ig_media_id"], []):
            age = (_dt(m["captured_at"]) - pub
                   ).total_seconds() / 3600.0
            if MIN_AGE_H <= age <= DAY_ONE_H:
                pick = m
        if pick is None:
            continue
        out.append({"id": r["id"], "format": r["format"] or "carousel",
                    "style": (r["style"] if "style" in r.keys() else None) or "card",
                    "watch_ms": pick["avg_watch_ms"] if "avg_watch_ms" in pick.keys() else None,
                    "reach": pick["reach"] or 0, "views": pick["views"] or 0,
                    "shares": pick["shares"] or 0, "likes": pick["likes"] or 0,
                    "published_at": r["published_at"]})
    return out


STYLES = ("card", "tweet", "chat", "notify", "comments")


def by_style(conn: sqlite3.Connection,
             since: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Card vs tweet vs chat, reels only, day-one numbers, since styles began.

    Same discipline as compare(): day-one reach so ages match, medians because
    reach is right-skewed, and shares per reach as the number that compounds.
    Watch time is in here because it is the thing the new styles exist to fix
    — the card's median was 3.4s.
    """
    since = since or relaunch()
    rows = [r for r in day_one(conn)
            if r["format"] == "reel" and (r["published_at"] or "") >= since]
    out = {}
    for s in STYLES:
        g = [r for r in rows if r["style"] == s]
        reach = sum(r["reach"] for r in g)
        out[s] = {"n": len(g),
                  "reach": median([r["reach"] for r in g]),
                  "watch_s": median([r["watch_ms"] / 1000.0 for r in g
                                     if r.get("watch_ms") is not None]),
                  "shares_per_1k": (1000.0 * sum(r["shares"] for r in g) / reach)
                  if reach else None,
                  "likes_per_1k": (1000.0 * sum(r["likes"] for r in g) / reach)
                  if reach else None}
    return out


def _segment(rows: List[sqlite3.Row], first: List[Dict[str, Any]],
             start: Optional[datetime], end: datetime) -> Dict[str, Any]:
    """Every number the two versions are compared on, for one version."""
    n = len(rows)
    days = max(1, (end - start).days + 1) if start else 0
    d1 = [r["reach"] for r in first]
    d1_reach = sum(d1)
    reels = [r for r in rows if (r["format"] or "carousel") == "reel"]
    watch = [r["avg_watch_ms"] / 1000.0 for r in reels if r["avg_watch_ms"] is not None]
    tot = dict((k, sum((r[k] or 0) for r in rows)) for k in COUNTED)
    best = max(first, key=lambda r: r["reach"]) if first else None
    return {
        "posts": n, "days": days, "per_day": (n / float(days)) if days else None,
        "start": start, "end": end,
        "reels": len(reels), "carousels": n - len(reels),
        "with_day_one": len(first),
        "day1_median": median(d1), "day1_mean": (d1_reach / float(len(d1))) if d1 else None,
        "best": (best["id"], best["reach"]) if best else None,
        "watch_median": median(watch), "watch_mean": (sum(watch) / len(watch)) if watch else None,
        "reach": tot["reach"], "views": tot["views"], "shares": tot["shares"],
        "saved": tot["saved"], "likes": tot["likes"], "comments": tot["comments"],
        "shares_per_post": (tot["shares"] / float(n)) if n else None,
        "saves_per_post": (tot["saved"] / float(n)) if n else None,
        "likes_per_1k": (1000.0 * sum(r["likes"] for r in first) / d1_reach) if d1_reach else None,
        "shares_per_1k": (1000.0 * sum(r["shares"] for r in first) / d1_reach) if d1_reach else None,
    }


def phases(conn: sqlite3.Connection, split: Optional[str] = None) -> Dict[str, Any]:
    """v1 (the 70-day test) against v2 (since the relaunch), on the same numbers.

    First-day figures are the fair comparison — a v2 post a day old cannot be
    compared with a v1 post's two-month lifetime total — so medians of day-one
    reach lead, and lifetime totals are shown for scale only. Followers come
    from the account table, which only exists from the relaunch onwards.
    """
    split = split or relaunch()
    now = datetime.now(timezone.utc)
    rows = [r for r in store.published_with_metrics(conn) if r["published_at"]]
    first = day_one(conn)
    v1 = [r for r in rows if r["published_at"] < split]
    v2 = [r for r in rows if r["published_at"] >= split]
    s_dt = _dt(split + "T00:00:00+00:00")
    v1_end = _dt(v1[-1]["published_at"]) if v1 else s_dt
    out = {
        "split": split,
        "v1": _segment(v1, [r for r in first if r["published_at"] < split],
                       _dt(v1[0]["published_at"]) if v1 else None, v1_end),
        "v2": _segment(v2, [r for r in first if r["published_at"] >= split],
                       s_dt, now),
        "gap_days": max(0, (s_dt - v1_end).days) if v1 else 0,
    }
    acc = store.account_history(conn) if hasattr(store, "account_history") else []
    out["followers"] = {"first": (acc[0]["captured_at"], acc[0]["followers"]) if acc else None,
                        "last": (acc[-1]["captured_at"], acc[-1]["followers"]) if acc else None}
    return out


def trend(conn: sqlite3.Connection, n: int = 5) -> Dict[str, Any]:
    """Day-one reach for the last n posts against the n before them.

    Two medians rather than a line: with a dozen posts a chart of reach over
    time is mostly the difference between a Tuesday and a Saturday. The
    question worth answering on this much data is whether the recent stretch
    is doing better than the one before it.
    """
    xs = [r["reach"] for r in day_one(conn)]
    if len(xs) < 2 * n:
        return {"now": median(xs), "before": None, "n": n, "have": len(xs)}
    return {"now": median(xs[-n:]), "before": median(xs[-2 * n:-n]),
            "n": n, "have": len(xs)}


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

    # Reach and views are compared at 24 hours old; the rates are lifetime,
    # because a ratio of two numbers from the same capture is already fair.
    one = day_one(conn)
    d1 = dict((k, [r for r in one if r["format"] == k]) for k in ARMS)

    lines = []
    for label, va, vb, pct in (
            ("reach at 24h", _vals(d1["carousel"], "reach"),
             _vals(d1["reel"], "reach"), False),
            ("views at 24h", _vals(d1["carousel"], "views"),
             _vals(d1["reel"], "views"), False),
            ("shares / reach", _rates(a, "shares"), _rates(b, "shares"), True),
            ("saves / reach", _rates(a, "saved"), _rates(b, "saved"), True),
            ("likes / reach", _rates(a, "likes"), _rates(b, "likes"), True)):
        ma, mb = median(va), median(vb)
        lines.append({"label": label, "carousel": ma, "reel": mb, "pct": pct,
                      "ratio": (mb / ma) if (ma and mb) else None})

    ci = bootstrap(_vals(d1["carousel"], "reach"), _vals(d1["reel"], "reach"))
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

    reach = lines[0]
    if ci is None:
        headline = "Not enough posts yet to say which format reaches further."
    elif ci[0] > 1.0:
        headline = "Reels reach %.1f\u00d7 further than carousels." % reach["ratio"]
    elif ci[1] < 1.0:
        headline = ("Carousels reach %.1f\u00d7 further than reels."
                    % (1.0 / reach["ratio"]))
    else:
        headline = "No clear difference between reels and carousels yet."

    smallest = min(len(d1["carousel"]), len(d1["reel"]))
    caveat = None
    if one and smallest < CALL_IT_AT:
        caveat = ("Only %d in the smaller arm old enough to count. Medians "
                  "move a lot at this size — read it as a shape, not a "
                  "result. Look again at %d each, decide at %d."
                  % (smallest, CALL_IT_AT, CALL_IT_AT * 2))

    return {"n": dict((k, len(v)) for k, v in d1.items()),
            "rows": arm, "lines": lines, "ci": ci, "headline": headline,
            "verdict": verdict, "caveat": caveat}
