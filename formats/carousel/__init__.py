"""Carousel: 1080x1350 slides. Retired from publishing 2026-11-26 (manual only).

Fields: `slides` — same as card. The slides are still rendered for every post,
because they are what the Pages site shows and what `publish.py check` probes;
they just no longer go out as a carousel unless forced with
`publish.py next --format carousel`.
"""
from typing import Any, Dict, List

FIELDS = {"slides": "list[str], 2-3 items"}


def build_stills(post: Dict[str, Any], handle: str, outdir: str) -> List[str]:
    import render
    return render.render_post(post, handle, outdir)
