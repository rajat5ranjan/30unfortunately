"""Preview any format: `python -m formats --sample chat` (see formats/__init__.py)."""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import formats  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m formats")
    ap.add_argument("--sample", help="a format name, or 'all'")
    ap.add_argument("--id", help="an approved post id")
    ap.add_argument("--stills", action="store_true", help="storyboard PNGs, no encode")
    args = ap.parse_args()

    if not args.sample and not args.id:
        print("\n".join(formats.describe()))
        return 0

    import reel
    import render
    if args.id:
        path = os.path.join(ROOT, "content", "approved", "%s.json" % args.id)
        blob = json.load(open(path))
        posts = [p for p in blob["posts"] if p.get("id") == args.id]
        for p in posts:
            reel.render_reel(p, blob.get("handle", "@30unfortunately"), args.stills)
        return 0

    names = formats.ALL if args.sample == "all" else [args.sample]
    for name in names:
        post = formats.sample(name)
        if name == "carousel":
            out = os.path.join(ROOT, "build", "preview", "carousel")
            print("carousel ->", render.render_post(post, "@30unfortunately", out))
            continue
        post = dict(post, style=name)
        reel.render_reel(post, "@30unfortunately", args.stills, preview=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
