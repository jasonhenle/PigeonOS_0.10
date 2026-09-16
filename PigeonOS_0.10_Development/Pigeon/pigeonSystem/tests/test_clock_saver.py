"""Clock saver SVG layout + weather cache helpers."""

from __future__ import annotations

import os
import sys
import unittest
import xml.etree.ElementTree as ET

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

import numpy as np
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
        self.assertLess(hy, 200.0)
        self.assertLess(ly, 200.0)
        self.assertAlmostEqual(hy, ly, delta=2.0)

    def test_time_defaults_to_12_hour(self) -> None:
        from datetime import datetime
        from unittest.mock import patch

        when = datetime(2026, 1, 1, 13, 30, 5)
        with patch(
            "pigeon.widgets.options_settings.clock_uses_24h", return_value=False
        ):
            self.assertEqual(cs._time_label(when), "1:30:05")
        with patch(
            "pigeon.widgets.options_settings.clock_uses_24h", return_value=True
        ):
            self.assertEqual(cs._time_label(when), "13:30:05")
        with patch(
            "pigeon.widgets.options_settings.clock_uses_24h",
            side_effect=RuntimeError("missing"),
        ):
            self.assertEqual(cs._time_label(when), "1:30:05")

    def test_time_color_leaves_white_after_hold(self) -> None:
        white = cs._time_color_rgba(0.0)
        later = cs._time_color_rgba(20.0)
        self.assertEqual(white[:3], (255, 255, 255))
        self.assertNotEqual(later[:3], white[:3])

    def test_face_fits_widget_well(self) -> None:
        face = cs.render_clock_saver_face_bgra(width=386, height=249)
        self.assertEqual(face.shape[1], 386)
        self.assertEqual(face.shape[0], 249)
        ys, xs = np.where(face[:, :, 3] > 20)
        self.assertGreater(xs.size, 400)
        self.assertGreater(int(xs.min()), 2)
        self.assertLess(int(xs.max()), 383)
        self.assertGreater(int(xs.max()) - int(xs.min()), 260)
        self.assertLess(int(ys.min()), 55)
        self.assertGreater(int(ys.max()), 170)

    def test_apply_state_can_hide_weather(self) -> None:
        path = cs.default_clock_saver_svg_path()
        root = cs._svg_tree_from_path(path)
        cs._apply_clock_saver_svg_state(
            root, color_hex="#58ff00", include_weather=False
        )
        weather = cs._find_by_logical_id(root, "weather")
        self.assertIsNotNone(weather)
        assert weather is not None
        self.assertEqual((weather.get("display") or "").lower(), "none")
        date_el = cs._find_by_logical_id(
            root, "today_month_year_text", "tday_month_year_text"
        )
        self.assertIsNotNone(date_el)
        self.assertTrue("".join(date_el.itertext()).strip())

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
        self.assertGreater(cs._WEATHER_SCALE, 1.0)
        self.assertGreater(cs._HHMMSS_MID_Y_SVG, cs._DATE_BASELINE_Y_SVG)

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

    def test_clock_format_analog_is_the_np_widget(self) -> None:
        from unittest.mock import patch

        from pigeon.widgets.options_settings import _normalize
        from pigeon.widgets.view_circles import render_centered_clock_widget_bgra

        self.assertEqual(_normalize({"clock_format": "analog"})["clock_format"], "analog")
        self.assertEqual(_normalize({"clock_format": "digital"})["clock_format"], "digital")
        with patch(
            "pigeon.widgets.options_settings.clock_widget_analog", return_value=True
        ):
            (frame, rect), _ = cs.clock_saver_composite_bgra(shadow_bgr=None)
        self.assertEqual(frame.shape[0], cs.DESIGN_H)
        self.assertEqual(frame.shape[1], cs.DESIGN_W)
        self.assertEqual(rect, (0, 0, cs.DESIGN_W, cs.DESIGN_H))
        # Analog idle uses the centered NP clock widget (not the digital saver SVG).
        direct = render_centered_clock_widget_bgra()
        self.assertEqual(frame.shape, direct.shape)
        # Shared non-transparent ink near the disc center (HH:MM stays on).
        cy, cx = cs.DESIGN_H // 2, cs.DESIGN_W // 2
        self.assertGreater(int(frame[cy, cx, 3]), 20)


class ClockSaverSecondsBarTests(unittest.TestCase):
    def test_sixty_equal_segments_step_each_second(self) -> None:
        from pigeon.np_layout import (
            CLOCK_SAVER_SECONDS_SEGMENTS,
            clock_saver_seconds_filled,
            clock_saver_seconds_segment_rects,
            clock_saver_seconds_track_rect,
        )

        self.assertEqual(CLOCK_SAVER_SECONDS_SEGMENTS, 60)
        self.assertEqual(clock_saver_seconds_filled(0), 1)
        self.assertEqual(clock_saver_seconds_filled(29), 30)
        self.assertEqual(clock_saver_seconds_filled(59), 60)
        self.assertEqual(clock_saver_seconds_filled(60), 1)
        tx, ty, tw, th, _trx = clock_saver_seconds_track_rect()
        rects = clock_saver_seconds_segment_rects((tx, ty, tw, th))
        self.assertEqual(len(rects), 60)
        widths = [r[2] for r in rects]
        self.assertLessEqual(max(widths) - min(widths), 1)
        for a, b in zip(rects, rects[1:]):
            self.assertLessEqual(a[0] + a[2], b[0])
        self.assertEqual(rects[0][1], ty)
        self.assertEqual(rects[-1][0] + rects[-1][2], tx + tw)

    def test_digital_saver_paints_stepped_track(self) -> None:
        from datetime import datetime
        from unittest.mock import patch

        from pigeon.np_layout import (
            clock_saver_seconds_segment_rects,
            clock_saver_seconds_track_rect,
        )

        when = datetime(2026, 1, 1, 10, 30, 15)
        with (
            patch(
                "pigeon.widgets.options_settings.clock_widget_analog",
                return_value=False,
            ),
            patch(
                "pigeon.widgets.clock_saver._resolve_display_time",
                return_value=when,
            ),
        ):
            frame = cs.render_clock_saver_bgra(layer_opacity=1.0)
        self.assertEqual(frame.shape[0], cs.DESIGN_H)
        self.assertEqual(frame.shape[1], cs.DESIGN_W)
        tx, ty, tw, th, _ = clock_saver_seconds_track_rect()
        rects = clock_saver_seconds_segment_rects((tx, ty, tw, th))
        fx, fy, fw, fh = rects[8]
        ex, ey, ew, eh = rects[40]
        filled = frame[fy + fh // 2, fx + fw // 2, :3]
        empty = frame[ey + eh // 2, ex + ew // 2, :3]
        self.assertGreater(int(filled.max()), 20)
        self.assertLess(int(empty.max()), 12)

    def test_digital_time_baseline_sits_near_seconds_bar(self) -> None:
        from datetime import datetime
        from unittest.mock import patch

        from pigeon.np_layout import clock_saver_seconds_track_rect

        when = datetime(2026, 1, 1, 10, 30, 15)
        with (
            patch(
                "pigeon.widgets.options_settings.clock_widget_analog",
                return_value=False,
            ),
            patch(
                "pigeon.widgets.clock_saver._resolve_display_time",
                return_value=when,
            ),
        ):
            frame = cs.render_clock_saver_bgra(layer_opacity=1.0)
        _tx, bar_y, tw, _th, _ = clock_saver_seconds_track_rect()
        band = frame[0:bar_y, cs.DESIGN_W // 2 - 200 : cs.DESIGN_W // 2 + 200, :3]
        ink = np.where(band.max(axis=2) > 40)
        self.assertGreater(int(ink[0].size), 0)
        bottom = int(ink[0].max())
        self.assertGreater(bottom, bar_y - 80)
        self.assertLessEqual(bottom, bar_y - 4)
        self.assertGreater(int(tw), 200)

    def test_volume_line_grows_from_center(self) -> None:
        g25 = cs.clock_saver_volume_line_geometry(
            fraction=0.25, canvas_w=1280, text_w=80, max_width=1066
        )
        g50 = cs.clock_saver_volume_line_geometry(
            fraction=0.50, canvas_w=1280, text_w=80, max_width=1066
        )
        g100 = cs.clock_saver_volume_line_geometry(
            fraction=1.0, canvas_w=1280, text_w=80, max_width=1066
        )
        g0 = cs.clock_saver_volume_line_geometry(
            fraction=0.0, canvas_w=1280, text_w=80, max_width=1066
        )
        for line_l, line_r, gap_l, gap_r in (g0, g25, g50, g100):
            cx = 640
            self.assertEqual(cx - line_l, line_r - cx)
            self.assertEqual(cx - gap_l, gap_r - cx)
            self.assertGreaterEqual(gap_l, line_l)
            self.assertLessEqual(gap_r, line_r)
        self.assertLess(g25[1] - g25[0], g50[1] - g50[0])
        self.assertLess(g50[1] - g50[0], g100[1] - g100[0])
        self.assertEqual(g0[0], g0[2])
        self.assertEqual(g0[1], g0[3])
        self.assertEqual(cs.clock_saver_volume_label("-22.5 dB"), "-22.5")
        self.assertEqual(cs.clock_saver_volume_label("mute"), "MUTE")
        self.assertEqual(cs.clock_saver_volume_label(""), "")
        self.assertTrue(cs.clock_saver_volume_line_visible("-22.5 dB"))
        self.assertFalse(cs.clock_saver_volume_line_visible("mute"))
        self.assertFalse(cs.clock_saver_volume_line_visible(""))
        strip = cs.clock_saver_volume_strip_bgra(
            "-22.5 dB", width=1190, height=130
        )
        self.assertEqual(strip.shape[0], 130)
        self.assertEqual(strip.shape[1], 1190)
        self.assertGreater(int(strip[:, :, 3].max()), 8)
        mute = cs.clock_saver_volume_strip_bgra("MUTE", width=400, height=80)
        self.assertGreater(int(mute[:, :, 3].max()), 8)
        self.assertEqual(cs.step_clock_saver_volume("-22.5 dB", "volume_up"), "-22.0 dB")
        self.assertEqual(cs.step_clock_saver_volume("-22.5 dB", "volume_down"), "-23.0 dB")
        self.assertEqual(cs.step_clock_saver_volume("40", "volume_up"), "41")
        self.assertEqual(cs.step_clock_saver_volume("-22.5 dB", "mute_toggle"), "MUTE")
        self.assertEqual(
            cs.step_clock_saver_volume("MUTE", "mute_toggle", unmute_to="-22.5 dB"),
            "-22.5 dB",
        )

    def test_digital_saver_paints_centered_volume_line(self) -> None:
        from datetime import datetime
        from unittest.mock import patch

        from pigeon.np_layout import clock_saver_seconds_track_rect

        when = datetime(2026, 1, 1, 10, 30, 15)
        with (
            patch(
                "pigeon.widgets.options_settings.clock_widget_analog",
                return_value=False,
            ),
            patch(
                "pigeon.widgets.clock_saver._resolve_display_time",
                return_value=when,
            ),
        ):
            quiet = cs.render_clock_saver_bgra(layer_opacity=1.0, volume="")
            loud = cs.render_clock_saver_bgra(layer_opacity=1.0, volume="100")
            mid = cs.render_clock_saver_bgra(layer_opacity=1.0, volume="40")
        _tx, bar_y, track_w, _th, _ = clock_saver_seconds_track_rect()
        path = cs.default_clock_saver_svg_path()
        root = cs._svg_tree_from_path(path)
        wx_svg = cs._apply_clock_saver_svg_state(root, color_hex="#58ff00")
        wx_bottom = cs.clock_saver_svg_y_to_design_y(wx_svg, cs.DESIGN_H)
        # Volume sits in the gap under weather; do not sample the weather cluster.
        band_y0, band_y1 = int(round(wx_bottom)) + 8, 400
        cx = cs.DESIGN_W // 2

        def _extent(frame: np.ndarray) -> tuple[int, int] | None:
            ink = frame[band_y0:band_y1, 8 : cs.DESIGN_W - 8, :3].max(axis=2) > 40
            cols = np.where(ink.any(axis=0))[0]
            if cols.size == 0:
                return None
            return 8 + int(cols.min()), 8 + int(cols.max())

        self.assertIsNone(_extent(quiet))
        loud_ext = _extent(loud)
        mid_ext = _extent(mid)
        self.assertIsNotNone(loud_ext)
        self.assertIsNotNone(mid_ext)
        assert loud_ext is not None and mid_ext is not None
        loud_span = loud_ext[1] - loud_ext[0]
        mid_span = mid_ext[1] - mid_ext[0]
        self.assertGreater(loud_span, mid_span)
        self.assertGreater(loud_span, int(track_w) - 8)
        self.assertAlmostEqual((loud_ext[0] + loud_ext[1]) / 2.0, cx, delta=2)
        self.assertAlmostEqual((mid_ext[0] + mid_ext[1]) / 2.0, cx, delta=2)

    def test_mute_hides_line_and_off_hides_all(self) -> None:
        from datetime import datetime
        from unittest.mock import patch

        when = datetime(2026, 1, 1, 10, 30, 15)
        with (
            patch(
                "pigeon.widgets.options_settings.clock_widget_analog",
                return_value=False,
            ),
            patch(
                "pigeon.widgets.clock_saver._resolve_display_time",
                return_value=when,
            ),
        ):
            quiet = cs.render_clock_saver_bgra(layer_opacity=1.0, volume="")
            muted = cs.render_clock_saver_bgra(layer_opacity=1.0, volume="mute")
            loud = cs.render_clock_saver_bgra(layer_opacity=1.0, volume="-22.5 dB")
        path = cs.default_clock_saver_svg_path()
        root = cs._svg_tree_from_path(path)
        wx_svg = cs._apply_clock_saver_svg_state(root, color_hex="#58ff00")
        wx_bottom = cs.clock_saver_svg_y_to_design_y(wx_svg, cs.DESIGN_H)
        band_y0, band_y1 = int(round(wx_bottom)) + 8, 400
        cx = cs.DESIGN_W // 2

        def _extent(frame: np.ndarray) -> tuple[int, int] | None:
            ink = frame[band_y0:band_y1, 8 : cs.DESIGN_W - 8, :3].max(axis=2) > 40
            cols = np.where(ink.any(axis=0))[0]
            if cols.size == 0:
                return None
            return 8 + int(cols.min()), 8 + int(cols.max())

        self.assertIsNone(_extent(quiet))
        mute_ext = _extent(muted)
        loud_ext = _extent(loud)
        self.assertIsNotNone(mute_ext)
        self.assertIsNotNone(loud_ext)
        assert mute_ext is not None and loud_ext is not None
        self.assertLess(mute_ext[1] - mute_ext[0], 280)
        self.assertGreater(loud_ext[1] - loud_ext[0], mute_ext[1] - mute_ext[0] + 200)
        self.assertAlmostEqual((mute_ext[0] + mute_ext[1]) / 2.0, cx, delta=8)

    def test_volume_line_centers_between_temp_and_time(self) -> None:
        from datetime import datetime
        from unittest.mock import patch

        from pigeon.np_layout import clock_saver_seconds_track_rect

        when = datetime(2026, 1, 1, 10, 30, 15)
        path = cs.default_clock_saver_svg_path()
        root = cs._svg_tree_from_path(path)
        wx_svg = cs._apply_clock_saver_svg_state(root, color_hex="#58ff00")
        wx_bottom = cs.clock_saver_svg_y_to_design_y(wx_svg, cs.DESIGN_H)
        _tx, bar_y, _tw, _th, _ = clock_saver_seconds_track_rect()
        with (
            patch(
                "pigeon.widgets.options_settings.clock_widget_analog",
                return_value=False,
            ),
            patch(
                "pigeon.widgets.clock_saver._resolve_display_time",
                return_value=when,
            ),
        ):
            quiet = cs.render_clock_saver_bgra(layer_opacity=1.0, volume="")
            loud = cs.render_clock_saver_bgra(layer_opacity=1.0, volume="-38.5 dB")
        delta = np.abs(loud.astype(np.int16) - quiet.astype(np.int16)).max(axis=2)
        rows = np.where(delta[:, 8 : cs.DESIGN_W - 8] > 20)[0]
        self.assertGreater(int(rows.size), 0)
        vol_cy = float(rows.min() + rows.max()) / 2.0
        time_ink = np.where(
            quiet[int(wx_bottom) : int(bar_y), 200 : cs.DESIGN_W - 200, :3].max(axis=2)
            > 40
        )[0]
        self.assertGreater(int(time_ink.size), 0)
        time_top = float(wx_bottom) + float(time_ink.min())
        mid = (float(wx_bottom) + time_top) / 2.0
        self.assertGreater(vol_cy, wx_bottom + 8)
        self.assertLess(vol_cy, time_top - 8)
        self.assertAlmostEqual(vol_cy, mid, delta=16)

    def test_volume_does_not_overlap_time_or_weather(self) -> None:
        from datetime import datetime
        from unittest.mock import patch

        when = datetime(2026, 9, 14, 12, 2, 30)
        with (
            patch(
                "pigeon.widgets.options_settings.clock_widget_analog",
                return_value=False,
            ),
            patch(
                "pigeon.widgets.clock_saver._resolve_display_time",
                return_value=when,
            ),
        ):
            quiet = cs.render_clock_saver_bgra(layer_opacity=1.0, volume="")
            loud = cs.render_clock_saver_bgra(layer_opacity=1.0, volume="-33.0 dB")
        delta = np.abs(loud.astype(np.int16) - quiet.astype(np.int16)).max(axis=2) > 20
        quiet_ink = quiet[:, :, :3].max(axis=2) > 40
        overlap_rows = np.where((delta & quiet_ink).any(axis=1))[0]
        self.assertLess(
            int(overlap_rows.size),
            3,
            f"volume overlaps other chrome on rows {overlap_rows[:8]!r}",
        )

    def test_digital7_volume_hangs_below_mm_anchor(self) -> None:
        from PIL import Image, ImageDraw

        font_path = cs.resolve_digital7_font() or cs.resolve_ui_font_bold()
        draw = ImageDraw.Draw(Image.new("RGBA", (64, 64)))
        font = cs._load_font(font_path, cs._CLOCK_SAVER_VOLUME_FONT_SIZE_PX)
        above, below, width = cs._anchor_mm_extents(draw, "-33.0", font)
        self.assertGreater(width, 40)
        self.assertGreater(below, above)

    def test_volume_hold_ignores_stale_poll_during_grace(self) -> None:
        hold = cs.ClockSaverVolumeHold(grace_s=2.0)
        hold.remember("-38.5 dB", source="poll", now=100.0)
        hold.remember("-38.0 dB", source="nudge", now=100.5)
        self.assertTrue(hold.is_stale_poll("-38.5 dB", now=101.0))
        self.assertEqual(
            hold.pick(["-38.5 dB", "-38.5 dB"], now=101.0),
            "-38.0 dB",
        )
        self.assertEqual(
            hold.pick(["-37.5 dB"], now=101.0),
            "-37.5 dB",
        )
        hold.remember("-38.0 dB", source="nudge", now=200.0)
        self.assertEqual(
            hold.pick(["-38.5 dB"], now=203.0),
            "-38.5 dB",
        )
        hold.remember("-22.5 dB", source="poll", now=300.0)
        self.assertEqual(hold.pick(["-22.5 dB"], now=300.0, receiver_off=True), "")
        self.assertEqual(hold.hold, "")

    def test_wake_hold_keeps_nudge_past_default_grace(self) -> None:
        hold = cs.ClockSaverVolumeHold(grace_s=2.0)
        hold.remember("-30.5 dB", source="poll", now=10.0)
        hold.remember("-30.0 dB", source="nudge", now=10.1, hold_s=12.0)
        self.assertTrue(hold.is_stale_poll("-30.5 dB", now=13.0))
        self.assertEqual(hold.pick(["-30.5 dB"], now=13.0), "-30.0 dB")
        self.assertEqual(hold.pick(["-30.5 dB"], now=23.0), "-30.5 dB")

    def test_display_line_is_last_remembered_hold(self) -> None:
        hold = cs.ClockSaverVolumeHold(grace_s=2.0)
        self.assertEqual(hold.display_line(), "")
        hold.remember("-18.5 dB", source="poll", now=10.0)
        self.assertEqual(hold.display_line(), "-18.5 dB")
        hold.remember("-20.0 dB", source="poll", now=11.0)
        self.assertEqual(hold.display_line(), "-20.0 dB")

    def test_volume_line_hold_then_fade(self) -> None:
        reveal = cs.VolumeLineReveal(hold_s=3.0, fade_s=0.75)
        reveal.note("-13.0 dB", now=100.0)
        self.assertEqual(reveal.opacity(now=100.0), 0.0)
        reveal.note("-12.5 dB", now=101.0)
        self.assertEqual(reveal.opacity(now=102.0), 1.0)
        self.assertEqual(reveal.opacity(now=104.0), 1.0)
        self.assertTrue(reveal.fading(now=104.2))
        mid = reveal.opacity(now=104.375)
        self.assertGreater(mid, 0.2)
        self.assertLess(mid, 0.8)
        self.assertEqual(reveal.opacity(now=106.0), 0.0)
        self.assertFalse(reveal.fading(now=106.0))

    def test_live_poll_wins_over_nudge_when_avr_moved(self) -> None:
        hold = cs.ClockSaverVolumeHold(grace_s=2.0)
        hold.remember("-30.5 dB", source="poll", now=50.0)
        hold.remember("-30.0 dB", source="nudge", now=50.2)
        self.assertEqual(hold.pick(["-13.0 dB"], now=50.4), "-13.0 dB")

    def test_analog_saver_omits_seconds_bar(self) -> None:
        from unittest.mock import patch

        from pigeon.np_layout import clock_saver_seconds_track_rect
        from pigeon.widgets.view_circles import render_centered_clock_widget_bgra

        with patch(
            "pigeon.widgets.options_settings.clock_widget_analog",
            return_value=True,
        ):
            frame = cs.render_clock_saver_bgra(layer_opacity=1.0)
        direct = render_centered_clock_widget_bgra()
        tx, ty, tw, th, _ = clock_saver_seconds_track_rect()
        self.assertEqual(
            frame[ty : ty + th, tx : tx + tw].tobytes(),
            direct[ty : ty + th, tx : tx + tw].tobytes(),
        )


class ClockSaverTriggerTests(unittest.TestCase):
    def test_pause_does_not_arm_before_thirty_seconds(self) -> None:
        from pigeon.clock_saver_policy import (
            CLOCK_SAVER_PAUSED_AFTER_S,
            clock_saver_due_for_pause,
            next_paused_since_mono,
            pause_hold_s,
            should_hold_paused_screen,
        )

        self.assertEqual(CLOCK_SAVER_PAUSED_AFTER_S, 30.0)
        self.assertFalse(clock_saver_due_for_pause(True, 29.9))
        self.assertTrue(clock_saver_due_for_pause(True, 30.0))
        self.assertFalse(clock_saver_due_for_pause(False, 90.0))
        started = next_paused_since_mono(True, 100.0, 0.0)
        self.assertEqual(started, 100.0)
        self.assertEqual(next_paused_since_mono(True, 125.0, started), 100.0)
        self.assertAlmostEqual(pause_hold_s(True, 129.9, started), 29.9)
        self.assertAlmostEqual(pause_hold_s(True, 130.0, started), 30.0)
        self.assertEqual(next_paused_since_mono(False, 140.0, started), 0.0)
        self.assertTrue(
            should_hold_paused_screen(
                paused_with_content=True,
                has_backdrop=True,
                clock_saver_active=False,
            )
        )
        self.assertFalse(
            should_hold_paused_screen(
                paused_with_content=True,
                has_backdrop=True,
                clock_saver_active=True,
            )
        )

    def test_no_content_and_same_title_skip_tmdb(self) -> None:
        from pigeon.clock_saver_policy import (
            clock_saver_due_for_no_content,
            tmdb_should_skip_refetch_on_resume,
        )

        self.assertTrue(
            clock_saver_due_for_no_content(
                playing=False,
                paused_with_content=False,
                content_idle=True,
            )
        )
        self.assertFalse(
            clock_saver_due_for_no_content(
                playing=False,
                paused_with_content=True,
                content_idle=False,
            )
        )
        self.assertFalse(
            clock_saver_due_for_no_content(
                playing=True,
                paused_with_content=False,
                content_idle=False,
            )
        )
        self.assertFalse(
            clock_saver_due_for_no_content(
                playing=False,
                paused_with_content=False,
                content_idle=True,
                incoming_audio=True,
            )
        )
        self.assertTrue(
            tmdb_should_skip_refetch_on_resume(
                content_key="show|auto|Show",
                prev_content_key="show|auto|Show",
                has_tmdb_identity=True,
            )
        )
        self.assertFalse(
            tmdb_should_skip_refetch_on_resume(
                content_key="other|auto|Other",
                prev_content_key="show|auto|Show",
                has_tmdb_identity=True,
            )
        )


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
