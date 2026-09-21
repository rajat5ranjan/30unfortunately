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
.dash{background:#FBF9F4;border-radius:14px;padding:24px 26px 28px;margin:0 0 34px;
box-shadow:0 3px 12px rgba(21,20,15,.1);max-width:820px}
.dash h2{font-size:12px;letter-spacing:1.3px;text-transform:uppercase;color:#9A9384;
margin:30px 0 12px;font-weight:700}
.dash h2:first-child{margin-top:0}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.kpi{background:#F2EEE4;border-radius:10px;padding:13px 14px 12px}
.kpi b{display:block;font-size:30px;line-height:1;letter-spacing:-1.5px}
.kpi span{display:block;font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;
color:#6E675A;margin-top:5px}
.kpi em{display:block;font-style:normal;font-size:11px;color:#9A9384;margin-top:6px}
.kpi em.up{color:#2E7D4F}
.kpi em.down{color:#D8451F}
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
.headline{font-size:15.5px}.vsrow{grid-template-columns:52px 1fr 30px}}
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


def post_chart(rows: List[Dict[str, Any]]) -> str:
    """Every post's day-one reach, newest first, longest bar = best ever."""
    rows = list(reversed(rows))
    top = max([r["reach"] for r in rows] + [1])
    return '<div class="posts">%s</div>' % "".join(
        '<div class="prow"><span>%s</span><div class="track">'
        '<i class="%s" style="width:%.1f%%"></i></div><b>%s</b></div>'
        % (escape(r["id"]), "reel" if r["format"] == "reel" else "car",
           100.0 * r["reach"] / top, r["reach"])
        for r in rows)


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



def pin_these(one: List[Dict[str, Any]], n: int = 3) -> str:
    """The three best posts by day-one reach, for the profile.

    Pinning is app-only — there is no Graph API call for it, and there is no
    way around that. But CHOOSING what to pin is a data question and the data
    is right here: a stranger who taps the profile sees the grid before they
    see anything else, and the grid should open with the three posts that
    already proved they travel rather than whatever went out on Tuesday.

    Day-one reach, not lifetime, or this is just a list of the oldest posts.
    """
    if len(one) < n:
        return ('<p class="none">Not enough posts measured yet to say what '
                'belongs on the profile.</p>')
    best = sorted(one, key=lambda r: -r["reach"])[:n]
    rows = "".join(
        '<div class="prow"><span>%s</span><div class="track">'
        '<i class="%s" style="width:%.1f%%"></i></div><b>%s</b></div>'
        % (escape(r["id"]), "reel" if r["format"] == "reel" else "car",
           100.0 * r["reach"] / best[0]["reach"], r["reach"])
        for r in best)
    return ('<div class="posts">%s</div>'
            '<p class="none" style="margin-top:10px">Pin these three, in this '
            'order. Instagram has no API for pinning, so it is three long '
            'presses in the app &mdash; and it is the only thing on this page '
            'that changes what a stranger sees before they have read '
            'anything.</p>' % rows)


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

    kpis = ('<div class="kpis">%s%s%s%s</div>'
            % (kpi(_n(t["now"]), "reach, first day", note, cls),
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

    return ('<section class="dash">%s%s'
            '<h2>Which format travels</h2>'
            '<p class="headline">%s</p>%s<p class="caveat">%s</p>'
            '<h2>Pin these to the profile</h2>%s'
            '<h2>Every post, first-day reach</h2>%s'
            '<h2>Today&rsquo;s candidates</h2>%s'
            '</section>'
            % (kpis, when, escape(c["headline"]), versus(c), escape(footnote),
               pin_these(one), post_chart(one), todays_candidates()))
