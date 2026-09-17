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
    body = _flat(post).lower()
    for phrase in brand["hard_bans"]["phrases"]:
        if phrase.lower() in body:
            fails.append("banned phrase: '%s'" % phrase)
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
    if len(hook) > max_hook:
        fails.append("hook is %d chars (max %d) — it is carrying the joke, not opening a loop"
                     % (len(hook), max_hook))
    if hook.rstrip().endswith("?") and len(hook) < 30:
        fails.append("hook is a bare question — weak open loop")
    for i, s in enumerate(slides):
        if len(s) > 240:
            fails.append("slide %d is %d chars (max 240)" % (i + 1, len(s)))
    landing = slides[-1]
    if landing.rstrip().endswith("?"):
        fails.append("landing ends on a question — this account states, it does not beg")
    # Hinglish in this brand is defined as reported speech, so it is concrete by construction.
    if not post.get("hinglish") and not re.search(r"[0-9₹]|\"|'", _flat(post)):
        fails.append("no concrete detail (no number, rupee figure or quoted speech) — too generic")
    return fails


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
            + gate_source(post) + gate_novelty(post, history, cfg["novelty_threshold"]))


# ----------------------------------------------------------------------- prompt
def build_system_prompt(brand: Dict[str, Any], history: List[Dict[str, Any]],
                        cfg: Dict[str, Any]) -> str:
    examples = history[: cfg["few_shot_count"]]
    shares, _ = structure_report(history, brand["structures"]["max_share_of_output"],
                                 cfg["structure_enforce_from"])
    hinglish_share = (sum(1 for p in history if p.get("hinglish")) / max(len(history), 1))

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
        yaml.safe_dump({k: brand[k] for k in
                        ("identity", "audience", "formula", "slides", "voice", "hinglish",
                         "satire_ladder", "structures", "hard_bans", "output_schema")},
                       sort_keys=False, allow_unicode=True, width=100),
        "APPROVED EXAMPLES — match this register, never reuse these jokes:\n\n"
        + "\n\n===\n\n".join(ex_blocks),
        "WHAT THE ACCOUNT HAS OVERUSED SO FAR (steer away from the top entries):\n"
        + "\n".join("  %s: %.0f%%" % (k, v * 100) for k, v in
                    sorted(shares.items(), key=lambda kv: -kv[1]))
        + "\n  hinglish: %.0f%% (target %s)" % (hinglish_share * 100,
                                                brand["hinglish"]["target_ratio"]),
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
                "satire_level": {"type": "integer"},
                "hinglish": {"type": "boolean"},
                "source": {"type": "string"},
            }, "required": ["trend", "context", "angle", "slides", "caption",
                            "trigger", "structure", "satire_level", "hinglish", "source"]},
        },
    },
    "required": ["posts"],
}


# ------------------------------------------------------------------------ model
def call_model(system: str, user: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
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
        response_schema=RESPONSE_SCHEMA,
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
def cmd_check(brand, cfg):
    """Self-test: every approved post must survive its own gates."""
    history = load_history()
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
    print("\n%s" % ("ALL PASS" if bad == 0 else "%d post(s) failed" % bad))
    return 0 if bad == 0 else 1


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

    os.makedirs(APPROVED_DIR, exist_ok=True)
    out = os.path.join(APPROVED_DIR, "%s.json" % post["id"])
    with open(out, "w") as f:
        json.dump({"handle": "@30unfortunately", "posts": [post]}, f,
                  indent=2, ensure_ascii=False)
    print("approved %s -> %s" % (post["id"], os.path.relpath(out, ROOT)))
    print("  %s" % post["slides"][0])
    return 0


def cmd_summary() -> int:
    """Markdown for the Actions job summary — the approval UI on a phone."""
    files = sorted(glob.glob(os.path.join(CANDIDATE_DIR, "*.json")))
    if not files:
        print("No candidates."); return 0
    blob = json.load(open(files[-1]))
    acc = blob.get("accepted", [])
    print("## %d candidates\n" % len(acc))
    print("Approve one from **Actions -> approve -> Run workflow**, "
          "entering its number.\n")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trends", default=os.path.join(ROOT, "content", "trends.json"))
    ap.add_argument("--n", type=int, default=None, help="posts per usable trend")
    ap.add_argument("--dry-run", action="store_true", help="print the prompt, call nothing")
    ap.add_argument("--check", action="store_true", help="run gates over approved posts")
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
    if args.summary:
        sys.exit(cmd_summary())
    if args.approve:
        sys.exit(cmd_approve(args.approve, cfg))

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

    os.makedirs(CANDIDATE_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = os.path.join(CANDIDATE_DIR, "%s.json" % stamp)
    with open(out, "w") as f:
        json.dump({"generated_at": stamp, "model": cfg["model"],
                   "accepted": accepted, "rejected": rejected}, f,
                  indent=2, ensure_ascii=False)

    print("\n%d accepted, %d rejected by gates -> %s\n"
          % (len(accepted), len(rejected), os.path.relpath(out, ROOT)))
    for p in accepted:
        print("  [%s/%s] %s" % (p["structure"], p["satire_level"], p["slides"][0]))
        for s in p["slides"][1:]:
            print("      %s" % s.replace("\n", " / "))
        print()
    for p in rejected:
        print("  REJECTED: %s" % p["slides"][0] if p.get("slides") else "  REJECTED")
        for f in p["_gate_failures"]:
            print("      - %s" % f)


if __name__ == "__main__":
    main()
