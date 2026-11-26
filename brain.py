#!/usr/bin/env python3
"""
brain.py — the content agent for Thirty Unfortunately.

    python3 brain.py --dry-run     print the assembled prompt, no API call, no key needed
    python3 brain.py --check       run every gate over the approved seed posts (self-test)
    python3 brain.py               generate candidates from content/trends.json
    python3 brain.py --trends x.json --n 3

Deliberately one file. Modules get split out when they earn it, not before.
Python 3.9 compatible.
"""
import argparse
import difflib
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import yaml
from html import escape as html_escape

import control
import store

import envfile

ROOT = os.path.dirname(os.path.abspath(__file__))
BRAND = os.path.join(ROOT, "brand.yaml")
CONFIG = os.path.join(ROOT, "config.json")
SEED = os.path.join(ROOT, "content", "seed_posts.json")
APPROVED_DIR = os.path.join(ROOT, "content", "approved")
CANDIDATE_DIR = os.path.join(ROOT, "content", "candidates")
LEGACY = os.path.join(ROOT, "content", "legacy_failures.json")

EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF←-⇿⬀-⯿]"
)


# --------------------------------------------------------------------------- io
def load_brand() -> Dict[str, Any]:
    with open(BRAND) as f:
        return yaml.safe_load(f)


def load_config() -> Dict[str, Any]:
    with open(CONFIG) as f:
        return json.load(f)


def load_history() -> List[Dict[str, Any]]:
    """Everything the account has already said. Seed posts plus anything approved."""
    posts = []
    if os.path.exists(SEED):
        with open(SEED) as f:
            posts.extend(json.load(f).get("posts", []))
    for path in sorted(glob.glob(os.path.join(APPROVED_DIR, "*.json"))):
        with open(path) as f:
            blob = json.load(f)
            posts.extend(blob if isinstance(blob, list) else blob.get("posts", []))
    # One spelling per target, here rather than at the gate: the files on disk
    # are the record of what was approved and are not rewritten, so the
    # collapsing happens every time they are read. Everything that divides by
    # a target count — the self cap, the "aimed at" breakdown — reads through
    # this function.
    for p in posts:
        if p.get("target"):
            p["target"] = store.normalise_target(p["target"])
    return posts


# ------------------------------------------------------------------------ gates

def _flat(post: Dict[str, Any]) -> str:
    return " ".join(post.get("slides", []))


def gate_hard_bans(post: Dict[str, Any], brand: Dict[str, Any]) -> List[str]:
    fails = []
    # Phrases are banned from the whole post, caption included — "adulting" in
    # the caption is the same tell as "adulting" on a card, and _flat() reads
    # only the slides, so the caption used to walk straight past this.
    body = (_flat(post) + " " + (post.get("caption") or "")).lower()
    for phrase in brand["hard_bans"]["phrases"]:
        if phrase.lower() in body:
            fails.append("banned phrase: '%s'" % phrase)
    # Borrowed slang is a separate list because it fails for a different
    # reason: not that it is a cliche, but that it dates the writer. Only the
    # unambiguous tokens are in the list — see the note above it in brand.yaml.
    for word in brand["hard_bans"].get("slang") or []:
        if word.lower() in body:
            fails.append("borrowed slang: '%s' — mechanics, not vocabulary"
                         % word)
    # Emoji, unlike phrases, ARE allowed in the caption and only there.
    if EMOJI.search(_flat(post)):
        fails.append("emoji in card art (captions only)")
    return fails


PRICE = re.compile(r"[\u20b9$]\s?\d[\d,]*")
# A quote mark standing where a rupee sign should be. The model emitted
# "\u20192,999" on 2026-09-21 and both the price gate and the renderer would
# have taken it at face value — a card reading \u20192,999 in Bricolage Grotesque
# at 96px. Narrow on purpose: it only fires before a figure that looks like
# money, so "the '90s" and "'26 batch" are untouched.
MONEY = r"\d{1,3}(?:,\d{2,3})+|\d{3,}"
CURRENCY_LOOKALIKE = re.compile(
    r"[\u2018\u2019\u201b\u0060\u00b4']\s?(?:%s)" % MONEY)
TITLE_CARD = re.compile(
    r"^\s*(there\s+(is|are)|let'?s|let\s+us|here\s+(is|are)|presenting|"
    r"introducing|a\s+look\s+at|the\s+story\s+of)\b", re.I)


def gate_one_price(post: Dict[str, Any], brand: Dict[str, Any]) -> List[str]:
    """A column of rupee amounts is a spreadsheet, not a joke.

    Counts currency figures only. Percentages, years and ages are comparisons
    and are left alone — "8% salary, 15% blood sugar" is the good version of
    using two numbers, and a gate that cannot tell them apart would take the
    best post in the corpus down with the worst.
    """
    body = _flat(post)
    # Lookalikes count: a mangled rupee sign is still a price, and a receipt
    # of four of them is still a spreadsheet.
    found = PRICE.findall(body) + CURRENCY_LOOKALIKE.findall(body)
    if len(found) > 1:
        return ["%d prices in one post (%s) — the reader has to do arithmetic "
                "to reach the joke. One figure, alone, does the work"
                % (len(found), ", ".join(found[:4]))]
    return []


def gate_currency_is_real(post: Dict[str, Any],
                          brand: Dict[str, Any]) -> List[str]:
    """A rupee sign that is actually an apostrophe.

    Nothing downstream would have caught it. The price gate counts currency
    marks and would not see one; render.py draws whatever string it is given,
    so the card ships with a quote mark where the money should be.
    """
    hit = CURRENCY_LOOKALIKE.search(_flat(post) + " " + (post.get("caption") or ""))
    if hit:
        return ["a quote mark is standing in for a currency sign — write "
                "\u20b9 or leave the figure bare"]
    return []


def gate_hook_is_not_a_title_card(post: Dict[str, Any],
                                  brand: Dict[str, Any]) -> List[str]:
    """Slide 1 announcing that a joke is coming, instead of being one.

    Only the openings that are pure throat-clearing are machine-checked; the
    wider habit is prose in slides.slide_1_is_a_HOOK, because "is this an
    announcement" is a judgement and this is a regex.
    """
    hook = (post.get("slides") or [""])[0]
    if TITLE_CARD.match(hook):
        return ["slide 1 opens as a title card, not a hook — it announces "
                "that a joke is coming instead of being one"]
    return []


# Hyphens allowed: "co-living" is how people type it. Nothing else,
# because every other punctuation mark in a keyword is a hashtag
# trying to get in.
KEYWORD = re.compile(r"^[a-z0-9][a-z0-9 -]{1,28}[a-z0-9]$")


def gate_keywords(post: Dict[str, Any], brand: Dict[str, Any]) -> List[str]:
    """Three plain search terms, and nothing that looks like a hashtag.

    Shape only. Whether "co-living bangalore" is a term anybody types is a
    judgement, and this is a regex — the judgement lives in
    discoverability.keywords, where the model can read it.
    """
    kws = post.get("keywords")
    if kws is None:
        return []          # predates the field; the response schema requires it
    if not isinstance(kws, list) or len(kws) != 3:
        return ["keywords must be exactly three terms, got %s"
                % (len(kws) if isinstance(kws, list) else type(kws).__name__)]
    bad = [k for k in kws if not KEYWORD.match((k or "").strip())]
    if bad:
        return ["keywords must be plain lowercase words a person would type, "
                "no hash and no camel case: %s" % ", ".join(repr(b) for b in bad)]

    # Reachability, not shape. A keyword becomes a hashtag, and a hashtag with
    # ten million posts in it is a lottery this account does not win: forty-four
    # posts in, saves were zero and shares were eight. Single words are where
    # that happens — nineteen of the first ninety-three keywords were one word,
    # and they were "yoga", "fitness", "health", "friends", "parenting".
    disc = brand.get("discoverability", {})
    floor = disc.get("min_keyword_words", 2)
    thin = [k for k in kws
            if len([w for w in re.split(r"[ -]+", (k or "").strip()) if w]) < floor]
    if thin:
        return ["keywords must be at least %d words — a one-word tag is a head "
                "term this account cannot rank in: %s"
                % (floor, ", ".join(repr(t) for t in thin))]

    heads = set(disc.get("head_terms") or [])
    hit = [k for k in kws if (k or "").strip().lower() in heads]
    if hit:
        return ["keywords are banned head terms, too broad to reach anybody "
                "from: %s" % ", ".join(repr(h) for h in hit)]
    return []


def gate_move_is_declared(post: Dict[str, Any], brand: Dict[str, Any]) -> List[str]:
    """Every post has to say which move it is running.

    Not a quality check — a visibility one. The structure field said how a
    post was laid out and nothing said why it was funny, so the account ran
    one joke eleven times under nine structure names and no rule could see
    it. Declaring the move is what lets rank() space them out.
    """
    known = brand["reply_moves"]["moves"]
    move = post.get("move")
    if not move:
        # Silent for the back catalogue: the field did not exist when those
        # were written, and baselining thirty-four posts to say so would bury
        # the signal this check exists to give. New posts cannot skip it —
        # RESPONSE_SCHEMA marks move required, so the model must emit one.
        return []
    if move not in known:
        return ["unknown move '%s' — one of: %s"
                % (move, ", ".join(sorted(known)))]
    return []


def gate_shape(post: Dict[str, Any], brand: Dict[str, Any]) -> List[str]:
    fails = []
    slides = post.get("slides") or []
    if not 2 <= len(slides) <= 3:
        fails.append("slide count %d (must be 2 or 3)" % len(slides))
        return fails
    hook = slides[0]
    max_hook = brand["slides"]["slide_1_is_a_HOOK"]["max_chars"]
    # From the contract, not from here. A gate enforcing a number the writer was
    # never given is not a gate, it is a trap — and it silently emptied the
    # queue for two days.
    max_slide = brand["slides"]["max_chars_per_slide"]
    if len(hook) > max_hook:
        fails.append("hook is %d chars (max %d) — it is carrying the joke, not opening a loop"
                     % (len(hook), max_hook))
    if hook.rstrip().endswith("?") and len(hook) < 30:
        fails.append("hook is a bare question — weak open loop")
    for i, s in enumerate(slides):
        if len(s) > max_slide:
            fails.append("slide %d is %d chars (max %d) — cut lines, not the joke"
                         % (i + 1, len(s), max_slide))
    landing = slides[-1]
    if landing.rstrip().endswith("?"):
        fails.append("landing ends on a question — this account states, it does not beg")
    # Hinglish in this brand is defined as reported speech, so it is concrete by construction.
    if not post.get("hinglish") and not re.search(r"[0-9₹]|\"|'", _flat(post)):
        fails.append("no concrete detail (no number, rupee figure or quoted speech) — too generic")
    return fails


# Bare verbs that start a command. Used only to measure the MIX — a single
# imperative hook is fine and several of the best posts open that way.
IMPERATIVES = set("""read place list follow go open compare name check count look
ask try watch tell find write add call send describe explain pick take think
imagine picture remember consider""".split())

STOPWORDS = set("""a an the and or but if of to in on at for with from by is are
was were be been being it its this that these those you your yours he she they
them his her their not no nor so as than then there here what which who whom
have has had do does did will would can could should may might must just only
now still even more most much many one two three all any some each every""".split())


def is_imperative(hook: str) -> bool:
    w = re.sub(r"[^a-z\s]", " ", (hook or "").lower()).split()
    return bool(w) and w[0] in IMPERATIVES


COUPLET_MAX_WORDS = 12


def is_couplet(slides: List[str]) -> bool:
    """Does the post land on two balanced short lines with a pause between?

    The shape the old landing rule asked for by name, and 84% of the first
    fifty-eight posts delivered: two sentences, both under a dozen words,
    separated by a blank line, averaging 6.3 and 6.6 words. Correct every
    time and identical every time, which is the whole problem — the account
    reads as verse rather than as somebody talking.

    Measured on the rendered slide rather than on the structure name, because
    the structures that produce it have eight different names. Same lesson as
    the parallel lists: name the shape, not the label.
    """
    last = (slides or [""])[-1] or ""
    if "\n\n" not in last:
        return False
    parts = [x.strip() for x in last.split("\n\n") if x.strip()]
    if len(parts) != 2:
        return False
    return all(len(x.split()) <= COUPLET_MAX_WORDS for x in parts)


def _words(text: str) -> List[str]:
    return [w for w in re.sub(r"[^a-z\s]", " ", (text or "").lower()).split()
            if len(w) > 3 and w not in STOPWORDS]


def gate_landing_turns(post: Dict[str, Any], brand: Dict[str, Any]) -> List[str]:
    """The landing has to bring in a word the post has not used.

    Catches the literal version of the failure: a last slide assembled entirely
    from the vocabulary of the first two, which is a restatement wearing a
    pause. "She still treats you like an unstable chemical" passes because
    `chemical` arrives from nowhere, and that is the whole joke.

    It does NOT catch a gloss — "Your body is an invoice for your twenties. /
    You are paying it off in monthly instalments." shares no words with itself
    and sails through. A paraphrase reuses the idea, not the vocabulary, and no
    amount of string comparison sees that. The rule is in brand.yaml and the
    judge scores it under surprise and laugh; pretending a regex covers it
    would be worse than admitting it does not.
    """
    slides = post.get("slides") or []
    if len(slides) < 2:
        return []
    earlier = set(w for s in slides[:-1] for w in _words(s))
    if not [w for w in _words(slides[-1]) if w not in earlier]:
        return ["the landing introduces no word the post has not already used "
                "— it restates instead of turning"]
    return []


LANDING_FILLER = {"anyway", "anyways", "still", "again", "just", "too", "either",
                  "though", "somehow", "apparently"}
STOCK_LANDING = re.compile(r"\byou are not\b[^.]*\.\s*you are\b", re.I | re.S)


def gate_landing_lands(post: Dict[str, Any], brand: Dict[str, Any]) -> List[str]:
    """The two mechanical tells of a landing that is not a punchline.

    The surprise belongs in the last words, so a landing ending on filler has
    buried it ("He finished the tub anyway."). And "You are not X. You are Y."
    is the account's stock template for stating a theme — the research on LLM
    humour says models converge on a few such shapes and stop being funny.
    Everything subtler (does it reinterpret, does it connect) is the judge's
    `lands` score, because a regex cannot see it. Added 2026-11-26.
    """
    slides = post.get("slides") or []
    if not slides:
        return []
    fails = []
    words = re.findall(r"[A-Za-z']+", slides[-1].lower())
    if words and words[-1] in LANDING_FILLER:
        fails.append("landing ends on filler '%s' — the funniest word goes last"
                     % words[-1])
    if STOCK_LANDING.search(slides[-1]):
        fails.append("landing is the stock 'You are not X. You are Y.' — states "
                     "the theme instead of landing a joke")
    return fails


QUOTED = re.compile(r'"[^"]*"|\u201c[^\u201d]*\u201d')


def plain_language_issues(text: str, brand: Dict[str, Any],
                          long_words: bool = True) -> List[str]:
    """Too-long sentences and big words, measured OUTSIDE quoted speech.

    Quotes are exempt on purpose: a manager saying "structural realignment" is
    the joke; the narration saying it is the account sounding like the manager.
    """
    rules = brand["plain_language"]
    narration = QUOTED.sub(" ", text)
    out = []
    for sent in re.split(r"[.!?\n]+", narration):
        n = len(sent.split())
        if n > rules["max_sentence_words"]:
            out.append("a %d-word sentence (max %d): '%s'"
                       % (n, rules["max_sentence_words"], " ".join(sent.split()[:8]) + "…"))
    if long_words:
        big = [w for w in re.findall(r"[A-Za-z]+", narration) if len(w) >= 11]
        if len(big) > rules["max_long_words"]:
            out.append("%d big words outside quotes (%s) — say it simply"
                       % (len(big), ", ".join(big[:4])))
    return out


def gate_plain_language(post: Dict[str, Any], brand: Dict[str, Any]) -> List[str]:
    """Hinglish or very simple English — the owner's rule of 2026-11-26."""
    return plain_language_issues("\n".join(post.get("slides") or []), brand)


def gate_target(post: Dict[str, Any], brand: Dict[str, Any]) -> List[str]:
    """Who the joke is on. Not the value of it — only that it was decided.

    The share of posts aimed at the reader is a property of the mix, not of one
    post, so the cap is enforced in rank() where the other mix penalties live.
    Here we only refuse a post that never answered the question, because
    "target: self" chosen on purpose is a different thing from not having
    thought about it, and the first is allowed.
    """
    t = (post.get("target") or "").strip().lower()
    if not t:
        return ["no target — who is this joke ON? See the target section."]
    if len(t.split()) > 3:
        return ["target is a sentence, not a target: '%s'" % t]
    return []


def gate_travels(post: Dict[str, Any], brand: Dict[str, Any]) -> List[str]:
    """The one mechanically checkable half of the travels rule.

    Everything else about whether a post crosses a border is a judgement call
    and lives in the ranker. This is not: a rupee figure or a brand name in the
    LANDING means the final beat depends on knowing what that thing costs or
    what that company does, and the post dies at the border. In the escalation
    the same detail is texture and is wanted.
    """
    landing = (post.get("slides") or [""])[-1]
    # A currency mark, or a digit group written with a separator. Bare four
    # digits are years — "In 2026 an app auto-rejects your WFH" is a date, and
    # an earlier version of this rule threw it out as a price.
    if re.search(r"[₹$]\s?[0-9]|[0-9]{1,3},[0-9]{2,3}", landing):
        return ["landing carries a currency figure — that beat cannot travel; "
                "move the number into the escalation"]
    return []


def gate_source(post: Dict[str, Any]) -> List[str]:
    src = (post.get("source") or "").strip()
    if not src:
        return ["missing source"]
    if src != "evergreen" and not src.startswith("http"):
        return ["source must be a URL or the literal string 'evergreen'"]
    return []


def gate_novelty(post: Dict[str, Any], history: List[Dict[str, Any]],
                 threshold: float) -> List[str]:
    """The failure mode that actually kills these accounts: saying it again."""
    body = _flat(post)
    worst = (0.0, None)  # type: Tuple[float, Optional[str]]
    for old in history:
        r = difflib.SequenceMatcher(None, body.lower(), _flat(old).lower()).ratio()
        if r > worst[0]:
            worst = (r, old.get("id"))
        angle_r = difflib.SequenceMatcher(
            None, (post.get("angle") or "").lower(), (old.get("angle") or "").lower()
        ).ratio()
        if angle_r > threshold:
            return ["angle repeats %s (%.0f%% similar)" % (old.get("id"), angle_r * 100)]
    if worst[0] > threshold:
        return ["too close to %s (%.0f%% similar)" % (worst[1], worst[0] * 100)]
    return []


def structure_report(posts: List[Dict[str, Any]], cap: float,
                     enforce_from: int) -> Tuple[Dict[str, float], List[str]]:
    counts = {}  # type: Dict[str, int]
    for p in posts:
        k = p.get("structure", "unknown")
        counts[k] = counts.get(k, 0) + 1
    total = max(len(posts), 1)
    shares = dict((k, v / total) for k, v in counts.items())
    warnings = []
    for k, share in sorted(shares.items(), key=lambda kv: -kv[1]):
        if share > cap:
            verb = "OVER CAP" if total >= enforce_from else "over cap (not enforced yet, n<%d)" % enforce_from
            warnings.append("%s: %.0f%% %s" % (k, share * 100, verb))
    return shares, warnings


def run_gates(post: Dict[str, Any], brand: Dict[str, Any], history: List[Dict[str, Any]],
              cfg: Dict[str, Any]) -> List[str]:
    return (gate_hard_bans(post, brand) + gate_shape(post, brand)
            + gate_target(post, brand) + gate_travels(post, brand)
            + gate_landing_turns(post, brand) + gate_landing_lands(post, brand)
            + gate_plain_language(post, brand)
            + gate_one_price(post, brand)
            + gate_hook_is_not_a_title_card(post, brand)
            + gate_currency_is_real(post, brand)
            + gate_move_is_declared(post, brand)
            + gate_keywords(post, brand)
            + gate_source(post) + gate_novelty(post, history, cfg["novelty_threshold"]))


# ------------------------------------------------------------------- renditions
# A post is always shippable as the text card. The tweet and the chat are extra
# renditions of the same joke (brand.yaml `renditions`); one that breaks its own
# rules is dropped, not the post, so a clumsy chat can never cost a good joke.
SPECIAL_KINDS = ("forwarded", "deleted", "voice")
NOTIFY_APPS = ("bank", "upi", "messages", "family", "mail", "work", "calendar",
               "reminder", "news", "delivery", "food", "health", "landlord", "system")
STYLES = ("card", "tweet", "chat", "notify", "comments")
ME_NAMES = {"you", "me", "main", "mai", "self"}


def _banned_in(text: str, brand: Dict[str, Any]) -> List[str]:
    low = text.lower()
    words = list(brand["hard_bans"]["phrases"]) + list(brand["hard_bans"].get("slang") or [])
    return [w for w in words if w.lower() in low]


def vet_renditions(post: Dict[str, Any], brand: Dict[str, Any]) -> List[str]:
    """Check the tweet and chat renditions; drop any that fail. Returns notes."""
    notes = []
    R = brand["renditions"]

    t = post.get("tweet")
    if t is not None:
        why = []
        if not isinstance(t, dict):
            why.append("not an object")
        else:
            setup, punch = (t.get("setup") or "").strip(), (t.get("punch") or "").strip()
            t["setup"], t["punch"] = setup, punch
            if not setup or not punch:
                why.append("setup or punch missing")
            if len(setup) > R["tweet"]["max_setup_chars"]:
                why.append("setup %d chars (max %d)" % (len(setup), R["tweet"]["max_setup_chars"]))
            if len(punch) > R["tweet"]["max_punch_chars"]:
                why.append("punch %d chars (max %d)" % (len(punch), R["tweet"]["max_punch_chars"]))
            if punch.endswith("?"):
                why.append("punch ends on a question")
            why += plain_language_issues(setup + "\n" + punch, brand)
            if EMOJI.search(setup + punch):
                why.append("emoji")
            why += ["banned: '%s'" % w for w in _banned_in(setup + " " + punch, brand)]
        if why:
            post.pop("tweet", None)
            notes.append("tweet dropped: " + "; ".join(why))

    c = post.get("chat")
    if c is not None:
        why = []
        msgs = c.get("messages") if isinstance(c, dict) else None
        if not isinstance(msgs, list):
            why.append("no messages")
            msgs = []
        lo, hi = R["chat"]["min_messages"], R["chat"]["max_messages"]
        if msgs and not lo <= len(msgs) <= hi:
            why.append("%d messages (need %d-%d)" % (len(msgs), lo, hi))
        if isinstance(c, dict) and not (c.get("title") or "").strip():
            why.append("no title")
        specials = 0
        for m in msgs:
            kind = (m.get("kind") or "text").lower()
            m["kind"] = kind if kind in ("text",) + SPECIAL_KINDS else "text"
            specials += m["kind"] in SPECIAL_KINDS
            if (m.get("from") or "").strip().lower() in ME_NAMES:
                m["from"] = "You"
            text = (m.get("text") or "").strip()
            m["text"] = text
            if not text and m["kind"] != "deleted":
                why.append("an empty message")
            if len(text) > R["chat"]["max_message_chars"]:
                why.append("a %d-char message (max %d)" % (len(text), R["chat"]["max_message_chars"]))
            if EMOJI.search(text):
                why.append("emoji")
            why += ["banned: '%s'" % w for w in _banned_in(text, brand)]
            if m.get("kind") == "text":
                why += plain_language_issues(text, brand, long_words=False)
        if specials > 1:
            why.append("%d special kinds (max 1)" % specials)
        if msgs and msgs[-1]["kind"] == "deleted":
            why.append("ends on a deleted message")
        if why:
            post.pop("chat", None)
            notes.append("chat dropped: " + "; ".join(sorted(set(why))))

    n = post.get("notify")
    if n is not None:
        why, N = [], R["notify"]
        items = n.get("items") if isinstance(n, dict) else None
        if not isinstance(items, list):
            why.append("no items")
            items = []
        if items and not N["min_items"] <= len(items) <= N["max_items"]:
            why.append("%d items (need %d-%d)" % (len(items), N["min_items"], N["max_items"]))
        for it in items:
            it["app"] = (it.get("app") or "messages").strip().lower()
            if it["app"] not in NOTIFY_APPS:
                it["app"] = "messages"
            title, text = (it.get("title") or "").strip(), (it.get("text") or "").strip()
            it["title"], it["text"] = title, text
            if not title or not text:
                why.append("an item without title or text")
            if len(title) > N["max_title_chars"]:
                why.append("a %d-char title (max %d)" % (len(title), N["max_title_chars"]))
            if len(text) > N["max_text_chars"]:
                why.append("a %d-char item (max %d)" % (len(text), N["max_text_chars"]))
            if EMOJI.search(title + text):
                why.append("emoji")
            why += ["banned: '%s'" % w for w in _banned_in(title + " " + text, brand)]
            why += plain_language_issues(text, brand, long_words=False)
        if why:
            post.pop("notify", None)
            notes.append("notify dropped: " + "; ".join(sorted(set(why))))

    c = post.get("comments")
    if c is not None:
        why, C = [], R["comments"]
        items = c.get("items") if isinstance(c, dict) else None
        if not isinstance(items, list):
            why.append("no items")
            items = []
        if items and not C["min_items"] <= len(items) <= C["max_items"]:
            why.append("%d comments (need %d-%d)" % (len(items), C["min_items"], C["max_items"]))
        if isinstance(c, dict):
            body = (c.get("post") or "").strip()
            c["post"] = body
            if not body or not (c.get("source") or "").strip():
                why.append("no post or no source")
            if len(body) > C["max_post_chars"]:
                why.append("post %d chars (max %d)" % (len(body), C["max_post_chars"]))
            why += plain_language_issues(body, brand, long_words=False)
            texts = [body]
        else:
            texts = []
        if sum(1 for it in items if it.get("reply")) > 2:
            why.append("more than two replies")
        for it in items:
            text = (it.get("text") or "").strip()
            it["text"] = text
            if not text or not (it.get("user") or "").strip():
                why.append("a comment without user or text")
            if len(text) > C["max_comment_chars"]:
                why.append("a %d-char comment (max %d)" % (len(text), C["max_comment_chars"]))
            why += plain_language_issues(text, brand, long_words=False)
            texts.append(text)
        joined = " ".join(texts)
        if EMOJI.search(joined):
            why.append("emoji")
        why += ["banned: '%s'" % w for w in _banned_in(joined, brand)]
        if why:
            post.pop("comments", None)
            notes.append("comments dropped: " + "; ".join(sorted(set(why))))
    return notes


def available_styles(post: Dict[str, Any]) -> List[str]:
    return ["card"] + [s for s in STYLES[1:] if post.get(s)]


STYLE_WINDOW = 30        # recent styled posts the mix is measured over
STYLE_BALANCE = 5.0      # fit points per unit of share behind target
STYLE_MIN_FIT = 3        # a new shape must be at least as good as the card
STYLE_PRIOR = 10         # pseudo-posts at the target mix, so three posts in a
                         # row of one style read as "a bit ahead", not "100%"


def style_counts(approved_dir: str = None) -> Dict[str, int]:
    """Styles of the last STYLE_WINDOW approved posts that carry one."""
    rows = []
    for path in glob.glob(os.path.join(approved_dir or APPROVED_DIR, "*.json")):
        with open(path) as f:
            for p in json.load(f).get("posts", []):
                if p.get("style"):
                    rows.append((p.get("id", ""), p["style"]))
    counts = dict((st, 0) for st in STYLES)
    for _, st in sorted(rows)[-STYLE_WINDOW:]:
        counts[st] = counts.get(st, 0) + 1
    return counts


def pick_style(post: Dict[str, Any], cfg: Dict[str, Any],
               approved_dir: str = None, explain: bool = False):
    """The reel style this post ships as. The judge says what fits; code decides.

    Each candidate style scores its fit (the judge's 1-5 for this joke in that
    shape, `style_fit`; 3 when the judge said nothing) plus a pull toward the
    target mix in config `reel_styles`: STYLE_BALANCE points per unit of share
    the style is behind its weight over the last STYLE_WINDOW posts. So a
    clearly better shape wins, and between near-equals the one the account is
    short of wins — which keeps every style getting posts to measure.

    A tweet/chat/notify/comments rendition the judge scored under
    STYLE_MIN_FIT is not used: a forced shape is worse than the plain card.
    The card is always allowed; it is the control and the fallback. A weight of
    0 retires a style. Ties go chat, notify, comments, tweet, card.
    """
    mix = cfg.get("reel_styles") or {"chat": 1, "tweet": 1, "card": 1}
    fit = post.get("style_fit") or {}
    counts = style_counts(approved_dir)
    total = float(sum(counts.values()))
    wsum = float(sum(w for w in mix.values() if w > 0) or 1)
    order = ["chat", "notify", "comments", "tweet", "card"]
    options = [st for st in available_styles(post) if mix.get(st, 0) > 0
               and (st == "card" or fit.get(st, 3) >= STYLE_MIN_FIT)] or ["card"]

    def value(st: str) -> float:
        target = mix.get(st, 0) / wsum
        share = (counts.get(st, 0) + STYLE_PRIOR * target) / (total + STYLE_PRIOR)
        return fit.get(st, 3) + STYLE_BALANCE * (target - share)

    best = max(options, key=lambda st: (round(value(st), 6),
                                        -(order.index(st) if st in order else 9)))
    if not explain:
        return best
    why = "fit %s, %d of last %d" % (fit.get(best, "-"), counts.get(best, 0), int(total)) \
        + ("; over " + ", ".join("%s %.1f" % (st, value(st)) for st in options if st != best)
           if len(options) > 1 else "; only option")
    return best, why

# ----------------------------------------------------------------------- prompt
# Everything here is sent to the writer. A section added to brand.yaml and not
# added here is invisible to the model while looking, in the file, exactly like
# a rule — `target` and `travels` both shipped that way, and `territory` nearly
# did. NOT_A_RULE names the sections that are deliberately withheld, so the
# check below can tell an omission from a decision.
CONTRACT_SECTIONS = ("identity", "audience", "formula", "slides", "target", "voice",
                     "hinglish", "travels", "territory", "reply_moves",
                     "satire_ladder", "structures", "hard_bans",
                     "discoverability", "plain_language", "renditions",
                     "output_schema")
NOT_A_RULE = ("trend_gate",           # applied to trends before the writer runs
              "few_shot_examples")    # a path, and the examples are sent separately


def check_contract_is_whole(brand: Dict[str, Any]) -> List[str]:
    unseen = [k for k in brand
              if k not in CONTRACT_SECTIONS and k not in NOT_A_RULE]
    return ["brand.yaml section '%s' is never sent to the model — add it to "
            "CONTRACT_SECTIONS, or to NOT_A_RULE if that is deliberate" % k
            for k in unseen]


def build_system_prompt(brand: Dict[str, Any], history: List[Dict[str, Any]],
                        cfg: Dict[str, Any]) -> str:
    # The examples set the register harder than any rule does, so pick the
    # ones that sound like the account should now: Hinglish first, then the
    # plainest (fewest big words, shortest sentences). Stable sort, so ties
    # keep their order.
    def _plainness(p):
        issues = plain_language_issues("\n".join(p.get("slides") or []), brand)
        return (0 if p.get("hinglish") else 1, len(issues))
    examples = sorted(history, key=_plainness)[: cfg["few_shot_count"]]
    shares, _ = structure_report(history, brand["structures"]["max_share_of_output"],
                                 cfg["structure_enforce_from"])
    hinglish_share = (sum(1 for p in history if p.get("hinglish")) / max(len(history), 1))
    # Untargeted posts predate this field; counting them as 'self' is right —
    # that is what they were, and the share is meant to shame the batch.
    self_share = (sum(1 for p in history if (p.get("target") or "self") == "self")
                  / max(len(history), 1))
    voices = set(brand["structures"]["voices"])
    voice_share = (sum(1 for p in history if p.get("structure") in voices)
                   / max(len(history), 1))
    imp_share = (sum(1 for p in history if is_imperative((p.get("slides") or [""])[0]))
                 / max(len(history), 1))
    couplet_share = (sum(1 for p in history if is_couplet(p.get("slides") or []))
                     / max(len(history), 1))

    ex_blocks = []
    for p in examples:
        ex_blocks.append(
            "TREND: %s\nANGLE: %s\nSLIDES:\n%s\nSTRUCTURE: %s | SATIRE: %s | HINGLISH: %s"
            % (p["trend"], p["angle"],
               "\n---\n".join(p["slides"]), p.get("structure"), p.get("satire_level"),
               p.get("hinglish"))
        )

    return "\n\n".join([
        "You write for an Instagram account. This is the entire contract. Follow it exactly.",
        yaml.safe_dump({k: brand[k] for k in CONTRACT_SECTIONS},
                       sort_keys=False, allow_unicode=True, width=100),
        "APPROVED EXAMPLES — match this register, never reuse these jokes:\n\n"
        + "\n\n===\n\n".join(ex_blocks),
        "WHAT THE ACCOUNT HAS OVERUSED SO FAR (steer away from the top entries):\n"
        + "\n".join("  %s: %.0f%%" % (k, v * 100) for k, v in
                    sorted(shares.items(), key=lambda kv: -kv[1]))
        + "\n  hinglish: %.0f%% (target %s)" % (hinglish_share * 100,
                                                brand["hinglish"]["target_ratio"])
        + "\n  jokes aimed at the reader ('self'): %.0f%% (cap %.0f%%)"
          % (self_share * 100, brand["target"]["self_cap"] * 100)
        + "\n  posts with somebody SPEAKING in them: %.0f%% (floor %.0f%%)"
          % (voice_share * 100, brand["structures"]["min_voice_share"] * 100)
        + "\n  hooks that open with a command: %.0f%% (cap %.0f%%)"
          % (imp_share * 100,
             brand["slides"]["slide_1_is_a_HOOK"]["max_imperative_share"] * 100)
        + "\n  posts landing on a two-line couplet: %.0f%% (cap %.0f%%)"
          % (couplet_share * 100,
             brand["slides"]["last_slide_is_the_LANDING"]["max_couplet_share"] * 100),
        "Return ONLY JSON matching the schema. No preamble, no markdown fence.",
    ])


def build_user_prompt(trends: List[Dict[str, Any]], n: int) -> str:
    lines = ["Here are today's candidate trends.", ""]
    for i, t in enumerate(trends, 1):
        lines.append("%d. TREND: %s" % (i, t["trend"]))
        lines.append("   SOURCE: %s" % t.get("source", "evergreen"))
        if t.get("note"):
            lines.append("   NOTE: %s" % t["note"])
        lines.append("")
    lines += [
        "For each trend you judge usable, write %d DIFFERENT posts — different angle," % n,
        "different structure, not three phrasings of one joke.",
        "",
        "Discard any trend that fails the gate or has no honest connection to being 30",
        "in India. Discarding is correct and expected. Say which you discarded and why.",
    ]
    return "\n".join(lines)


RANK_SCHEMA = {
    "type": "object",
    "properties": {"scores": {"type": "array", "items": {"type": "object", "properties": {
        "index": {"type": "integer"},
        "laugh": {"type": "integer"},
        "share_trigger": {"type": "integer"},
        "specificity": {"type": "integer"},
        "surprise": {"type": "integer"},
        "voice": {"type": "integer"},
        "travels": {"type": "integer"},
        "lands": {"type": "integer"},
        "plain": {"type": "integer"},
        "best_landing": {"type": "integer"},
        "fit": {"type": "object", "properties": dict(
            (st, {"type": "integer"}) for st in STYLES)},
        "verdict": {"type": "string"},
    }, "required": ["index", "laugh", "share_trigger", "specificity", "surprise",
                    "voice", "travels", "lands", "plain", "best_landing",
                    "verdict"]}}},
    "required": ["scores"],
}

# Weights. share_trigger dominates because the stated goal of the account is a
# forward, not a like. These are guesses until there is performance data:
# at n>=30 published posts, refit them against shares/reach.
RANK_WEIGHTS = {"laugh": 3, "share_trigger": 3, "surprise": 2, "specificity": 2,
                "voice": 2, "travels": 2, "lands": 4, "plain": 3}


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "discarded": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "trend": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["trend", "reason"]},
        },
        "posts": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "trend": {"type": "string"},
                "context": {"type": "string"},
                "angle": {"type": "string"},
                # Before the slides on purpose (propertyOrdering below): the
                # punchline is found first and the slides are built toward it.
                "landing_options": {"type": "array", "minItems": 4, "maxItems": 6,
                                    "items": {"type": "object", "properties": {
                                        "line": {"type": "string"},
                                        "mechanism": {"type": "string", "enum": [
                                            "reinterpretation", "callback", "escalation",
                                            "deadpan_response", "rule_of_three_break"]}},
                                        "required": ["line", "mechanism"]}},
                "slides": {"type": "array", "items": {"type": "string"},
                           "minItems": 2, "maxItems": 3},
                "caption": {"type": "string"},
                "trigger": {"type": "string"},
                "structure": {"type": "string"},
                "move": {"type": "string"},
                "target": {"type": "string"},
                "satire_level": {"type": "integer"},
                "hinglish": {"type": "boolean"},
                "keywords": {"type": "array", "items": {"type": "string"},
                             "minItems": 3, "maxItems": 3},
                "source": {"type": "string"},
                "tweet": {"type": "object", "properties": {
                    "setup": {"type": "string"}, "punch": {"type": "string"}},
                    "required": ["setup", "punch"]},
                "chat": {"type": "object", "properties": {
                    "title": {"type": "string"},
                    "messages": {"type": "array", "minItems": 3, "maxItems": 7,
                                 "items": {"type": "object", "properties": {
                                     "from": {"type": "string"},
                                     "text": {"type": "string"},
                                     "kind": {"type": "string", "enum": [
                                         "text", "forwarded", "deleted", "voice"]}},
                                     "required": ["from", "text"]}}},
                    "required": ["title", "messages"]},
                "notify": {"type": "object", "properties": {
                    "time": {"type": "string"},
                    "date": {"type": "string"},
                    "items": {"type": "array", "minItems": 3, "maxItems": 6,
                              "items": {"type": "object", "properties": {
                                  "app": {"type": "string", "enum": list(NOTIFY_APPS)},
                                  "title": {"type": "string"},
                                  "text": {"type": "string"},
                                  "time": {"type": "string"}},
                                  "required": ["app", "title", "text"]}}},
                    "required": ["items"]},
                "comments": {"type": "object", "properties": {
                    "source": {"type": "string"},
                    "post": {"type": "string"},
                    "items": {"type": "array", "minItems": 3, "maxItems": 6,
                              "items": {"type": "object", "properties": {
                                  "user": {"type": "string"},
                                  "text": {"type": "string"},
                                  "reply": {"type": "boolean"}},
                                  "required": ["user", "text"]}}},
                    "required": ["source", "post", "items"]},
            }, "required": ["trend", "context", "angle", "landing_options", "slides",
                            "caption", "trigger", "target", "structure", "move",
                            "satire_level", "hinglish", "keywords", "source",
                            "tweet"],
                "propertyOrdering": ["trend", "context", "angle", "landing_options",
                                     "slides", "caption", "trigger", "target",
                                     "structure", "move", "satire_level", "hinglish",
                                     "keywords", "source", "tweet", "chat", "notify",
                                     "comments"]},
        },
    },
    "required": ["posts"],
}


# ------------------------------------------------------------------------ model
# Which model actually answered the last call_model(). The writer and the judge
# can each land on the fallback, and a post written by the fallback has to be
# labelled as such or the style comparison quietly mixes two writers.
LAST_MODEL = None  # type: Optional[str]


def _transient(e: Exception) -> bool:
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    text = str(e)
    return code in (429, 500, 503, 504) or any(
        k in text for k in ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED"))


def call_model(system: str, user: str, cfg: Dict[str, Any],
               schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One structured call: the pinned model first, then `fallback_models`.

    503 (overloaded), 504 (deadline) and 429 (quota) are routine on the free
    tier. The pinned model gets the full back-off; only if every attempt on it
    is transient does a fallback get a shorter one. On 2026-11-26 gemini-3.5-
    flash answered nothing all afternoon and every generate run died with the
    queue empty — a weaker writer beats no posts, and the judge still ranks
    what it writes. A non-transient error (bad schema, bad key) stops at once:
    trying another model would only hide it.
    """
    global LAST_MODEL
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY is not set. Put it in .env and export it, or use --dry-run.")
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        sys.exit("pip install -r requirements.txt  (google-genai is missing)")

    client = genai.Client(api_key=key)
    conf = types.GenerateContentConfig(
        system_instruction=system,
        temperature=cfg["temperature"],
        response_mime_type="application/json",
        response_schema=schema or RESPONSE_SCHEMA,
    )

    models = [cfg["model"]] + [m for m in cfg.get("fallback_models") or []
                               if m != cfg["model"]]
    delay = cfg.get("retry_base_seconds", 20)
    last = None
    for i, model in enumerate(models):
        attempts = cfg.get("retry_attempts", 4) if i == 0 else \
            cfg.get("fallback_retry_attempts", 2)
        for attempt in range(1, attempts + 1):
            try:
                resp = client.models.generate_content(
                    model=model, contents=user, config=conf)
                LAST_MODEL = model
                if i:
                    print("  answered by FALLBACK %s (%s unavailable)"
                          % (model, cfg["model"]))
                return json.loads(resp.text)
            except Exception as e:
                code = getattr(e, "code", None) or getattr(e, "status_code", None)
                if not _transient(e):
                    sys.exit("Gemini call failed on %s (%s): %s"
                             % (model, code or "error", str(e).split("\n")[0][:300]))
                last = "%s on %s" % (code or "transient error", model)
                if attempt == attempts:
                    break
                wait = delay * (2 ** (attempt - 1))
                print("  %s — retrying in %ds (attempt %d/%d)"
                      % (last, wait, attempt, attempts))
                time.sleep(wait)
        if i + 1 < len(models):
            print("  %s gave up after %d attempts — trying %s"
                  % (model, attempts, models[i + 1]))
    sys.exit("Gemini call failed on every model (%s): last was %s"
             % (", ".join(models), last))


# ------------------------------------------------------------------------- main
RANK_PROMPT = """You are ranking finished posts for the account described below.
You did not write them. Be a harsh editor, not a supportive one.

Score each 1-5 on:
  laugh          Did you actually exhale through your nose? Not "is this clever",
                 not "is this true" — funny. 5 = you would read it out to
                 whoever is nearest. 1 = accurate and sad. Most posts that fail
                 this fail by being an observation with an ironic shape rather
                 than a joke, and they score well on everything else while
                 doing it, which is why this axis exists.
  share_trigger  Would a real person send this to ONE specific named friend?
                 5 = they have someone in mind before finishing it.
                 1 = they might like it and scroll on.
  specificity    Concrete, checkable detail: a rupee figure, a brand, a date, a
                 line of real speech. 1 = could have been written about any
                 country in any decade.
  travels        Cover every proper noun and every number with your thumb. Does
                 it still land on a 30-year-old in Manila who has never heard of
                 Zomato? 5 = the feeling is universal and the detail is only
                 texture. 1 = the joke WAS the trivia, and outside India it is
                 just a sentence. This is not the opposite of specificity — the
                 best posts score 5 on both.
  surprise       Is the landing earned or visible from slide 1?
                 5 = the turn is genuinely unexpected but obvious in hindsight.
  voice          Deadpan, second person, ends on a noun, explains nothing.
                 1 = it winks at the reader or moralises.

  lands          Does the ending actually land as a punchline? Each post lists
                 LANDING OPTIONS (a, b, c, ...). First choose the best one —
                 that is best_landing, 1 for (a), 2 for (b) and so on — then
                 score lands for THAT option. Run these checks on it:
                   - Can you say "the setup seemed to be X, the landing reveals Y"?
                   - Is the surprise in its last one to three words?
                   - Does it hook onto a word, number or person already shown?
                   - Could a reader have guessed it from slide 1? (bad)
                   - Does it explain, summarise, moralise or state the theme? (bad)
                   - Would it work as the last line of a different post? (bad)
                 5 = all pass, and it is funnier than the best middle line.
                 1 = an abrupt, oblique or literary ending that is not a joke.
                 This is the axis the account fails most, so be strict.

  plain          Could a tired 30-year-old read it in one go, on a phone, on the
                 metro? Everyday Hinglish or very simple English, short lines,
                 the joke carried by facts and speech. 5 = reads like a text from
                 a friend. 1 = literary, abstract, a metaphor to decode, or
                 corporate words in the narration (inside quotes is fine).

  fit            Each post also lists its RENDITIONS: the same joke as a text
                 card (always), and some of: a tweet, a chat thread, lock-screen
                 notifications, a comment section. For EACH rendition it has,
                 score 1-5: would this joke be funnier in this shape than as
                 plain text? 5 = the shape is part of the joke (a balance of
                 Rs 212 as the last notification; a reply that wins the comment
                 section; a family member answering the wrong question).
                 1 = forced: messages nobody would send, notifications no phone
                 would show, comments that are the narrator in disguise, or a
                 landing that got weaker in the move. card is the baseline, 3,
                 unless the text itself is unusually strong or weak. Leave out
                 the renditions a post does not have.

Use the full range. If everything scores 4 you have not done the job.
verdict: one blunt sentence on the single biggest weakness.
"""


SEND_COMMAND = re.compile(r"^\s*(send|tag|share|forward|show)\b", re.I)


def is_send_command(caption: Optional[str]) -> bool:
    """Does the caption open by telling the reader to pass it on?

    Deliberately only the opening, and only these five verbs: the rule is
    about the rhythm every caption starts with, not about whether a send is
    requested at all. Requesting one is the point.
    """
    return bool(SEND_COMMAND.match(caption or ""))


def landing_choices(post: Dict[str, Any]) -> List[str]:
    """The writer's landing first, then the other options it wrote, deduplicated."""
    out = [(post.get("slides") or [""])[-1]]
    for o in post.get("landing_options") or []:
        line = (o.get("line") if isinstance(o, dict) else str(o) or "").strip()
        if line and line not in out:
            out.append(line)
    return out


def renditions_text(p: Dict[str, Any]) -> List[str]:
    """Each rendition a post has, one line each, as the judge reads them."""
    out = ["card: " + " / ".join(p.get("slides") or []).replace("\n", " ")]
    t = p.get("tweet")
    if isinstance(t, dict):
        out.append("tweet: %s / %s" % (t.get("setup", ""), t.get("punch", "")))
    c = p.get("chat")
    if isinstance(c, dict):
        out.append("chat (%s): " % c.get("title", "") + " / ".join(
            "%s: %s" % (m.get("from", ""), m.get("text", "") or "[%s]" % m.get("kind"))
            for m in c.get("messages", [])))
    n = p.get("notify")
    if isinstance(n, dict):
        out.append("notify: " + " / ".join(
            "[%s] %s: %s" % (it.get("app", ""), it.get("title", ""), it.get("text", ""))
            for it in n.get("items", [])))
    cm = p.get("comments")
    if isinstance(cm, dict):
        out.append("comments (%s posted: %s): " % (cm.get("source", ""), cm.get("post", ""))
                   + " / ".join("%s%s: %s" % ("  reply " if it.get("reply") else "",
                                              it.get("user", ""), it.get("text", ""))
                                for it in cm.get("items", [])))
    return out


def _judge_entry(i: int, p: Dict[str, Any]) -> str:
    opts = landing_choices(p)
    return ("%d. %s\n   LANDING OPTIONS: %s\n   RENDITIONS:\n%s"
            % (i, " / ".join(p["slides"]).replace("\n", " "),
               "  ".join("(%s) %s" % (chr(97 + k), o.replace("\n", " "))
                         for k, o in enumerate(opts)),
               "\n".join("     - " + r for r in renditions_text(p))))


def apply_best_landing(post: Dict[str, Any], choice: Optional[int],
                       brand: Dict[str, Any], history: List[Dict[str, Any]],
                       cfg: Dict[str, Any]) -> Optional[str]:
    """Swap in the judge's pick for the last slide if it still passes every gate.

    Generate many, keep one: the Onion pitches hundreds of headlines to print a
    handful, and the humour-generation papers do the same with candidates and a
    ranker. The writer picks first; the judge, who did not write them, can
    overrule it. Returns the replaced line, or None if nothing changed.
    """
    opts = landing_choices(post)
    if not choice or not 1 <= choice <= len(opts) or choice == 1:
        return None
    trial = dict(post, slides=list(post["slides"][:-1]) + [opts[choice - 1]])
    if run_gates(trial, brand, history, cfg):
        return None
    old = post["slides"][-1]
    post["slides"] = trial["slides"]
    return old


def rank(posts: List[Dict[str, Any]], brand: Dict[str, Any],
         history: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Score, then penalise repetition. Returns posts sorted best-first.

    The judge is a second, separate call: asking the writer to grade its own
    work in the same breath produces uniformly high marks.
    """
    listing = "\n\n".join(_judge_entry(i, p) for i, p in enumerate(posts, 1))
    system = RANK_PROMPT + "\n\nACCOUNT:\n" + yaml.safe_dump(
        {k: brand[k] for k in ("identity", "audience", "voice", "slides", "travels")},
        sort_keys=False, allow_unicode=True, width=100)

    scores = {}
    try:
        res = call_model(system, listing, cfg, schema=RANK_SCHEMA)
        for sc in res.get("scores", []):
            scores[sc["index"]] = sc
    except SystemExit:
        print("  ranking unavailable — falling back to generation order")

    # Repetition is invisible to a judge looking at one batch, so it is applied
    # here: the account's recent shape, not the post's own quality.
    recent_struct = [p.get("structure") for p in history[-6:]]
    recent_trig = [p.get("trigger") for p in history[-4:]]
    # A post aimed at the reader is not wrong — it is only wrong in bulk, which
    # no judge looking at one batch can see. Free while the account is under
    # the cap, expensive once it is over.
    recent_self = sum(1 for p in history[-8:] if (p.get("target") or "self") == "self")
    self_cap = brand["target"]["self_cap"]
    over_self = recent_self > self_cap * max(len(history[-8:]), 1)

    # Voices are the one quantity the account is short of rather than long on,
    # so this is a pull rather than a push: a speaking post is worth more only
    # while the recent mix is starved of them, and worth nothing extra once it
    # is not. Same for the hook monotone, in the other direction.
    recent = history[-8:] or history
    voices = set(brand["structures"]["voices"])
    under_voice = (sum(1 for p in recent if p.get("structure") in voices)
                   < brand["structures"]["min_voice_share"] * max(len(recent), 1))
    over_imp = (sum(1 for p in recent if is_imperative((p.get("slides") or [""])[0]))
                > brand["slides"]["slide_1_is_a_HOOK"]["max_imperative_share"]
                * max(len(recent), 1))
    # Twelve of the first twelve published posts had a parallel list in the
    # middle slide, under eight different structure names — so the per-
    # structure cap could not see it and neither could a judge reading one
    # batch. Same remedy as the voices, pointing the other way.
    lists = set(brand["structures"]["list_shaped"])
    over_list = (sum(1 for p in recent if p.get("structure") in lists)
                 > brand["structures"]["max_list_share"] * max(len(recent), 1))
    # And the move, which is the thing the structure name was hiding.
    recent_move = [p.get("move") for p in history[-6:] if p.get("move")]
    # The caption rule asking for a send was read as a formula: the first batch
    # under it opened "Send this to..." eight times out of eight. Same monotone
    # as the hooks, same remedy — a pull on the mix, not a gate on the post.
    over_send = (sum(1 for p in recent if is_send_command(p.get("caption")))
                 > brand["slides"]["the_caption"]["max_command_share"]
                 * max(len(recent), 1))
    # The landing shape, which the contract used to mandate outright. Same
    # remedy again: a pull on the mix rather than a gate on the post, because
    # a two-line couplet is a perfectly good ending and only wrong in bulk.
    over_couplet = (sum(1 for p in recent if is_couplet(p.get("slides") or []))
                    > brand["slides"]["last_slide_is_the_LANDING"]["max_couplet_share"]
                    * max(len(recent), 1))

    for i, p in enumerate(posts, 1):
        sc = scores.get(i, {})
        swapped = apply_best_landing(p, sc.get("best_landing"), brand, history, cfg)
        if swapped:
            p["landing_replaced"] = swapped
        base = sum(RANK_WEIGHTS[k] * sc.get(k, 3) for k in RANK_WEIGHTS)
        penalty = 0
        if p.get("structure") in recent_struct:
            penalty += 4 * recent_struct.count(p.get("structure"))
        if p.get("trigger") in recent_trig:
            penalty += 3
        if over_self and (p.get("target") or "self") == "self":
            penalty += 5
        if under_voice and p.get("structure") in voices:
            penalty -= 5
        if over_imp and is_imperative((p.get("slides") or [""])[0]):
            penalty += 4
        if over_send and is_send_command(p.get("caption")):
            penalty += 3
        if over_couplet and is_couplet(p.get("slides") or []):
            penalty += 4
        if over_list and p.get("structure") in lists:
            penalty += 4
        if p.get("move") in recent_move:
            penalty += 3 * recent_move.count(p.get("move"))
        p["_score"] = base - penalty
        p["_judge"] = sc.get("verdict", "")
        p["_detail"] = dict((k, sc.get(k)) for k in RANK_WEIGHTS)
        fit = sc.get("fit") or {}
        p["_fit"] = dict((st, int(fit[st])) for st in available_styles(p)
                         if isinstance(fit.get(st), (int, float)) and fit[st])
        p["_penalty"] = penalty
    return sorted(posts, key=lambda p: -p["_score"])


def load_legacy() -> Dict[str, List[str]]:
    """Failures already on the record when the rule that causes them was written.

    A rule tightened today will reject posts published last week, and there is
    no fixing those: they are on Instagram. Without a baseline the self-test is
    permanently red and stops meaning anything, which defeats the point of
    running it before every generation — the question it exists to answer is
    "did the change I just made break the back catalogue", and that only reads
    if the catalogue was green a minute ago.

    Matched on the exact failure text, not on the post id, so an old post
    breaking for a NEW reason still shows up.
    """
    try:
        with open(LEGACY) as f:
            return json.load(f)
    except (IOError, ValueError):
        return {}


def cmd_check(brand, cfg, record: bool = False):
    """Self-test: every approved post must survive its own gates."""
    history = load_history()
    legacy = {} if record else load_legacy()
    for w in check_contract_is_whole(brand):
        print("  ! %s" % w)
    print("Running gates over %d approved posts\n" % len(history))
    bad = 0
    known = 0
    baseline = {}  # type: Dict[str, List[str]]
    for i, p in enumerate(history):
        others = history[:i] + history[i + 1:]
        fails = run_gates(p, brand, others, cfg)
        if not fails:
            continue
        pid = p.get("id") or "?"
        baseline[pid] = fails
        fresh = [f for f in fails if f not in legacy.get(pid, [])]
        if not fresh:
            known += 1
            continue
        bad += 1
        print("  %s FAIL" % pid)
        for f in fresh:
            print("      - %s" % f)
    if known:
        print("  (%d published post(s) fail rules written after them — "
              "content/legacy_failures.json)" % known)
    if record:
        with open(LEGACY, "w") as f:
            json.dump(baseline, f, indent=2, sort_keys=True)
        print("  recorded %d as the baseline" % len(baseline))
    shares, warnings = structure_report(history, brand["structures"]["max_share_of_output"],
                                        cfg["structure_enforce_from"])
    print("\nStructure mix:")
    for k, v in sorted(shares.items(), key=lambda kv: -kv[1]):
        print("  %-18s %.0f%%" % (k, v * 100))
    for w in warnings:
        print("  ! %s" % w)
    hin = sum(1 for p in history if p.get("hinglish"))
    print("\nHinglish: %d/%d (%.0f%%)  target %s"
          % (hin, len(history), hin / max(len(history), 1) * 100,
             brand["hinglish"]["target_ratio"]))

    aims = {}  # type: Dict[str, int]
    for p in history:
        aims[(p.get("target") or "untargeted")] = aims.get(
            p.get("target") or "untargeted", 0) + 1
    print("\nAimed at:")
    for k, v in sorted(aims.items(), key=lambda kv: -kv[1]):
        print("  %-18s %d (%.0f%%)" % (k, v, v / max(len(history), 1) * 100))
    voices = set(brand["structures"]["voices"])
    vs = sum(1 for p in history if p.get("structure") in voices) / max(len(history), 1)
    imps = sum(1 for p in history
               if is_imperative((p.get("slides") or [""])[0])) / max(len(history), 1)
    print("\nSomebody speaking: %.0f%% (floor %.0f%%)%s"
          % (vs * 100, brand["structures"]["min_voice_share"] * 100,
             "" if vs >= brand["structures"]["min_voice_share"] else "  <-- short"))
    print("Hooks that are commands: %.0f%% (cap %.0f%%)%s"
          % (imps * 100,
             brand["slides"]["slide_1_is_a_HOOK"]["max_imperative_share"] * 100,
             "" if imps <= brand["slides"]["slide_1_is_a_HOOK"]["max_imperative_share"]
             else "  <-- one rhythm"))
    cpl = sum(1 for p in history if is_couplet(p.get("slides") or [])) \
        / max(len(history), 1)
    cpl_cap = brand["slides"]["last_slide_is_the_LANDING"]["max_couplet_share"]
    print("Landings that are a two-line couplet: %.0f%% (cap %.0f%%)%s"
          % (cpl * 100, cpl_cap * 100,
             "" if cpl <= cpl_cap else "  <-- reads as verse"))

    at_self = (aims.get("self", 0) + aims.get("untargeted", 0)) / max(len(history), 1)
    if at_self > brand["target"]["self_cap"]:
        print("  ! %.0f%% of posts are aimed at the reader (cap %.0f%%) — "
              "that reads sad, not satirical"
              % (at_self * 100, brand["target"]["self_cap"] * 100))
    print("\n%s" % ("ALL PASS" if bad == 0 else "%d post(s) failed" % bad))
    return 0 if bad == 0 else 1


def _alert(msg: str) -> None:
    """Loud on the console, and on the Actions run page where it gets read."""
    print("!! %s" % msg.replace("\n", "\n   "), file=sys.stderr)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a") as f:
                f.write("### Nothing was approved\n\n%s\n" % msg)
        except OSError:
            pass


def cmd_approve(pick: int, cfg) -> int:
    """Promote one candidate into content/approved/ with a stable id.

    Approved posts become both the publish queue's source and the few-shot
    examples for the next run, so the account learns from what you kept.
    """
    files = sorted(glob.glob(os.path.join(CANDIDATE_DIR, "*.json")))
    if not files:
        sys.exit("no candidate files yet — run brain.py first")
    blob = json.load(open(files[-1]))
    acc = blob.get("accepted", [])
    if not 1 <= pick <= len(acc):
        sys.exit("--approve must be 1..%d" % len(acc))
    post = acc[pick - 1]

    existing = [os.path.basename(f)[:-5] for f in glob.glob(os.path.join(APPROVED_DIR, "*.json"))]
    n = 1 + max([int(x[1:]) for x in existing if x[:1] == "g" and x[1:].isdigit()] or [0])
    post["id"] = "g%03d" % n
    post["status"] = "approved"
    post["rank_score"] = post.pop("_score", None)
    post["rank_verdict"] = post.pop("_judge", "")
    post["style_fit"] = post.pop("_fit", None) or {}
    for k in ("_detail", "_penalty", "_rendition_notes"):
        post.pop(k, None)
    post["style"], post["style_why"] = pick_style(post, cfg, explain=True)

    os.makedirs(APPROVED_DIR, exist_ok=True)
    out = os.path.join(APPROVED_DIR, "%s.json" % post["id"])
    with open(out, "w") as f:
        json.dump({"handle": "@30unfortunately", "posts": [post]}, f,
                  indent=2, ensure_ascii=False)
    print("approved %s -> %s  (%s reel: %s)" % (post["id"], os.path.relpath(out, ROOT),
                                                 post["style"], post["style_why"]))
    print("  %s" % post["slides"][0])
    return 0


def cmd_summary() -> int:
    """Markdown for the Actions job summary — the record of what a run decided.

    It used to be the approval UI. Approval is automatic now, so this reports
    rather than asks: the veto arrives by email, and manual approval is only
    the override in approve.yml.
    """
    files = sorted(glob.glob(os.path.join(CANDIDATE_DIR, "*.json")))
    if not files:
        print("No candidates."); return 0
    blob = json.load(open(files[-1]))
    acc = blob.get("accepted", [])
    print("## %d candidates, ranked best first\n" % len(acc))
    print("The top ones were approved and queued automatically. Reply "
          "`skip <id>` to the queue email to stop one; **Actions -> approve "
          "-> Run workflow** is the manual override, by number.\n")
    for i, p in enumerate(acc, 1):
        print("### %d. %s `%s` satire %s%s" % (
            i, p["trigger"], p["structure"], p["satire_level"],
            " **hinglish**" if p.get("hinglish") else ""))
        print("> _%s_\n" % p["angle"])
        for n, sl in enumerate(p["slides"], 1):
            print("**%d.** %s\n" % (n, sl.replace("\n", "  \n")))
        print("`%s`\n" % p["caption"])
        print("---\n")
    for d in blob.get("rejected", []):
        print("- rejected by gates: %s" % "; ".join(d.get("_gate_failures", [])))
    return 0


CAND_CSS = """
*{box-sizing:border-box}body{margin:0;background:#E7E2D6;color:#15140F;padding:40px;
font-family:'Bricolage Grotesque',system-ui,sans-serif}
h1{font-size:32px;letter-spacing:-1px;margin:0 0 4px}
p.sub{margin:0 0 26px;color:#5E5849;font-size:14px}
a.back{color:#D8451F;font-weight:700;text-decoration:none}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:22px}
.c{background:#FBF9F4;border-radius:12px;padding:16px;box-shadow:0 3px 12px rgba(21,20,15,.1)}
.n{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.num{background:#D8451F;color:#FBF9F4;font-weight:700;font-size:15px;
border-radius:8px;padding:3px 11px}
.tag{font-size:10px;letter-spacing:.8px;color:#6E675A;font-weight:700}
.id{margin-left:auto;font-size:10px;letter-spacing:.8px;font-weight:700;
border-radius:6px;padding:3px 8px;background:#15140F;color:#F2EEE4}
.id.off{background:none;color:#9A9384;border:1px solid #D6D0C2}
.sub code{background:#FBF9F4;border-radius:4px;padding:1px 5px;font-size:13px}
.slides{display:flex;gap:6px}
.s{flex:1;border-radius:5px;padding:11px 10px;font-size:11.5px;font-weight:700;
line-height:1.34;white-space:pre-line;min-height:118px}
.s.paper{background:#F2EEE4}
.s.ink{background:#15140F;color:#F2EEE4}
.angle{font-size:11.5px;color:#6E675A;line-height:1.45;margin-top:11px}
.cap{font-size:11px;color:#3A3629;margin-top:7px;font-style:italic}
"""


def _stamp(raw: str) -> str:
    """20260917-150929 -> 17 Sep 2026, 15:09 UTC."""
    try:
        return datetime.strptime(raw, "%Y%m%d-%H%M%S").strftime(
            "%-d %b %Y, %H:%M UTC")
    except ValueError:
        return raw


def cmd_html() -> int:
    """Put today's candidates on the Pages site next to the contact sheet.

    Rendered as CSS boxes rather than PNGs — same paper/paper/ink rhythm the
    cards use, so the shape of a post is legible, at a few KB instead of 1.7MB
    a day in a repo that also serves its own images.
    """
    files = sorted(glob.glob(os.path.join(CANDIDATE_DIR, "*.json")))
    if not files:
        print("no candidates"); return 0
    blob = json.load(open(files[-1]))
    acc = blob.get("accepted", [])

    # Which of these actually shipped. Nothing is chosen by hand any more, so
    # the page's job is to say what the run decided, not to ask for a decision.
    # Matched on caption rather than on rank, because rank is what cmd_approve
    # consumed and the answer should come from the record, not from arithmetic.
    live = {}
    for f in glob.glob(os.path.join(APPROVED_DIR, "*.json")):
        for q in json.load(open(f)).get("posts", []):
            live[(q.get("caption") or "").strip()] = q.get("id")
    status = {}
    try:
        import store
        conn = store.connect()
        status = dict((r["id"], r["status"])
                      for r in conn.execute("SELECT id, status FROM posts"))
    except Exception:
        pass

    cards = []
    for i, p in enumerate(acc, 1):
        last = len(p["slides"]) - 1
        slides = "".join(
            '<div class="s %s">%s</div>' % ("ink" if n == last else "paper",
                                            html_escape(sl))
            for n, sl in enumerate(p["slides"]))
        pid = live.get((p.get("caption") or "").strip())
        badge = ('<span class="id">%s &middot; %s</span>'
                 % (pid, status.get(pid, "approved")) if pid else
                 '<span class="id off">not approved</span>')
        cards.append(
            '<div class="c"><div class="n"><span class="num">%d</span>'
            '<span class="tag">%s &middot; %s &middot; SATIRE %s%s</span>%s</div>'
            '<div class="slides">%s</div>'
            '<div class="angle">%s</div><div class="cap">%s</div>%s</div>'
            % (i, p["trigger"].upper(), p["structure"], p["satire_level"],
               " &middot; HINGLISH" if p.get("hinglish") else "", badge,
               slides, html_escape(p["angle"]), html_escape(p["caption"]),
               control.bar(pid, status.get(pid) if pid else None, rank=i)))

    shipped = [live[k] for k in
               [(p.get("caption") or "").strip() for p in acc] if k in live]
    # Only a post that is still queued can be vetoed, so only name one of those.
    stoppable = [i for i in shipped if status.get(i) == "queued"]

    # The heading used to say "today" whichever day the run was from. It is
    # read on a phone in the morning, so the one thing it must not do is
    # present three-day-old candidates as this morning's.
    ran = os.path.basename(files[-1])[:8]
    today = ran == datetime.now(timezone.utc).strftime("%Y%m%d")
    doc = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           '<title>Candidates</title><link rel="stylesheet" href="https://fonts.googleapis.com/'
           'css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,700&display=swap">'
           '<style>%s</style></head><body><h1>%s</h1>'
           '<p class="sub">%s &middot; %d passed the gates, %d approved &middot; '
           'ranked best first &middot; nothing here needs approving. %s%s'
           '&nbsp;&middot;&nbsp; '
           '<a class="back" href="./">the dashboard &amp; everything published &rarr;</a></p>'
           '<div class="grid">%s</div></body></html>'
           % (CAND_CSS + control.CSS,
              "Today&rsquo;s candidates" if today else "The latest candidates",
              _stamp(blob.get("generated_at", "")),
              len(acc), len(shipped),
              '' if today else 'Nothing has generated today yet. ',
              ('%d still to go out — the buttons below file a one-tap issue and '
               'the workflow does the rest. ' % len(stoppable)) if stoppable else
              'Nothing from this run is still waiting to go out. ',
              "".join(cards)))

    out = os.path.join(ROOT, "docs", "candidates.html")
    with open(out, "w") as f:
        f.write(doc)
    print("wrote %s (%d candidates)" % (os.path.relpath(out, ROOT), len(acc)))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trends", default=os.path.join(ROOT, "content", "trends.json"))
    ap.add_argument("--n", type=int, default=None, help="posts per usable trend")
    ap.add_argument("--dry-run", action="store_true", help="print the prompt, call nothing")
    ap.add_argument("--check", action="store_true", help="run gates over approved posts")
    ap.add_argument("--record-legacy", action="store_true",
                    help="with --check: freeze today's failures as the baseline")
    ap.add_argument("--min-backlog", type=int, metavar="N", default=None,
                    help="do nothing if at least N posts are already queued")
    ap.add_argument("--auto", type=int, metavar="N", default=0,
                    help="auto-approve the top N ranked candidates")
    ap.add_argument("--html", action="store_true",
                    help="write docs/candidates.html for the Pages site")
    ap.add_argument("--summary", action="store_true",
                    help="markdown digest of the latest candidates")
    ap.add_argument("--approve", type=int, metavar="N",
                    help="promote candidate N from the latest run into content/approved/")
    args = ap.parse_args()

    envfile.load()
    brand, cfg = load_brand(), load_config()
    if args.n:
        cfg["posts_per_trend"] = args.n

    if args.check:
        sys.exit(cmd_check(brand, cfg, record=args.record_legacy))
    if args.html:
        sys.exit(cmd_html())
    if args.summary:
        sys.exit(cmd_summary())
    if args.approve:
        sys.exit(cmd_approve(args.approve, cfg))

    if args.min_backlog is not None:
        # 0 reads as "skip when at least 0 posts are queued", which is always
        # true, so it does not disable the guard — it disables generation. It
        # sat in generate.yml from 2026-09-18 13:05 and every scheduled run for
        # two days returned in zero seconds without calling the model. Refuse it
        # rather than let it mean the opposite of what it looks like.
        if args.min_backlog < 1:
            sys.exit("--min-backlog must be >= 1. It means 'do nothing if at "
                     "least N are queued', so 0 is always satisfied and nothing "
                     "is ever generated. Omit the flag to generate regardless.")
        import store
        queued = store.counts(store.connect()).get("queued", 0)
        if queued >= args.min_backlog:
            print("%d posts already queued (>= %d) — not generating."
                  % (queued, args.min_backlog))
            return
        print("%d queued, below %d — generating." % (queued, args.min_backlog))

    history = load_history()
    with open(args.trends) as f:
        trends = json.load(f)["trends"]

    system = build_system_prompt(brand, history, cfg)
    user = build_user_prompt(trends, cfg["posts_per_trend"])

    if args.dry_run:
        print("=" * 78 + "\nSYSTEM\n" + "=" * 78)
        print(system)
        print("\n" + "=" * 78 + "\nUSER\n" + "=" * 78)
        print(user)
        print("\n" + "=" * 78)
        print("~%d chars (~%d tokens). %d trends, %d few-shot, %d in history."
              % (len(system) + len(user), (len(system) + len(user)) // 4,
                 len(trends), cfg["few_shot_count"], len(history)))
        print("One call. No key used.")
        return

    print("Generating from %d trends via %s ..." % (len(trends), cfg["model"]))
    result = call_model(system, user, cfg)
    writer = LAST_MODEL or cfg["model"]

    for d in result.get("discarded", []):
        print("  discarded: %s\n      %s" % (d["trend"][:70], d["reason"]))

    accepted, rejected = [], []
    for p in result.get("posts", []):
        fails = run_gates(p, brand, history + accepted, cfg)
        p["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        p["model"] = writer
        if fails:
            p["_gate_failures"] = fails
            rejected.append(p)
        else:
            p["status"] = "draft"
            notes = vet_renditions(p, brand)
            if notes:
                p["_rendition_notes"] = notes
            accepted.append(p)

    if accepted:
        accepted = rank(accepted, brand, history, cfg)
        print("\nranked:")
        for i, p in enumerate(accepted, 1):
            print("  %d. score %-3s %-16s %s" % (i, p["_score"], p["structure"],
                                                 p["slides"][0][:52]))
            if p.get("_judge"):
                print("       %s" % p["_judge"][:96])
            print("       styles: %s%s" % (", ".join(available_styles(p)),
                                          "  (" + "; ".join(p["_rendition_notes"]) + ")"
                                          if p.get("_rendition_notes") else ""))

    os.makedirs(CANDIDATE_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = os.path.join(CANDIDATE_DIR, "%s.json" % stamp)
    with open(out, "w") as f:
        json.dump({"generated_at": stamp, "model": writer,
                   "accepted": accepted, "rejected": rejected}, f,
                  indent=2, ensure_ascii=False)

    print("\n%d accepted, %d rejected by gates -> %s\n"
          % (len(accepted), len(rejected), os.path.relpath(out, ROOT)))

    # Always, in both paths. --auto used to return before reaching this, so a
    # run that rejected everything printed no reason anywhere — the workflow
    # went green, the commit was skipped, and the queue drained for two days
    # with the evidence living only in a runner that had been destroyed.
    for p in rejected:
        print("  REJECTED: %s" % (p["slides"][0] if p.get("slides") else "(no slides)"))
        for f in p["_gate_failures"]:
            print("      - %s" % f)

    if args.auto:
        n = min(args.auto, len(accepted))
        for i in range(1, n + 1):
            cmd_approve(i, cfg)
        if not n:
            reasons = {}
            for p in rejected:
                for f in p["_gate_failures"]:
                    key = f.split(" — ")[0].split(" (")[0]
                    reasons[key] = reasons.get(key, 0) + 1
            _alert("Approved nothing. %d posts came back, all rejected.\n%s\n"
                   "The queue does not refill by itself from here."
                   % (len(rejected),
                      "\n".join("  %dx %s" % (v, k) for k, v
                                in sorted(reasons.items(), key=lambda kv: -kv[1]))
                      or "  (the model returned no posts at all)"))
        return

    for p in accepted:
        print("  [%s/%s] %s" % (p["structure"], p["satire_level"], p["slides"][0]))
        for s in p["slides"][1:]:
            print("      %s" % s.replace("\n", " / "))
        print()


if __name__ == "__main__":
    main()
