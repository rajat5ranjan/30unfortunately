#!/usr/bin/env python3
"""
publish.py — Instagram carousel publishing via the official Graph API.

    python3 publish.py check                     token, rate limit, queue depth
    python3 publish.py queue --ids p001,p002     add approved posts to the queue
    python3 publish.py next --dry-run            show what would publish
    python3 publish.py next                      publish the next queued post
    python3 publish.py insights                  pull metrics for published posts
    python3 publish.py refresh-token             extend the 60-day token

Publishing is a three-step dance: a container per slide, a CAROUSEL container
holding their ids, then media_publish. Meta downloads each image from a public
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
def slide_urls(post_id: str, n_slides: int) -> List[str]:
    with open(os.path.join(ROOT, "config.json")) as f:
        base = json.load(f)["pages_base_url"].rstrip("/")
    return ["%s/media/%s/%d.png" % (base, post_id, i + 1) for i in range(n_slides)]


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

    conn = store.connect()
    c = store.counts(conn)
    print("queue      %s" % (", ".join("%s %d" % kv for kv in sorted(c.items())) or "empty"))

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

    conn = store.connect()
    added = sum(1 for p in posts if store.enqueue(conn, p))
    print("queued %d new, %d already known" % (added, len(posts) - added))
    print("queue now: %s" % store.counts(conn))
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    conn = store.connect()

    # Read vetoes immediately before publishing. Actions cannot receive a
    # webhook, but this job runs seconds before the post goes out, so polling
    # here makes the veto window real-time rather than a scheduled sweep.
    for pid in notify.commands():
        print("veto: %s" % ("skipped %s" % pid if store.skip(conn, pid)
                            else "%s was not queued" % pid))

    row = store.next_queued(conn)
    if not row:
        print("queue is empty")
        return 0

    slides = json.loads(row["slides"])
    urls = slide_urls(row["id"], len(slides))
    caption = row["caption"]

    print("%s — %d slides" % (row["id"], len(slides)))
    for u in urls:
        print("  %s" % u)
    print("caption: %s" % caption)

    if args.dry_run:
        print("\n--dry-run: nothing sent.")
        return 0

    token, ig_id = need("IG_ACCESS_TOKEN"), ig_user()

    missing = [u for u in urls if not url_is_live(u)]
    if missing:
        store.mark_failed(conn, row["id"], "images not reachable: %s" % missing[0])
        sys.exit("images are not publicly reachable yet — run `check` for detail")

    try:
        children = []
        for u in urls:
            r = post("%s/media" % ig_id, token, image_url=u, is_carousel_item="true")
            children.append(r["id"])
            print("  container %s" % r["id"], end=" ", flush=True)
            wait_for_container(r["id"], token, "slide")
            print("FINISHED")

        parent = post("%s/media" % ig_id, token, media_type="CAROUSEL",
                      children=",".join(children), caption=caption)
        print("  carousel  %s" % parent["id"], end=" ", flush=True)
        wait_for_container(parent["id"], token, "carousel")
        print("FINISHED")

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
        store.mark_failed(conn, row["id"], str(e))
        sys.exit("publish failed: %s" % e)


def cmd_skip(args: argparse.Namespace) -> int:
    conn = store.connect()
    for pid in args.ids:
        print("skipped %s" % pid if store.skip(conn, pid)
              else "%s was not queued — nothing to skip" % pid)
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
    conn = store.connect()
    rows = store.published(conn)
    if not rows:
        print("nothing published yet")
        return 0

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
    n.set_defaults(fn=cmd_next)

    sub.add_parser("insights").set_defaults(fn=cmd_insights)
    sub.add_parser("list").set_defaults(fn=cmd_queue_list)

    sk = sub.add_parser("skip")
    sk.add_argument("ids", nargs="+", help="post ids to remove from the queue")
    sk.set_defaults(fn=cmd_skip)

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
