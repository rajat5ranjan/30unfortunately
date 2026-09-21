#!/usr/bin/env python3
"""
ensure_reels.py — every queued post must have a reel on the Release.

    python3 tools/ensure_reels.py            render and upload whatever is missing
    python3 tools/ensure_reels.py --check    say what is missing, change nothing

publish.py is stdlib-only and cannot render anything, and the format is decided
on the day a post goes out — so by then BOTH assets have to already exist. The
slides do, because they are committed and served by Pages. The reel does not
unless something put it on the Release.

generate.yml uploads reels for posts it has just approved, which covers the
normal path and nothing else: a post queued before reels existed, an upload that
failed, a re-render. This closes that gap, and is safe to run at any time —
anything already on the Release is left alone.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import publish
import store


def approved_post(pid: str):
    path = os.path.join(ROOT, "content", "approved", "%s.json" % pid)
    if not os.path.exists(path):
        return None, None
    blob = json.load(open(path))
    for p in blob.get("posts", []):
        if p.get("id") == pid:
            return p, blob.get("handle", "@30unfortunately")
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only")
    ap.add_argument("--all", action="store_true",
                    help="include published posts, not just the queue")
    ap.add_argument("--rebuild", action="store_true",
                    help="re-render and re-upload even where a reel exists")
    args = ap.parse_args()

    conn = store.connect()
    rows = list(store.queued(conn))
    if args.all:
        rows += list(store.published(conn))

    missing = []
    for row in rows:
        have = [publish.url_is_live(publish.reel_url(row["id"])),
                publish.url_is_live(publish.cover_url(row["id"]))]
        label = {(True, True): "reel + cover",
                 (True, False): "MISSING cover",
                 (False, True): "MISSING reel",
                 (False, False): "MISSING both"}[tuple(have)]
        print("%-6s %s" % (row["id"], label))
        if not all(have) or args.rebuild:
            missing.append(row["id"])

    if not missing:
        print("\nevery queued post has a reel.")
        return 0
    if args.rebuild:
        # The normal path is idempotent on purpose — it is a safety net that
        # runs every four hours and must never re-upload what is already
        # there. --rebuild is for the other case: the renderer changed and
        # the queue is holding assets built by the old one.
        print("\nrebuilding all %d, including the ones already uploaded"
              % len(missing))
    if args.check:
        print("\n%d missing: %s" % (len(missing), ", ".join(missing)))
        return 1

    import reel
    from release_upload import upload

    print("\nrendering %d:" % len(missing))
    for pid in missing:
        post, handle = approved_post(pid)
        if not post:
            print("  %s has no file in content/approved — skipped" % pid)
            continue
        path = reel.render_reel(post, handle)
        for f in (path, path[:-4] + "-cover.jpg"):
            print("  uploaded -> %s" % upload(f))
    return 0


if __name__ == "__main__":
    sys.exit(main())
