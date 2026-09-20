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

import envfile

ROOT = os.path.dirname(os.path.abspath(__file__))
BRAND = os.path.join(ROOT, "brand.yaml")
CONFIG = os.path.join(ROOT, "config.json")
SEED = os.path.join(ROOT, "content", "seed_posts.json")
APPROVED_DIR = os.path.join(ROOT, "content", "approved")
CANDIDATE_DIR = os.path.join(ROOT, "content", "candidates")

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
    # Emoji, unlike phrases, ARE allowed in the caption and only there.
    if EMOJI.search(_flat(post)):
        fails.append("emoji in card art (captions only)")
    return fails


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
            + gate_landing_turns(post, brand)
            + gate_source(post) + gate_novelty(post, history, cfg["novelty_threshold"]))


# ----------------------------------------------------------------------- prompt
# Everything here is sent to the writer. A section added to brand.yaml and not
# added here is invisible to the model while looking, in the file, exactly like
# a rule — `target` and `travels` both shipped that way, and `territory` nearly
# did. NOT_A_RULE names the sections that are deliberately withheld, so the
# check below can tell an omission from a decision.
CONTRACT_SECTIONS = ("identity", "audience", "formula", "slides", "target", "voice",
                     "hinglish", "travels", "territory", "satire_ladder",
                     "structures", "hard_bans", "output_schema")
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
    examples = history[: cfg["few_shot_count"]]
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
             brand["slides"]["slide_1_is_a_HOOK"]["max_imperative_share"] * 100),
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
        "verdict": {"type": "string"},
    }, "required": ["index", "laugh", "share_trigger", "specificity", "surprise",
                    "voice", "travels", "verdict"]}}},
    "required": ["scores"],
}

# Weights. share_trigger dominates because the stated goal of the account is a
# forward, not a like. These are guesses until there is performance data:
# at n>=30 published posts, refit them against shares/reach.
RANK_WEIGHTS = {"laugh": 3, "share_trigger": 3, "surprise": 2, "specificity": 2,
                "voice": 2, "travels": 2}


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
                "slides": {"type": "array", "items": {"type": "string"},
                           "minItems": 2, "maxItems": 3},
                "caption": {"type": "string"},
                "trigger": {"type": "string"},
                "structure": {"type": "string"},
                "target": {"type": "string"},
                "satire_level": {"type": "integer"},
                "hinglish": {"type": "boolean"},
                "source": {"type": "string"},
            }, "required": ["trend", "context", "angle", "slides", "caption",
                            "trigger", "target", "structure", "satire_level",
                            "hinglish", "source"]},
        },
    },
    "required": ["posts"],
}


# ------------------------------------------------------------------------ model
def call_model(system: str, user: str, cfg: Dict[str, Any],
               schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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

    # 503 (overloaded) and 429 (quota) are routine on the free tier. A twice-daily
    # job must ride them out rather than dying, so back off and retry.
    attempts = cfg.get("retry_attempts", 4)
    delay = cfg.get("retry_base_seconds", 20)
    for attempt in range(1, attempts + 1):
        try:
            resp = client.models.generate_content(
                model=cfg["model"], contents=user, config=conf)
            return json.loads(resp.text)
        except Exception as e:
            code = getattr(e, "code", None) or getattr(e, "status_code", None)
            transient = code in (429, 500, 503) or "UNAVAILABLE" in str(e) or \
                "RESOURCE_EXHAUSTED" in str(e)
            if not transient or attempt == attempts:
                sys.exit("Gemini call failed (%s): %s"
                         % (code or "error", str(e).split("\n")[0][:300]))
            wait = delay * (2 ** (attempt - 1))
            print("  %s — retrying in %ds (attempt %d/%d)"
                  % (code or "transient error", wait, attempt, attempts))
            time.sleep(wait)


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

Use the full range. If everything scores 4 you have not done the job.
verdict: one blunt sentence on the single biggest weakness.
"""


def rank(posts: List[Dict[str, Any]], brand: Dict[str, Any],
         history: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Score, then penalise repetition. Returns posts sorted best-first.

    The judge is a second, separate call: asking the writer to grade its own
    work in the same breath produces uniformly high marks.
    """
    listing = "\n\n".join(
        "%d. %s" % (i, " / ".join(p["slides"]).replace("\n", " "))
        for i, p in enumerate(posts, 1))
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

    for i, p in enumerate(posts, 1):
        sc = scores.get(i, {})
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
        p["_score"] = base - penalty
        p["_judge"] = sc.get("verdict", "")
        p["_detail"] = dict((k, sc.get(k)) for k in RANK_WEIGHTS)
        p["_penalty"] = penalty
    return sorted(posts, key=lambda p: -p["_score"])


def cmd_check(brand, cfg):
    """Self-test: every approved post must survive its own gates."""
    history = load_history()
    for w in check_contract_is_whole(brand):
        print("  ! %s" % w)
    print("Running gates over %d approved posts\n" % len(history))
    bad = 0
    for i, p in enumerate(history):
        others = history[:i] + history[i + 1:]
        fails = run_gates(p, brand, others, cfg)
        if fails:
            bad += 1
            print("  %s FAIL" % p.get("id"))
            for f in fails:
                print("      - %s" % f)
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
    for k in ("_detail", "_penalty"):
        post.pop(k, None)

    os.makedirs(APPROVED_DIR, exist_ok=True)
    out = os.path.join(APPROVED_DIR, "%s.json" % post["id"])
    with open(out, "w") as f:
        json.dump({"handle": "@30unfortunately", "posts": [post]}, f,
                  indent=2, ensure_ascii=False)
    print("approved %s -> %s" % (post["id"], os.path.relpath(out, ROOT)))
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

    doc = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           '<title>Candidates</title><link rel="stylesheet" href="https://fonts.googleapis.com/'
           'css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,700&display=swap">'
           '<style>%s</style></head><body><h1>Today&rsquo;s candidates</h1>'
           '<p class="sub">%s &middot; %d passed the gates, %d approved &middot; '
           'ranked best first &middot; nothing here needs approving. %s'
           '&nbsp;&middot;&nbsp; '
           '<a class="back" href="./">published &amp; queued posts &rarr;</a></p>'
           '<div class="grid">%s</div></body></html>'
           % (CAND_CSS + control.CSS, _stamp(blob.get("generated_at", "")),
              len(acc), len(shipped),
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
        sys.exit(cmd_check(brand, cfg))
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

    for d in result.get("discarded", []):
        print("  discarded: %s\n      %s" % (d["trend"][:70], d["reason"]))

    accepted, rejected = [], []
    for p in result.get("posts", []):
        fails = run_gates(p, brand, history + accepted, cfg)
        p["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if fails:
            p["_gate_failures"] = fails
            rejected.append(p)
        else:
            p["status"] = "draft"
            accepted.append(p)

    if accepted:
        accepted = rank(accepted, brand, history, cfg)
        print("\nranked:")
        for i, p in enumerate(accepted, 1):
            print("  %d. score %-3s %-16s %s" % (i, p["_score"], p["structure"],
                                                 p["slides"][0][:52]))
            if p.get("_judge"):
                print("       %s" % p["_judge"][:96])

    os.makedirs(CANDIDATE_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = os.path.join(CANDIDATE_DIR, "%s.json" % stamp)
    with open(out, "w") as f:
        json.dump({"generated_at": stamp, "model": cfg["model"],
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
