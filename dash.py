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
from datetime import datetime, timezone
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
.dash{background:#FBF9F4;border-radius:14px;padding:22px 22px 26px;margin:0 0 30px;
box-shadow:0 3px 12px rgba(21,20,15,.1)}
.dash h2{font-size:13px;letter-spacing:1.2px;text-transform:uppercase;color:#9A9384;
margin:26px 0 12px;font-weight:700}
.dash h2:first-child{margin-top:0}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(88px,1fr));gap:12px}
.stat{background:#F2EEE4;border-radius:10px;padding:11px 12px}
.stat b{display:block;font-size:24px;line-height:1.05;letter-spacing:-1px}
.stat span{display:block;font-size:10.5px;letter-spacing:.6px;text-transform:uppercase;
color:#6E675A;margin-top:3px}
.stat.hot b{color:#D8451F}
.when{font-size:12px;color:#6E675A;margin:14px 0 0}
.when.stale{color:#D8451F;font-weight:700}
.row{display:grid;grid-template-columns:104px 1fr 44px;gap:10px;align-items:center;
margin-bottom:9px}
.lbl{font-size:11px;color:#6E675A;text-align:right;line-height:1.25}
.ratio{font-size:11px;font-weight:700;color:#15140F;text-align:right}
.pair{display:grid;gap:4px}
.b{display:flex;align-items:center;gap:7px}
.track{flex:1;height:13px;background:#EDE8DC;border-radius:3px;overflow:hidden}
.track i{display:block;height:100%;border-radius:3px}
.track i.car{background:#15140F}
.track i.reel{background:#D8451F}
.track i.views{background:#CFC8B8}
.b em{font-style:normal;font-size:11px;color:#3A3629;min-width:52px}
.legend{font-size:11px;color:#6E675A;margin:0 0 14px}
.key{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;
vertical-align:baseline}
.key.car{background:#15140F}.key.reel{background:#D8451F}.key.views{background:#CFC8B8}
.verdict{font-size:12.5px;color:#15140F;margin:14px 0 0;line-height:1.5}
.caveat{font-size:11.5px;color:#6E675A;margin:6px 0 0;line-height:1.5}
.prow{display:grid;grid-template-columns:78px 1fr 84px;gap:10px;align-items:center;
margin-bottom:6px}
.pid{font-size:11px;color:#6E675A}
.pid b{color:#15140F;font-weight:700}
.pnum{font-size:11px;color:#6E675A;text-align:right}
.cands{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
.cand{background:#F2EEE4;border-radius:9px;padding:11px 12px;font-size:12px;
line-height:1.4}
.cand .h{display:block;color:#15140F;margin-bottom:5px}
.cand .m{font-size:10px;letter-spacing:.5px;text-transform:uppercase;color:#9A9384}
.none{font-size:12.5px;color:#6E675A;margin:0}
@media(max-width:520px){.dash{padding:16px 14px 20px}
.row{grid-template-columns:76px 1fr 38px}.lbl{font-size:10px}
.prow{grid-template-columns:62px 1fr 70px}}
"""


def _bar(kind: str, frac: float, value: str) -> str:
    pct = max(0.0, min(1.0, frac)) * 100
    return ('<div class="b"><div class="track"><i class="%s" style="width:%.1f%%">'
            '</i></div><em>%s</em></div>' % (kind, pct, value))


def _fmt(v: Optional[float], pct: bool) -> str:
    if v is None:
        return "—"
    return "%.1f%%" % (v * 100) if pct else "%.0f" % v


def ab_chart(cmp_: Dict[str, Any]) -> str:
    """One row per metric, two bars, scaled against the larger of the pair.

    Scaled per row rather than across the chart because the rows are in
    different units — a percentage and a reach count share no axis.
    """
    rows = []
    for line in cmp_["lines"]:
        a, b = line["carousel"], line["reel"]
        top = max(v for v in (a or 0, b or 0, 1e-9))
        rows.append(
            '<div class="row"><div class="lbl">%s</div><div class="pair">%s%s</div>'
            '<div class="ratio">%s</div></div>'
            % (escape(line["label"]),
               _bar("car", (a or 0) / top, _fmt(a, line["pct"])),
               _bar("reel", (b or 0) / top, _fmt(b, line["pct"])),
               "%.1fx" % line["ratio"] if line["ratio"] else "—"))
    return "".join(rows)


def post_chart(rows) -> str:
    """Every published post, oldest first, reach inside views.

    Two layers on one track rather than two bars: views is always the larger
    of the pair, so reach sitting inside it is the same picture with half the
    ink — and the gap between them is the share of viewers Instagram counted
    more than once.
    """
    top = max([r["views"] or 0 for r in rows] + [1])
    out = []
    for r in rows:
        fmt = r["format"] or "carousel"
        reach, views = r["reach"] or 0, r["views"] or 0
        out.append(
            '<div class="prow"><div class="pid"><b>%s</b> %s</div>'
            '<div class="track" style="position:relative">'
            '<i class="views" style="width:%.1f%%"></i>'
            '<i class="%s" style="width:%.1f%%;position:absolute;top:0;left:0"></i>'
            '</div><div class="pnum">%d / %d</div></div>'
            % (escape(r["id"]), escape(fmt), 100.0 * views / top,
               "reel" if fmt == "reel" else "car", 100.0 * reach / top,
               reach, views))
    return "".join(out)


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
    """The whole dashboard, ready to drop into a page."""
    o = metrics.overview(conn)
    c = metrics.compare(conn)
    counts = o["counts"]

    stats = [("published", counts.get("published", 0), False),
             ("queued", counts.get("queued", 0), False),
             ("reach", o["reach"], True),
             ("views", o["views"], True),
             ("interactions", o["total_interactions"], False),
             ("shares", o["shares"], False)]
    cells = "".join('<div class="stat%s"><b>%s</b><span>%s</span></div>'
                    % (" hot" if hot else "", v, k) for k, v, hot in stats)

    stale = metrics.hours_since(o["captured"]) > STALE_HOURS
    when = ('<p class="when%s">Metrics pulled %s &middot; %s%s</p>'
            % (" stale" if stale else "",
               o["captured"].strftime("%-d %b, %H:%M UTC") if o["captured"]
               else "never",
               metrics.ago(o["captured"]),
               ". Older than it should be — the collector only runs when a "
               "publish window is open." if stale else ""))

    return ('<section class="dash">'
            '<h2>Where it stands</h2>%s%s'
            '<h2>Carousel vs reel</h2>'
            '<p class="legend"><span class="key car"></span>carousel (%d) '
            '&nbsp; <span class="key reel"></span>reel (%d)</p>%s'
            '<p class="verdict">%s</p>%s'
            '<h2>Reach per post</h2>'
            '<p class="legend"><span class="key views"></span>views '
            '&nbsp; <span class="key car"></span>reach &mdash; oldest first</p>%s'
            '<h2>Today&rsquo;s candidates</h2>%s'
            '</section>'
            % (cells, when, c["n"]["carousel"], c["n"]["reel"], ab_chart(c),
               escape(c["verdict"]),
               '<p class="caveat">%s</p>' % escape(c["caveat"])
               if c["caveat"] else "",
               post_chart(store.published_with_metrics(conn)),
               todays_candidates()))
