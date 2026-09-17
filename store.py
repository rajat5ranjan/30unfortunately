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


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


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
