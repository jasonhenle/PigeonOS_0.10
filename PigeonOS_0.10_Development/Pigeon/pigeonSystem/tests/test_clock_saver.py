"""Clock saver SVG layout + weather cache helpers."""

from __future__ import annotations

import os
import sys
import unittest
import xml.etree.ElementTree as ET

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from PIL import Image, ImageDraw

from pigeon.widgets import clock_saver as cs  # noqa: E402
from pigeon import weather as wx  # noqa: E402


class ClockSaverSvgTests(unittest.TestCase):
    def test_asset_exists(self) -> None:
        path = cs.default_clock_saver_svg_path()
        self.assertTrue(path.is_file(), f"missing {path}")

    def test_apply_state_sets_date_and_degrees(self) -> None:
        path = cs.default_clock_saver_svg_path()
        root = cs._svg_tree_from_path(path)
        cs._apply_clock_saver_svg_state(root, color_hex="#58ff00")
        date_el = cs._find_by_logical_id(
            root, "today_month_year_text", "tday_month_year_text"
        )
        self.assertIsNotNone(date_el)
        self.assertTrue("".join(date_el.itertext()).strip())
        left = cs._find_by_logical_id(root, "degrees_left_stroke")
        right = cs._find_by_logical_id(
            root, "degrees_right_stroke", "degrees_rifght_stroke"
        )
        high = cs._find_by_logical_id(root, "high_temp")
        low = cs._find_by_logical_id(root, "low_temp")
        self.assertIsNotNone(left)
        self.assertIsNotNone(right)
        self.assertIsNotNone(high)
        self.assertIsNotNone(low)
        self.assertEqual((left.get("fill") or "").lower(), "none")
        self.assertEqual((right.get("fill") or "").lower(), "none")
        self.assertEqual((left.get("stroke") or "").lower(), "#58ff00")
        # Sun/moon glyphs removed from the art; keep only temp + degree marks.
        self.assertIsNone(cs._find_by_logical_id(root, "sun_fill", "moon"))
        scaled_fs = f"{cs._TEMP_FONT_SIZE_SVG * cs._WEATHER_SCALE:.2f}"
        self.assertEqual((high.get("font-size") or "").strip(), scaled_fs)
        self.assertEqual((low.get("font-size") or "").strip(), scaled_fs)
        weather = cs._find_by_logical_id(root, "weather")
        self.assertIsNotNone(weather)
        assert weather is not None
        xform = weather.get("transform") or ""
        self.assertIn("scale(", xform)
        self.assertIn(f"scale({cs._WEATHER_SCALE})", xform)
        # Temps sit under the date with the scaled cluster (Pillow bakes abs coords).
        self.assertEqual((high.get("text-anchor") or "").lower(), "end")
        self.assertEqual((low.get("text-anchor") or "").lower(), "end")
        hx, hy = cs._parse_translate_xy(high)
        lx, ly = cs._parse_translate_xy(low)
        self.assertGreater(hx, 250.0)
        self.assertGreater(lx, hx)
        self.assertLess(hy, 160.0)
        self.assertLess(ly, 160.0)
        self.assertAlmostEqual(hy, ly, delta=2.0)

    def test_composite_returns_full_frame(self) -> None:
        (frame, rect), (empty, _er) = cs.clock_saver_composite_bgra(
            shadow_bgr=None,
            layer_opacity=1.0,
        )
        self.assertEqual(frame.shape[0], cs.DESIGN_H)
        self.assertEqual(frame.shape[1], cs.DESIGN_W)
        self.assertEqual(rect, (0, 0, cs.DESIGN_W, cs.DESIGN_H))
        self.assertEqual(empty.shape[0], 1)

    def test_matching_width_crops_skinny_glyphs(self) -> None:
        """Full digits use matching width; 1 and : crop to half (25%+25%)."""
        font_path = cs.resolve_digital7_font() or cs.resolve_ui_font_bold()
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        font = cs._fit_digital7_fixed_cells(
            font_path,
            cs._HHMMSS_MATCHING_UNITS,
            max_w=700,
            max_h=200,
            prefer_sz=180,
        )
        matching_w, _cell_h = cs._cell_metrics(probe, font, cs._HHMMSS_CHAR_SET)
        l0, _t0, r0, _b0 = cs._char_bbox(probe, font, "0")
        self.assertEqual(matching_w, max(1, r0 - l0))
        half = max(1, int(round(0.5 * matching_w)))
        for ch in "023456789":
            self.assertEqual(cs._hhmmss_advance(matching_w, ch), matching_w)
        for ch in cs._HHMMSS_SKINNY_CHARS:
            self.assertEqual(cs._hhmmss_advance(matching_w, ch), half)
        self.assertEqual(
            cs._hhmmss_block_width(matching_w, "00:00:00"),
            6 * matching_w + 2 * half,
        )
        self.assertEqual(
            cs._hhmmss_block_width(matching_w, "11:11:11"),
            8 * half,
        )
        self.assertGreater(cs._HHMMSS_MID_Y_SVG, cs._DATE_BASELINE_Y_SVG)
        self.assertLess(cs._HHMMSS_MID_Y_SVG, cs._WEATHER_ICON_TOP_SVG)

    def test_colons_locked_pairs_recenter(self) -> None:
        """Colon X is fixed; HH/MM/SS pair widths shrink with skinny 1s."""
        matching_w = 100
        regions, colon_cx = cs._hhmmss_locked_scaffold(matching_w, 800)
        regions2, colon_cx2 = cs._hhmmss_locked_scaffold(matching_w, 800)
        self.assertEqual(colon_cx, colon_cx2)
        self.assertEqual(regions, regions2)
        # Scaffold bands are always two full matching cells wide.
        for left, right in regions:
            self.assertEqual(right - left, 2 * matching_w)
        # Pair groups: full "08" vs skinny "11" — different widths, same band.
        self.assertEqual(cs._hhmmss_pair_width(matching_w, "08"), 2 * matching_w)
        self.assertEqual(
            cs._hhmmss_pair_width(matching_w, "11"),
            2 * cs._hhmmss_advance(matching_w, "1"),
        )
        self.assertEqual(cs._parse_hhmmss_pairs("12:34:56"), ("12", "34", "56"))


class WeatherCacheTests(unittest.TestCase):
    def tearDown(self) -> None:
        with wx._lock:
            wx._cache_high = None
            wx._cache_low = None
            wx._cache_zip = ""
            wx._cache_mono = 0.0

    def test_store_and_read_cache(self) -> None:
        wx._store(wx.WeatherTemps(high_f=89, low_f=77, zip_code="21704"))
        got = wx.cached_weather_temps()
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got.high_f, 89)
        self.assertEqual(got.low_f, 77)


if __name__ == "__main__":
    unittest.main()
