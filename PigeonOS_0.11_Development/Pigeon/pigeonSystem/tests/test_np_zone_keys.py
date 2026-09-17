"""Temporary 1–8 now-playing zone cycling."""

from __future__ import annotations

import os
import sys
import unittest

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon.np_layout import NOW_PLAYING_ZONES, header_clock_center_x
from pigeon.np_zone_keys import (
    TT_WIDE,
    cycle_now_playing_zone,
    zone_current_widget,
)
from pigeon.widgets.preferences_settings import (
    DEFAULT_MUSIC_ZONE_WIDGETS,
    DEFAULT_ZONE_WIDGETS,
    _normalize_zone_widgets,
)
from pigeon.widgets.view_circles import _effective_zone_widgets


class ZoneCycleCollisionTests(unittest.TestCase):
    def test_zone6_on_then_1_disables_wide_tt(self) -> None:
        a = DEFAULT_ZONE_WIDGETS
        self.assertEqual(zone_current_widget(a, 6), TT_WIDE)
        self.assertEqual(zone_current_widget(a, 1), "")
        nxt, header = cycle_now_playing_zone(a, 1, header_clock=True)
        self.assertNotEqual(nxt[0], TT_WIDE)
        self.assertTrue(nxt[0])
        self.assertEqual(zone_current_widget(nxt, 6), "")
        self.assertTrue(header)

    def test_zone1_and_2_then_6_clears_portraits(self) -> None:
        a = ("tt_countdown", "poster", "volume", "cast_info", "status_bar")
        nxt, _header = cycle_now_playing_zone(a, 6, header_clock=True)
        self.assertEqual(nxt[0], TT_WIDE)
        self.assertEqual(nxt[1], "")
        self.assertEqual(zone_current_widget(nxt, 6), TT_WIDE)

    def test_repeat_press_cycles_then_off(self) -> None:
        a = ("", "", "volume", "cast_info", "status_bar")
        first, _h = cycle_now_playing_zone(a, 3, header_clock=True)
        self.assertNotEqual(first[2], "volume")
        cur = first
        seen = {cur[2]}
        for _ in range(12):
            cur, _h = cycle_now_playing_zone(cur, 3, header_clock=True)
            seen.add(cur[2])
            if cur[2] == "volume":
                break
        self.assertIn("", seen)
        self.assertIn("volume", seen)

    def test_zone8_toggles_header_without_killing_tt(self) -> None:
        a = DEFAULT_ZONE_WIDGETS
        off, header = cycle_now_playing_zone(a, 8, header_clock=True)
        self.assertEqual(off, a)
        self.assertFalse(header)
        on, header = cycle_now_playing_zone(off, 8, header_clock=False)
        self.assertEqual(on[0], TT_WIDE)
        self.assertTrue(header)

    def test_header_clock_centers_on_zone6(self) -> None:
        z6 = NOW_PLAYING_ZONES[6]
        want = z6.x + z6.w / 2.0
        self.assertAlmostEqual(header_clock_center_x(DEFAULT_ZONE_WIDGETS), want)
        self.assertAlmostEqual(
            header_clock_center_x(DEFAULT_MUSIC_ZONE_WIDGETS), want
        )


class MusicLayoutTests(unittest.TestCase):
    def test_music_defaults_volume_and_track_info(self) -> None:
        self.assertEqual(
            DEFAULT_MUSIC_ZONE_WIDGETS,
            ("tt_countdown_16x9", "", "volume", "cast_info", "status_bar"),
        )
        self.assertEqual(
            _normalize_zone_widgets(
                DEFAULT_MUSIC_ZONE_WIDGETS,
                defaults=DEFAULT_MUSIC_ZONE_WIDGETS,
            ),
            DEFAULT_MUSIC_ZONE_WIDGETS,
        )

    def test_effective_keeps_music_cast_info_without_tmdb_names(self) -> None:
        got = _effective_zone_widgets(
            has_position=True,
            cast_count=0,
            content_active=True,
            zone_widgets=DEFAULT_MUSIC_ZONE_WIDGETS,
            content_mode="music",
            has_volume=True,
        )
        self.assertEqual(got[2], "volume")
        self.assertEqual(got[3], "cast_info")
        video = _effective_zone_widgets(
            has_position=True,
            cast_count=0,
            content_active=True,
            zone_widgets=DEFAULT_ZONE_WIDGETS,
            content_mode="video",
        )
        self.assertEqual(video[3], "")
