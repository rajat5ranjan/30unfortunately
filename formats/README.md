# formats/

One folder per post type. A post picks one with `"style"`; no style means `card`.

| Folder | What it is | Post fields | Status |
|---|---|---|---|
| `card/` | Text card per slide, typed out, mark at the end (engine in `reel.py`) | `slides` | live |
| `tweet/` | Our own post in the dark-mode post view; punchline lands late, then the like is tapped | `tweet.setup`, `tweet.punch` | new |
| `chat/` | Phone chat thread: "is typing…", bubbles pop in, your lines typed in the input bar | `chat.title`, `chat.messages[{from, text, kind}]` | new |
| `carousel/` | 1080×1350 slides | `slides` | retired from publishing 2026-11-26 |

Each folder has `__init__.py` (a `build()` and its `FIELDS` contract) and a
`sample.json` that renders.

```bash
.venv/bin/python -m formats                      # list
.venv/bin/python -m formats --sample chat        # render a sample to build/reels/
.venv/bin/python -m formats --sample all
.venv/bin/python -m formats --id g120            # an approved post, in its style
```

Chat message kinds: `text` (default), `forwarded`, `deleted`, `voice` (`text` is
the duration, e.g. `"1:47"`). `from: "You"` is the viewer.

Rules every format keeps: everything invented (our handle, fictional names), no
app logo or app name, nothing important under Instagram's chrome (top 190px,
bottom 340px), something moving from frame 0, and a seamless loop.

To add a type: make `formats/<name>/` with `build(post, handle, fps)` returning
`(frames, score, cover)` and a `sample.json`, then add the name to `REELS` in
`formats/__init__.py`.
