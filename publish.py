#!/usr/bin/env python3
"""
publish.py — Instagram carousel publishing via the official Graph API.

    python3 publish.py check                     token, rate limit, queue depth
    python3 publish.py queue --ids p001,p002     add approved posts to the queue
    python3 publish.py next --dry-run            show what would publish
    python3 publish.py next                      publish the next queued post
    python3 publish.py insights                  pull metrics for published posts
    python3 publish.py due                       is a publish slot open right now?
    python3 publish.py ab                        carousel vs reel, so far
    python3 publish.py refresh-token             extend the 60-day token

A post ships as a carousel or as a reel depending on the day (store.format_for)
— that is the reach experiment, and `ab` reads it out.

A carousel is a three-step dance: a container per slide, a CAROUSEL container
holding their ids, then media_publish. A reel is one container and a longer
wait, because Meta has to transcode the video. Meta downloads each image from a public
HTTPS URL — there is no byte upload — so the slides must already be live on
GitHub Pages before this runs. `check` and `next` both verify that first.

stdlib only, so the scheduled job needs no install step.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from datetime import time as TimeOfDay   # NOT `time`: that is the stdlib module
                                         # this file polls with, and shadowing it
                                         # breaks every publish, not just reels
from typing import Any, Dict, List, Optional

import envfile
import notify
import store

ROOT = os.path.dirname(os.path.abspath(__file__))
GRAPH = "https://graph.instagram.com"
API_VERSION = "v23.0"
TIMEOUT = 60

MEDIA_METRICS = ["reach", "views", "likes", "comments", "saved", "shares",
                 "total_interactions"]


# ------------------------------------------------------------------------ env
def need(key: str) -> str:
    v = os.environ.get(key)
    if not v:
        sys.exit("%s is not set. See .env.example." % key)
    return v


def ig_user() -> str:
    """The numeric id, or 'me' — the Graph API accepts either."""
    return os.environ.get("IG_USER_ID") or "me"


# ----------------------------------------------------------------------- http
class GraphError(RuntimeError):
    pass


def _request(method: str, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    data = urllib.parse.urlencode(params).encode() if method == "POST" else None
    if method == "GET":
        url = "%s?%s" % (url, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            err = json.loads(body).get("error", {})
            msg = "%s (code %s, subcode %s)" % (
                err.get("message", body), err.get("code"), err.get("error_subcode"))
        except ValueError:
            msg = "HTTP %s: %s" % (e.code, body[:400])
        raise GraphError(msg) from None
    except urllib.error.URLError as e:
        # A timeout or reset says nothing about whether Meta acted on the call.
        # It has to surface as a GraphError so cmd_next's handler sees it and
        # leaves an in-flight publish parked for reconciliation.
        raise GraphError("no reply from %s (%s)" % (GRAPH, e.reason)) from None


def get(path: str, token: str, **params: Any) -> Dict[str, Any]:
    params["access_token"] = token
    return _request("GET", "%s/%s/%s" % (GRAPH, API_VERSION, path.lstrip("/")), params)


def post(path: str, token: str, **params: Any) -> Dict[str, Any]:
    params["access_token"] = token
    return _request("POST", "%s/%s/%s" % (GRAPH, API_VERSION, path.lstrip("/")), params)


def wait_for_container(container_id: str, token: str, label: str,
                       timeout: int = 180, interval: int = 5) -> None:
    """Block until a media container is FINISHED.

    Container creation is asynchronous: Meta downloads the image from its public
    URL and processes it in the background. Calling media_publish before every
    container reports FINISHED fails with 'Media ID is not available' (9007 /
    2207027). Locally the round-trip latency usually hides this; on a CI runner
    it does not.
    """
    deadline = time.time() + timeout
    status = "UNKNOWN"
    while time.time() < deadline:
        r = get(container_id, token, fields="status_code,status")
        status = r.get("status_code", "UNKNOWN")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise GraphError("%s %s: %s — %s"
                             % (label, container_id, status, r.get("status", "")))
        time.sleep(interval)
    raise GraphError("%s %s still %s after %ds"
                     % (label, container_id, status, timeout))


def url_is_live(url: str) -> bool:
    """Meta fetches the image itself; if this 404s, publishing fails opaquely."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


# ------------------------------------------------------------------- slide urls
def cfg(key: str, default: Any = None) -> Any:
    with open(os.path.join(ROOT, "config.json")) as f:
        return json.load(f).get(key, default)


def _cfg(key: str) -> str:
    return cfg(key).rstrip("/")


# ------------------------------------------------------------------- windows
def local_now() -> datetime:
    """Now, in the account's own timezone, as a naive datetime.

    A fixed offset rather than a timezone name: IST has no daylight saving, so
    this is exactly correct and does not need a tz database to be present.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        minutes=int(cfg("publish_utc_offset_minutes", 330)))


def _hhmm(s: str) -> TimeOfDay:
    h, m = s.split(":")
    return TimeOfDay(int(h), int(m))


def current_window(now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The publish window we are inside, if any.

    Scheduled workflows on GitHub are best-effort — they queue under load and
    are dropped outright when it is heavy. Aiming a cron at a minute does not
    work; on 2026-09-18 the 08:40 slot never ran at all. So the job polls and
    the window is the thing that is aimed.
    """
    now = now or local_now()
    for w in cfg("publish_windows", []):
        if _hhmm(w["after"]) <= now.time() < _hhmm(w["before"]):
            return w
    return None


def window_used(conn, w: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Has something already published in this window today?

    This is what makes polling safe: every poll inside an open window tries,
    and the first one that finds the slot empty fills it.
    """
    now = now or local_now()
    off = timedelta(minutes=int(cfg("publish_utc_offset_minutes", 330)))
    a, b = _hhmm(w["after"]), _hhmm(w["before"])
    for row in store.published(conn):
        if not row["published_at"]:
            continue
        try:
            at = datetime.fromisoformat(row["published_at"]).replace(
                tzinfo=None) + off
        except ValueError:
            continue
        if at.date() == now.date() and a <= at.time() < b:
            return True
    return False


def last_publish_local(conn) -> Optional[datetime]:
    """When anything last went out, in account-local time. None if nothing has."""
    off = timedelta(minutes=int(cfg("publish_utc_offset_minutes", 330)))
    best = None
    for row in store.published(conn):
        if not row["published_at"]:
            continue
        try:
            at = datetime.fromisoformat(row["published_at"]).replace(tzinfo=None) + off
        except ValueError:
            continue
        if best is None or at > best:
            best = at
    return best


def too_soon(conn, now: Optional[datetime] = None) -> int:
    """Minutes still owed before the next post, or 0 if it can go now.

    The windows had to be widened because GitHub delivers roughly one scheduled
    poll every few hours, not the seventy-two a day the cron asks for — and a
    window only catches a poll if it is wide. Widening the deadlines put the end
    of one window within an hour of the start of the next, so two posts could
    land back to back, which is the one thing that reliably costs reach: they
    compete for the same audience in the same hour.

    Deliberately not applied to --now. That button means now.
    """
    now = now or local_now()
    gap = int(cfg("min_publish_gap_minutes", 90))
    last = last_publish_local(conn)
    if last is None:
        return 0
    mins = (now - last).total_seconds() / 60.0
    return int(gap - mins) if 0 <= mins < gap else 0


def _alert(msg: str) -> None:
    """Loud on the console, and on the Actions run page where it will be read."""
    print("!! %s" % msg.replace("\n", "\n   "), file=sys.stderr)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a") as f:
                f.write("### Missed slot risk\n\n%s\n" % msg)
        except OSError:
            pass


def cmd_due(args: argparse.Namespace) -> int:
    """Exit 0 if a slot is open. Cheap: no API calls, no mailbox."""
    now = local_now()
    w = current_window(now)
    if not w:
        nxt = sorted(x["after"] for x in cfg("publish_windows", []))
        print("%s — outside the publish windows (%s)"
              % (now.strftime("%H:%M"), ", ".join(nxt)))
        return 1
    conn = store.connect()
    if window_used(conn, w, now):
        print("%s — the %s window already published today"
              % (now.strftime("%H:%M"), w["name"]))
        return 1
    wait = too_soon(conn, now)
    if wait:
        print("%s — %s window is open, but the last post was too recent; "
              "%d min to go" % (now.strftime("%H:%M"), w["name"], wait))
        return 1
    print("%s — %s window is open (%s-%s)"
          % (now.strftime("%H:%M"), w["name"], w["after"], w["before"]))
    return 0


def slide_urls(post_id: str, n_slides: int) -> List[str]:
    base = _cfg("pages_base_url")
    return ["%s/media/%s/%d.png" % (base, post_id, i + 1) for i in range(n_slides)]


def reel_url(post_id: str) -> str:
    """The rendered reel, on a GitHub Release rather than in the repo."""
    return "%s/%s.mp4" % (_cfg("reels_base_url"), post_id)


def cover_url(post_id: str) -> str:
    """The reel's own 9:16 cover.

    NOT the carousel's slide 1. That is 1080x1350 against a 1080x1920 cover
    frame, and Instagram makes up the difference by scaling to fill and
    cropping — the text arrives oversized and running off the tile.
    """
    return "%s/%s-cover.jpg" % (_cfg("reels_base_url"), post_id)


def reconcile(conn, token: str, ig_id: str) -> None:
    """Settle posts left mid-publish, before anything new goes out.

    media_publish is the one call whose outcome we cannot infer from a failure:
    if the reply is lost, the post may or may not be on the account. The row is
    parked as 'publishing' beforehand, and the only source of truth is the feed
    itself — so ask it. A caption is unique per post, which makes it the join.

    Confirmed on the feed  -> published, with the real media id.
    Definitively absent    -> back to the queue, to go out at the next slot.
    Cannot tell            -> stop. Publishing again is how you double-post.
    """
    stuck = store.in_flight(conn)
    if not stuck:
        return
    recent = get("%s/media" % ig_id, token, fields="id,caption,permalink", limit=25)
    data = recent.get("data") or []

    # The docstring has always promised three outcomes and the code only had
    # two. An empty page is the missing one: the account has posts, so an empty
    # reply is the API failing to answer, not the feed being empty — and
    # treating it as "never published" requeues a post that is already live.
    if not data:
        me = get(ig_id, token, fields="media_count")
        if int(me.get("media_count") or 0) > 0:
            raise GraphError(
                "the feed came back empty for an account with %s posts, so "
                "whether %s published cannot be determined. Leaving it parked."
                % (me.get("media_count"), ", ".join(r["id"] for r in stuck)))

    feed = dict(((m.get("caption") or "").strip(), m) for m in data)
    for row in stuck:
        hit = feed.get((row["caption"] or "").strip())
        if hit:
            store.mark_published(conn, row["id"], hit["id"], hit.get("permalink"))
            print("reconciled: %s did publish -> %s"
                  % (row["id"], hit.get("permalink") or hit["id"]))
        else:
            store.requeue(conn, row["id"])
            print("reconciled: %s never published — back in the queue" % row["id"])


# -------------------------------------------------------------------- commands
def cmd_check(args: argparse.Namespace) -> int:
    token, ig_id = need("IG_ACCESS_TOKEN"), ig_user()
    ok = True

    try:
        me = get(ig_id, token, fields="id,username,account_type,media_count")
        print("account    @%s (%s, %s posts)" % (
            me.get("username"), me.get("account_type"), me.get("media_count")))
    except GraphError as e:
        print("account    FAILED — %s" % e)
        return 1

    try:
        lim = get("%s/content_publishing_limit" % ig_id, token,
                  fields="quota_usage,config")
        d = (lim.get("data") or [{}])[0]
        quota = d.get("config", {}).get("quota_total", 50)
        print("rate limit %s/%s posts used in the last 24h" % (d.get("quota_usage", 0), quota))
    except GraphError as e:
        print("rate limit unavailable — %s" % e)

    # The single highest-consequence silent failure left in the system: the
    # token lasts 60 days and can only be refreshed while alive. If
    # refresh-token.yml is dropped often enough, everything stops at once and
    # the fix itself stops working. Cheap to ask, so ask on every check.
    try:
        days = token_days_left(token)
        print("token      %d days left%s"
              % (days, "  <-- REFRESH IT" if days < 14 else ""))
        if days < 14:
            ok = False
            _alert("The Instagram token expires in %d days. It can only be "
                   "refreshed while it is still alive:\n"
                   "  Actions -> refresh-token -> Run workflow" % days)
    except GraphError as e:
        print("token      expiry unknown — %s" % e)

    conn = store.connect()
    c = store.counts(conn)
    print("queue      %s" % (", ".join("%s %d" % kv for kv in sorted(c.items())) or "empty"))

    now = local_now()
    w = current_window(now)
    if not w:
        print("window     %s — closed (%s)" % (
            now.strftime("%H:%M"),
            ", ".join("%s %s-%s" % (x["name"], x["after"], x["before"])
                      for x in cfg("publish_windows", []))))
    else:
        print("window     %s — %s is %s" % (
            now.strftime("%H:%M"), w["name"],
            "already used today" if window_used(conn, w, now) else "OPEN"))
    print("format     today ships as a %s" % store.format_for(now))

    nxt = store.next_queued(conn)
    if nxt:
        urls = slide_urls(nxt["id"], len(json.loads(nxt["slides"])))
        missing = [u for u in urls if not url_is_live(u)]
        if missing:
            ok = False
            print("images     NOT LIVE — %d of %d missing:" % (len(missing), len(urls)))
            for u in missing:
                print("             %s" % u)
            print("           GitHub Pages has not deployed these yet. Meta pulls the")
            print("           image itself, so publishing now would fail.")
        else:
            print("images     all %d slides of %s are live" % (len(urls), nxt["id"]))
    return 0 if ok else 1


def cmd_whoami(args: argparse.Namespace) -> int:
    """Resolve IG_USER_ID from a token alone, so you only have to find one value."""
    token = need("IG_ACCESS_TOKEN")
    me = get("me", token, fields="user_id,username,account_type,media_count")
    uid = me.get("user_id") or me.get("id")
    print("username     @%s" % me.get("username"))
    print("account type %s" % me.get("account_type"))
    print("media count  %s" % me.get("media_count"))
    print("\nIG_USER_ID=%s" % uid)
    print("\nAdd that line to .env. ('me' also works in place of the id.)")
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    with open(args.src) as f:
        blob = json.load(f)
    posts = blob.get("posts") or blob.get("accepted") or []
    if args.ids:
        wanted = set(x.strip() for x in args.ids.split(","))
        posts = [p for p in posts if p.get("id") in wanted]
        unknown = wanted - set(p.get("id") for p in posts)
        if unknown:
            sys.exit("unknown post ids: %s" % ", ".join(sorted(unknown)))
    if not posts:
        sys.exit("nothing to queue")

    conn = store.connect(write=True)
    added = sum(1 for p in posts if store.enqueue(conn, p))
    print("queued %d new, %d already known" % (added, len(posts) - added))
    print("queue now: %s" % store.counts(conn))
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    conn = store.connect(write=True)

    # The window gate comes first, before the mailbox and before any API call:
    # this job now runs 72 times a day and only two of those should do work.
    w = None
    if not (args.now or args.dry_run):
        now = local_now()
        w = current_window(now)
        if not w:
            print("%s — outside the publish windows, nothing to do"
                  % now.strftime("%H:%M"))
            return 0
        if window_used(conn, w, now):
            print("%s — the %s window already published today"
                  % (now.strftime("%H:%M"), w["name"]))
            return 0
        wait = too_soon(conn, now)
        if wait:
            print("%s — %s window open, but only %d min since the last post; "
                  "waiting %d min so they do not compete"
                  % (now.strftime("%H:%M"), w["name"],
                     int(cfg("min_publish_gap_minutes", 90)) - wait, wait))
            return 0
        print("%s — %s window open" % (now.strftime("%H:%M"), w["name"]))

    # Read vetoes immediately before publishing. Actions cannot receive a
    # webhook, but this job runs seconds before the post goes out, so polling
    # here makes the veto window real-time rather than a scheduled sweep.
    done = []
    for uid, ids in notify.fetch_commands():
        for pid in ids:
            print("veto: %s" % ("skipped %s" % pid if store.skip(conn, pid)
                                else "%s was not queued" % pid))
        done.append(uid)
    notify.mark_done(done)      # only after the skips are committed

    token, ig_id = ("", "")
    if not args.dry_run:
        token, ig_id = need("IG_ACCESS_TOKEN"), ig_user()
        # Anything left mid-publish by a dropped connection is settled against
        # the real feed before a single new container is created. It can also
        # put a post back at the head of the queue, so it runs before the pick.
        reconcile(conn, token, ig_id)

    row = store.next_queued(conn, args.id)
    if not row and args.id:
        # Named by a button, so the answer has to be specific: the id is either
        # unknown, already out, or vetoed, and "nothing to publish" would read
        # as an empty queue.
        cur = conn.execute("SELECT status FROM posts WHERE id=?",
                           (args.id,)).fetchone()
        print("%s is %s — only a queued post can be published"
              % (args.id, cur["status"] if cur else "not a post in the queue"))
        return 1
    if not row:
        # An empty queue at any other moment is fine. An empty queue while a
        # window is OPEN is a missed slot, and it is the only way this system
        # silently falls behind — so say it loudly and put it where it will be
        # seen rather than buried in the log of one poll out of seventy-two.
        if w:
            _alert("Nothing to publish and the %s window is open (%s-%s IST).\n"
                   "A slot will be missed unless generate refills the queue "
                   "before %s." % (w["name"], w["after"], w["before"],
                                   w["before"]))
        else:
            print("queue is empty")
        return 0

    slides = json.loads(row["slides"])
    urls = slide_urls(row["id"], len(slides))
    caption = row["caption"]
    fmt = args.format or store.format_for(datetime.now(timezone.utc))

    print("%s — %s, %d slides" % (row["id"], fmt, len(slides)))
    for u in urls:
        print("  %s" % u)
    if fmt == "reel":
        print("  %s" % reel_url(row["id"]))
    print("caption: %s" % caption)

    if args.dry_run:
        print("\n--dry-run: nothing sent.")
        return 0

    missing = [u for u in urls if not url_is_live(u)]
    if missing:
        store.mark_failed(conn, row["id"], "images not reachable: %s" % missing[0])
        sys.exit("images are not publicly reachable yet — run `check` for detail")

    if fmt == "reel" and not (url_is_live(reel_url(row["id"]))
                              and url_is_live(cover_url(row["id"]))):
        # Ship the carousel rather than skip the slot, and record what actually
        # went out. The split drifts; a missing post would be worse, and an
        # arm labelled with what it was meant to be would be worse still.
        print("reel asset is not on the release yet — publishing as a carousel")
        fmt = "carousel"
    store.set_format(conn, row["id"], fmt)

    try:
        if fmt == "reel":
            r = post("%s/media" % ig_id, token, media_type="REELS",
                     video_url=reel_url(row["id"]), caption=caption,
                     cover_url=cover_url(row["id"]), share_to_feed="true")
            print("  reel container %s" % r["id"], end=" ", flush=True)
            # Meta transcodes the video, which takes far longer than pulling a
            # handful of PNGs. 180s is not enough for a reel.
            wait_for_container(r["id"], token, "reel", timeout=420, interval=8)
            print("FINISHED")
            parent = r
        else:
            parent = None

        children = []
        for u in (urls if parent is None else []):
            r = post("%s/media" % ig_id, token, image_url=u, is_carousel_item="true")
            children.append(r["id"])
            print("  container %s" % r["id"], end=" ", flush=True)
            wait_for_container(r["id"], token, "slide")
            print("FINISHED")

        if parent is None:
            parent = post("%s/media" % ig_id, token, media_type="CAROUSEL",
                          children=",".join(children), caption=caption)
            print("  carousel  %s" % parent["id"], end=" ", flush=True)
            wait_for_container(parent["id"], token, "carousel")
            print("FINISHED")

        # The point of no return: past this line a lost reply is ambiguous, so
        # the row is parked as 'publishing' and the next run reconciles it.
        store.mark_publishing(conn, row["id"], parent["id"])
        pub = post("%s/media_publish" % ig_id, token, creation_id=parent["id"])
        media_id = pub["id"]

        permalink = None
        try:
            permalink = get(media_id, token, fields="permalink").get("permalink")
        except GraphError:
            pass

        store.mark_published(conn, row["id"], media_id, permalink)
        print("\npublished %s -> %s" % (row["id"], permalink or media_id))
        return 0
    except GraphError as e:
        if store.in_flight(conn):
            # media_publish was already sent. Whether it landed is unknown, so
            # this row stays parked rather than being retried blindly.
            sys.exit("publish failed after media_publish: %s\n"
                     "%s is held as 'publishing' — the next run checks the feed\n"
                     "and either records it or puts it back in the queue." % (e, row["id"]))
        store.mark_failed(conn, row["id"], str(e))
        sys.exit("publish failed: %s" % e)


def cmd_skip(args: argparse.Namespace) -> int:
    conn = store.connect(write=True)
    for pid in args.ids:
        print("skipped %s" % pid if store.skip(conn, pid)
              else "%s was not queued — nothing to skip" % pid)
    print("queue now: %s" % store.counts(conn))
    return 0


def cmd_unskip(args: argparse.Namespace) -> int:
    conn = store.connect(write=True)
    for pid in args.ids:
        print("%s is back in the queue" % pid if store.unskip(conn, pid)
              else "%s was not skipped — nothing to put back" % pid)
    print("queue now: %s" % store.counts(conn))
    return 0


def cmd_queue_list(args: argparse.Namespace) -> int:
    conn = store.connect()
    rows = store.queued(conn)
    if not rows:
        print("queue is empty")
        return 0
    for r in rows:
        print("%-6s %s" % (r["id"], json.loads(r["slides"])[0][:70]))
    return 0


def cmd_insights(args: argparse.Namespace) -> int:
    token = need("IG_ACCESS_TOKEN")
    conn = store.connect(write=True)
    rows = store.published(conn)
    if not rows:
        print("nothing published yet")
        return 0

    # Newest first, and only the window where numbers still move. This runs on
    # every publish; without a bound it is one API call per post ever made,
    # three times a day, growing forever — 200 sequential calls on a job that
    # is meant to take a minute.
    rows = rows[:int(args.limit)]
    for row in rows:
        mid = row["ig_media_id"]
        try:
            res = get("%s/insights" % mid, token, metric=",".join(MEDIA_METRICS))
        except GraphError as e:
            print("%-6s insights unavailable — %s" % (row["id"], e))
            continue
        vals = {}
        for item in res.get("data", []):
            series = item.get("values") or [{}]
            vals[item["name"]] = series[0].get("value")
        store.record_metrics(conn, mid, row["id"], vals)

        reach = vals.get("reach") or 0
        rate = lambda k: ("%.2f%%" % (100.0 * (vals.get(k) or 0) / reach)) if reach else "-"
        print("%-6s reach %-7s shares %-5s (%s)  saves %-5s (%s)  likes %s"
              % (row["id"], reach, vals.get("shares"), rate("shares"),
                 vals.get("saved"), rate("saved"), vals.get("likes")))
    print("\nShares/reach and saves/reach are the primary metrics; per-post")
    print("profile_views was deprecated in Graph API v21.")
    return 0


def _median(xs: List[float]) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _bootstrap(a: List[float], b: List[float], rounds: int = 4000):
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
        ma = _median([rng.choice(a) for _ in a])
        mb = _median([rng.choice(b) for _ in b])
        if ma:
            out.append(mb / ma)
    if not out:
        return None
    out.sort()
    return out[int(0.05 * len(out))], out[int(0.95 * len(out)) - 1]


def cmd_ab(args: argparse.Namespace) -> int:
    """Carousel versus reel, on the metrics both formats actually report."""
    conn = store.connect()
    groups = store.by_format(conn)
    arms = ("carousel", "reel")
    rows = dict((a, groups.get(a, [])) for a in arms)

    def col(a, key):
        return [r[key] for r in rows[a] if r[key] is not None]

    def rate(a, key):
        return [(r[key] or 0) / float(r["reach"]) for r in rows[a]
                if r["reach"]]

    print("%-18s %10s %10s %10s" % ("", "carousel", "reel", "ratio"))
    print("%-18s %10d %10d" % ("posts", len(rows["carousel"]), len(rows["reel"])))

    def line(label, a_vals, b_vals, pct=False):
        ma, mb = _median(a_vals), _median(b_vals)
        fmt = (lambda v: "-" if v is None else
               ("%.2f%%" % (v * 100) if pct else "%.0f" % v))
        ratio = "%.1fx" % (mb / ma) if (ma and mb) else "—"
        print("%-18s %10s %10s %10s" % (label, fmt(ma), fmt(mb), ratio))

    line("reach (median)", col("carousel", "reach"), col("reel", "reach"))
    line("shares/reach", rate("carousel", "shares"), rate("reel", "shares"), True)
    line("saves/reach", rate("carousel", "saved"), rate("reel", "saved"), True)
    line("likes/reach", rate("carousel", "likes"), rate("reel", "likes"), True)

    ci = _bootstrap(col("carousel", "reach"), col("reel", "reach"))
    print()
    if ci:
        print("reel reach advantage: bootstrap 90%% CI %.1fx - %.1fx" % ci)
        if ci[0] > 1.0:
            print("  the interval clears 1.0 — reels are reaching further.")
        elif ci[1] < 1.0:
            print("  the interval is below 1.0 — carousels are reaching further.")
        else:
            print("  the interval spans 1.0, so this is not yet a difference.")
    smallest = min(len(rows["carousel"]), len(rows["reel"]))
    if smallest < 15:
        print("Only %d in the smaller arm. Medians move a lot at this size; treat"
              % smallest)
        print("anything here as a shape, not a result. Read it again at 15 each,")
        print("and decide at 30.")
    print("\nShares per reach is the tiebreaker: reach says Instagram showed it")
    print("to more people, shares says they passed it on. Only the second one")
    print("compounds. Reel watch time is not here because carousels cannot")
    print("report it — the comparison only uses metrics both formats produce.")
    return 0


def token_days_left(token: str) -> int:
    """Days until the long-lived token expires.

    refresh_access_token both refreshes and reports; calling it is idempotent
    and the returned token is only kept when we mean to rotate. Reading the
    expiry is the useful half.
    """
    res = _request("GET", "%s/refresh_access_token" % GRAPH,
                   {"grant_type": "ig_refresh_token", "access_token": token})
    return int(res.get("expires_in", 0)) // 86400


def cmd_refresh_token(args: argparse.Namespace) -> int:
    """Long-lived tokens last 60 days and can only be refreshed while still alive."""
    token = need("IG_ACCESS_TOKEN")
    res = _request("GET", "%s/refresh_access_token" % GRAPH,
                   {"grant_type": "ig_refresh_token", "access_token": token})
    new, expires = res.get("access_token"), res.get("expires_in", 0)
    if args.quiet:                      # for scripting: the token and nothing else
        print(new)
        return 0
    print("new token valid for %d days" % (expires // 86400))
    if args.write:
        path = os.path.join(ROOT, ".env")
        lines, seen = [], False
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if line.startswith("IG_ACCESS_TOKEN="):
                        lines.append("IG_ACCESS_TOKEN=%s\n" % new)
                        seen = True
                    else:
                        lines.append(line)
        if not seen:
            lines.append("IG_ACCESS_TOKEN=%s\n" % new)
        with open(path, "w") as f:
            f.writelines(lines)
        print("written to .env")
    else:
        print("\n%s\n\nStore this. Re-run with --write to update .env, or update the\n"
              "IG_ACCESS_TOKEN repository secret." % new)
    return 0


def main() -> None:
    envfile.load()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("whoami").set_defaults(fn=cmd_whoami)

    q = sub.add_parser("queue")
    q.add_argument("--src", default=os.path.join(ROOT, "content", "seed_posts.json"))
    q.add_argument("--ids", help="comma-separated post ids; omit for all")
    q.set_defaults(fn=cmd_queue)

    n = sub.add_parser("next")
    n.add_argument("--dry-run", action="store_true")
    n.add_argument("--format", choices=("carousel", "reel"),
                   help="override the day's format; the A/B assumes you do not")
    n.add_argument("--now", action="store_true",
                   help="ignore the publish windows and go")
    n.add_argument("--id", help="publish this queued post instead of the head")
    n.set_defaults(fn=cmd_next)

    sub.add_parser("due").set_defaults(fn=cmd_due)
    sub.add_parser("ab").set_defaults(fn=cmd_ab)

    ins = sub.add_parser("insights")
    ins.add_argument("--limit", type=int, default=25,
                     help="how many of the most recent posts to refresh")
    ins.set_defaults(fn=cmd_insights)
    sub.add_parser("list").set_defaults(fn=cmd_queue_list)

    sk = sub.add_parser("skip")
    sk.add_argument("ids", nargs="+", help="post ids to remove from the queue")
    sk.set_defaults(fn=cmd_skip)

    us = sub.add_parser("unskip")
    us.add_argument("ids", nargs="+", help="post ids to put back in the queue")
    us.set_defaults(fn=cmd_unskip)

    r = sub.add_parser("refresh-token")
    r.add_argument("--write", action="store_true", help="update .env in place")
    r.add_argument("--quiet", action="store_true", help="print only the token")
    r.set_defaults(fn=cmd_refresh_token)

    args = ap.parse_args()
    try:
        sys.exit(args.fn(args))
    except GraphError as e:
        sys.exit("Instagram API error: %s" % e)


if __name__ == "__main__":
    main()
