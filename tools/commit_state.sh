#!/usr/bin/env bash
# Commit and push the pipeline's state, surviving a race with another push.
#
#     tools/commit_state.sh "<commit message>" <path> [<path> ...]
#
# Every scheduled job persists its work by committing it: the runners are
# ephemeral, so an unpushed commit is a lost one. For the publish job that is
# not a cosmetic failure — if the row that records "this went out on Instagram"
# never reaches main, the next run reads a stale posts.db and ships the same
# carousel a second time. So a rejected push is retried rather than reported.
set -euo pipefail

MSG="$1"; shift

git config user.name  "30unfortunately-bot"
git config user.email "rajat5ranjan@users.noreply.github.com"

git add "$@"
if git diff --staged --quiet; then
  echo "nothing to commit"
  exit 0
fi
git commit -q -m "$MSG"

for attempt in 1 2 3; do
  if git push -q; then
    echo "pushed: $MSG"
    exit 0
  fi
  echo "push rejected — main moved under us; rebasing (attempt $attempt)"

  # --autostash: a job may leave unrelated files dirty (a log, a re-rendered
  # asset) and rebase refuses to start with a dirty tree.
  if git pull --rebase --autostash -q origin main; then
    continue
  fi

  # The only file here that git cannot merge is posts.db, because it is binary.
  # During a rebase "theirs" is the commit being replayed — ours. Keep it: it
  # holds the publish record, and losing that double-posts. Anything the other
  # side queued is then restored from content/approved, which is text, merges
  # cleanly, and re-queues idempotently.
  if [ "$(git diff --name-only --diff-filter=U)" = "posts.db" ]; then
    echo "posts.db conflicted — keeping this run's copy and re-queueing from content/approved"
    git checkout --theirs -- posts.db
    for f in content/approved/*.json; do
      [ -e "$f" ] || continue
      SKIP_DB_GUARD=1 python3 publish.py queue --src "$f" >/dev/null || true
    done
    git add posts.db
    GIT_EDITOR=true git rebase --continue
  else
    echo "::error::conflict outside posts.db — resolve by hand:"
    git diff --name-only --diff-filter=U
    git rebase --abort || true
    exit 1
  fi
done

echo "::error::could not push state after 3 attempts — posts.db on main is stale"
exit 1
