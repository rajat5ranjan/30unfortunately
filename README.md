# Thirty Unfortunately

An autonomous Instagram content agent. Two carousels a day, satirising Indian
adulthood, generated from live trends, rendered locally, published via the
official Meta API.

**Account:** [@30unfortunately](https://instagram.com/30unfortunately) ·
**Budget:** ₹0/month · **Status:** Phase 3 of 8

## The hypothesis

The original question — *"can an AI content system build an organic Instagram
audience?"* — is not falsifiable with n=1 and no control. It is split into three:

| | Hypothesis | Resolves |
|---|---|---|
| **H1** | The pipeline ships ≥2 publishable posts/day for 90 days on <5 min/day of human time, ₹0 infra | ~90 days, fully in our control |
| **H2** | In a blind test, AI posts are indistinguishable from hand-written ones | ~2 weeks, independent of Instagram |
| **H3** | Share rate ≥1% of reach; N followers by day 90 | ~90 days, noisy, may return null |

H1 and H2 are the real experiment. H3 is the lottery. The design assumes a null
H3 must still leave a usable result.

## Why carousels, not single images

Instagram 2026 organic reach: Reels ~30.8%, carousels ~14.5%, **single images
~13.1%** — and single-image reach fell ~22% YoY. Testing a content system using
the platform's worst-performing format confounds the experiment: a null result
would measure the format, not the content. Carousels cost ~20 lines more in the
renderer and add a swipe-completion signal. Reels are the Phase 7 A/B.

## Layout

```
brand.yaml              the content contract — identity, voice, gates. The product.
brain.py                trends → prompt → LLM → gates → candidates
render.py               posts → 1080×1350 PNGs + contact sheet
publish.py              queue → carousel containers → publish → insights
store.py                SQLite: the queue and the metrics history
config.json             model, thresholds, font, Pages base URL
posts.db                committed on purpose — see .gitignore
content/
  seed_posts.json       12 approved posts, the few-shot source
  trends.json           trend input (manual until Phase 5)
  candidates/           generated, gitignored
docs/                   GitHub Pages root — index.html is the approval sheet,
                        media/<id>/N.png is what Meta pulls at publish time
assets/brand/           battery mark, end-mark, profile picture
fonts/                  Bricolage Grotesque (OFL)
tools/                  make_mark_svg.py, make_pfp.py — regenerate brand assets
                        from the card font, so they cannot drift from the cards
```

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python brain.py --check      # gate self-test, no key needed
.venv/bin/python brain.py --dry-run    # print the exact prompt, no key needed
.venv/bin/python brain.py              # needs GEMINI_API_KEY
.venv/bin/python render.py             # PNGs + docs/index.html

python3 publish.py check               # token, rate limit, queue, image liveness
python3 publish.py queue --ids p001    # add approved posts to the queue
python3 publish.py next --dry-run      # show what would go out
python3 publish.py next                # publish it
python3 publish.py insights            # pull metrics
python3 publish.py refresh-token       # extend the 60-day token
```

`publish.py`, `store.py` and `notify.py` are stdlib-only, so the scheduled job
has no install step that can break.

`posts.db` is binary, so git cannot merge it. Local commands that write to it
fetch first and refuse if the remote has a newer copy — otherwise a publish on
the runner and a queue locally resolve as a conflict and one side is lost with
no warning. Skipped in CI, where the checkout is always fresh;
`SKIP_DB_GUARD=1` overrides it when offline.

## Secrets

Set as repository secrets, never in the repo — it is public.

| Secret | Needed for |
|---|---|
| `IG_ACCESS_TOKEN` | everything |
| `IG_USER_ID` | publishing |
| `GEMINI_API_KEY` | generation only |
| `GH_PAT` | optional: lets the refresh job rotate `IG_ACCESS_TOKEN` itself. Without it the job refreshes, then fails loudly telling you to update the secret by hand. |

## The gates

Every generated post must survive: banned phrases · no emoji in card art ·
2–3 slides · hook ≤90 chars · landing cannot end on a question · at least one
concrete detail · a source URL or `evergreen` · and **novelty — diffed against
the entire history at 72% on both body and angle.**

That last one matters most. The failure mode that kills these accounts is not
bad posts, it is the same post again around week six. The prompt also feeds the
account's own structure-overuse stats back to the model each run.

## Platform constraints worth knowing before you build this

- **No byte upload.** Meta *pulls* images from a public HTTPS URL. Hence `docs/`
  on GitHub Pages, hence the repo is public.
- **No App Review needed** to post to your own account: a Meta app in
  Development mode plus your account added as an Instagram Tester.
- **50 posts / 24h** rate limit. Two a day is not close.
- **Tokens expire at 60 days** and can only be refreshed while alive. A 90-day
  experiment dies at day 60 without a refresh job.
- **GitHub Actions cron is disabled after 60 days** with no commit on the default
  branch, and is delayed or dropped under load. The publish job commits the DB
  back, which doubles as the keepalive.
- **API-published Reels cannot use Instagram's trending audio.** Audio must be
  baked into the file. Relevant to the Phase 7 Reels test.
- **Per-post `profile_views` was deprecated** in Graph API v21. Available per
  media: views, reach, likes, comments, saved, shares, reposts. So shares/reach
  and saves/reach are the primary metrics; follower attribution is account-level.

## The daily loop

```
06:15 IST  generate.yml   ONLY if fewer than 4 posts are queued:
                          trends -> generate -> judge ranks -> auto-approve
                          top 6 -> render -> queue -> email you the list
08:40      publish.yml    read email replies -> publish next -> insights
19:40      publish.yml    the second post
every 21d  refresh-token  keeps the 60-day token alive
```

**There is no approval step.** The best-ranked candidate is queued
automatically and ships. You get an email listing what is about to go out; to
stop one, reply `skip g005`. Do nothing and it publishes.

Generation is backlog-driven, and the arithmetic has to work or the gate never
trips. Publishing consumes 2/day, so a run that approves 2 leaves the queue
empty every morning, satisfies min-backlog every time, and calls Gemini daily —
the opposite of the intent. Approving 6 per run gives roughly one call every two
or three days and a 2-6 post cushion if Gemini is unavailable.

The veto is read by `publish.py` seconds before posting. Actions cannot receive
a webhook, but the publish job already runs at exactly the moment a veto
matters, so polling there makes the window real-time and costs nothing.

### Ranking

A second, separate LLM call grades each candidate 1-5 on share_trigger,
specificity, surprise and voice — asking the writer to grade its own work in the
same breath returns uniformly high marks. Repetition penalties are applied in
code afterwards, because a judge looking at one batch cannot see that the
account has published four escalating lists this week.

**These weights are guesses.** LLM self-scoring correlates weakly with what
actually gets shared. At n>=30 published posts, refit `RANK_WEIGHTS` against
real shares/reach rather than trusting the judge. Everything else is committed state
moving between jobs: `content/trends.json`, `content/candidates/`,
`content/approved/`, `docs/media/` and `posts.db` all travel through git,
because runners are ephemeral and git is the only storage this design has.

Candidates are text only. Rendering all eight daily would add ~1.7MB of PNGs a
day to a repo that also *serves* them — about 150MB over 90 days. Only the
approved post is rendered.

## Phases

1. ~~Content contract~~ · 2. ~~Seed posts~~ · 3. ~~Renderer~~ ·
4. ~~Publisher + scheduler~~ · 5. ~~Trend discovery~~ · 6. ~~First live posts~~ ·
7. 30-post run → insights → carousel vs Reel A/B ·
8. Learning loop (only at n≥60; before that it fits noise)

Fonts are SIL OFL. Everything else is a personal experiment.

To reconsider the typeface, re-download candidates from the Google Fonts repo,
point `config.json` at one, then run `tools/make_mark_svg.py` and `render.py` —
cards, ghost, watermark and logo all follow.
