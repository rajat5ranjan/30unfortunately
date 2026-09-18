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
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))

import command
import control
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
    def test_boundaries(self):
        day = "2026-09-18 "
        for t, expected in (("08:29", None), ("08:30", "morning"),
                            ("10:29", "morning"), ("10:30", None),
                            ("15:00", "afternoon"), ("17:00", None),
                            ("19:30", "evening"), ("21:30", None)):
            now = datetime.strptime(day + t, "%Y-%m-%d %H:%M")
            w = publish.current_window(now)
            self.assertEqual(w["name"] if w else None, expected, t)


class Formats(unittest.TestCase):
    def test_alternates_by_day_not_by_post(self):
        days = [store.format_for(datetime(2026, 9, d)) for d in range(1, 9)]
        self.assertEqual(len(set(days)), 2)
        for a, b in zip(days, days[1:]):
            self.assertNotEqual(a, b, "consecutive days share a format")

    def test_urls(self):
        self.assertTrue(publish.reel_url("g005").endswith("/reels/g005.mp4"))
        self.assertEqual(len(publish.slide_urls("g005", 3)), 3)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
