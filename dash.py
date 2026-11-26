#!/usr/bin/env python3
"""dash.py — the dashboard at the top of the Pages site.

The contact sheet answers "what have we made"; this answers "is any of it
working". Everything here is a div with a width on it — no chart library, no
script tag, nothing to load. The page is served from a public repo and read on
a phone, so the whole dashboard costs about 4KB and renders with the CSS.

Numbers come from metrics.py, which is also what `publish.py ab` prints. A
chart that disagrees with the terminal is worse than no chart.
"""
import glob
import json
import os
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Dict, List, Optional, Tuple

import metrics
import store

ROOT = os.path.dirname(os.path.abspath(__file__))
CANDIDATE_DIR = os.path.join(ROOT, "content", "candidates")

# Metrics only refresh when the publish job finds an open window, so a few
# hours of age is normal and half a day is not. The page says which.
STALE_HOURS = 14

CSS = """
.dash{background:#FBF9F4;border-radius:14px;padding:24px 26px 28px;margin:0 0 34px;
box-shadow:0 3px 12px rgba(21,20,15,.1);max-width:820px}
.dash h2{font-size:12px;letter-spacing:1.3px;text-transform:uppercase;color:#9A9384;
margin:30px 0 12px;font-weight:700}
.dash h2:first-child{margin-top:0}
.era{display:inline-block;font:700 11px/1 'Bricolage Grotesque',system-ui,sans-serif;
letter-spacing:.8px;text-transform:uppercase;padding:7px 11px;border-radius:999px;
background:#D8451F;color:#FBF9F4;margin:0 0 12px}
.era.old{background:#15140F}
.lede{font-size:15px;line-height:1.5;margin:0 0 6px;color:#15140F}
.changes{margin:8px 0 0;padding-left:18px;font-size:13px;line-height:1.55;color:#3E3A31}
.cmp{width:100%;border-collapse:collapse;font-size:13px}
.cmp th{font-size:10.5px;letter-spacing:.6px;text-transform:uppercase;color:#9A9384;
text-align:right;padding:0 0 8px;font-weight:700}
.cmp th:first-child{text-align:left}
.cmp td{padding:8px 0;border-top:1px solid #ECE7DB;text-align:right;
font-variant-numeric:tabular-nums}
.cmp td:first-child{text-align:left;color:#3E3A31}
.cmp td.v2{font-weight:700;color:#15140F}
.cmp td small{display:block;font-size:10.5px;color:#9A9384;font-weight:400}
.cmp tr.lead td{font-size:15px}
.dash.archive{background:#F6F3EC}
details.learn{margin-top:26px;border-top:1px solid #ECE7DB;padding-top:14px}
details.learn summary{cursor:pointer;font-size:12px;letter-spacing:1.1px;
text-transform:uppercase;color:#6E675A;font-weight:700}
.cmp.lt td{text-align:left;vertical-align:top;padding-right:12px;font-size:12.5px;line-height:1.45}
.cmp.lt th{text-align:left}
.chart2 .bar.reel{fill:#2E7D4F}.chart2 .bar.car{fill:#C9A227}
.chart2 .base{stroke:#15140F;stroke-width:1.2;stroke-dasharray:4 4}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.kpi{background:#F2EEE4;border-radius:10px;padding:13px 14px 12px}
.kpi b{display:block;font-size:30px;line-height:1;letter-spacing:-1.5px}
.kpi span{display:block;font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;
color:#6E675A;margin-top:5px}
.kpi em{display:block;font-style:normal;font-size:11px;color:#9A9384;margin-top:6px}
.kpi em.up{color:#2E7D4F}
.kpi em.down{color:#D8451F}
.day{font-size:11.5px;letter-spacing:.4px;color:#6E675A;margin:14px 0 0}
.day b{color:#15140F;letter-spacing:0}
/* Series colours. Blue and violet are reach and views; the green and gold
   below them are reel and carousel. Four colours on one card, checked as a
   set against this background for colour-blind and normal-vision separation
   rather than picked by eye. */
.dash{--s1:#2a78d6;--s2:#4a3aa7}
.chart2{width:100%;height:190px;display:block}
.chart2 .grid line{stroke:#DED8CA;stroke-width:1}
.chart2 .ylab{font-size:9.5px;fill:#9A9384;text-anchor:end}
.chart2 .ln{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.chart2 .ln.s1{stroke:var(--s1)}
.chart2 .ln.s2{stroke:var(--s2)}
.chart2 .hit{fill:transparent}
.chart2 .end{stroke:#FBF9F4;stroke-width:2}
.chart2 .end.s1{fill:var(--s1)}
.chart2 .end.s2{fill:var(--s2)}
.chart2 .dlab{font-size:10.5px;font-weight:700}
.chart2 .dlab.s1{fill:var(--s1)}
.chart2 .dlab.s2{fill:var(--s2)}
.axis i.sw.s1{background:var(--s1)}
.axis i.sw.s2{background:var(--s2)}
.axis{display:flex;justify-content:space-between;font-size:10.5px;color:#9A9384;
margin:5px 0 0}
.axis i.sw{display:inline-block;width:9px;height:9px;border-radius:2px;
margin-right:5px;vertical-align:-1px}
.weeks{display:flex;gap:10px;align-items:flex-end;height:120px;margin-top:4px}
.weeks .wk{flex:1;display:flex;flex-direction:column;justify-content:flex-end;
height:100%}
.weeks .bars{display:flex;gap:3px;align-items:flex-end;height:100%}
.weeks .bars i{flex:1;border-radius:3px 3px 0 0;min-height:2px}
.weeks .bars i.reel,.axis i.sw.reel{background:#2E7D4F}
.weeks .bars i.car,.axis i.sw.car{background:#C9A227}
.weeks .bars i.none{background:#E6E1D5;height:2px}
.weeks .wk span{font-size:10px;color:#9A9384;text-align:center;margin-top:6px}
.when{font-size:11.5px;color:#9A9384;margin:12px 0 0}
.when.stale{color:#D8451F}
.headline{font-size:17px;line-height:1.35;letter-spacing:-.3px;margin:0 0 14px}
.vs{max-width:520px}
.vsrow{display:grid;grid-template-columns:66px 1fr 34px;gap:9px;align-items:center;
margin-bottom:7px}
.vsrow span{font-size:11.5px;color:#6E675A}
.track{height:15px;background:#EDE8DC;border-radius:3px;overflow:hidden}
.track i{display:block;height:100%;border-radius:3px}
.track i.car{background:#15140F}
.track i.reel{background:#D8451F}
.vsrow b{font-size:12px;text-align:right}
.caveat{font-size:11.5px;color:#6E675A;margin:12px 0 0;line-height:1.55;
max-width:560px}
.posts{max-width:560px}
.prow{display:grid;grid-template-columns:44px 1fr 30px;gap:9px;align-items:center;
margin-bottom:5px}
.prow span{font-size:11.5px;color:#15140F;font-weight:700}
.prow b{font-size:11.5px;text-align:right;color:#6E675A;font-weight:400}
.prow .track{height:11px}
.cands{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.cand{background:#F2EEE4;border-radius:9px;padding:11px 12px;font-size:12px;
line-height:1.4}
.cand .h{display:block;color:#15140F;margin-bottom:5px}
.cand .m{font-size:10px;letter-spacing:.5px;text-transform:uppercase;color:#9A9384}
.none{font-size:12.5px;color:#6E675A;margin:0}
@media(max-width:560px){.dash{padding:16px 14px 20px}
.kpis{grid-template-columns:repeat(2,1fr)}.kpi b{font-size:26px}
.headline{font-size:15.5px}.vsrow{grid-template-columns:52px 1fr 30px}
/* The weekly chart grows a column a week forever. On a phone the labels are
   the first thing that stops fitting, so they go at 7 weeks' width and the
   bars — which carry the shape — keep the room. */
.kpis{grid-template-columns:repeat(2,1fr)}
.chart2{height:168px}
.weeks{gap:5px;height:96px}.weeks .bars{gap:2px}
.weeks .wk span{font-size:0}
.weeks .wk:first-child span,.weeks .wk:last-child span{font-size:9.5px}
}
"""


def _n(v, dash="\u2014") -> str:
    return dash if v is None else ("%.1f" % v).rstrip("0").rstrip(".")


def kpi(value, label, note="", cls="") -> str:
    return ('<div class="kpi"><b>%s</b><span>%s</span>%s</div>'
            % (value, escape(label),
               '<em class="%s">%s</em>' % (cls, escape(note)) if note else ""))


def versus(cmp_: Dict[str, Any]) -> str:
    """One comparison, not five. The rest is a sentence underneath.

    The first chart put reach, views and three rates on equal footing as five
    pairs of bars, which is a table pretending to be a picture. Only one row
    ever decided anything, so only that row is drawn.
    """
    reach = cmp_["lines"][0]
    top = max(reach["carousel"] or 0, reach["reel"] or 0, 1)
    rows = []
    for arm, kind in (("carousel", "car"), ("reel", "reel")):
        v = reach[arm] or 0
        rows.append('<div class="vsrow"><span>%s (%d)</span>'
                    '<div class="track"><i class="%s" style="width:%.1f%%"></i>'
                    '</div><b>%s</b></div>'
                    % (arm, cmp_["n"][arm], kind, 100.0 * v / top, _n(v)))
    return '<div class="vs">%s</div>' % "".join(rows)


# ------------------------------------------------------------- v1 vs v2
def _pct(v) -> str:
    return "\u2014" if v is None else "%.1f" % v


def comparison(ph: Dict[str, Any]) -> str:
    """The two versions side by side, first-day numbers leading."""
    a, b = ph["v1"], ph["v2"]
    wait = ("" if b["with_day_one"] else
            "<small>from 12h after a post</small>")

    def row(label, va, vb, note="", lead=False):
        return ('<tr%s><td>%s%s</td><td>%s</td><td class="v2">%s</td></tr>'
                % (' class="lead"' if lead else "", escape(label),
                   "<small>%s</small>" % escape(note) if note else "", va, vb))
    best = lambda x: ("%s &middot; %d" % x["best"]) if x["best"] else "\u2014"
    fol = ph["followers"]
    rows = [
        row("Median first-day reach", _n(a["day1_median"]), _n(b["day1_median"]) + wait,
            "people per post, at 24h — the fair comparison", lead=True),
        row("Average first-day reach", _n(a["day1_mean"]), _n(b["day1_mean"])),
        row("Best post, first day", best(a), best(b)),
        row("Median watch time (reels)", "%ss" % _n(a["watch_median"]),
            ("%ss" % _n(b["watch_median"])) if b["watch_median"] is not None else "\u2014",
            "of a ~20s reel"),
        row("Shares per 1,000 reached", _pct(a["shares_per_1k"]), _pct(b["shares_per_1k"]),
            "the number that compounds"),
        row("Likes per 1,000 reached", _pct(a["likes_per_1k"]), _pct(b["likes_per_1k"])),
        row("Shares per post", "%.2f" % (a["shares_per_post"] or 0),
            "%.2f" % (b["shares_per_post"] or 0) if b["posts"] else "\u2014"),
        row("Saves per post", "%.2f" % (a["saves_per_post"] or 0),
            "%.2f" % (b["saves_per_post"] or 0) if b["posts"] else "\u2014"),
        row("Posts", "%d in %d days" % (a["posts"], a["days"]),
            "%d in %d day%s" % (b["posts"], b["days"], "" if b["days"] == 1 else "s")),
        row("Format", "%d reels, %d carousels" % (a["reels"], a["carousels"]),
            "%d reels" % b["reels"]),
        row("Reach / views, lifetime", "{:,} / {:,}".format(a["reach"], a["views"]),
            "{:,} / {:,}".format(b["reach"], b["views"]),
            "totals for scale; v2 posts are younger"),
        row("Followers", "not tracked",
            ("%s" % fol["last"][1]) + ((" <small>%+d since %s</small>"
                                         % (fol["last"][1] - fol["first"][1],
                                            fol["first"][0][:10]))
                                        if fol["first"] and fol["last"][1] is not None
                                        and fol["first"][1] is not None
                                        and fol["first"][0] != fol["last"][0] else "")
            if fol["last"] and fol["last"][1] is not None else "from the next metrics pull"),
    ]
    return ('<table class="cmp"><tr><th></th><th>v1 &middot; the test</th>'
            '<th>v2 &middot; since %s</th></tr>%s</table>'
            % (datetime.strptime(ph["split"], "%Y-%m-%d").strftime("%-d %b"), "".join(rows)))


LEARNINGS = [
    ("Carousels reached 2.8&times; less than reels.", "Reels only."),
    ("A reel was watched for 3.4s of ~20: the text card was not stopping the scroll.",
     "Five reel styles — tweet, family chat, lock-screen notifications, comment "
     "section, text card. The judge scores how well each joke fits each shape and "
     "the best fit ships."),
    ("8 shares in 92 posts: the jokes were sharp and true, not funny enough to send.",
     "Funny first, satire second. The judge weighs the laugh most and marks down "
     "anything bitter."),
    ("The topics were money, family and the body, every time.",
     "Fresh topics on top: the stock market, big-company drama, city traffic — "
     "from the last 72 hours of news."),
    ("The first chat reel cut its punchline and had no sign-off.",
     "Every reel lands its ending, then the 30 sign-off."),
    ("Posting stopped on 1 Nov and reach went to zero within days.",
     "The queue refills itself; no reach without posts."),
]


def learnings() -> str:
    return ('<details class="learn"><summary>Learnings from the test, and what v2 '
            'changed</summary><table class="cmp lt"><tr><th>v1 taught</th>'
            '<th>so v2</th></tr>%s</table></details>'
            % "".join('<tr><td>%s</td><td class="v2">%s</td></tr>' % (a, b)
                      for a, b in LEARNINGS))


def _vs(now, then, higher_is_better=True) -> Tuple[str, str]:
    """The note under a v2 tile: the v1 figure, coloured by which side wins."""
    if now is None or then is None:
        return "", ""
    better = now > then if higher_is_better else now < then
    return ("up" if better else ("down" if now != then else ""),
            "▲" if now > then else ("▼" if now < then else "="))


def post_bars(rows: List[Dict[str, Any]], baseline: Optional[float],
              w: int = 760, h: int = 170) -> str:
    """First-day reach per v2 post, against the v1 median as a dashed line."""
    if not rows:
        return ('<p class="caveat">A bar per post appears once it is 12 hours old. '
                'The dashed line will be v1&rsquo;s median, %s.</p>' % _n(baseline))
    top = _nice(max([r["reach"] for r in rows] + [baseline or 0, 1]))
    padl, padb, padt = 34, 22, 8
    iw, ih = w - padl - 6, h - padb - padt
    bw = min(46.0, iw / float(len(rows)) * 0.7)
    step = iw / float(len(rows))
    out = ['<svg class="chart2" viewBox="0 0 %d %d" role="img" '
           'aria-label="first-day reach per post">' % (w, h)]
    for k in range(3):
        v = top * k / 2.0
        y = padt + ih - ih * v / top
        out.append('<g class="grid"><line x1="%d" x2="%d" y1="%.1f" y2="%.1f"/></g>'
                   '<text class="ylab" x="%d" y="%.1f">%s</text>'
                   % (padl, w - 6, y, y, padl - 6, y + 3, _n(v)))
    for i, r in enumerate(rows):
        x = padl + step * i + (step - bw) / 2
        bh = ih * r["reach"] / float(top)
        cls = "reel" if baseline is None or r["reach"] >= baseline else "car"
        out.append('<rect class="bar %s" x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                   'rx="3"><title>%s · %s · %d reached</title></rect>'
                   '<text class="ylab" x="%.1f" y="%d" style="text-anchor:middle">%s</text>'
                   % (cls, x, padt + ih - bh, bw, max(bh, 1), r["id"], r.get("style", ""),
                      r["reach"], x + bw / 2, h - 6, r["id"]))
    if baseline:
        y = padt + ih - ih * baseline / top
        out.append('<line class="base" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>'
                   '<text class="ylab" x="%d" y="%.1f" style="text-anchor:end">'
                   'v1 median %s</text>' % (padl, w - 6, y, y, w - 8, y - 4, _n(baseline)))
    out.append("</svg>")
    return "".join(out)


def _when(o) -> str:
    stale = metrics.hours_since(o["captured"]) > STALE_HOURS
    if o["captured"] is None:
        return '<p class="when stale">Metrics have never been pulled.</p>'
    return ('<p class="when%s">Metrics pulled %s, %s%s</p>'
            % (" stale" if stale else "", o["captured"].strftime("%-d %b %H:%M UTC"),
               metrics.ago(o["captured"]), ". Older than it should be." if stale else ""))


def overview_tab(conn) -> str:
    """v2 at a glance: six numbers against v1, three charts, learnings folded."""
    ph = metrics.phases(conn)
    a, b = ph["v1"], ph["v2"]
    day = max(1, (datetime.now(timezone.utc).date()
                  - datetime.strptime(ph["split"], "%Y-%m-%d").date()).days + 1)
    fol = ph["followers"]
    f_now = fol["last"][1] if fol["last"] else None
    f_gain = (f_now - fol["first"][1]) if (fol["first"] and f_now is not None
                                           and fol["first"][1] is not None) else None

    def tile(value, label, then_txt, now_v=None, then_v=None, hib=True):
        cls, arrow = _vs(now_v, then_v, hib)
        return kpi(value, label, ("%s v1 %s" % (arrow, then_txt)).strip(), cls)
    tiles = ('<div class="kpis">%s%s%s%s%s%s</div>' % (
        tile(_n(b["day1_median"]), "median first-day reach", _n(a["day1_median"]),
             b["day1_median"], a["day1_median"]),
        tile(("%ss" % _n(b["watch_median"])) if b["watch_median"] is not None else "\u2014",
             "median watch", "%ss" % _n(a["watch_median"]),
             b["watch_median"], a["watch_median"]),
        tile(_pct(b["shares_per_1k"]), "shares per 1k reached", _pct(a["shares_per_1k"]),
             b["shares_per_1k"], a["shares_per_1k"]),
        tile(b["posts"], "posts", "%.1f a day" % (a["per_day"] or 0)),
        tile("{:,}".format(b["reach"]), "people reached",
             "{:,} in %d days".format(a["reach"]) % a["days"]),
        kpi(f_now if f_now is not None else "\u2014", "followers",
            ("%+d since %s" % (f_gain, fol["first"][0][5:10])) if f_gain is not None
            else "tracked from the next pull", "up" if (f_gain or 0) > 0 else "")))
    first = [r for r in metrics.day_one(conn) if r["published_at"] >= ph["split"]]
    o = metrics.overview(conn)
    return ('<section class="dash"><span class="era">v2 &middot; day %d</span>'
            '<p class="lede">New start on %s. Every number is v2; the small line '
            'under it is what the 70-day test did.</p>%s%s'
            '<h2>First-day reach, per post</h2>%s'
            '<h2>Reach and views, running total (v2)</h2>%s'
            '<h2>Which reel style travels</h2>%s'
            '%s</section>'
            % (day, datetime.strptime(ph["split"], "%Y-%m-%d").strftime("%-d %b %Y"),
               tiles, _when(o), post_bars(first, a["day1_median"]),
               running_v2(metrics.cumulative(conn, since=ph["split"])),
               styles(conn), learnings()))


def running_v2(cum: List[Dict[str, Any]]) -> str:
    if not cum or not cum[-1]["views"]:
        return ('<p class="caveat">The line starts with the first metrics pull after '
                'a v2 post is published.</p>')
    return totals_over_time(cum)


def compare_tab(conn) -> str:
    ph = metrics.phases(conn)
    return ('<section class="dash"><h2>v1 vs v2, side by side</h2>%s'
            '<p class="caveat">First-day numbers are the fair comparison: a v2 post a '
            'day old cannot be set against a v1 post&rsquo;s two-month total. Read it '
            'properly at ~30 v2 posts; before that one good post moves everything.</p>'
            '%s</section>' % (comparison(ph), learnings()))


v2_block = overview_tab   # the name the tests and older callers use


def v1_summary(ph: Dict[str, Any]) -> str:
    """What the 70-day test gave, in sentences, from the same numbers."""
    a = ph["v1"]
    if not a["posts"] or not a["start"]:
        return '<p class="lede">Nothing was published before the relaunch.</p>'
    return ('<p class="lede">%d posts from %s to %s (%.1f a day), then nothing for '
            '%d days. They reached <b>{:,}</b> people and were played <b>{:,}</b> '
            'times — a median of <b>%s</b> people per post on day one. %s, but '
            'a reel was '
            'watched for %ss of ~20 on average and the whole run earned %d shares '
            'and %d save%s. When posting stopped, reach went to zero.</p>'
            '<p class="caveat">What it taught: the formats were not stopping the '
            'scroll, the jokes leaned sharp rather than funny, and with no new '
            'posts there is no reach at all. v2 is built on those three.</p>'
            % (a["posts"], a["start"].strftime("%-d %b"), a["end"].strftime("%-d %b"),
               a["per_day"] or 0, ph["gap_days"], _n(a["day1_median"]),
               escape(ph.get("ab_headline") or "reels reached further than carousels").capitalize(),
               _n(a["watch_mean"]), a["shares"], a["saved"],
               "" if a["saved"] == 1 else "s")
            ).format(a["reach"], a["views"])


STYLE_MIN = 5   # per style, before a median means anything


def styles(conn) -> str:
    """Card vs tweet vs chat — the question the next month of posting answers.

    Day-one numbers from metrics.by_style, which only counts reels published
    since the styles began on 26 Nov. Drawn as the same bars as the format
    comparison above, and labelled as too early until every style has
    STYLE_MIN posts, because two posts per style is a coin toss.
    """
    got = metrics.by_style(conn)
    top = max([g["reach"] or 0 for g in got.values()] + [1])
    rows = []
    for name in ("chat", "tweet", "notify", "comments", "card"):
        g = got.get(name) or {"n": 0, "reach": None, "watch_s": None,
                              "shares_per_1k": None}
        rows.append('<div class="vsrow"><span>%s (%d)</span>'
                    '<div class="track"><i class="reel" style="width:%.1f%%"></i>'
                    '</div><b>%s</b></div>'
                    % (name, g["n"], 100.0 * (g["reach"] or 0) / top, _n(g["reach"])))
    have = min(g["n"] for g in got.values()) if got else 0
    total = sum(g["n"] for g in got.values()) if got else 0
    watch = ", ".join("%s %ss" % (k, _n(got[k]["watch_s"]))
                      for k in ("chat", "tweet", "notify", "comments", "card")
                      if got.get(k) and got[k]["watch_s"] is not None)
    if total == 0:
        note = ("Nothing published in the new styles yet. Every reel now goes "
                "out as a chat, a tweet, notifications, comments or a text card; the "
                "bars fill in from the first one.")
    elif have < STYLE_MIN:
        note = ("Too early to call: %d published so far, and a style needs %d "
                "before its median means anything.%s"
                % (total, STYLE_MIN, " Median watch time: %s." % watch if watch else ""))
    else:
        best = max(got, key=lambda k: got[k]["reach"] or 0)
        note = ("%s reels reach furthest so far.%s"
                % (best.capitalize(), " Median watch time: %s." % watch if watch else ""))
    return ('<div class="vs">%s</div><p class="caveat">%s Median day-one reach '
            'per style, since 26 Nov.</p>' % ("".join(rows), escape(note)))


def _nice(n: int) -> int:
    """A round number at or above n, for the top of an axis."""
    if n <= 10:
        return 10
    import math
    mag = 10 ** int(math.log10(n))
    for mult in (1, 2, 2.5, 5, 10):
        if mult * mag >= n:
            return int(mult * mag)
    return int(10 * mag)


def totals_over_time(cum: List[Dict[str, Any]], w: int = 760,
                     h: int = 190) -> str:
    """Running total of reach and views since the first post.

    Two series on ONE axis. They are the same unit and within 1.5x of each
    other, so a shared scale is honest; a second y-axis would let the lines
    be dragged into any relationship you wanted, which is why there is never
    one here.

    Every value is readable without hovering: the axis is labelled, both series
    carry their final figure as a direct label, and `publish.py posts` is the
    table view for the per-post numbers. The tooltips add the post id; they do
    not gate anything.

    Views sits above reach by construction — reach counts people, views counts
    plays, and one person can play a reel twice — so the gap between the lines
    is repeat viewing and is worth being able to see.

    Blue and violet, not the green and gold of the format chart below: those
    two mean reel and carousel on this page, and a colour has to keep meaning
    the same thing down the whole page. The pair was checked rather than
    chosen by eye — all four colours on this page clear the colour-blind and
    normal-vision separation floors against the card background as a set.
    """
    if len(cum) < 2:
        return '<p class="caveat">Not enough published posts to draw a line yet.</p>'
    top = _nice(max(c["views"] for c in cum))
    padl, padb, padt = 46, 18, 10
    iw, ih = w - padl - 8, h - padb - padt

    def xy(i, v):
        return (padl + iw * i / float(len(cum) - 1),
                padt + ih - ih * v / float(top))

    # Hover targets as wide as the spacing allows. A nearest-point layer would
    # be better and needs JavaScript; half the gap between points is what is
    # available without it, capped so a four-post account does not get
    # saucer-sized targets.
    hit = max(6.0, min(12.0, iw / float(len(cum) - 1) / 2.0))

    grid, labels = [], []
    for frac in (0, 0.5, 1.0):
        v = top * frac
        y = padt + ih - ih * frac
        grid.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>'
                    % (padl, y, w - 8, y))
        labels.append('<text x="%d" y="%.1f" class="ylab">%s</text>'
                      % (padl - 8, y + 3.5, "{:,}".format(int(v))))

    out = ['<svg class="chart2" viewBox="0 0 %d %d" role="img" '
           'aria-label="Running total of reach and views since the first post. '
           'Reach ends at %s, views at %s." >'
           % (w, h, "{:,}".format(cum[-1]["reach"]),
              "{:,}".format(cum[-1]["views"])),
           '<g class="grid">%s</g><g>%s</g>' % ("".join(grid), "".join(labels))]

    for key, cls, label in (("views", "s2", "views"), ("reach", "s1", "reach")):
        pts = [xy(i, c[key]) for i, c in enumerate(cum)]
        out.append('<polyline class="ln %s" points="%s"/>'
                   % (cls, " ".join("%.1f,%.1f" % q for q in pts)))
        # Hover without a script tag: a <title> per point is the browser's own
        # tooltip. A crosshair would need JavaScript and this page has none.
        out.append("".join(
            '<circle class="hit %s" cx="%.1f" cy="%.1f" r="%.1f"><title>%s'
            '</title></circle>' % (cls, x, y, hit, "%s · %s %s" % (
                escape(c["id"]), "{:,}".format(c[key]), label))
            for (x, y), c in zip(pts, cum)))
        lx, ly = pts[-1]
        out.append('<circle class="end %s" cx="%.1f" cy="%.1f" r="3.4"/>'
                   % (cls, lx, ly))
        # Direct labels, not just a legend: the validator flags both series
        # as under 3:1 against this background, which obliges visible labels.
        out.append('<text class="dlab %s" x="%.1f" y="%.1f">%s</text>'
                   % (cls, min(lx, w - 54), ly - 9,
                      "%s %s" % ("{:,}".format(cum[-1][key]), label)))
    out.append('</svg>')
    return ("".join(out)
            + '<p class="axis"><span><i class="sw s1"></i>reach — people</span>'
              '<span><i class="sw s2"></i>views — plays</span>'
              '<span>%s → %s</span></p>'
              % (escape(cum[0]["at"][:10]), escape(cum[-1]["at"][:10])))



def weekly_bars(rows: List[Dict[str, Any]]) -> str:
    """Mean first-day reach per week, split by format.

    The per-post chart is noisy by construction — one post reaching 154 makes
    every other bar look flat. Weekly means are what the format decision was
    actually argued from, so the page should show the same shape the argument
    used.
    """
    weeks = {}  # type: Dict[str, Dict[str, List[int]]]
    for r in rows:
        d = datetime.strptime(r["published_at"][:10], "%Y-%m-%d")
        key = (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
        weeks.setdefault(key, {"reel": [], "carousel": []})
        weeks[key][r["format"] if r["format"] == "reel" else "carousel"].append(
            r["reach"])
    if not weeks:
        return ""
    means = {}
    for k, v in weeks.items():
        means[k] = dict((f, (sum(x) / float(len(x))) if x else None)
                        for f, x in v.items())
    top = max([m for w in means.values() for m in w.values() if m] + [1])
    out = []
    for k in sorted(means):
        cells = []
        for f, cls in (("reel", "reel"), ("carousel", "car")):
            m = means[k][f]
            cells.append(
                '<i class="%s" style="height:%.1f%%" title="%s %s: %.0f"></i>'
                % (cls, 100.0 * m / top, k, f, m) if m else
                '<i class="none" title="no %s that week"></i>' % f)
        out.append('<div class="wk"><div class="bars">%s</div><span>%s</span>'
                   '</div>' % ("".join(cells),
                               datetime.strptime(k, "%Y-%m-%d").strftime("%-d %b")))
    return ('<div class="weeks">%s</div>'
            '<p class="axis"><span><i class="sw reel"></i>reel</span>'
            '<span><i class="sw car"></i>carousel</span>'
            '<span>tallest %d</span></p>' % ("".join(out), top))


def todays_candidates(today: Optional[str] = None) -> str:
    """The latest run, but only if it happened today.

    The page is read in the morning to decide whether anything needs vetoing,
    and a card from three days ago answers a question nobody asked.
    """
    today = today or datetime.now(timezone.utc).strftime("%Y%m%d")
    files = sorted(glob.glob(os.path.join(CANDIDATE_DIR, "*.json")))
    if not files:
        return '<p class="none">No candidate runs on record yet.</p>'
    latest = os.path.basename(files[-1])[:8]
    if latest != today:
        when = datetime.strptime(latest, "%Y%m%d").strftime("%-d %b")
        return ('<p class="none">Nothing generated today. The last run was %s '
                '— <a href="candidates.html">see it &rarr;</a></p>' % when)
    acc = json.load(open(files[-1])).get("accepted", [])
    cards = "".join(
        '<div class="cand"><span class="h">%s</span>'
        '<span class="m">%s &middot; satire %s%s</span></div>'
        % (escape(p["slides"][0]), escape(p.get("structure", "")),
           p.get("satire_level", ""),
           " &middot; hinglish" if p.get("hinglish") else "")
        for p in acc)
    return ('<div class="cands">%s</div>'
            '<p class="none" style="margin-top:10px">%d passed the gates today '
            '— <a href="candidates.html">full slides and the buttons &rarr;</a></p>'
            % (cards, len(acc)))




def block(conn) -> str:
    """The whole dashboard: four numbers, one verdict, two lists."""
    o = metrics.overview(conn)
    c = metrics.compare(conn)
    t = metrics.trend(conn)
    one = metrics.day_one(conn)
    counts = o["counts"]
    published = counts.get("published", 0)

    # Day-one reach is the headline because it is the only reach number that
    # means the same thing on every post. The trend beside it compares the
    # last five against the five before, which is the smallest window that is
    # not just one good Saturday.
    if t["before"]:
        delta = t["now"] - t["before"]
        note = "%s%s vs %s before" % ("+" if delta > 0 else "", _n(delta),
                                      _n(t["before"]))
        cls = "up" if delta > 0 else ("down" if delta < 0 else "")
    else:
        note, cls = "%d posts old enough to count" % t["have"], ""

    sends = o["shares"] / float(published) if published else 0
    best = max(one, key=lambda r: r["reach"]) if one else None

    # Six tiles, three across. Totals first because they answer "how big is
    # this" in one glance; the medians under them answer "is it working",
    # which is a different question and used to be the only one on the card.
    # v1 only (2026-11-26): this card is now the record of the test, and an
    # all-time total would quietly fold v2 into it.
    ph = metrics.phases(conn)
    v1 = ph["v1"]
    kpis = ('<div class="kpis">%s%s%s%s%s%s</div>'
            % (kpi("{:,}".format(v1["reach"]), "total reach", "people, v1"),
               kpi("{:,}".format(v1["views"]), "total views",
                   "%.1f per person reached" % (v1["views"] / float(v1["reach"]))
                   if v1["reach"] else ""),
               kpi(_n(v1["day1_median"]), "median first-day reach",
                   "average %s" % _n(v1["day1_mean"])),
               kpi("%.2f" % (v1["shares_per_post"] or 0), "sends per post",
                   "%d in total" % v1["shares"]),
               kpi(v1["posts"], "published", "%d reels, %d carousels"
                   % (v1["reels"], v1["carousels"])),
               kpi(v1["best"][1] if v1["best"] else "\u2014", "best post",
                   v1["best"][0] if v1["best"] else "")))

    stale = metrics.hours_since(o["captured"]) > STALE_HOURS
    if o["captured"] is None:
        when = '<p class="when stale">Metrics have never been pulled.</p>'
    else:
        when = ('<p class="when%s">Metrics pulled %s, %s%s</p>'
                % (" stale" if stale else "",
                   o["captured"].strftime("%-d %b %H:%M UTC"),
                   metrics.ago(o["captured"]),
                   ". Older than it should be." if stale else ""))

    rates = c["lines"][4]
    footnote = ("%s Likes run at %s%% of reach on carousels against %s%% on "
                "reels, and %s" % (
                    c["caveat"] or "",
                    _n((rates["carousel"] or 0) * 100),
                    _n((rates["reel"] or 0) * 100),
                    "nothing has been saved yet." if not o["saved"] else
                    "%d post%s been saved." % (o["saved"],
                                               " has" if o["saved"] == 1 else "s have")))

    run = metrics.running(conn)
    day = ('<p class="day">Ran <b>%s &ndash; %s</b> &middot; %d published in %d '
           'days, %.1f a day &middot; then a %d-day pause</p>'
           % (v1["start"].strftime("%-d %b"), v1["end"].strftime("%-d %b %Y"),
              v1["posts"], v1["days"], v1["per_day"] or 0,
              ph["gap_days"])) if v1["start"] else ""

    w = metrics.watch(conn)
    if w["n"]:
        watch_note = (
            "Reels are watched %.1fs on average (median %.1fs). Correlation "
            "with reach is %+.2f — %s" % (
                w["mean"], w["median"], w["corr_reach"],
                "the reels watched longest reached fewest, because average "
                "watch time is per play and a cold audience swipes. Not a "
                "target yet." if (w["corr_reach"] or 0) < 0 else
                "longer watches go with wider reach."))
    else:
        watch_note = "No reel has reported watch time yet."

    # What the per-post bar list used to say, in one line. The list itself is
    # gone: a column per post is unreadable by post forty and nobody was
    # reading it. Every number behind it is still in posts.db, and
    # `publish.py posts` prints it per post when there is a reason to ask.
    if one:
        spread = sorted(one, key=lambda r: r["reach"])
        insight = ("%d posts have a first-day number. Best %s reached %d, "
                   "worst %s reached %d, median %s."
                   % (len(one), spread[-1]["id"], spread[-1]["reach"],
                      spread[0]["id"], spread[0]["reach"], _n(t["now"])))
    else:
        insight = "No post is old enough for a first-day number yet."

    when = ""   # shown once, at the top
    ph["ab_headline"] = c["headline"].rstrip(".").lower()
    return ('<section class="dash archive"><span class="era old">v1 &middot; the '
            'first 70 days</span>%s%s%s%s'
            '<h2>Reach and views, running total</h2>%s'
            '<h2>Reels vs carousels, before carousels were retired</h2>'
            '<p class="headline">%s</p>%s<p class="caveat">%s</p>'
            '<h2>Weekly average reach, by format</h2>%s'
            '<h2>Per post</h2><p class="caveat">%s</p>'
            '<h2>How long reels are watched</h2>'
            '<p class="caveat">%s</p>'
            '</section>'
            % (v1_summary(ph), kpis, day, when, totals_over_time(metrics.cumulative(conn, until=ph["split"])),
               escape(c["headline"]), versus(c),
               escape(footnote + " Carousels stopped on 26 Nov 2026; every "
                      "post since is a reel."),
               weekly_bars(one), escape(insight), escape(watch_note)))
