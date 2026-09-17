"""Burst navigation must paint once, not once per encoder detent."""

from __future__ import annotations

import os
import sys
import unittest

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon.nav_coalesce import NavPaintCoalescer  # noqa: E402


class NavPaintCoalescerTests(unittest.TestCase):
    def test_burst_requests_collapse_to_one_idle_paint(self) -> None:
        idle: list[object] = []
        paints = {"n": 0}

        def after_idle(fn):
            idle.append(fn)
            return "idle"

        def after(_ms, fn):
            return ("after", fn)

        def cancel(_handle):
            return None

        c = NavPaintCoalescer(
            after_idle=after_idle,
            after=after,
            cancel=cancel,
            paint=lambda: paints.__setitem__("n", paints["n"] + 1),
            min_interval_s=0.0,
            settle_s=9.0,
            hot_s=9.0,
        )
        c.request()
        c.request()
        c.request()
        self.assertEqual(len(idle), 1)
        self.assertTrue(c.is_hot())
        idle[0]()
        self.assertEqual(paints["n"], 1)
        self.assertFalse(c.pending)

    def test_rate_limit_defers_second_paint(self) -> None:
        import time

        idle: list[object] = []
        paints = {"n": 0}

        def after_idle(fn):
            idle.append(fn)
            return "idle"

        def after(_ms, fn):
            return ("after", fn)

        def cancel(_handle):
            return None

        c = NavPaintCoalescer(
            after_idle=after_idle,
            after=after,
            cancel=cancel,
            paint=lambda: paints.__setitem__("n", paints["n"] + 1),
            min_interval_s=0.05,
            settle_s=9.0,
            hot_s=9.0,
        )
        c.request()
        idle[0]()
        self.assertEqual(paints["n"], 1)
        c._last_paint = time.monotonic()
        c.request()
        idle[-1]()
        self.assertEqual(paints["n"], 1)
        self.assertTrue(c.pending)


if __name__ == "__main__":
    unittest.main()
