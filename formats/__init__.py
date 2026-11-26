"""
formats/ — one folder per post type. Each folder is self-contained:

    formats/<name>/__init__.py   build(post, handle, fps) and the field contract
    formats/<name>/sample.json   a post that renders, for previews and tests

    .venv/bin/python -m formats                    list them
    .venv/bin/python -m formats --sample chat      render a folder's sample
    .venv/bin/python -m formats --sample all
    .venv/bin/python -m formats --id g120          an approved post, its own style

A post picks its type with `style` ("card" when absent, which is the original
typed-out text card). reel.render_reel dispatches here, so ensure_reels, the
Release and the publisher never need to know which type a post is.

Reel types return (frames, score, cover): RGB 1080x1920 frames, a timing plan
in the shape audio.render() reads, and a 1080x1920 cover. The carousel folder
is different — it makes 1080x1350 slides — and is kept for manual use only;
publishing has been reels-only since 2026-11-26.
"""
import importlib
import json
import os
from types import ModuleType
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
REELS = ("card", "tweet", "chat", "notify", "comments")
ALL = REELS + ("carousel",)


def get(name: str) -> ModuleType:
    if name not in ALL:
        raise ValueError("unknown format %r — have %s" % (name, ", ".join(ALL)))
    return importlib.import_module("formats.%s" % name)


def sample(name: str) -> Dict[str, Any]:
    with open(os.path.join(HERE, name, "sample.json")) as f:
        return json.load(f)


def describe() -> List[str]:
    return ["%-9s %s" % (n, (get(n).__doc__ or "").strip().splitlines()[0])
            for n in ALL]
