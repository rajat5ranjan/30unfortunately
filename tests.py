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
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))

import audio
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

    def run_with(self, feed_data, media_count, stuck=("g001",), keywords=None):
        rows = [{"id": i, "caption": "cap-%s" % i,
                 "keywords": json.dumps(keywords) if keywords else None}
                for i in stuck]
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

    def test_it_matches_the_caption_instagram_actually_received(self):
        """The hashtag line is part of the published caption, so it is part
        of the string reconcile has to search for. Get this wrong and a post
        that DID publish reads as absent, and absent means publish it again."""
        live = "cap-g001\n\n#rent #gym #thirties"
        c = self.run_with([{"id": "17", "caption": live, "permalink": "http://x"}],
                          9, keywords=["rent", "gym", "thirties"])
        self.assertEqual(c["published"], ["g001"])
        self.assertEqual(c["requeued"], [])

    def test_a_post_queued_before_keywords_existed_still_matches(self):
        c = self.run_with([{"id": "17", "caption": "cap-g001",
                            "permalink": "http://x"}], 9, keywords=None)
        self.assertEqual(c["published"], ["g001"])

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



class Discoverability(unittest.TestCase):
    def _row(self, caption, keywords):
        return {"caption": caption,
                "keywords": json.dumps(keywords) if keywords is not None else None}

    def test_the_visible_caption_is_untouched_without_keywords(self):
        row = self._row("Whose mother is this?", None)
        self.assertEqual(publish.full_caption(row), "Whose mother is this?")

    def test_tags_go_on_their_own_line_never_into_the_joke(self):
        out = publish.full_caption(self._row("Whose mother is this?",
                                             ["co-living bangalore", "rent", "gym"]))
        first = out.split("\n")[0]
        self.assertEqual(first, "Whose mother is this?")
        self.assertIn("#colivingbangalore", out)
        self.assertNotIn("#", first)

    def test_at_most_three_tags_and_no_duplicates(self):
        out = publish.full_caption(self._row("x", ["rent", "rent", "gym", "emi"]))
        self.assertEqual(out.count("#"), 2)

    def test_alt_text_carries_the_slide_text_verbatim(self):
        alt = publish.alt_text_for(self._row("x", ["rent"]),
                                   "Same mattress.\nThe phone costs more.", 2, 3)
        self.assertIn("Same mattress. The phone costs more.", alt)
        self.assertIn("Slide 2 of 3", alt)
        self.assertIn("rent", alt)

    def test_alt_text_stays_under_the_api_limit(self):
        alt = publish.alt_text_for(self._row("x", ["rent"]), "w " * 900, 1, 3)
        self.assertLessEqual(len(alt), 1000)


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


class Soundtrack(unittest.TestCase):
    """audio.py is stdlib-only on purpose, so the publish job can test it.

    None of this is about taste. It is about the four things that are audible
    as faults rather than as choices: clipping, a click, a seam on the loop,
    and a level that never moves.
    """

    def score(self, slides=3, dur=5.0, keys=70, type_dur=2.0):
        segs, t = [], 0.0
        for n in range(slides):
            segs.append({"role": "hook" if n == 0 else "list",
                         "start": t, "dur": dur, "keys": keys,
                         "type_dur": 0.0 if n == 0 else type_dur,
                         "charge0": 1.0 - n / float(slides),
                         "charge1": 1.0 - (n + 1) / float(slides)})
            t += dur
        return {"slides": segs, "outro": {"start": t, "dur": 3.5},
                "loop": 0.5, "duration": t + 4.0}

    def pcm(self, **kw):
        import array
        a = array.array("h")
        a.frombytes(audio.render(self.score(**kw)))
        return a

    def test_length_matches_the_plan(self):
        sc = self.score()
        a = self.pcm()
        self.assertAlmostEqual(len(a) / float(audio.SR), sc["duration"], delta=0.02)

    def test_nothing_clips(self):
        a = self.pcm()
        self.assertLessEqual(max(abs(v) for v in a), int(32767 * audio.PEAK) + 2,
                             "the limiter let something past PEAK")

    def test_no_step_big_enough_to_click(self):
        """A click is a discontinuity. One sample's worth of the highest
        partial in the mix at full level is the steepest a real waveform can
        be; anything above it is a join that was never faded. TOP_HZ rather
        than a number here, so changing the material cannot quietly widen the
        thing this test is guarding."""
        a = self.pcm()
        step = max(abs(a[i + 1] - a[i]) for i in range(len(a) - 1))
        ceiling = 32767 * audio.PEAK * 2 * 3.14159 * audio.TOP_HZ / audio.SR
        self.assertLess(step, ceiling, "a sample step too big to be a waveform")

    def test_the_loop_has_no_seam(self):
        """Both ends silent and equal, or the replay clicks — and replays are
        most of a reel's watch time."""
        a = self.pcm()
        self.assertEqual(list(a[:3]), [0, 0, 0])
        self.assertEqual(list(a[-3:]), [0, 0, 0])

    def test_the_typing_leads(self):
        """Keys on top, melody underneath. A reel where the tune leads is a
        reel whose soundtrack is about nothing, which is how three earlier
        versions of this file went wrong — so it is worth a test rather
        than a comment.

        Slide 1 does not type and slide 2 does, so the same file gives both
        measurements and normalisation cannot flatter either of them.
        """
        import math
        a = self.pcm()
        sc = self.score()
        quiet, loud = sc["slides"][0], sc["slides"][1]

        def rms(t0, t1):
            seg = a[int(t0 * audio.SR):int(t1 * audio.SR)]
            return math.sqrt(sum(v * v for v in seg) / max(1, len(seg)))

        melody = rms(quiet["start"] + 0.8, quiet["start"] + quiet["dur"])
        typing = rms(loud["start"], loud["start"] + loud["type_dur"])
        self.assertGreater(typing / melody, 1.6,
                           "the melody is competing with the typing")

    def test_it_is_not_monotone(self):
        """The brief was smooth, not flat. Half-second RMS has to move."""
        import math
        a = self.pcm()
        b = audio.SR // 2
        rms = [math.sqrt(sum(v * v for v in a[i:i + b]) / b)
               for i in range(0, len(a) - b, b)]
        self.assertGreater(max(rms) / max(1.0, min(rms)), 2.0,
                           "the level never moves — that is a drone")

    def test_an_empty_plan_is_survivable(self):
        """No slides, no crash: ensure_reels hands this whatever is queued."""
        pcm = audio.render({"slides": [], "outro": {"start": 0.0, "dur": 1.0},
                            "loop": 0.0, "duration": 1.0})
        self.assertEqual(len(pcm), 2 * audio.SR)


if __name__ == "__main__":
    unittest.main(verbosity=2)
