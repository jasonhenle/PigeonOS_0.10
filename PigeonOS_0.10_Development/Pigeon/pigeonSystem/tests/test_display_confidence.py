"""OCR stay-alert, identity confidence, and zone fallbacks."""

from __future__ import annotations

import os
import sys
import unittest

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon import display_confidence as dc  # noqa: E402
from pigeon import hdmi_ocr as ho  # noqa: E402
from pigeon.widgets.view_circles import _effective_zone_widgets  # noqa: E402


class PlayerMetadataTests(unittest.TestCase):
    def test_pyatv_title_is_adequate(self) -> None:
        md = {"query": "Ted Lasso", "identity_source": "pyatv"}
        self.assertTrue(dc.player_metadata_adequate(md))
        self.assertFalse(dc.ocr_is_in_charge(md))
        self.assertGreaterEqual(dc.identity_confidence(md), dc.DISPLAY_MIN)

    def test_ocr_filled_query_is_not_player_metadata(self) -> None:
        md = {"query": "The Crown", "ocr_title": "The Crown", "identity_source": "ocr"}
        self.assertFalse(dc.player_metadata_adequate(md))
        self.assertTrue(dc.ocr_is_in_charge(md))

    def test_stale_identity_is_not_displayable(self) -> None:
        md = {"query": "", "identity_source": "stale", "identity_confidence": dc.STALE}
        self.assertFalse(dc.identity_displayable(md))
        self.assertTrue(dc.ocr_is_in_charge(md))

    def test_foreground_app_without_title_stays_active(self) -> None:
        md = {"app_name": "Netflix", "query": "", "device_state": "Idle"}
        self.assertTrue(dc.content_should_stay_active(md, hdmi_on=True))
        self.assertFalse(dc.content_should_stay_active(md, hdmi_on=False))

    def test_ocr_not_in_charge_when_dongle_missing(self) -> None:
        md = {"query": "", "app_name": "Netflix", "identity_source": "stale"}
        self.assertTrue(dc.ocr_is_in_charge(md, hdmi_on=True, hdmi_present=True))
        self.assertFalse(dc.ocr_is_in_charge(md, hdmi_on=True, hdmi_present=False))
        self.assertFalse(
            dc.content_should_stay_active(md, hdmi_on=True, hdmi_present=False)
        )


class OcrScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        ho.reset_ocr_schedule()

    def tearDown(self) -> None:
        ho.reset_ocr_schedule()

    def test_keeps_ocr_until_tmdb_ready(self) -> None:
        md = {
            "device_state": "Idle",
            "query": "",
            "app_name": "Netflix",
            "content_key": "nf",
        }
        self.assertEqual(ho.decide_ocr_reason(md, now=100.0), "no_metadata")
        self.assertEqual(ho.decide_ocr_reason(md, now=106.0), "no_metadata")

    def test_watches_every_five_seconds_after_title(self) -> None:
        md = {
            "device_state": "Playing",
            "query": "Ted Lasso",
            "content_key": "ted",
            "identity_source": "pyatv",
            "position": 10.0,
        }
        self.assertEqual(ho.decide_ocr_reason(md, now=100.0), "watch")
        self.assertIsNone(ho.decide_ocr_reason(md, now=102.0))
        self.assertEqual(ho.decide_ocr_reason(md, now=106.0), "watch")

    def test_pause_still_triggers(self) -> None:
        md = {
            "device_state": "Playing",
            "query": "Ted Lasso",
            "content_key": "ted",
            "identity_source": "pyatv",
            "position": 10.0,
        }
        self.assertEqual(ho.decide_ocr_reason(md, now=100.0), "watch")
        md = dict(md)
        md["device_state"] = "Paused"
        self.assertEqual(ho.decide_ocr_reason(md, now=106.0), "pause")

    def test_frame_fingerprint_counts_unchanged_streak(self) -> None:
        import numpy as np

        frame_a = np.full((108, 192, 3), 40, dtype=np.uint8)
        frame_b = np.full((108, 192, 3), 200, dtype=np.uint8)
        self.assertTrue(ho._note_frame_fingerprint(frame_a))
        self.assertEqual(ho.hdmi_unchanged_streak(), 0)
        self.assertFalse(ho._note_frame_fingerprint(frame_a))
        self.assertEqual(ho.hdmi_unchanged_streak(), 1)
        self.assertFalse(ho._note_frame_fingerprint(frame_a))
        self.assertEqual(ho.hdmi_unchanged_streak(), 2)
        self.assertTrue(ho._note_frame_fingerprint(frame_b))
        self.assertEqual(ho.hdmi_unchanged_streak(), 0)
        self.assertFalse(ho.hdmi_clock_saver_due())

    def test_clock_saver_due_after_streak(self) -> None:
        ho._schedule.consecutive_unchanged = ho.CLOCK_SAVER_OCR_STREAK
        self.assertTrue(ho.hdmi_clock_saver_due())


class TitleDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        from pigeon.title_decision import reset_title_decisions

        reset_title_decisions()

    def tearDown(self) -> None:
        from pigeon.title_decision import reset_title_decisions

        reset_title_decisions()

    def test_records_and_explains(self) -> None:
        from pigeon.title_decision import (
            apply_decision_to_metadata,
            decision_from_metadata,
            latest_title_decision,
            record_title_decision,
        )

        d = record_title_decision(
            "The Crown",
            source="ocr",
            reason="HDMI OCR filled an empty player title",
        )
        self.assertIn("The Crown", d.explain())
        self.assertIn("ocr", d.explain())
        md: dict = {}
        apply_decision_to_metadata(md, d)
        self.assertEqual(decision_from_metadata(md), d.explain())
        self.assertIs(latest_title_decision(), d)


class OcrIdentityHandoffTests(unittest.TestCase):
    def test_empty_identity_takes_first_ocr_title(self) -> None:
        md = {"ocr_title": "The Crown", "query": "", "identity_source": "stale"}
        self.assertTrue(ho.apply_ocr_title_as_identity(md))
        self.assertEqual(md["query"], "The Crown")
        self.assertEqual(md["identity_source"], "ocr")

    def test_new_ocr_title_needs_two_hits(self) -> None:
        md = {
            "ocr_title": "The Night Agent",
            "query": "The Crown",
            "identity_source": "ocr",
            "identity_confidence": dc.OCR_IDENTITY,
        }
        self.assertFalse(ho.apply_ocr_title_as_identity(md))
        self.assertEqual(md["query"], "The Crown")
        self.assertEqual(md["ocr_pending_hits"], 1)
        md["ocr_title"] = "The Night Agent"
        self.assertTrue(ho.apply_ocr_title_as_identity(md))
        self.assertEqual(md["query"], "The Night Agent")

    def test_does_not_overwrite_pyatv_title(self) -> None:
        md = {
            "ocr_title": "Something Else",
            "query": "Ted Lasso",
            "identity_source": "pyatv",
        }
        self.assertFalse(ho.apply_ocr_title_as_identity(md))
        self.assertEqual(md["query"], "Ted Lasso")


class ZoneAdaptTests(unittest.TestCase):
    def test_no_position_uses_extra_cast_in_zone5(self) -> None:
        zones = _effective_zone_widgets(
            has_position=False,
            cast_count=6,
            zone_widgets=("clock", "poster", "volume", "cast_info", "now_playing"),
        )
        self.assertEqual(zones[4], "cast_info")

    def test_no_position_hides_zone5_when_cast_would_duplicate(self) -> None:
        zones = _effective_zone_widgets(
            has_position=False,
            cast_count=3,
            zone_widgets=("clock", "poster", "volume", "cast_info", "now_playing"),
        )
        self.assertEqual(zones[4], "")

    def test_duplicate_clock_is_dropped(self) -> None:
        zones = _effective_zone_widgets(
            has_position=True,
            cast_count=0,
            zone_widgets=("clock", "clock", "volume", "cast_info", "now_playing"),
        )
        self.assertEqual(zones[0], "clock")
        self.assertEqual(zones[1], "")


if __name__ == "__main__":
    unittest.main()
