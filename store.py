#!/usr/bin/env python3
"""SQLite state: the publish queue and the metrics history.

The DB is committed back to the repo by the scheduled job. That is deliberate —
GitHub Actions runners are ephemeral, so anything not committed is lost, and the
commit doubles as the keepalive that stops GitHub disabling the cron after 60
days of no activity on the default branch.
"""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posts.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id            TEXT PRIMARY KEY,
    slides        TEXT NOT NULL,
    caption       TEXT NOT NULL,
    trigger       TEXT,
    structure     TEXT,
    satire_level  INTEGER,
    hinglish      INTEGER,
    source        TEXT,
    status        TEXT NOT NULL DEFAULT 'queued',
    queued_at     TEXT,
    published_at  TEXT,
    ig_media_id   TEXT,
    permalink     TEXT,
    last_error    TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
    ig_media_id        TEXT NOT NULL,
    post_id            TEXT,
    captured_at        TEXT NOT NULL,
    reach              INTEGER,
    views              INTEGER,
    likes              INTEGER,
    comments           INTEGER,
    saved              INTEGER,
    shares             INTEGER,
    total_interactions INTEGER,
    PRIMARY KEY (ig_media_id, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
"""

# CREATE TABLE IF NOT EXISTS cannot add a column to a table that already exists,
# and posts.db is live with published rows in it. Anything added after the first
# release goes here instead.
MIGRATIONS = [
    ("format", "ALTER TABLE posts ADD COLUMN format TEXT",
     "UPDATE posts SET format='carousel' WHERE format IS NULL"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


ROOT = os.path.dirname(os.path.abspath(__file__))


def _git_ok(*args) -> bool:
    try:
        return subprocess.run(["git"] + list(args), cwd=ROOT,
                              capture_output=True, timeout=25).returncode == 0
    except Exception:
        return False


def _git(*args) -> str:
    try:
        r = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                           text=True, timeout=25)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def guard_stale() -> None:
    """Refuse to write posts.db when the remote already has a newer one.

    posts.db is binary, so git cannot merge it. If the publish job writes it on
    a runner while you queue something locally, `git pull` resolves as a
    conflict and whichever side you discard is lost with no warning — a post
    silently unqueued, or one published twice.

    Skipped in CI, where the checkout is always fresh, and bypassable with
    SKIP_DB_GUARD=1 when offline.
    """
    if os.environ.get("GITHUB_ACTIONS") or os.environ.get("SKIP_DB_GUARD"):
        return
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        return
    _git("fetch", "-q", "origin", "main")
    # Being AHEAD of the remote is the normal case — you have committed and not
    # pushed yet. Only a remote that holds commits you do not have can cost you
    # anything, so if origin/main is already an ancestor of HEAD there is
    # nothing to lose and the blobs are allowed to differ.
    if _git_ok("merge-base", "--is-ancestor", "origin/main", "HEAD"):
        return
    here = _git("rev-parse", "HEAD:posts.db")
    there = _git("rev-parse", "origin/main:posts.db")
    if here and there and here != there:
        sys.exit(
            "posts.db on origin/main differs from your checkout — the publish job\n"
            "has written it since you last pulled. It is a binary file, so writing\n"
            "now would lose one side on the next merge.\n\n"
            "    git pull --rebase origin main\n\n"
            "then run this again. (SKIP_DB_GUARD=1 to override.)")


def connect(write: bool = False) -> sqlite3.Connection:
    if write:
        guard_stale()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    have = set(r["name"] for r in conn.execute("PRAGMA table_info(posts)"))
    for col, add, backfill in MIGRATIONS:
        if col not in have:
            conn.execute(add)
            conn.execute(backfill)   # every row that predates the column was a
            conn.commit()            # carousel, which is the A/B control arm
    return conn


def format_for(day: "datetime") -> str:
    """Which format publishes on a given day.

    By DAY, not by post. With two slots a day, alternating per post would pin
    carousels to the morning and reels to the evening forever, and the
    experiment could never separate format from time of day. Alternating by day
    gives each format both slots.
    """
    return "reel" if day.toordinal() % 2 else "carousel"


def set_format(conn: sqlite3.Connection, post_id: str, fmt: str) -> None:
    conn.execute("UPDATE posts SET format=? WHERE id=?", (fmt, post_id))
    conn.commit()


def enqueue(conn: sqlite3.Connection, post: Dict[str, Any]) -> bool:
    """Returns False if the post is already known — re-queueing is a no-op."""
    cur = conn.execute("SELECT 1 FROM posts WHERE id = ?", (post["id"],))
    if cur.fetchone():
        return False
    conn.execute(
        "INSERT INTO posts (id, slides, caption, trigger, structure, satire_level,"
        " hinglish, source, status, queued_at) VALUES (?,?,?,?,?,?,?,?,'queued',?)",
        (post["id"], json.dumps(post["slides"], ensure_ascii=False), post["caption"],
         post.get("trigger"), post.get("structure"), post.get("satire_level"),
         int(bool(post.get("hinglish"))), post.get("source"), now()))
    conn.commit()
    return True


def next_queued(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM posts WHERE status = 'queued' ORDER BY queued_at, id LIMIT 1"
    ).fetchone()


def mark_publishing(conn: sqlite3.Connection, post_id: str, container_id: str) -> None:
    """Record the in-flight publish BEFORE media_publish is called.

    If the connection drops on that call, Instagram may have accepted the post
    while we never saw the reply. A row left as 'queued' would be published a
    second time at the next slot; a row parked here is reconciled against the
    account's real feed first. The container id lands in ig_media_id and is
    overwritten with the real media id once the publish is confirmed.
    """
    conn.execute("UPDATE posts SET status='publishing', ig_media_id=? WHERE id=?",
                 (container_id, post_id))
    conn.commit()


def in_flight(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM posts WHERE status='publishing' ORDER BY queued_at, id"
    ).fetchall()


def requeue(conn: sqlite3.Connection, post_id: str) -> None:
    """Only ever called once the feed has been checked and the post is not on it."""
    conn.execute("UPDATE posts SET status='queued', ig_media_id=NULL WHERE id=?",
                 (post_id,))
    conn.commit()


def mark_published(conn: sqlite3.Connection, post_id: str, media_id: str,
                   permalink: Optional[str]) -> None:
    conn.execute("UPDATE posts SET status='published', published_at=?, ig_media_id=?,"
                 " permalink=?, last_error=NULL WHERE id=?",
                 (now(), media_id, permalink, post_id))
    conn.commit()


def mark_failed(conn: sqlite3.Connection, post_id: str, error: str) -> None:
    conn.execute("UPDATE posts SET status='failed', last_error=? WHERE id=?",
                 (error[:1000], post_id))
    conn.commit()


def skip(conn: sqlite3.Connection, post_id: str) -> bool:
    """Veto a queued post. Kept as a row, not deleted: what was rejected and why
    is training data for the ranking weights once performance data exists."""
    cur = conn.execute(
        "UPDATE posts SET status='skipped' WHERE id=? AND status='queued'", (post_id,))
    conn.commit()
    return cur.rowcount > 0


def queued(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM posts WHERE status='queued' ORDER BY queued_at, id").fetchall()


def by_format(conn: sqlite3.Connection) -> Dict[str, List[sqlite3.Row]]:
    """Published posts grouped by format, each with its latest metrics row."""
    rows = conn.execute("""
        SELECT p.*, m.reach, m.likes, m.saved, m.shares, m.total_interactions
        FROM posts p
        LEFT JOIN (SELECT ig_media_id, MAX(captured_at) AS t FROM metrics
                   GROUP BY ig_media_id) last ON last.ig_media_id = p.ig_media_id
        LEFT JOIN metrics m ON m.ig_media_id = p.ig_media_id
                           AND m.captured_at = last.t
        WHERE p.status = 'published'
        ORDER BY p.published_at""").fetchall()
    out = {}  # type: Dict[str, List[sqlite3.Row]]
    for r in rows:
        out.setdefault(r["format"] or "carousel", []).append(r)
    return out


def published(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM posts WHERE status='published' ORDER BY published_at DESC"
    ).fetchall()


def record_metrics(conn: sqlite3.Connection, media_id: str, post_id: Optional[str],
                   values: Dict[str, Any]) -> None:
    cols = ("reach", "views", "likes", "comments", "saved", "shares",
            "total_interactions")
    conn.execute(
        "INSERT OR REPLACE INTO metrics (ig_media_id, post_id, captured_at, %s)"
        " VALUES (?,?,?,%s)" % (",".join(cols), ",".join("?" * len(cols))),
        tuple([media_id, post_id, now()] + [values.get(c) for c in cols]))
    conn.commit()


def counts(conn: sqlite3.Connection) -> Dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) n FROM posts GROUP BY status").fetchall()
    return dict((r["status"], r["n"]) for r in rows)
