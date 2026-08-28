"""1280×800 now-playing zones, display fit, and leftover-screen markers."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import numpy as np

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon.design import (  # noqa: E402
    DESIGN_H,
    DESIGN_W,
    LEGACY_DESIGN_H,
    LEGACY_DESIGN_W,
    legacy_fit_scale,
    map_legacy_xy,
)
from pigeon.np_layout import (  # noqa: E402
    LEGACY_UPDATE_MARK_BGR,
    NOW_PLAYING_ZONES,
    apply_zone_restrictions,
    display_fit_scale,
    letterbox_legacy_ui,
    stamp_legacy_update_mark,
    zone_fits_canvas,
)


class ZoneGeometryTests(unittest.TestCase):
    def test_all_zones_fit_canvas(self) -> None:
        for zone in NOW_PLAYING_ZONES.values():
            self.assertTrue(zone_fits_canvas(zone), msg=f"zone {zone.index}")

    def test_portrait_columns_stack_left_to_right(self) -> None:
        self.assertAlmostEqual(NOW_PLAYING_ZONES[1].x, 44.5)
        self.assertAlmostEqual(NOW_PLAYING_ZONES[2].x, 442.5)
        self.assertAlmostEqual(NOW_PLAYING_ZONES[3].x, 840.0)

    def test_zone9_is_full_frame_idle(self) -> None:
        z9 = NOW_PLAYING_ZONES[9]
        self.assertEqual(z9.x, 0.0)
        self.assertEqual(z9.y, 0.0)
        self.assertEqual(z9.w, float(DESIGN_W))
        self.assertEqual(z9.h, float(DESIGN_H))

    def test_zone1_disables_conflicting_wide_slots(self) -> None:
        enabled = apply_zone_restrictions({1, 6, 8})
        self.assertIn(1, enabled)
        self.assertNotIn(6, enabled)
        self.assertNotIn(8, enabled)

    def test_zone6_and_zone7_span_portrait_pairs(self) -> None:
        z6 = NOW_PLAYING_ZONES[6]
        z7 = NOW_PLAYING_ZONES[7]
        self.assertAlmostEqual(z6.x, NOW_PLAYING_ZONES[1].x)
        self.assertAlmostEqual(z6.w, 793.0)
        self.assertEqual(z6.restrictions, (1, 2, 8))
        self.assertAlmostEqual(z7.x, NOW_PLAYING_ZONES[2].x)
        self.assertAlmostEqual(z7.w, 793.0)
        self.assertEqual(z7.restrictions, (2, 3, 8))
        self.assertEqual(z6.h, NOW_PLAYING_ZONES[1].h)
        self.assertEqual(z7.h, NOW_PLAYING_ZONES[1].h)


class DisplayFitTests(unittest.TestCase):
    def test_wider_display_scales_by_height(self) -> None:
        # 1920×800 is wider than 1280×800 → pillarbox, scale from height.
        self.assertAlmostEqual(display_fit_scale(1920, 800), 1.0)

    def test_narrower_display_scales_by_width(self) -> None:
        # 1280×900 is narrower than 1280×800 → letterbox, scale from width.
        self.assertAlmostEqual(display_fit_scale(1280, 900), 1.0)

    def test_legacy_panel_never_crops(self) -> None:
        # 800×480 is wider aspect than 1280×800 → scale by height.
        scale = display_fit_scale(800, 480)
        self.assertAlmostEqual(scale, 480 / 800)
        self.assertLessEqual(1280 * scale, 800 + 1e-6)
        self.assertLessEqual(800 * scale, 480 + 1e-6)

    def test_legacy_ui_letterbox_keeps_full_frame(self) -> None:
        src = np.full((LEGACY_DESIGN_H, LEGACY_DESIGN_W, 3), 40, dtype=np.uint8)
        out = letterbox_legacy_ui(src)
        self.assertEqual(out.shape[0], DESIGN_H)
        self.assertEqual(out.shape[1], DESIGN_W)
        self.assertAlmostEqual(legacy_fit_scale(), 1.6)
        x, y = map_legacy_xy(0, 0)
        self.assertAlmostEqual(x, 0.0)
        self.assertAlmostEqual(y, 16.0)


class LegacyMarkTests(unittest.TestCase):
    def test_red_square_top_left(self) -> None:
        img = np.zeros((DESIGN_H, DESIGN_W, 3), dtype=np.uint8)
        stamp_legacy_update_mark(img)
        self.assertEqual(tuple(int(v) for v in img[0, 0]), LEGACY_UPDATE_MARK_BGR)
        self.assertEqual(tuple(int(v) for v in img[20, 20]), (0, 0, 0))


def _force_default_np_zones() -> None:
    from pigeon.widgets import view_circles as vc

    vc._default_zone_widget_assignments = lambda: (  # type: ignore[method-assign]
        "clock",
        "poster",
        "volume",
        "cast_info",
        "status_bar",
    )


class NowPlayingFrameTests(unittest.TestCase):
    def test_view_circles_frame_is_design_size(self) -> None:
        from pigeon.widgets.view_circles import ViewCirclesWidget

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        widget.update_state(
            progress=0.25,
            elapsed_text="0:10",
            remaining_text="-1:00",
            volume_text="-22.5 dB",
            incoming_audio="MULTI-IN",
            playback_config="AURO3D",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            paused=False,
            service_name="peacock",
            cast=[("Bill Murray", "Bob Wiley")],
        )
        frame = widget.bgra_frame()
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.shape[0], DESIGN_H)
        self.assertEqual(frame.shape[1], DESIGN_W)
        self.assertEqual(frame.shape[2], 4)

    def test_zone4_cast_is_stacked_actor_over_character(self) -> None:
        from pigeon.np_layout import NOW_PLAYING_ZONES
        from pigeon.widgets.view_circles import ViewCirclesWidget

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        widget.update_state(
            progress=0.25,
            elapsed_text="0:11",
            remaining_text="-2:40",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            paused=True,
            service_name="disney+",
            content_mode="video",
            cast=[("Dave McCormack", "Bandit Heeler (voice)")],
        )
        frame = widget.bgra_frame()
        self.assertIsNotNone(frame)
        assert frame is not None
        zx, zy, zw, zh = NOW_PLAYING_ZONES[4].xywh
        overlap = max(0, (zy + zh) - int(round(NOW_PLAYING_ZONES[5].y)))
        col = frame[zy : zy + zh - overlap, zx : zx + zw // 3, :3]
        vis = col.max(axis=2) > 70
        rows = np.where(vis.any(axis=1))[0]
        self.assertGreater(int(rows.size), 12)
        # Actor stacked over character: a gap between the two ink bands.
        self.assertGreater(int(np.diff(rows).max()), 4)
        self.assertGreater(int(rows[-1] - rows[0]), 50)
        mid = (int(rows[0]) + int(rows[-1])) / 2.0
        self.assertLess(abs(mid - zh / 2.0), 24)

    def test_zone4_cast_pairs_center_in_portrait_columns(self) -> None:
        from pigeon.np_layout import NOW_PLAYING_ZONES, strip_cast_columns
        from pigeon.widgets.view_circles import ViewCirclesWidget

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        widget.update_state(
            progress=0.4,
            elapsed_text="40:43",
            remaining_text="-1:06:11",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            paused=False,
            service_name="apple tv",
            content_mode="video",
            cast=[
                ("Brandon Soo Hoo", "Tran"),
                ("Matthew McConaughey", "Rick Peck"),
                ("Tom Cruise", "Les Grossman"),
            ],
        )
        frame = widget.bgra_frame()
        self.assertIsNotNone(frame)
        assert frame is not None
        zone = NOW_PLAYING_ZONES[4]
        overlap = max(0, int(round(zone.y + zone.h)) - int(round(NOW_PLAYING_ZONES[5].y)))
        y0 = int(round(zone.y))
        y1 = int(round(zone.y + zone.h)) - overlap
        for x0, col_w in strip_cast_columns(zone):
            x_i = int(round(x0))
            x_j = int(round(x0 + col_w))
            col = frame[y0:y1, x_i:x_j, :3]
            vis = col.max(axis=2) > 70
            xs = np.where(vis.any(axis=0))[0]
            self.assertGreater(int(xs.size), 4, msg=f"no ink in column {x0}")
            mid = (int(xs[0]) + int(xs[-1])) / 2.0
            self.assertLess(abs(mid - col_w / 2.0), 28)
            # Actor and character share a center axis (two stacked bands).
            rows = np.where(vis.any(axis=1))[0]
            gap_at = int(np.argmax(np.diff(rows)))
            actor_rows = rows[: gap_at + 1]
            char_rows = rows[gap_at + 1 :]
            if actor_rows.size and char_rows.size and int(np.diff(rows).max()) > 4:
                actor_xs = np.where(vis[actor_rows[0] : actor_rows[-1] + 1].any(axis=0))[0]
                char_xs = np.where(vis[char_rows[0] : char_rows[-1] + 1].any(axis=0))[0]
                actor_mid = (int(actor_xs[0]) + int(actor_xs[-1])) / 2.0
                char_mid = (int(char_xs[0]) + int(char_xs[-1])) / 2.0
                self.assertLess(abs(actor_mid - char_mid), 12)

    def test_portrait_cast_lines_share_center_x(self) -> None:
        from pigeon.np_layout import CAST_LOCAL_ROWS, CAST_VIEW_H, NOW_PLAYING_ZONES
        from pigeon.widgets import view_circles as vc
        from pigeon.widgets.view_circles import ViewCirclesWidget

        vc._default_zone_widget_assignments = lambda: (  # type: ignore[method-assign]
            "clock",
            "poster",
            "cast_info",
            "cast_info",
            "status_bar",
        )
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        widget.update_state(
            progress=0.4,
            elapsed_text="40:58",
            remaining_text="-1:29:16",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            paused=False,
            service_name="apple tv",
            content_mode="video",
            cast=[
                ("Tom Cruise", "Capt. Pete 'Maverick' Mitchell"),
                ("Miles Teller", "Lt. Bradley 'Rooster' Bradshaw"),
            ],
        )
        frame = widget.bgra_frame()
        self.assertIsNotNone(frame)
        assert frame is not None
        zone = NOW_PLAYING_ZONES[3]
        zx, zy, zw, zh = zone.xywh
        _ax, actor_y, char_y = CAST_LOCAL_ROWS[0]
        sy = float(zh) / CAST_VIEW_H
        actor_band = int(round(zy + actor_y * sy))
        char_band = int(round(zy + char_y * sy))
        actor_row = frame[max(0, actor_band - 40) : actor_band + 2, zx : zx + zw, :3]
        char_row = frame[max(0, char_band - 36) : char_band + 2, zx : zx + zw, :3]
        a_vis = actor_row.max(axis=2) > 70
        c_vis = char_row.max(axis=2) > 70
        a_xs = np.where(a_vis.any(axis=0))[0]
        c_xs = np.where(c_vis.any(axis=0))[0]
        self.assertGreater(int(a_xs.size), 4)
        self.assertGreater(int(c_xs.size), 4)
        a_mid = (int(a_xs[0]) + int(a_xs[-1])) / 2.0
        c_mid = (int(c_xs[0]) + int(c_xs[-1])) / 2.0
        self.assertLess(abs(a_mid - c_mid), 10)
        self.assertLess(abs(a_mid - zw / 2.0), 16)

    def test_clock_digital_ink_centers_in_oval(self) -> None:
        from datetime import datetime

        from pigeon.np_layout import (
            CLOCK_DIGITAL_LOCAL,
            CLOCK_DIGITAL_NUDGE,
            CLOCK_VIEW_H,
            CLOCK_VIEW_W,
            NOW_PLAYING_ZONES,
        )
        from pigeon.widgets.view_circles import ViewCirclesWidget

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        when = datetime(2026, 8, 24, 12, 18)
        widget._clock_now_for_display = lambda: when  # type: ignore[method-assign]
        widget.update_state(
            progress=0.4,
            elapsed_text="40:43",
            remaining_text="-1:06:11",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            paused=False,
            service_name="apple tv",
            content_mode="video",
        )
        frame = widget.bgra_frame()
        self.assertIsNotNone(frame)
        assert frame is not None
        zone = NOW_PLAYING_ZONES[1]
        zx, zy, zw, zh = zone.xywh
        cx = float(zx) + CLOCK_DIGITAL_LOCAL[0] * (float(zw) / CLOCK_VIEW_W)
        cy = float(zy) + CLOCK_DIGITAL_LOCAL[1] * (float(zh) / CLOCK_VIEW_H)
        x0, y0 = int(round(cx - 70)), int(round(cy - 44))
        crop = frame[y0 : y0 + 88, x0 : x0 + 140, :]
        ink = crop[:, :, 3] > 180
        bright = crop[:, :, :3].max(axis=2) > 200
        mask = ink & bright
        ys, xs = np.where(mask)
        self.assertGreater(int(ys.size), 40)
        ink_cx = x0 + float(xs.mean())
        ink_cy = y0 + float(ys.mean())
        self.assertLess(abs(ink_cx - (cx + CLOCK_DIGITAL_NUDGE[0])), 6)
        self.assertLess(abs(ink_cy - (cy + CLOCK_DIGITAL_NUDGE[1])), 5)

    def test_clock_rings_are_black_open_black(self) -> None:
        from datetime import datetime

        from pigeon.np_layout import (
            CLOCK_LOCAL_CX,
            CLOCK_LOCAL_CY,
            CLOCK_VIEW_H,
            CLOCK_VIEW_W,
        )
        from pigeon.widgets.view_circles import (
            _CLOCK_EXTERIOR_ACCENT_R,
            _CLOCK_INTERIOR_ACCENT_R,
            _CLOCK_MIDDLE_ACCENT_R,
            _rasterize_named_widget,
            np_theme_from_settings,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        patch = _rasterize_named_widget(
            assets_dir=assets,
            widget_key="clock",
            dest_w=int(CLOCK_VIEW_W),
            dest_h=int(CLOCK_VIEW_H),
            now=datetime(2026, 8, 24, 14, 22),
            theme=np_theme_from_settings(),
        )
        self.assertIsNotNone(patch)
        assert patch is not None
        cx, cy = CLOCK_LOCAL_CX, CLOCK_LOCAL_CY

        def _sample(r: float) -> np.ndarray:
            x = int(round(cx))
            y = int(round(cy - r))
            return patch[y, x]

        outer = _sample((_CLOCK_EXTERIOR_ACCENT_R + _CLOCK_MIDDLE_ACCENT_R) * 0.5)
        open_band = _sample((_CLOCK_MIDDLE_ACCENT_R + _CLOCK_INTERIOR_ACCENT_R) * 0.5)
        inner = _sample((_CLOCK_INTERIOR_ACCENT_R + 40.0) * 0.5)
        self.assertGreater(int(outer[3]), 200)
        self.assertLess(int(outer[:3].max()), 40)
        self.assertLess(int(open_band[3]), 40)
        self.assertGreater(int(inner[3]), 200)
        self.assertLess(int(inner[:3].max()), 40)

    def test_minute_ticks_are_half_opaque(self) -> None:
        from datetime import datetime

        from pigeon.widgets.view_circles import (
            _CLOCK_MINUTE_TICK_OPACITY,
            _apply_standalone_clock_ticks,
            _find_by_key,
            _now_playing_widget_path,
            _svg_tree_from_path,
            np_theme_from_settings,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        root = _svg_tree_from_path(_now_playing_widget_path(assets, "clock"))
        _apply_standalone_clock_ticks(
            root, datetime(2026, 8, 24, 14, 22), theme=np_theme_from_settings()
        )
        tick = _find_by_key(root, "minutes_22")
        self.assertIsNotNone(tick)
        assert tick is not None
        self.assertAlmostEqual(float(tick.get("opacity") or 0), _CLOCK_MINUTE_TICK_OPACITY)

    def test_zone2_poster_keeps_cover_art(self) -> None:
        from pigeon.widgets.view_circles import ViewCirclesWidget, _poster_geometry

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        green_poster = np.full((80, 54, 3), (0, 255, 0), dtype=np.uint8)
        widget.update_state(
            progress=0.2,
            elapsed_text="0:10",
            remaining_text="-1:00",
            volume_text="",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            poster_bgra=green_poster,
        )
        frame = widget.bgra_frame()
        self.assertIsNotNone(frame)
        assert frame is not None
        px, py, pw, ph, _prx = _poster_geometry("video", zone=2)
        roi = frame[
            py + ph // 3 : py + 2 * ph // 3,
            px + pw // 3 : px + 2 * pw // 3,
        ]
        green = (roi[:, :, 1] > 200) & (roi[:, :, 0] < 40) & (roi[:, :, 2] < 40)
        self.assertGreater(int(green.sum()), 80)

    def test_16x9_poster_overrides_prefs_into_zone7(self) -> None:
        from pigeon.np_layout import DEFAULT_16X9_POSTER_ZONE
        from pigeon.widgets.view_circles import ViewCirclesWidget, _poster_geometry

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        landscape = np.full((90, 160, 3), (0, 255, 0), dtype=np.uint8)
        widget.update_state(
            progress=0.2,
            elapsed_text="0:10",
            remaining_text="-1:00",
            volume_text="",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            service_name="YouTube",
            poster_bgra=landscape,
        )
        self.assertEqual(DEFAULT_16X9_POSTER_ZONE, 7)
        self.assertEqual(widget._poster_zone(), 7)
        self.assertEqual(
            widget._assignments(),
            ("clock", "", "", "", "status_bar"),
        )
        frame = widget.bgra_frame()
        self.assertIsNotNone(frame)
        assert frame is not None
        px, py, pw, ph, _prx = _poster_geometry("video", zone=7)
        self.assertAlmostEqual(pw / float(ph), 16.0 / 9.0, places=2)
        roi = frame[
            py + ph // 3 : py + 2 * ph // 3,
            px + pw // 3 : px + 2 * pw // 3,
        ]
        green = (roi[:, :, 1] > 200) & (roi[:, :, 0] < 40) & (roi[:, :, 2] < 40)
        self.assertGreater(int(green.sum()), 80)
        portrait = _poster_geometry("video", zone=2)
        # Default zone-2 2×3 slot is inside zone 7, but the 16×9 frame is
        # shorter; the 2×3 top inset should not be the 16×9 home.
        self.assertNotEqual((px, py, pw, ph), portrait[:4])

    def test_settings_main_keeps_np_status_bar_gate(self) -> None:
        from pigeon.widgets.view_circles import settings_main_keeps_np_status_bar

        self.assertTrue(
            settings_main_keeps_np_status_bar(
                show_pigeon_settings=False, content_playing=True
            )
        )
        self.assertFalse(
            settings_main_keeps_np_status_bar(
                show_pigeon_settings=False, content_playing=False
            )
        )
        self.assertFalse(
            settings_main_keeps_np_status_bar(
                show_pigeon_settings=True, content_playing=True
            )
        )

    def test_overlay_status_bar_paints_zone5(self) -> None:
        from pigeon.np_layout import NOW_PLAYING_ZONES
        from pigeon.widgets.view_circles import ViewCirclesWidget

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        widget.update_state(
            progress=0.4,
            elapsed_text="12:12",
            remaining_text="-1:00",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            service_name="peacock",
        )
        canvas = np.zeros((DESIGN_H, DESIGN_W, 3), dtype=np.uint8)
        self.assertTrue(widget.overlay_status_bar(canvas))
        zx, zy, zw, zh = NOW_PLAYING_ZONES[5].xywh
        roi = canvas[zy : zy + zh, zx : zx + zw]
        grey = (
            (roi[:, :, 0] > 100)
            & (roi[:, :, 1] > 100)
            & (roi[:, :, 2] > 100)
            & (np.abs(roi[:, :, 0].astype(int) - roi[:, :, 1].astype(int)) < 20)
        )
        self.assertGreater(int(grey.sum()), 200)


class StatusBarTimecodeTests(unittest.TestCase):
    def test_drops_zero_hours_and_prefixes_remaining(self) -> None:
        from pigeon.widgets.view_circles import format_status_bar_timecode

        self.assertEqual(format_status_bar_timecode("0:12:12"), "12:12")
        self.assertEqual(format_status_bar_timecode("1:12:12"), "1:12:12")
        self.assertEqual(format_status_bar_timecode("10"), "00:10")
        self.assertEqual(format_status_bar_timecode("1:00", remaining=True), "-01:00")
        self.assertEqual(format_status_bar_timecode("-2:38:22", remaining=True), "-2:38:22")
        self.assertEqual(format_status_bar_timecode("LIVE", remaining=True), "LIVE")


class ZoneWidgetAssetTests(unittest.TestCase):
    def test_cast_capacity_by_zone(self) -> None:
        from pigeon.np_layout import cast_names_for_zone

        self.assertEqual(cast_names_for_zone(1), 5)
        self.assertEqual(cast_names_for_zone(4), 3)
        self.assertEqual(cast_names_for_zone(5), 3)

    def test_zone_specific_filenames(self) -> None:
        from pigeon.np_layout import widget_filename

        self.assertEqual(widget_filename("cast_info", 4), "widget_np_04_cast_info.svg")
        self.assertEqual(widget_filename("cast_info", 5), "widget_np_05_cast_info.svg")
        self.assertEqual(widget_filename("now_playing", 5), "widget_np_05_status_bar.svg")
        self.assertEqual(widget_filename("status_bar", 5), "widget_np_05_status_bar.svg")
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets" / "nowPlaying"
        self.assertTrue((assets / "widget_np_04_cast_info.svg").is_file())
        self.assertTrue((assets / "widget_np_05_cast_info.svg").is_file())
        self.assertTrue((assets / "widget_np_05_status_bar.svg").is_file())
        self.assertEqual(widget_filename("poster_16x9", 6), "widget_np_06_16x9.svg")
        self.assertEqual(widget_filename("poster_16x9", 7), "widget_np_07_16x9.svg")
        self.assertEqual(widget_filename("poster_16x9"), "widget_np_07_16x9.svg")
        self.assertTrue((assets / "widget_np_06_16x9.svg").is_file())
        self.assertTrue((assets / "widget_np_07_16x9.svg").is_file())


class DefaultZoneWidgetTests(unittest.TestCase):
    def test_defaults_are_clock_poster_volume_cast_status_bar(self) -> None:
        from pigeon.widgets.preferences_settings import (
            DEFAULT_ZONE_WIDGETS,
            _normalize_zone_widgets,
        )

        self.assertEqual(
            DEFAULT_ZONE_WIDGETS,
            ("clock", "poster", "volume", "cast_info", "status_bar"),
        )
        self.assertEqual(
            _normalize_zone_widgets(("clock", "poster", "volume", "cast_info", "now_playing")),
            DEFAULT_ZONE_WIDGETS,
        )

    def test_canonical_zone_widget_aliases(self) -> None:
        from pigeon.np_layout import canonical_zone_widget, is_status_bar_widget

        self.assertEqual(canonical_zone_widget(5, "now_playing"), "status_bar")
        self.assertEqual(canonical_zone_widget(5, "status_bar"), "status_bar")
        self.assertEqual(canonical_zone_widget(1, "now_playing"), "now_playing")
        self.assertEqual(canonical_zone_widget(1, "status_bar"), "now_playing")
        self.assertTrue(is_status_bar_widget("now_playing", 5))
        self.assertFalse(is_status_bar_widget("now_playing", 1))


class VolumeReadoutShiftTests(unittest.TestCase):
    def test_keeps_authored_layout_when_source_is_present(self) -> None:
        from pigeon.np_layout import volume_readout_y_shift

        self.assertEqual(
            volume_readout_y_shift(has_source=True, value_h=90.0, scale_h=70.0),
            0.0,
        )

    def test_centers_value_and_scale_as_a_pair(self) -> None:
        from pigeon.np_layout import (
            VOLUME_LOCAL_CY,
            VOLUME_SCALE_LOCAL,
            VOLUME_VALUE_LOCAL,
            volume_readout_y_shift,
        )

        value_h = 90.0
        scale_h = 70.0
        dy = volume_readout_y_shift(
            has_source=False, value_h=value_h, scale_h=scale_h
        )
        top = min(VOLUME_VALUE_LOCAL[1] - value_h, VOLUME_SCALE_LOCAL[1] - scale_h) + dy
        bottom = max(VOLUME_VALUE_LOCAL[1], VOLUME_SCALE_LOCAL[1]) + dy
        self.assertAlmostEqual((top + bottom) / 2.0, VOLUME_LOCAL_CY)
        self.assertAlmostEqual(
            (VOLUME_SCALE_LOCAL[1] + dy) - (VOLUME_VALUE_LOCAL[1] + dy),
            VOLUME_SCALE_LOCAL[1] - VOLUME_VALUE_LOCAL[1],
        )


class SixteenByNinePosterTests(unittest.TestCase):
    def test_youtube_and_landscape_art_request_16x9(self) -> None:
        from pigeon.np_layout import wants_16x9_poster

        portrait = np.zeros((80, 54, 3), dtype=np.uint8)
        landscape = np.zeros((90, 160, 3), dtype=np.uint8)
        self.assertTrue(wants_16x9_poster(service_name="YouTube"))
        self.assertTrue(wants_16x9_poster(poster_bgra=landscape))
        self.assertFalse(wants_16x9_poster(service_name="Peacock", poster_bgra=portrait))
        self.assertFalse(
            wants_16x9_poster(service_name="YouTube", content_mode="music")
        )

    def test_zone7_override_keeps_clock_drops_volume(self) -> None:
        from pigeon.np_layout import apply_16x9_poster_override

        self.assertEqual(
            apply_16x9_poster_override(
                ("clock", "poster", "volume", "cast_info", "status_bar")
            ),
            ("clock", "", "", "cast_info", "status_bar"),
        )

    def test_zone7_override_relocates_clock_from_zone3(self) -> None:
        from pigeon.np_layout import apply_16x9_poster_override

        self.assertEqual(
            apply_16x9_poster_override(
                ("poster", "volume", "clock", "cast_info", "status_bar")
            ),
            ("clock", "", "", "cast_info", "status_bar"),
        )

    def test_landscape_art_uses_zone7_without_youtube(self) -> None:
        from pigeon.widgets.view_circles import ViewCirclesWidget

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        landscape = np.full((90, 160, 3), (0, 255, 0), dtype=np.uint8)
        widget.update_state(
            progress=0.2,
            elapsed_text="0:10",
            remaining_text="-1:00",
            volume_text="",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            service_name="Peacock",
            poster_bgra=landscape,
        )
        self.assertEqual(widget._poster_zone(), 7)

    def test_youtube_forces_zone7_even_with_portrait_art(self) -> None:
        from pigeon.widgets.view_circles import ViewCirclesWidget

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        portrait = np.full((80, 54, 3), (0, 255, 0), dtype=np.uint8)
        widget.update_state(
            progress=0.2,
            elapsed_text="0:10",
            remaining_text="-1:00",
            volume_text="",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            service_name="YouTube",
            poster_bgra=portrait,
        )
        self.assertEqual(widget._poster_zone(), 7)

    def test_16x9_slot_is_sixteen_by_nine(self) -> None:
        from pigeon.np_layout import POSTER_16X9_LOCAL

        _w, _h = POSTER_16X9_LOCAL[2], POSTER_16X9_LOCAL[3]
        self.assertAlmostEqual(_w / _h, 16.0 / 9.0, places=4)


class EmptyWidgetTests(unittest.TestCase):
    def test_idle_now_playing_fills_screen_with_clock(self) -> None:
        from datetime import datetime

        from pigeon.widgets.view_circles import (
            ViewCirclesWidget,
            _fallback_base_bgra,
            _layout_is_fullscreen_clock,
            _paste_patch_bgra,
            render_centered_clock_widget_bgra,
        )

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        widget.update_state(
            progress=0.0,
            elapsed_text="",
            remaining_text="",
            volume_text="",
            has_now_playing=True,
            has_position=False,
            content_active=False,
            content_mode="video",
            missing_art=True,
        )
        self.assertTrue(_layout_is_fullscreen_clock(widget._assignments()))
        frozen = datetime(2026, 8, 28, 17, 14, 32)
        widget._clock_now_for_display = lambda: frozen  # type: ignore[method-assign]
        widget.clear_cache()
        got = widget.bgra_frame()
        self.assertIsNotNone(got)
        clock = render_centered_clock_widget_bgra(assets_dir=assets, now=frozen)
        expected = _fallback_base_bgra()
        _paste_patch_bgra(expected, clock, 0, 0)
        self.assertEqual(got.shape, expected.shape)
        self.assertTrue(np.array_equal(got, expected))

    def test_missing_poster_does_not_occupy_a_zone(self) -> None:
        from pigeon.widgets.view_circles import (
            ViewCirclesWidget,
            _layout_is_fullscreen_clock,
        )

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        widget.update_state(
            progress=0.2,
            elapsed_text="0:10",
            remaining_text="-1:00",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            missing_art=True,
            service_name="YouTube",
        )
        self.assertIsNone(widget._poster_zone())
        self.assertNotIn("poster", widget._assignments())
        self.assertFalse(_layout_is_fullscreen_clock(widget._assignments()))

    def test_populated_poster_keeps_zoned_clock(self) -> None:
        from pigeon.widgets.view_circles import (
            ViewCirclesWidget,
            _layout_is_fullscreen_clock,
        )

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        poster = np.full((80, 54, 3), 40, dtype=np.uint8)
        widget.update_state(
            progress=0.2,
            elapsed_text="0:10",
            remaining_text="-1:00",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            poster_bgra=poster,
            service_name="Peacock",
        )
        self.assertFalse(_layout_is_fullscreen_clock(widget._assignments()))
        self.assertEqual(widget._assignments()[0], "clock")
        self.assertEqual(widget._poster_zone(), 2)


if __name__ == "__main__":
    unittest.main()
