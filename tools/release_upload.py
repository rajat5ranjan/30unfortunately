#!/usr/bin/env python3
"""
release_upload.py — put a file on a GitHub Release and print its public URL.

    python3 tools/release_upload.py build/reels/g004.mp4
    python3 tools/release_upload.py --url-only g004.mp4

Reels need a public HTTPS URL for Meta to pull the video from, exactly like the
carousel slides do. The slides live in docs/ and are served by Pages, which is
free and simple — but a slide is 200KB and a reel is half a megabyte, and
anything committed stays in git history forever. Ninety reels is a repo that
takes noticeably longer to clone for the rest of time, on a project whose whole
state-passing mechanism is cloning the repo.

Release assets are public, free, unlimited, and live outside git. The cost is
this file.

stdlib only, and GITHUB_TOKEN is provided automatically to Actions, so nothing
has to be configured for this to work in CI.
"""
import json
import mimetypes
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
TAG = os.environ.get("REEL_RELEASE_TAG", "reels")


def repo() -> str:
    """owner/name, from Actions' own env or from the git remote."""
    r = os.environ.get("GITHUB_REPOSITORY")
    if r:
        return r
    url = subprocess.run(["git", "remote", "get-url", "origin"],
                         capture_output=True, text=True).stdout.strip()
    if url.endswith(".git"):
        url = url[:-4]
    if url.startswith("git@"):
        url = url.split(":", 1)[-1]
    return "/".join(url.rstrip("/").split("/")[-2:])


def token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_PAT")
    if not t:
        sys.exit("GITHUB_TOKEN is not set. Actions provides it automatically;\n"
                 "locally, export a PAT with 'contents: write' on this repo.")
    return t


def _call(method: str, url: str, data=None, headers=None, ctype=None):
    h = {"Authorization": "Bearer %s" % token(),
         "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28",
         "User-Agent": "30unfortunately"}
    h.update(headers or {})
    if ctype:
        h["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        sys.exit("GitHub API %s %s -> %s\n%s"
                 % (method, url, e.code, e.read().decode(errors="replace")[:400]))


def release_id(slug: str) -> int:
    """The release for TAG, created on first use."""
    got = _call("GET", "%s/repos/%s/releases/tags/%s" % (API, slug, TAG))
    if got:
        return got["id"]
    made = _call("POST", "%s/repos/%s/releases" % (API, slug),
                 data=json.dumps({
                     "tag_name": TAG, "name": "Reel assets",
                     "body": "Rendered reels, hosted here rather than committed "
                             "so they stay out of git history.",
                     "make_latest": "false"}).encode(),
                 ctype="application/json")
    return made["id"]


def asset_url(slug: str, name: str) -> str:
    return "https://github.com/%s/releases/download/%s/%s" % (slug, TAG, name)


def upload(path: str) -> str:
    slug = repo()
    name = os.path.basename(path)
    rid = release_id(slug)

    # Replacing is the normal case: a post gets re-rendered and re-uploaded.
    for a in _call("GET", "%s/repos/%s/releases/%d/assets?per_page=100"
                   % (API, slug, rid)) or []:
        if a["name"] == name:
            _call("DELETE", "%s/repos/%s/releases/assets/%d" % (API, slug, a["id"]))

    with open(path, "rb") as f:
        blob = f.read()
    _call("POST", "%s/repos/%s/releases/%d/assets?name=%s"
          % (UPLOADS, slug, rid, urllib.parse.quote(name)), data=blob,
          ctype=mimetypes.guess_type(name)[0] or "application/octet-stream")
    return asset_url(slug, name)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    if "--url-only" in sys.argv:          # what the URL will be, without uploading
        print(asset_url(repo(), os.path.basename(args[0])))
    else:
        print(upload(args[0]))
