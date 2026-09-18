#!/usr/bin/env python3
"""control.py — the buttons on the Pages site, and where a tap goes.

The site is static and the repo is public, so no page here can carry a token:
anything that called the API from the browser would publish the credential
along with the HTML. The one channel that turns a tap into a workflow without a
secret is a prefilled issue link. GitHub takes the title from the query string,
you are already signed in on the phone, and one more tap files it.

command.yml picks it up from `issues: opened`, runs it, replies with the output
and closes the issue. So the cost of a decision is two taps instead of writing
an email, and the record of every approve/skip/force is a closed issue with the
run attached to it.
"""
import json
import os
from urllib.parse import quote

ROOT = os.path.dirname(os.path.abspath(__file__))

BODY = ("Filed from the Pages site. The command is the **title** — this body is "
        "ignored, so just press Create.\n\n"
        "The reply and the run log arrive here in a minute or so.")

# Buttons are the only thing on these pages that is not text, so they carry the
# brand's three colours and nothing else. Sized for a thumb, not a mouse.
CSS = """
.bar{display:flex;gap:7px;margin-top:12px;flex-wrap:wrap}
.btn{display:inline-block;font:700 12px/1 'Bricolage Grotesque',system-ui,sans-serif;
letter-spacing:.3px;text-decoration:none;border-radius:999px;padding:9px 15px;
border:1px solid transparent;-webkit-tap-highlight-color:transparent}
.btn.go{background:#15140F;color:#F2EEE4}
.btn.yes{background:#D8451F;color:#FBF9F4}
.btn.no{background:none;color:#6E675A;border-color:#D6D0C2}
.btn.no:hover{color:#D8451F;border-color:#D8451F}
.btn:active{transform:translateY(1px)}
/* Both pages set body{padding:40px}, which is a third of a phone's width.
   These buttons exist to be tapped on a phone, so the gutter gives way. */
@media(max-width:520px){body{padding:20px 14px}.bar .btn{flex:1;text-align:center}}
.state{font:700 11px/1 'Bricolage Grotesque',system-ui,sans-serif;letter-spacing:.7px;
color:#9A9384;text-transform:uppercase;align-self:center}
"""


def repo() -> str:
    try:
        with open(os.path.join(ROOT, "config.json")) as f:
            return json.load(f).get("repo", "")
    except Exception:
        return ""


def link(command: str) -> str:
    """A prefilled new-issue URL. `command` becomes the title verbatim."""
    return "https://github.com/%s/issues/new?title=%s&body=%s" % (
        repo(), quote(command), quote(BODY))


def button(command: str, label: str, kind: str) -> str:
    return '<a class="btn %s" href="%s" rel="nofollow">%s</a>' % (
        kind, link(command), label)


def bar(post_id, status, rank=None) -> str:
    """The controls a post in this state can actually accept.

    Nothing is offered that the workflow would refuse — a published post has no
    button at all, because the only honest option there is to delete it by hand
    on Instagram, and a button that half-works is worse than no button.
    """
    if status == "queued":
        b = [button("now %s" % post_id, "Post now", "go"),
             button("skip %s" % post_id, "Skip", "no")]
    elif status == "skipped":
        b = ['<span class="state">skipped</span>',
             button("unskip %s" % post_id, "Put it back", "no")]
    elif status == "published":
        b = ['<span class="state">published</span>']
    elif status == "publishing":
        b = ['<span class="state">going out now</span>']
    elif rank:
        # Not approved by the run. The id does not exist yet, so the command
        # has to name the candidate by its rank on this page.
        b = [button("approve %d" % rank, "Approve this one", "yes")]
    else:
        return ""
    return '<div class="bar">%s</div>' % "".join(b)
