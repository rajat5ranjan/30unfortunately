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
from typing import Any, Dict, List, Optional

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
.weeks .bars i.reel{background:#2E7D4F}
.weeks .bars i.car{background:#C9A227}
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
    kpis = ('<div class="kpis">%s%s%s%s%s%s</div>'
            % (kpi("{:,}".format(o["reach"]), "total reach",
                   "people, all time"),
               kpi("{:,}".format(o["views"]), "total views",
                   "%.1f per person reached" % (o["views"] / float(o["reach"]))
                   if o["reach"] else ""),
               kpi(_n(t["now"]), "median first-day reach", note, cls),
               kpi(_n(sends), "sends per post", "%d in total" % o["shares"]),
               kpi(published, "published", "%d queued" % counts.get("queued", 0)),
               kpi(best["reach"] if best else "\u2014", "best post",
                   "%s, a %s" % (best["id"], best["format"]) if best else "")))

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
                    "%d posts have been saved." % o["saved"]))

    run = metrics.running(conn)
    day = ('<p class="day">Day <b>%d</b> &middot; started %s &middot; '
           '%d published, %.1f a day</p>'
           % (run["days"], run["since"].strftime("%-d %B %Y"), published,
              published / float(run["days"]))) if run["since"] else ""

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

    return ('<section class="dash">%s%s%s'
            '<h2>Reach and views, running total</h2>%s'
            '<h2>Which format travels</h2>'
            '<p class="headline">%s</p>%s<p class="caveat">%s</p>'
            '<h2>Weekly average reach, by format</h2>%s'
            '<h2>Per post</h2><p class="caveat">%s</p>'
            '<h2>How long reels are watched</h2>'
            '<p class="caveat">%s</p>'
            '<h2>Today&rsquo;s candidates</h2>%s'
            '</section>'
            % (kpis, day, when, totals_over_time(metrics.cumulative(conn)),
               escape(c["headline"]), versus(c), escape(footnote),
               weekly_bars(one), escape(insight), escape(watch_note),
               todays_candidates()))
