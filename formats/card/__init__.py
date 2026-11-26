"""The original reel: a text card per slide, typed out, the mark at the end.

Fields: `slides` — 2-3 strings (hook, middle, landing). The engine still lives in
reel.py, because render.py and the carousel share its drawing code; this folder
is its entry in the registry so every type loads the same way.
"""
from typing import Any, Dict, List, Tuple

FIELDS = {"slides": "list[str], 2-3 items: hook, body, landing"}


def build(post: Dict[str, Any], handle: str, fps: int) -> Tuple[List[Any], Dict[str, Any], Any]:
    import reel
    sc = reel.plan(post)
    return ([f.convert("RGB") for f in reel.build_frames(post, handle, sc)], sc,
            reel.cover_image(post))
