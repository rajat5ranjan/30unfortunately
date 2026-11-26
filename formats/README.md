# formats/

One folder per post type. A post picks one with `"style"`; no style means `card`.

| Folder | What it is | Post fields | Status |
|---|---|---|---|
| `card/` | Text card per slide, typed out, mark at the end (engine in `reel.py`) | `slides` | live |
| `tweet/` | Our own post in the dark-mode post view; punchline lands late, then the like is tapped | `tweet.setup`, `tweet.punch` | live |
| `chat/` | Phone chat thread: "is typing…", bubbles pop in, your lines typed in the input bar | `chat.title`, `chat.messages[{from, text, kind}]` | live |
| `notify/` | Lock screen: notifications drop in one at a time, newest on top; the last is the punchline | `notify.time`, `notify.date`, `notify.items[{app, title, text, time}]` | new |
| `comments/` | A post from an invented page, then its comments one by one; the last gets the most likes and the like | `comments.source`, `comments.post`, `comments.items[{user, text, reply}]` | new |
| `carousel/` | 1080×1350 slides | `slides` | retired from publishing 2026-11-26 |

Each folder has `__init__.py` (a `build()` and its `FIELDS` contract) and a
`sample.json` that renders.

```bash
.venv/bin/python -m formats                      # list
.venv/bin/python -m formats --sample chat        # render a sample to build/reels/
.venv/bin/python -m formats --sample all
.venv/bin/python -m formats --id g120            # an approved post, in its style
```

Notify `app` picks only the icon colour: bank, upi, messages, family, mail,
work, calendar, reminder, news, delivery, food, health, landlord, system.

Which style a post ships as: the judge scores each rendition's fit 1-5
(`style_fit` on the approved post), and `brain.pick_style` takes the best fit,
nudged toward the `reel_styles` mix in config.json. `style_why` records why.

Chat message kinds: `text` (default), `forwarded`, `deleted`, `voice` (`text` is
the duration, e.g. `"1:47"`). `from: "You"` is the viewer.

Rules every format keeps: everything invented (our handle, fictional names), no
app logo or app name, nothing important under Instagram's chrome (top 190px,
bottom 340px), something moving from frame 0, and a seamless loop.

To add a type: make `formats/<name>/` with `build(post, handle, fps)` returning
`(frames, score, cover)` and a `sample.json`, then add the name to `REELS` in
`formats/__init__.py`.
