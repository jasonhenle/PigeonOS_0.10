"""Diagnostic SVG stereo meter: mapping + LED-segment visibility."""

from __future__ import annotations

import os
import sys
import time
import unittest

import numpy as np

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon.widgets import audio_meter_saver as am  # noqa: E402


class AudioMeterMappingTests(unittest.TestCase):
    def test_thresholds_are_ten_and_increasing(self) -> None:
        self.assertEqual(len(am.METER_SEGMENT_THRESHOLDS_DBFS), 10)
        seq = list(am.METER_SEGMENT_THRESHOLDS_DBFS)
        self.assertEqual(seq, sorted(seq))

    def test_silence_is_zero_segments(self) -> None:
        self.assertEqual(am.meter_segments_from_dbfs(-120.0), 0)
        self.assertEqual(am.meter_segments_from_dbfs(-48.01), 0)

    def test_each_threshold_lights_that_segment(self) -> None:
        for i, thr in enumerate(am.METER_SEGMENT_THRESHOLDS_DBFS, start=1):
            self.assertEqual(am.meter_segments_from_dbfs(thr), i)
            self.assertEqual(am.meter_segments_from_dbfs(thr + 0.01), i)

    def test_just_below_next_threshold_holds(self) -> None:
        self.assertEqual(am.meter_segments_from_dbfs(-12.0), 7)
        self.assertEqual(am.meter_segments_from_dbfs(-8.01), 7)
        self.assertEqual(am.meter_segments_from_dbfs(-8.0), 8)
        self.assertEqual(am.meter_segments_from_dbfs(-1.0), 10)
        self.assertEqual(am.meter_segments_from_dbfs(0.0), 10)

    def test_not_a_linear_0_1_amplitude_map(self) -> None:
        # -20 dBFS is ~0.1 linear. A naive *10 map would show 1 LED; ours is higher.
        dbfs = -20.0
        linear = 10 ** (dbfs / 20.0)
        naive = int(round(linear * 10.0))
        self.assertLess(naive, 4)
        self.assertGreaterEqual(am.meter_segments_from_dbfs(dbfs), 5)


class AudioMeterSvgTests(unittest.TestCase):
    def test_asset_exists_with_named_shapes(self) -> None:
        path = am.default_audio_meter_svg_path()
        self.assertTrue(path.is_file(), f"missing {path}")
        root = am.svg_tree_from_path(path)
        self.assertIsNotNone(am.find_meter_element(root, "left_meter_group"))
        # Illustrator export currently misspells the right group id.
        self.assertIsNotNone(
            am.find_meter_element(root, "right_meter_group", "right_meter_grouop")
        )
        for side in ("left", "right"):
            for i in range(1, 11):
                el = am.find_meter_element(root, am.meter_shape_id(side, i))
                self.assertIsNotNone(el, am.meter_shape_id(side, i))

    def test_authored_led_colors_are_preserved(self) -> None:
        root = am.svg_tree_from_path()
        green = "#39b54a"
        yellow = "#fff200"
        red = "#d60000"
        for side in ("left", "right"):
            for i in range(1, 8):
                el = am.find_meter_element(root, am.meter_shape_id(side, i))
                self.assertEqual((el.get("fill") or "").lower(), green)
            for i in (8, 9):
                el = am.find_meter_element(root, am.meter_shape_id(side, i))
                self.assertEqual((el.get("fill") or "").lower(), yellow)
            el = am.find_meter_element(root, am.meter_shape_id(side, 10))
            self.assertEqual((el.get("fill") or "").lower(), red)
        fills_before = {
            (side, i): am.find_meter_element(root, am.meter_shape_id(side, i)).get("fill")
            for side in ("left", "right")
            for i in range(1, 11)
        }
        am.apply_meter_leds(root, 10, 10)
        am.apply_meter_leds(root, 3, 0)
        for side in ("left", "right"):
            for i in range(1, 11):
                el = am.find_meter_element(root, am.meter_shape_id(side, i))
                self.assertEqual(el.get("fill"), fills_before[(side, i)])

    def test_led_visibility_is_cumulative(self) -> None:
        root = am.svg_tree_from_path()
        am.apply_meter_leds(root, 0, 7)
        for i in range(1, 11):
            left = am.find_meter_element(root, am.meter_shape_id("left", i))
            right = am.find_meter_element(root, am.meter_shape_id("right", i))
            self.assertEqual(left.get("opacity"), "0")
            if i <= 7:
                self.assertNotEqual(right.get("opacity"), "0")
            else:
                self.assertEqual(right.get("opacity"), "0")
        am.apply_meter_leds(root, 10, 1)
        for i in range(1, 11):
            left = am.find_meter_element(root, am.meter_shape_id("left", i))
            right = am.find_meter_element(root, am.meter_shape_id("right", i))
            self.assertNotEqual(left.get("opacity"), "0")
            if i == 1:
                self.assertNotEqual(right.get("opacity"), "0")
            else:
                self.assertEqual(right.get("opacity"), "0")

    def test_prune_removes_hidden_named_shapes(self) -> None:
        root = am.svg_tree_from_path()
        am.apply_meter_leds(root, 2, 0)
        am._prune_hidden_meter_shapes(root)
        self.assertIsNotNone(am.find_meter_element(root, "left_meter_01_shape"))
        self.assertIsNotNone(am.find_meter_element(root, "left_meter_02_shape"))
        self.assertIsNone(am.find_meter_element(root, "left_meter_03_shape"))
        self.assertIsNone(am.find_meter_element(root, "right_meter_01_shape"))


class AudioMeterRasterTests(unittest.TestCase):
    def test_raster_lights_left_without_right(self) -> None:
        try:
            import fitz  # noqa: F401
        except ImportError:
            self.skipTest("PyMuPDF required to rasterize meter SVG")
        am.clear_audio_meter_render_caches()
        dark = am.render_audio_meter_bgra(left_level=0, right_level=0)
        left = am.render_audio_meter_bgra(left_level=7, right_level=0)
        self.assertEqual(dark.shape[:2], (800, 1280))
        self.assertGreater(int(np.abs(left.astype(np.int16) - dark.astype(np.int16)).sum()), 0)
        # Left column (~x 548–596) should change; right column (~x 684–732) should not.
        left_band = np.abs(left[:, 548:597].astype(np.int16) - dark[:, 548:597].astype(np.int16)).sum()
        right_band = np.abs(left[:, 684:733].astype(np.int16) - dark[:, 684:733].astype(np.int16)).sum()
        self.assertGreater(int(left_band), int(right_band) * 10)


class ProgramAudioPresentTests(unittest.TestCase):
    def tearDown(self) -> None:
        am._latest = am._SILENCE
        am._last_pcm_mono = 0.0
        am._capture_dead = False
        am._program_audio_until = 0.0
        am._program_audio_session_until = 0.0

    def test_session_hold_outlasts_short_gate(self) -> None:
        now = time.monotonic()
        am._latest = am._SILENCE
        am._last_pcm_mono = now
        am._capture_dead = False
        am._program_audio_until = now - 0.1
        am._program_audio_session_until = now + 60.0
        self.assertFalse(am.program_audio_present())
        self.assertTrue(am.program_audio_session_present())

    def test_loud_fill_arms_session_hold(self) -> None:
        now = time.monotonic()
        am._latest = am.MeterLevels(
            rms_l=0.1,
            rms_r=0.1,
            env_l=0.1,
            env_r=0.1,
            dbfs_l=-20.0,
            dbfs_r=-20.0,
            cal_dbfs_l=-20.0,
            cal_dbfs_r=-20.0,
            fill_l=0.2,
            fill_r=0.2,
            seg_l=3,
            seg_r=3,
            rms_lfe=0.0,
            env_lfe=0.0,
            lfe_fill=0.0,
        )
        am._last_pcm_mono = now
        am._capture_dead = False
        am._program_audio_until = 0.0
        am._program_audio_session_until = 0.0
        self.assertTrue(am.program_audio_present())
        self.assertTrue(am.program_audio_session_present())
        self.assertGreater(am._program_audio_session_until, now + 60.0)


class StereoMeterWidgetLayoutTests(unittest.TestCase):
    def test_volume_band_is_the_lower_fifth(self) -> None:
        self.assertEqual(am.stereo_meter_volume_band_h(400), 88)
        self.assertEqual(am.stereo_meter_volume_band_h(488), 107)

    def test_bars_stay_above_volume_band(self) -> None:
        try:
            import fitz  # noqa: F401
        except ImportError:
            self.skipTest("PyMuPDF required to rasterize meter SVG")
        if not am.default_audio_meter_svg_path().is_file():
            self.skipTest("meter SVG not in this tree")
        am.clear_audio_meter_render_caches()
        h = 400
        patch = am.render_stereo_meter_widget_bgra(
            200, h, left_fill=1.0, right_fill=1.0
        )
        ys = np.where(patch[:, :, 3] > 16)[0]
        self.assertGreater(int(ys.size), 50)
        reserve = am.stereo_meter_volume_band_h(h)
        self.assertLess(int(ys.max()), h - reserve + 2)
        self.assertLess(float(ys.mean()), h * 0.45)


class TitleMeterCalTests(unittest.TestCase):
    def setUp(self) -> None:
        am.reset_meter_title_calibration(persist=False)

    def tearDown(self) -> None:
        am.reset_meter_title_calibration(persist=False)

    def test_content_key_requires_a_title(self) -> None:
        self.assertEqual(am.meter_content_key(title=""), "")
        self.assertEqual(
            am.meter_content_key(title="Popstar", artist="", mode="video"),
            "video|popstar|",
        )

    def test_new_title_does_not_boost_during_warmup(self) -> None:
        am.set_meter_content_key("video|popstar|")
        am.note_title_peak_db(-18.0, -18.0, -30.0)
        self.assertEqual(am.title_meter_gain_db(), 0.0)

    def test_loud_peak_cuts_immediately(self) -> None:
        am.set_meter_content_key("video|popstar|")
        am.note_title_peak_db(18.0, 16.0, 10.0)
        gain = am.title_meter_gain_db()
        self.assertAlmostEqual(gain, am.TITLE_CAL_TARGET_DB - 18.0, places=5)
        raw = am.meter_fill_from_calibrated_dbfs(18.0)
        shown = am.title_calibrated_fill(18.0)
        self.assertLess(shown, raw)
        self.assertGreater(shown, 0.7)

    def test_quiet_title_boosts_after_warmup(self) -> None:
        am.set_meter_content_key("video|quiet film|")
        am.note_title_peak_db(-12.0, -12.0, -20.0)
        am._title_since_mono = time.monotonic() - 20.0
        gain = am.title_meter_gain_db()
        self.assertGreater(gain, 0.0)
        self.assertAlmostEqual(
            gain,
            min(am.TITLE_CAL_MAX_BOOST_DB, am.TITLE_CAL_TARGET_DB - (-12.0)),
            places=5,
        )

    def test_title_change_isolates_peaks(self) -> None:
        am.set_meter_content_key("video|loud|")
        am.note_title_peak_db(16.0, 16.0, 8.0)
        am.set_meter_content_key("video|quiet|")
        self.assertLess(am._title_peak_db, am.TITLE_CAL_MIN_PEAK_DB)
        am.set_meter_content_key("video|loud|")
        self.assertAlmostEqual(am._title_peak_db, 16.0, places=5)


if __name__ == "__main__":
    unittest.main()
