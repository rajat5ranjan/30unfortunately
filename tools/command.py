#!/usr/bin/env python3
"""command.py — read a command out of an issue title.

    python3 tools/command.py "skip g006"

The titles come from the buttons in control.py, so they are always well formed;
this exists for the two cases that are not. A title that names a known verb with
a bad argument gets a usage reply, and a title that is not a command at all is
left completely alone — the repo's issues are still issues, and a workflow that
commented on every one of them would make the tracker unusable.

Writes verb/arg/go to $GITHUB_OUTPUT. The argument is validated to a strict
shape here precisely BECAUSE the workflow interpolates it into a shell line
afterwards: an issue title is text a stranger can write, and the owner check in
command.yml is an access control, not an escaping one.
"""
import os
import re
import sys

VERBS = {
    "skip":   ("id",   "skip <id>      take a queued post out of the queue"),
    "unskip": ("id",   "unskip <id>    put a skipped post back"),
    "now":    ("id",   "now <id>       publish a queued post immediately"),
    "approve": ("rank", "approve <n>    promote candidate n from the latest run"),
}

ID_RE = re.compile(r"^[a-z][0-9]{3}$")


def parse(title: str):
    """-> (go, verb, arg). go is 'ok', 'bad' or 'ignore'."""
    words = (title or "").strip().lower().replace(":", " ").split()
    if not words or words[0].lstrip("/") not in VERBS:
        return "ignore", "", ""
    verb = words[0].lstrip("/")
    kind = VERBS[verb][0]
    arg = words[1] if len(words) > 1 else ""
    if kind == "id":
        return ("ok", verb, arg) if ID_RE.match(arg) else ("bad", verb, arg)
    if arg.isdigit() and 1 <= int(arg) <= 50:
        return "ok", verb, str(int(arg))
    return "bad", verb, arg


def usage() -> str:
    return "\n".join(v[1] for v in VERBS.values())


def main() -> int:
    go, verb, arg = parse(sys.argv[1] if len(sys.argv) > 1 else "")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write("go=%s\nverb=%s\narg=%s\n" % (go, verb, arg))
    print("%s: %s %s" % (go, verb, arg) if verb else "ignore: not a command")
    return 0


if __name__ == "__main__":
    sys.exit(main())
