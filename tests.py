#!/usr/bin/env python3
"""
tests.py — the publish path, exercised without touching the network.

    python3 tests.py

stdlib only, so it runs in the publish job, which installs nothing.

It exists because of one bug. `from datetime import time` shadowed the stdlib
`time` module that wait_for_container polls with, and nothing caught it:
publish.py imported, compiled and passed `check` — the failure only appeared
against a live container, after a post had already been half-created. Anything
reached only when a real publish is in flight needs a stub and a test.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))

import command
import control
import dash
import metrics
import publish
import store


class ImportsAreNotShadowed(unittest.TestCase):
    def test_time_is_the_stdlib_module(self):
        import time as stdlib_time
        self.assertIs(publish.time, stdlib_time,
                      "publish.time is not the stdlib module — something in an "
                      "import list shadowed it")
        self.assertIsInstance(publish.time.time(), float)


class WaitForContainer(unittest.TestCase):
    """The path that only runs while a post is half-created."""

    def _stub(self, statuses):
        seq = list(statuses)
        def fake_get(cid, token, **kw):
            return {"status_code": seq.pop(0) if seq else "FINISHED"}
        return fake_get

    def test_returns_once_finished(self):
        publish.get = self._stub(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
        publish.wait_for_container("1", "t", "slide", timeout=30, interval=0)

    def test_raises_on_error_status(self):
        publish.get = self._stub(["ERROR"])
        with self.assertRaises(publish.GraphError):
            publish.wait_for_container("1", "t", "slide", timeout=30, interval=0)

    def test_times_out_rather_than_hanging(self):
        publish.get = self._stub(["IN_PROGRESS"] * 100)
        with self.assertRaises(publish.GraphError):
            publish.wait_for_container("1", "t", "reel", timeout=0, interval=0)


class Windows(unittest.TestCase):
    """Read from config.json rather than hardcoded, because the deadlines moved
    once already and a test that restates them only proves it was edited twice.
    What is asserted is the shape: no overlap, and a boundary that is inclusive
    at the start and exclusive at the end."""

    def windows(self):
        return publish.cfg("publish_windows", [])

    def test_every_window_opens_and_closes_where_config_says(self):
        day = "2026-09-18 "
        for w in self.windows():
            for t, expected in ((w["after"], w["name"]), (w["before"], None)):
                now = datetime.strptime(day + t, "%Y-%m-%d %H:%M")
                got = publish.current_window(now)
                self.assertEqual(got["name"] if got else None, expected,
                                 "%s at %s" % (w["name"], t))

    def test_a_minute_before_opening_is_still_closed(self):
        for w in self.windows():
            h, m = map(int, w["after"].split(":"))
            now = datetime(2026, 9, 18, h, m) - timedelta(minutes=1)
            self.assertIsNone(publish.current_window(now), w["name"])

    def test_windows_do_not_overlap(self):
        spans = sorted((w["after"], w["before"], w["name"]) for w in self.windows())
        for (a1, b1, n1), (a2, _, n2) in zip(spans, spans[1:]):
            self.assertLess(a1, b1, n1)
            self.assertLessEqual(b1, a2, "%s overruns into %s" % (n1, n2))


class Spacing(unittest.TestCase):
    """The widened deadlines put one window's end within an hour of the next
    window's start. Without this guard a single sparse afternoon could put two
    posts out 50 minutes apart, competing for the same audience."""

    class FakeConn(object):
        def __init__(self, rows):
            self.rows = rows

    def since(self, minutes_ago, now=None):
        now = now or datetime(2026, 9, 18, 20, 0)
        # published_at is stored in UTC; local_now() is UTC + the offset, so the
        # fixture has to be written in UTC or the guard reads hours off.
        off = timedelta(minutes=int(publish.cfg("publish_utc_offset_minutes", 330)))
        at = (now - timedelta(minutes=minutes_ago)) - off
        rows = [{"published_at": at.isoformat(timespec="seconds")}]
        real = store.published
        store.published = lambda conn: rows
        try:
            return publish.too_soon(None, now)
        finally:
            store.published = real

    def test_blocks_a_post_that_is_too_close_to_the_last_one(self):
        gap = int(publish.cfg("min_publish_gap_minutes", 90))
        self.assertEqual(self.since(gap - 30), 30)
        self.assertEqual(self.since(1), gap - 1)

    def test_allows_one_once_the_gap_has_passed(self):
        gap = int(publish.cfg("min_publish_gap_minutes", 90))
        self.assertEqual(self.since(gap), 0)
        self.assertEqual(self.since(gap + 600), 0)

    def test_an_empty_history_never_blocks(self):
        real = store.published
        store.published = lambda conn: []
        try:
            self.assertEqual(publish.too_soon(None), 0)
        finally:
            store.published = real


class Formats(unittest.TestCase):
    def test_alternates_by_day_not_by_post(self):
        days = [store.format_for(datetime(2026, 9, d)) for d in range(1, 9)]
        self.assertEqual(len(set(days)), 2)
        for a, b in zip(days, days[1:]):
            self.assertNotEqual(a, b, "consecutive days share a format")

    def test_urls(self):
        self.assertTrue(publish.reel_url("g005").endswith("/reels/g005.mp4"))
        self.assertEqual(len(publish.slide_urls("g005", 3)), 3)


class Reconcile(unittest.TestCase):
    """The only place in the system that can double-post.

    A row parked as 'publishing' means media_publish was sent and the reply was
    lost. reconcile() decides from the live feed whether it landed. Getting
    "absent" wrong publishes it twice.
    """

    class Conn(object):
        """Enough of a connection for reconcile: it only reaches the DB through
        the store functions, which are stubbed."""

    def run_with(self, feed_data, media_count, stuck=("g001",)):
        rows = [{"id": i, "caption": "cap-%s" % i} for i in stuck]
        calls = {"published": [], "requeued": []}
        real = (store.in_flight, store.mark_published, store.requeue, publish.get)
        store.in_flight = lambda conn: rows
        store.mark_published = lambda c, i, m, p: calls["published"].append(i)
        store.requeue = lambda c, i: calls["requeued"].append(i)

        def fake_get(path, token, **kw):
            if "media_count" in kw.get("fields", ""):
                return {"media_count": media_count}
            return {"data": feed_data}

        publish.get = fake_get
        try:
            publish.reconcile(self.Conn(), "tok", "me")
        finally:
            (store.in_flight, store.mark_published,
             store.requeue, publish.get) = real
        return calls

    def test_a_post_found_on_the_feed_is_recorded(self):
        c = self.run_with([{"id": "17", "caption": "cap-g001",
                            "permalink": "http://x"}], 9)
        self.assertEqual(c["published"], ["g001"])
        self.assertEqual(c["requeued"], [])

    def test_a_post_genuinely_absent_goes_back_in_the_queue(self):
        c = self.run_with([{"id": "17", "caption": "something else"}], 9)
        self.assertEqual(c["requeued"], ["g001"])
        self.assertEqual(c["published"], [])

    def test_an_empty_feed_from_a_non_empty_account_refuses_to_guess(self):
        # The failure this guards: the API answers with an empty page, every
        # parked row looks absent, and a post that is already live is requeued
        # and published a second time.
        with self.assertRaises(publish.GraphError):
            self.run_with([], 9)

    def test_an_empty_feed_from_an_empty_account_is_believed(self):
        c = self.run_with([], 0)
        self.assertEqual(c["requeued"], ["g001"])


class IssueCommands(unittest.TestCase):
    """The issue title is the only input to this system that arrives as free
    text from outside a script, and command.yml interpolates the argument into
    a shell line afterwards. The owner check in the workflow is access control;
    this is the escaping."""

    def test_the_buttons_produce_commands_that_parse(self):
        for kind, expect in (("queued", ("now", "skip")),
                             ("skipped", ("unskip",)),
                             (None, ("approve",))):
            html = control.bar("g006", kind, rank=3)
            titles = [t.replace("%20", " ") for t in
                      __import__("re").findall(r"issues/new\?title=([^&]+)", html)]
            self.assertEqual(len(titles), len(expect), kind)
            for title, verb in zip(titles, expect):
                go, got, _ = command.parse(title)
                self.assertEqual((go, got), ("ok", verb), title)

    def test_a_published_post_gets_no_buttons(self):
        self.assertNotIn("issues/new", control.bar("g001", "published"))

    def test_junk_arguments_never_reach_the_shell(self):
        for title in ("now g006; rm -rf /", "skip $(whoami)", "skip g6",
                      "approve twelve", "approve 0", "skip ../../etc"):
            go, _, _ = command.parse(title)
            self.assertEqual(go, "bad", title)

    def test_a_real_issue_is_left_alone(self):
        for title in ("Reel audio is broken", "", "skipping the gym"):
            self.assertEqual(command.parse(title)[0], "ignore", title)


class Numbers(unittest.TestCase):
    """The arithmetic behind `ab` and the dashboard, on a throwaway DB.

    Both readings come from metrics.py now, so a test here covers the page and
    the terminal at once. The fixture is deliberately lopsided: one arm with a
    single huge post is exactly the shape that makes a mean lie and a median
    hold.
    """

    def _db(self, rows, base=None):
        """rows: (format, reach, views, shares, age_hours_at_capture).

        age_hours is how old the post was when the capture was taken, because
        that is the whole question day_one answers: a post measured at three
        hours and one measured at three days are not comparable numbers.
        """
        import sqlite3
        base = base or datetime(2026, 9, 1, tzinfo=timezone.utc)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(store.SCHEMA)
        conn.execute("ALTER TABLE posts ADD COLUMN format TEXT")
        for i, row in enumerate(rows):
            fmt, reach, views, shares = row[:4]
            age = row[4] if len(row) > 4 else 20
            mid, pid = "m%d" % i, "x%03d" % i
            pub = base + timedelta(days=i)
            conn.execute(
                "INSERT INTO posts (id, slides, caption, status, format,"
                " published_at, ig_media_id) VALUES (?,'[]','',?,?,?,?)",
                (pid, "published", fmt, pub.isoformat(), mid))
            conn.execute(
                "INSERT INTO metrics (ig_media_id, post_id, captured_at, reach,"
                " views, shares) VALUES (?,?,?,?,?,?)",
                (mid, pid, (pub + timedelta(hours=age)).isoformat(), reach,
                 views, shares))
        conn.commit()
        return conn

    def test_only_the_newest_capture_counts(self):
        """Metrics rows are cumulative, so summing the history triples reach."""
        conn = self._db([("carousel", 10, 20, 1)])
        conn.execute("INSERT INTO metrics (ig_media_id, post_id, captured_at,"
                     " reach, views, shares) VALUES ('m0','x000',"
                     "'2026-09-05T06:00:00+00:00', 30, 60, 3)")
        conn.commit()
        o = metrics.overview(conn)
        self.assertEqual((o["reach"], o["views"], o["posts"]), (30, 60, 1))

    def test_a_post_too_young_to_judge_is_left_out(self):
        """Reach accrues for days; a three-hour-old post is not a data point."""
        conn = self._db([("carousel", 40, 80, 2, 20), ("reel", 3, 5, 0, 3)])
        self.assertEqual([r["id"] for r in metrics.day_one(conn)], ["x000"])

    def test_day_one_reads_the_last_capture_before_24h(self):
        """Not the newest capture — the newest one taken inside the window."""
        conn = self._db([("carousel", 10, 20, 0, 18)])
        conn.execute("INSERT INTO metrics (ig_media_id, post_id, captured_at,"
                     " reach, views, shares) VALUES ('m0','x000',"
                     "'2026-09-09T00:00:00+00:00', 999, 999, 9)")
        conn.commit()
        self.assertEqual(metrics.day_one(conn)[0]["reach"], 10)
        self.assertEqual(metrics.overview(conn)["reach"], 999)

    def test_a_naive_timestamp_does_not_crash_the_page(self):
        conn = self._db([("carousel", 10, 20, 0, 18)])
        conn.execute("UPDATE metrics SET captured_at='2026-09-01T18:00:00'")
        conn.commit()
        self.assertEqual(len(metrics.day_one(conn)), 1)

    def test_a_median_is_not_moved_by_one_outlier(self):
        conn = self._db([("carousel", 10, 1, 0), ("carousel", 12, 1, 0),
                         ("carousel", 900, 1, 0)])
        line = metrics.compare(conn)["lines"][0]
        self.assertEqual(line["carousel"], 12)

    def test_an_empty_arm_never_divides_by_zero(self):
        cmp_ = metrics.compare(self._db([("carousel", 10, 20, 0)]))
        self.assertEqual(cmp_["n"], {"carousel": 1, "reel": 0})
        self.assertIsNone(cmp_["ci"])
        self.assertTrue(all(l["ratio"] is None for l in cmp_["lines"][:2]))
        self.assertIn("Not enough posts", cmp_["headline"])

    def test_a_tiny_sample_always_carries_its_caveat(self):
        conn = self._db([("carousel", 10, 20, 0)] * 4 + [("reel", 90, 99, 2)] * 3)
        cmp_ = metrics.compare(conn)
        self.assertIsNotNone(cmp_["ci"])
        self.assertIn("Reels reach", cmp_["headline"])
        self.assertIsNotNone(cmp_["caveat"],
                             "3 posts in an arm must never read as a result")

    def test_the_dashboard_survives_an_empty_database(self):
        """It is best-effort on the sheet, but it should not need to be."""
        html = dash.block(self._db([]))
        self.assertIn("reach, first day", html)
        self.assertIn("Metrics have never been pulled", html)
        self.assertNotIn("smaller arm", html)

    def test_stale_metrics_say_so(self):
        conn = self._db([("carousel", 10, 20, 0)])
        conn.execute("UPDATE metrics SET captured_at = ?",
                     ((datetime.now(timezone.utc)
                       - timedelta(hours=dash.STALE_HOURS + 2)).isoformat(),))
        conn.commit()
        self.assertIn("Older than it should be", dash.block(conn))
        conn.execute("UPDATE metrics SET captured_at = ?",
                     (datetime.now(timezone.utc).isoformat(),))
        conn.commit()
        self.assertNotIn("Older than it should be", dash.block(conn))


if __name__ == "__main__":
    unittest.main(verbosity=2)
