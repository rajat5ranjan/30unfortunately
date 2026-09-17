#!/usr/bin/env python3
"""
trend_agent.py — find today's usable trends and write content/trends.json.

    python3 trend_agent.py              fetch, gate, score, write
    python3 trend_agent.py --dry-run    print what it would write
    python3 trend_agent.py --all        show rejected items and why

Replaces the hand-maintained trends file. stdlib only, so the scheduled job
needs no install step.

Three filters, in order, cheapest first:
  1. FRESHNESS  older than trend_max_age_hours is dropped
  2. HARD GATE  death, disaster, conflict, crime, party politics — dropped
                outright. This runs BEFORE the content agent ever sees an item,
                so no amount of clever prompting can route around it.
  3. RELEVANCE  scored against the triggers this account actually writes about.
                Below threshold is dropped: a trend with no honest connection to
                being 30 in India is worse than no trend at all.
Anything surviving is deduped against every trend already used.
"""
import argparse
import glob
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "content", "trends.json")
UA = "Mozilla/5.0 (compatible; 30unfortunately/1.0)"

# Subjects that can never become a joke on this account. Checked on the headline
# before anything else. Deliberately blunt: a false positive costs one trend, a
# false negative costs the account.
# Headline shapes that match triggers but are not events: SEO filler, listicles,
# evergreen explainers. They score well and are worthless.
NOT_A_TREND = [
    "career in", "salary guide", "how to", "best ", "top 10", "top 5",
    "complete guide", "step by step", "everything you need", "explained:",
    "job scope", "course", "tutorial", "vs ", "review:", "tips",
    # advertorials score well on triggers and are pure marketing
    "legacy of", "premium housing", "unveils", "launches", "partners with",
    "announces partnership", "success story", "leading provider", "awarded",
]

HARD_BLOCK = [
    "dead", "death", "died", "kill", "murder", "suicide", "body found",
    "rape", "assault", "molest", "abuse", "trafficking",
    "crash", "accident", "collapse", "blast", "explosion", "fire kills",
    "flood", "earthquake", "cyclone", "landslide", "disaster", "toll",
    "riot", "clash", "communal", "terror", "militant", "attack", "war",
    "arrest", "court", "jailed", "fir ", "probe", "raid", "scam accused",
    "hc ", "high court", "supreme court", "tribunal", "verdict", "plea",
    "petition", "summons", "bail", "chargesheet", "sets aside", "restrains",
    "bjp", "congress", "aap ", "election", "poll", "minister says", "opposition",
    "manifesto", "nsui", "abvp", "rally", "protest", "mla", "mp ", "cm ",
    "cancer", "hospitalised", "critical condition", "obituary", "passes away",
]

# What this account is actually about. An item must hit at least one.
TRIGGERS = {
    "money":    ["upi", "gst", "tax", "salary", "income", "emi", "loan", "rupee",
                 "inflation", "price", "fee", "charge", "subscription", "refund"],
    "work":     ["office", "wfh", "remote", "hybrid", "layoff", "hiring", "appraisal",
                 "employee", "workweek", "resign", "job", "intern", "startup"],
    "home":     ["rent", "tenant", "landlord", "housing", "flat", "broker", "deposit",
                 "society", "maid", "electricity", "water supply"],
    "daily":    ["swiggy", "zomato", "blinkit", "zepto", "ola", "uber", "rapido",
                 "metro", "traffic", "commute", "airline", "irctc", "train", "flight"],
    "culture":  ["cricket", "ipl", "bollywood", "netflix", "instagram", "reel",
                 "festival", "diwali", "navratri", "wedding", "shaadi"],
    "life":     ["gym", "sleep", "burnout", "screen time", "dating", "marriage",
                 "parents", "friendship", "loneliness", "therapy"],
}

# Words too common to identify a story. Without these, "India" and "new" alone
# make unrelated headlines look like the same event.
STOP = set("""india indian new news says said after over from with will can could
would report reports amid ahead here what why how this that than then more most
year years month week day today big top first next last set gets get make made
plan plans hint hints move moves may might""".split())

FEEDS = [
    "https://news.google.com/rss/search?q=UPI+OR+GST+OR+%22income+tax%22+India+when:3d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=rent+OR+housing+OR+landlord+India+city+when:3d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=%22return+to+office%22+OR+layoffs+OR+hiring+OR+salary+India+when:3d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=Swiggy+OR+Zomato+OR+Blinkit+OR+Ola+OR+Uber+India+when:3d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=India+consumer+OR+lifestyle+OR+spending+trend+when:3d&hl=en-IN&gl=IN&ceid=IN:en",
]


def fetch(url: str, timeout: int = 25) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print("  ! feed failed (%s): %s" % (urllib.parse.urlparse(url).netloc, e),
              file=sys.stderr)
        return None


def parse_items(xml_text: str) -> List[Dict[str, str]]:
    """RSS <item> and Atom <entry>, without a dependency."""
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        d = {}
        for child in node:
            ctag = child.tag.split("}")[-1]
            if ctag == "title":
                d["title"] = html.unescape((child.text or "").strip())
            elif ctag == "link":
                d["link"] = (child.text or child.attrib.get("href") or "").strip()
            elif ctag in ("pubDate", "published", "updated"):
                d.setdefault("date", (child.text or "").strip())
            elif ctag == "source":
                d["source_name"] = (child.text or "").strip()
        if d.get("title") and d.get("link"):
            out.append(d)
    return out


def parse_date(s: str) -> Optional[datetime]:
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def blocked(title: str) -> Optional[str]:
    low = " %s " % title.lower()
    for term in HARD_BLOCK:
        if term in low:
            return term.strip()
    return None


def score(title: str) -> Tuple[int, List[str]]:
    low = title.lower()
    hits = []
    for bucket, words in TRIGGERS.items():
        for w in words:
            if w in low:
                hits.append(bucket)
                break
    return len(hits), sorted(set(hits))


def signature(title: str) -> set:
    """Significant tokens — 3+ chars so UPI, GST and MDR survive, minus filler."""
    return set(w for w in re.findall(r"[a-z]{3,}", title.lower()) if w not in STOP)


def used_trends() -> List[str]:
    """Everything the account has already written about, for dedup."""
    out = []
    paths = [os.path.join(ROOT, "content", "seed_posts.json")] + \
        sorted(glob.glob(os.path.join(ROOT, "content", "approved", "*.json")))
    for p in paths:
        if os.path.exists(p):
            with open(p) as f:
                out.extend(x.get("trend", "") for x in json.load(f).get("posts", []))
    return [t.lower() for t in out if t]


def overlaps(title: str, prior: List[str]) -> bool:
    words = set(re.findall(r"[a-z]{4,}", title.lower()))
    for t in prior:
        tw = set(re.findall(r"[a-z]{4,}", t))
        if tw and len(words & tw) / float(len(tw)) > 0.45:
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true", help="show what was rejected and why")
    args = ap.parse_args()

    with open(os.path.join(ROOT, "config.json")) as f:
        cfg = json.load(f)
    max_age = timedelta(hours=cfg.get("trend_max_age_hours", 72))
    want = cfg.get("trend_count", 6)
    min_score = cfg.get("trend_min_score", 1)

    raw, seen_links = [], set()
    for url in FEEDS:
        body = fetch(url)
        if not body:
            continue
        for item in parse_items(body):
            if item["link"] in seen_links:
                continue
            seen_links.add(item["link"])
            raw.append(item)
    print("fetched %d items from %d feeds" % (len(raw), len(FEEDS)))

    now = datetime.now(timezone.utc)
    prior = used_trends()
    kept, dropped = [], []

    for item in raw:
        title = re.sub(r"\s+-\s+[^-]+$", "", item["title"]).strip()   # strip " - Publisher"
        dt = parse_date(item.get("date", "")) if item.get("date") else None
        if dt and now - dt > max_age:
            dropped.append((title, "stale (%dh)" % ((now - dt).total_seconds() // 3600)))
            continue
        term = blocked(title)
        if term:
            dropped.append((title, "hard gate: '%s'" % term))
            continue
        filler = next((f for f in NOT_A_TREND if f in title.lower()), None)
        if filler:
            dropped.append((title, "not an event: '%s'" % filler.strip()))
            continue
        s, buckets = score(title)
        if s < min_score:
            dropped.append((title, "no trigger match"))
            continue
        if overlaps(title, prior):
            dropped.append((title, "already covered"))
            continue
        kept.append({"trend": title, "source": item["link"],
                     "note": "triggers: %s" % ", ".join(buckets),
                     "_score": s,
                     "_published": dt.isoformat() if dt else None})

    kept.sort(key=lambda x: -x["_score"])

    # One story reaches us under several publishers' headlines. Without this the
    # agent hands the content agent five ways of saying the same thing.
    # Two headlines sharing two or more significant words are the same story,
    # however differently they are phrased. Proportional overlap does not catch
    # this: "UPI MDR: Ministry addresses concerns" and "UPI MDR for capital
    # markets" share only 25% of their words but are one event.
    chosen = []
    for c in kept:
        sig = signature(c["trend"])
        clash = next((p for p in chosen if len(sig & signature(p["trend"])) >= 2), None)
        if clash:
            dropped.append((c["trend"], "same story as: %s" % clash["trend"][:38]))
            continue
        chosen.append(c)
        if len(chosen) == want:
            break
    for c in chosen:
        c.pop("_score", None)

    print("kept %d, dropped %d -> writing %d" % (len(kept), len(dropped), len(chosen)))
    if args.all:
        print("\nrejected:")
        for t, why in dropped[:40]:
            print("  [%-22s] %s" % (why, t[:78]))

    print("\nselected:")
    for c in chosen:
        print("  %s\n      %s" % (c["trend"][:88], c["note"]))

    if args.dry_run:
        print("\n--dry-run: content/trends.json untouched.")
        return

    with open(OUT, "w") as f:
        json.dump({"_note": "written by trend_agent.py — do not hand-edit",
                   "pulled_at": now.strftime("%Y-%m-%d"),
                   "trends": chosen}, f, indent=2, ensure_ascii=False)
    print("\nwrote %s" % os.path.relpath(OUT, ROOT))


if __name__ == "__main__":
    main()
