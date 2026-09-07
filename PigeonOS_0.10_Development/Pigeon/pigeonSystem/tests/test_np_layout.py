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


class ParCompensationTests(unittest.TestCase):
    def test_pi7_par_fits_800x480_in_one_scale(self) -> None:
        from pigeon.display_par import OFFICIAL_PI_7_PAR, apply_par_compensation

        src = np.full((DESIGN_H, DESIGN_W, 3), 200, dtype=np.uint8)
        out = apply_par_compensation(
            src, display_w=800, display_h=480, par=OFFICIAL_PI_7_PAR
        )
        self.assertEqual(out.shape[0], 480)
        self.assertEqual(out.shape[1], 800)
        # Height-limited letterbox after the 1/PAR squeeze → pillarbox bars.
        self.assertLess(int(out[240, 10].max()), 8)
        self.assertGreater(int(out[240, 400].min()), 150)

    def test_square_par_matches_uniform_letterbox(self) -> None:
        from pigeon.compositing import scale_uniform_letterbox
        from pigeon.display_par import apply_par_compensation

        src = np.full((DESIGN_H, DESIGN_W, 3), 90, dtype=np.uint8)
        src[100:120, 100:140] = 255
        a = apply_par_compensation(src, display_w=800, display_h=480, par=1.0)
        b = scale_uniform_letterbox(src, 800, 480)
        self.assertEqual(a.shape, b.shape)
        self.assertLess(int(np.abs(a.astype(int) - b.astype(int)).max()), 2)


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

    def test_seconds_ticks_alternate_white_and_ui(self) -> None:
        from datetime import datetime

        from pigeon.widgets.view_circles import (
            _NpTheme,
            _apply_standalone_clock_ticks,
            _find_by_key,
            _now_playing_widget_path,
            _seconds_alternate_colors,
            _svg_tree_from_path,
        )

        ui = "#4ea6f7"
        white = "#ffffff"
        incoming, outgoing, n = _seconds_alternate_colors(
            datetime(2026, 1, 1, 12, 0, 0), ui_hex=ui
        )
        self.assertEqual((incoming.lower(), outgoing.lower(), n), (ui, white, 0))
        incoming, outgoing, n = _seconds_alternate_colors(
            datetime(2026, 1, 1, 12, 0, 15), ui_hex=ui
        )
        self.assertEqual((incoming.lower(), outgoing.lower(), n), (ui, white, 15))
        incoming, outgoing, n = _seconds_alternate_colors(
            datetime(2026, 1, 1, 12, 0, 59), ui_hex=ui
        )
        self.assertEqual((incoming.lower(), outgoing.lower(), n), (ui, white, 59))
        incoming, outgoing, n = _seconds_alternate_colors(
            datetime(2026, 1, 1, 12, 1, 0), ui_hex=ui
        )
        self.assertEqual((incoming.lower(), outgoing.lower(), n), (white, ui, 0))
        incoming, outgoing, n = _seconds_alternate_colors(
            datetime(2026, 1, 1, 12, 1, 1), ui_hex=ui
        )
        self.assertEqual((incoming.lower(), outgoing.lower(), n), (white, ui, 1))

        def _tick_fill(root, name: str) -> str:
            el = _find_by_key(root, name)
            self.assertIsNotNone(el, msg=name)
            assert el is not None
            for node in el.iter():
                fill = (node.get("fill") or "").strip()
                if fill and fill.lower() != "none":
                    return fill.lower()
            return ""

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        theme = _NpTheme(ui_hex="#4EA6F7")
        root = _svg_tree_from_path(_now_playing_widget_path(assets, "clock"))
        _apply_standalone_clock_ticks(
            root, datetime(2026, 1, 1, 12, 0, 15), theme=theme
        )
        self.assertTrue(_find_by_key(root, "seconds_60") is not None)
        self.assertTrue(_tick_fill(root, "seconds_01").endswith("4ea6f7"))
        self.assertTrue(_tick_fill(root, "seconds_15").endswith("4ea6f7"))
        self.assertTrue(_tick_fill(root, "seconds_16") in ("#ffffff", "white"))
        self.assertTrue(_tick_fill(root, "seconds_60") in ("#ffffff", "white"))

        root = _svg_tree_from_path(_now_playing_widget_path(assets, "clock"))
        _apply_standalone_clock_ticks(
            root, datetime(2026, 1, 1, 12, 1, 1), theme=theme
        )
        self.assertTrue(_tick_fill(root, "seconds_01") in ("#ffffff", "white"))
        self.assertTrue(_tick_fill(root, "seconds_02").endswith("4ea6f7"))
        self.assertTrue(_tick_fill(root, "seconds_60").endswith("4ea6f7"))

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

    def test_16x9_poster_overrides_prefs_into_zone6(self) -> None:
        from pigeon.np_layout import DEFAULT_16X9_POSTER_ZONE, YOUTUBE_ZONE_WIDGETS
        from pigeon.widgets.view_circles import ViewCirclesWidget, _poster_geometry

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        landscape = np.full((90, 160, 3), (0, 255, 0), dtype=np.uint8)
        widget.update_state(
            progress=0.2,
            elapsed_text="0:10",
            remaining_text="-1:00",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            service_name="YouTube",
            poster_bgra=landscape,
        )
        self.assertEqual(DEFAULT_16X9_POSTER_ZONE, 6)
        self.assertEqual(widget._poster_zone(), 6)
        self.assertEqual(widget._assignments(), YOUTUBE_ZONE_WIDGETS)
        frame = widget.bgra_frame()
        self.assertIsNotNone(frame)
        assert frame is not None
        px, py, pw, ph, _prx = _poster_geometry("video", zone=6)
        self.assertAlmostEqual(pw / float(ph), 16.0 / 9.0, places=2)
        roi = frame[
            py + ph // 3 : py + 2 * ph // 3,
            px + pw // 3 : px + 2 * pw // 3,
        ]
        green = (roi[:, :, 1] > 200) & (roi[:, :, 0] < 40) & (roi[:, :, 2] < 40)
        self.assertGreater(int(green.sum()), 80)
        portrait = _poster_geometry("video", zone=2)
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

    def test_overlay_status_bar_reuse_does_not_wipe_chrome(self) -> None:
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
        canvas = np.full((DESIGN_H, DESIGN_W, 3), 40, dtype=np.uint8)
        self.assertTrue(widget.overlay_status_bar(canvas))
        self.assertTrue(widget.overlay_status_bar(canvas))
        zx, zy, _zw, _zh = NOW_PLAYING_ZONES[5].xywh
        above = canvas[: max(0, zy - 2), :, :]
        self.assertTrue(np.all(above == 40))


class AlphaBlendTests(unittest.TestCase):
    def test_black_destination_scales_rgb_by_alpha(self) -> None:
        from pigeon.compositing import alpha_blend_bgra_over_bgr

        base = np.zeros((4, 4, 3), dtype=np.uint8)
        over = np.zeros((4, 4, 4), dtype=np.uint8)
        over[:, :, 2] = 200
        over[:, :, 3] = 128
        out = alpha_blend_bgra_over_bgr(base, over)
        expect = (200 * 128 + 127) // 255
        self.assertEqual(int(out[0, 0, 2]), expect)
        self.assertEqual(int(out[0, 0, 0]), 0)


class StatusBarTimecodeTests(unittest.TestCase):
    def test_drops_zero_hours_and_prefixes_remaining(self) -> None:
        from pigeon.widgets.view_circles import format_status_bar_timecode

        self.assertEqual(format_status_bar_timecode("0:12:12"), "12:12")
        self.assertEqual(format_status_bar_timecode("1:12:12"), "1:12:12")
        self.assertEqual(format_status_bar_timecode("10"), "00:10")
        self.assertEqual(format_status_bar_timecode("1:00", remaining=True), "-01:00")
        self.assertEqual(format_status_bar_timecode("-2:38:22", remaining=True), "-2:38:22")
        self.assertEqual(format_status_bar_timecode("LIVE", remaining=True), "LIVE")


class StatusBarElapsedTravelTests(unittest.TestCase):
    def test_elapsed_parks_left_then_rides_the_fill(self) -> None:
        from pigeon.np_layout import status_bar_elapsed_left_x

        park = 10.0
        early = status_bar_elapsed_left_x(
            track_x=70.0,
            track_w=1000.0,
            progress=0.0,
            elapsed_w=80.0,
            park_x=park,
        )
        later = status_bar_elapsed_left_x(
            track_x=70.0,
            track_w=1000.0,
            progress=0.5,
            elapsed_w=80.0,
            park_x=park,
        )
        self.assertEqual(early, park)
        self.assertGreater(later, early)
        self.assertAlmostEqual(later, 70.0 + 500.0 - 80.0)

    def test_service_waits_until_elapsed_clears_it(self) -> None:
        from pigeon.np_layout import status_bar_service_has_room

        self.assertFalse(
            status_bar_service_has_room(
                service_x=10.0, service_w=80.0, elapsed_x=10.0
            )
        )
        self.assertFalse(
            status_bar_service_has_room(
                service_x=10.0, service_w=80.0, elapsed_x=100.0
            )
        )
        self.assertTrue(
            status_bar_service_has_room(
                service_x=10.0, service_w=80.0, elapsed_x=130.0
            )
        )

    def test_early_progress_hides_service_name(self) -> None:
        from pigeon.np_layout import NOW_PLAYING_ZONES
        from pigeon.widgets.view_circles import ViewCirclesWidget

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"

        def _bar(progress: float, service: str) -> np.ndarray:
            widget = ViewCirclesWidget(assets_dir=assets)
            widget.update_state(
                progress=progress,
                elapsed_text="0:12",
                remaining_text="-1:00",
                volume_text="",
                has_now_playing=True,
                has_position=True,
                content_active=True,
                content_mode="video",
                service_name=service,
            )
            canvas = np.zeros((DESIGN_H, DESIGN_W, 3), dtype=np.uint8)
            widget.overlay_status_bar(canvas)
            zx, zy, zw, zh = NOW_PLAYING_ZONES[5].xywh
            return canvas[zy : zy + zh, zx : zx + zw]

        early_named = _bar(0.0, "peacock")
        early_blank = _bar(0.0, "")
        self.assertLess(
            int(np.abs(early_named.astype(int) - early_blank.astype(int)).max()),
            8,
        )

        late_named = _bar(0.85, "peacock")
        late_blank = _bar(0.85, "")
        self.assertGreater(
            int(np.abs(late_named.astype(int) - late_blank.astype(int)).max()),
            40,
        )

    def test_elapsed_shifts_right_as_progress_grows(self) -> None:
        from pigeon.np_layout import NOW_PLAYING_ZONES, STATUS_BAR_SERVICE_LOCAL
        from pigeon.widgets.view_circles import ViewCirclesWidget

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"

        def _elapsed_center_x(progress: float) -> float:
            widget = ViewCirclesWidget(assets_dir=assets)
            widget.update_state(
                progress=progress,
                elapsed_text="12:12",
                remaining_text="-1:00",
                volume_text="",
                has_now_playing=True,
                has_position=True,
                content_active=True,
                content_mode="video",
                service_name="",
            )
            canvas = np.zeros((DESIGN_H, DESIGN_W, 3), dtype=np.uint8)
            widget.overlay_status_bar(canvas)
            zx, zy, zw, zh = NOW_PLAYING_ZONES[5].xywh
            # Time row sits below the track; ignore the grey bar itself.
            y0 = int(round(STATUS_BAR_SERVICE_LOCAL[1])) - 45
            band = canvas[zy + y0 : zy + zh, zx : zx + zw]
            vis = np.any(band > 40, axis=2)
            xs = np.where(vis.any(axis=0))[0]
            self.assertGreater(int(xs.size), 0)
            return float(xs[0])

        self.assertGreater(_elapsed_center_x(0.7), _elapsed_center_x(0.15))

    def test_handoff_alphas_crossfade(self) -> None:
        from pigeon.np_layout import status_bar_handoff_alphas

        self.assertEqual(status_bar_handoff_alphas(0.0), (1.0, 0.0, 0.0))
        self.assertEqual(status_bar_handoff_alphas(1.0), (0.0, 1.0, 1.0))
        parked, service, traveling = status_bar_handoff_alphas(0.4)
        self.assertAlmostEqual(parked, 0.6)
        self.assertAlmostEqual(service, 0.4)
        self.assertAlmostEqual(traveling, 0.4)

    def test_travel_x_is_not_clamped_to_park(self) -> None:
        from pigeon.np_layout import (
            status_bar_elapsed_left_x,
            status_bar_elapsed_travel_x,
        )

        travel = status_bar_elapsed_travel_x(
            track_x=70.0, track_w=1000.0, progress=0.0, elapsed_w=80.0
        )
        parked = status_bar_elapsed_left_x(
            track_x=70.0,
            track_w=1000.0,
            progress=0.0,
            elapsed_w=80.0,
            park_x=10.0,
        )
        self.assertLess(travel, parked)
        self.assertEqual(parked, 10.0)

    def test_handoff_fades_parked_out_and_traveling_in(self) -> None:
        from pigeon.np_layout import NOW_PLAYING_ZONES, STATUS_BAR_SERVICE_LOCAL
        from pigeon.widgets.view_circles import ViewCirclesWidget

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"

        def _time_row(*, handoff: float) -> np.ndarray:
            widget = ViewCirclesWidget(assets_dir=assets)
            widget.update_state(
                progress=0.85,
                elapsed_text="12:12",
                remaining_text="-1:00",
                volume_text="",
                has_now_playing=True,
                has_position=True,
                content_active=True,
                content_mode="video",
                service_name="peacock",
            )
            widget._bar_handoff_inited = True
            widget._bar_handoff = float(handoff)
            widget._bar_handoff_want = float(handoff)
            canvas = np.zeros((DESIGN_H, DESIGN_W, 3), dtype=np.uint8)
            widget.overlay_status_bar(canvas)
            zx, zy, zw, zh = NOW_PLAYING_ZONES[5].xywh
            y0 = int(round(STATUS_BAR_SERVICE_LOCAL[1])) - 45
            return canvas[zy + y0 : zy + zh, zx : zx + zw]

        parked = _time_row(handoff=0.0)
        mid = _time_row(handoff=0.5)
        done = _time_row(handoff=1.0)
        # Mid-bar (past the service slot, before remaining) only has the
        # traveling elapsed once the handoff starts.
        travel_band_parked = parked[:, 820:980]
        travel_band_mid = mid[:, 820:980]
        travel_band_done = done[:, 820:980]
        self.assertLess(int(travel_band_parked.max()), 20)
        self.assertGreater(int(travel_band_done.max()), 80)
        self.assertGreater(int(travel_band_mid.max()), 20)
        self.assertLess(int(travel_band_mid.max()), int(travel_band_done.max()))
        # Left slot changes from elapsed to the service name.
        self.assertGreater(
            int(np.abs(parked[:, :220].astype(int) - done[:, :220].astype(int)).max()),
            40,
        )


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
        self.assertEqual(widget_filename("poster_16x9"), "widget_np_06_16x9.svg")
        self.assertTrue((assets / "widget_np_06_16x9.svg").is_file())
        self.assertTrue((assets / "widget_np_07_16x9.svg").is_file())


class DefaultZoneWidgetTests(unittest.TestCase):
    def test_defaults_are_wide_countdown_volume_cast_status_bar(self) -> None:
        from pigeon.widgets.preferences_settings import (
            DEFAULT_ZONE_WIDGETS,
            _normalize_zone_widgets,
        )

        self.assertEqual(
            DEFAULT_ZONE_WIDGETS,
            ("tt_countdown_16x9", "", "volume", "cast_info", "status_bar"),
        )
        self.assertEqual(
            _normalize_zone_widgets(("clock", "poster", "volume", "cast_info", "now_playing")),
            ("clock", "poster", "volume", "cast_info", "status_bar"),
        )
        self.assertEqual(
            _normalize_zone_widgets(DEFAULT_ZONE_WIDGETS),
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
        self.assertFalse(wants_16x9_poster(poster_bgra=landscape))
        self.assertFalse(wants_16x9_poster(service_name="Peacock", poster_bgra=portrait))
        self.assertFalse(wants_16x9_poster(service_name="Peacock", poster_bgra=landscape))
        self.assertFalse(
            wants_16x9_poster(service_name="YouTube", content_mode="music")
        )

    def test_zone6_override_keeps_volume_drops_clock(self) -> None:
        from pigeon.np_layout import apply_16x9_poster_override

        self.assertEqual(
            apply_16x9_poster_override(
                ("clock", "poster", "volume", "cast_info", "status_bar")
            ),
            ("", "", "volume", "cast_info", "status_bar"),
        )

    def test_zone7_override_relocates_clock_from_zone3(self) -> None:
        from pigeon.np_layout import apply_16x9_poster_override

        self.assertEqual(
            apply_16x9_poster_override(
                ("poster", "volume", "clock", "cast_info", "status_bar"),
                zone=7,
            ),
            ("clock", "", "", "cast_info", "status_bar"),
        )

    def test_landscape_art_stays_zone2_without_youtube(self) -> None:
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
        self.assertEqual(widget._poster_zone(), 2)
        self.assertEqual(widget._assignments()[0], "clock")
        self.assertEqual(widget._assignments()[1], "poster")
        self.assertEqual(widget._assignments()[2], "volume")

    def test_youtube_forces_zone6_even_with_portrait_art(self) -> None:
        from pigeon.np_layout import YOUTUBE_ZONE_WIDGETS
        from pigeon.widgets.view_circles import ViewCirclesWidget

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        portrait = np.full((80, 54, 3), (0, 255, 0), dtype=np.uint8)
        widget.update_state(
            progress=0.2,
            elapsed_text="0:10",
            remaining_text="-1:00",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            service_name="YouTube",
            poster_bgra=portrait,
        )
        self.assertEqual(widget._poster_zone(), 6)
        self.assertEqual(widget._assignments(), YOUTUBE_ZONE_WIDGETS)

    def test_is_youtube_flag_uses_zone6_without_service_label(self) -> None:
        from pigeon.np_layout import YOUTUBE_ZONE_WIDGETS
        from pigeon.widgets.view_circles import ViewCirclesWidget

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        landscape = np.full((90, 160, 3), (0, 255, 0), dtype=np.uint8)
        widget.update_state(
            progress=0.2,
            elapsed_text="0:10",
            remaining_text="-1:00",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            service_name="",
            is_youtube=True,
            poster_bgra=landscape,
        )
        self.assertTrue(widget._wants_16x9_poster())
        self.assertEqual(widget._poster_zone(), 6)
        self.assertEqual(widget._assignments(), YOUTUBE_ZONE_WIDGETS)
        self.assertEqual(widget._assignments()[0], "")
        self.assertEqual(widget._assignments()[2], "volume")

    def test_16x9_slot_is_sixteen_by_nine(self) -> None:
        from pigeon.np_layout import POSTER_16X9_LOCAL

        _w, _h = POSTER_16X9_LOCAL[2], POSTER_16X9_LOCAL[3]
        self.assertAlmostEqual(_w / _h, 16.0 / 9.0, places=4)

    def test_youtube_title_draws_in_zone4_like_music(self) -> None:
        from pigeon.widgets.view_circles import ViewCirclesWidget, _zone4_title_xywh

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        landscape = np.full((90, 160, 3), (0, 255, 0), dtype=np.uint8)
        zx, zy, zw, zh = _zone4_title_xywh()
        z5x, z5y, z5w, _z5h = NOW_PLAYING_ZONES[5].xywh
        self.assertLessEqual(zy + zh, z5y)

        yt = ViewCirclesWidget(assets_dir=assets)
        yt.update_state(
            progress=0.2,
            elapsed_text="0:10",
            remaining_text="-1:00",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            is_youtube=True,
            song_title="Never Gonna Give You Up",
            artist_title="Rick Astley",
            poster_bgra=landscape,
        )
        yt_frame = yt.bgra_frame()
        self.assertIsNotNone(yt_frame)
        assert yt_frame is not None
        yt_roi = yt_frame[zy : zy + zh, zx : zx + zw, :3]
        yt_lit = int(np.count_nonzero(yt_roi.max(axis=2) > 180))
        self.assertGreater(yt_lit, 400)
        bar_band = yt_frame[z5y : z5y + 28, z5x + z5w // 4 : z5x + 3 * z5w // 4, :3]
        bar_white = int(np.count_nonzero(np.all(bar_band > 200, axis=2)))
        self.assertLess(bar_white, 40)

        blank = ViewCirclesWidget(assets_dir=assets)
        blank.update_state(
            progress=0.2,
            elapsed_text="0:10",
            remaining_text="-1:00",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            is_youtube=True,
            song_title="",
            artist_title="",
            poster_bgra=landscape,
        )
        blank_frame = blank.bgra_frame()
        self.assertIsNotNone(blank_frame)
        assert blank_frame is not None
        blank_roi = blank_frame[zy : zy + zh, zx : zx + zw, :3]
        blank_lit = int(np.count_nonzero(blank_roi.max(axis=2) > 180))
        self.assertGreater(yt_lit, blank_lit + 200)

        music = ViewCirclesWidget(assets_dir=assets)
        album = np.full((80, 80, 3), (0, 255, 0), dtype=np.uint8)
        music.update_state(
            progress=0.2,
            elapsed_text="0:10",
            remaining_text="-1:00",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="music",
            song_title="Never Gonna Give You Up",
            artist_title="Rick Astley",
            album_title="Whenever You Need Somebody",
            poster_bgra=album,
        )
        music_frame = music.bgra_frame()
        self.assertIsNotNone(music_frame)
        assert music_frame is not None
        music_roi = music_frame[zy : zy + zh, zx : zx + zw, :3]
        music_lit = int(np.count_nonzero(music_roi.max(axis=2) > 180))
        self.assertGreater(music_lit, 400)

    def test_zone4_artist_album_use_height_not_full_string_width(self) -> None:
        from pigeon.view_one_variants import render_ui_music_text_patch_bgra
        from pigeon.widgets.view_circles import (
            _ZONE4_SUBTITLE_TOP_FRAC,
            _zone4_title_xywh,
        )

        _zx, _zy, zw, zh = _zone4_title_xywh()
        long_album = render_ui_music_text_patch_bgra(
            "Overcompensate (Live in Mexico City)",
            "twenty one pilots",
            "More Than We Ever Imagined (Live in Mexico City)",
            zw,
            zh,
            subtitle_top_frac=_ZONE4_SUBTITLE_TOP_FRAC,
        )
        short_album = render_ui_music_text_patch_bgra(
            "Overcompensate (Live in Mexico City)",
            "twenty one pilots",
            "Vessel",
            zw,
            zh,
            subtitle_top_frac=_ZONE4_SUBTITLE_TOP_FRAC,
        )
        self.assertIsNotNone(long_album)
        self.assertIsNotNone(short_album)
        assert long_album is not None and short_album is not None

        def _subtitle_span(patch: np.ndarray) -> int:
            y0 = int(round(zh * float(_ZONE4_SUBTITLE_TOP_FRAC)))
            band = patch[y0:, :, :3]
            vis = band.max(axis=2) > 180
            rows = np.where(vis.any(axis=1))[0]
            if rows.size < 2:
                return 0
            return int(rows[-1] - rows[0])

        long_span = _subtitle_span(long_album)
        short_span = _subtitle_span(short_album)
        self.assertGreater(long_span, 36)
        self.assertGreater(long_span, int(short_span * 0.75))
        # Two subtitle lines should reach into the lower half of the leftover strip.
        self.assertGreater(long_span, int((zh - int(round(zh * float(_ZONE4_SUBTITLE_TOP_FRAC)))) * 0.55))

    def test_bright_look_leaves_youtube_thumbnail(self) -> None:
        from pigeon.widgets.options_settings import apply_ui_bright_bgr
        from pigeon.widgets.ui_color_settings import write_ui_color_keys
        from pigeon.widgets.view_circles import ViewCirclesWidget, _poster_geometry

        _force_default_np_zones()
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        write_ui_color_keys({"ui": "bright"}, persist=False)
        try:
            widget = ViewCirclesWidget(assets_dir=assets)
            landscape = np.full((90, 160, 3), 40, dtype=np.uint8)
            widget.update_state(
                progress=0.2,
                elapsed_text="0:10",
                remaining_text="-1:00",
                volume_text="-22.5 dB",
                has_now_playing=True,
                has_position=True,
                content_active=True,
                content_mode="video",
                is_youtube=True,
                poster_bgra=landscape,
            )
            frame = widget.bgra_frame()
            self.assertIsNotNone(frame)
            assert frame is not None
            bgr = apply_ui_bright_bgr(frame[:, :, :3])
            px, py, pw, ph, _prx = _poster_geometry("video", zone=6)
            roi = bgr[
                py + ph // 3 : py + 2 * ph // 3,
                px + pw // 3 : px + 2 * pw // 3,
            ]
            self.assertLess(float(roi.mean()), 80.0)
        finally:
            write_ui_color_keys({"ui": "blue"}, persist=False)

    def test_bright_look_does_not_invert_artwork_blur(self) -> None:
        from pigeon.compositing import alpha_blend_bgra_over_bgr
        from pigeon.design import DESIGN_H, DESIGN_W
        from pigeon.widgets.options_settings import apply_ui_bright_bgr
        from pigeon.widgets.view_circles import _build_artwork_blur_bgra

        src = np.zeros((90, 160, 4), dtype=np.uint8)
        src[:, :] = (12, 48, 220, 255)
        src[15:75, 35:125] = (20, 90, 255, 255)
        blur = _build_artwork_blur_bgra(src)
        base = np.full((DESIGN_H, DESIGN_W, 3), 255, dtype=np.uint8)
        composed = alpha_blend_bgra_over_bgr(base, blur)
        out = apply_ui_bright_bgr(composed)
        self.assertLess(float(np.mean(np.abs(out.astype(int) - composed.astype(int)))), 1.0)
        self.assertGreater(float(out.mean()), 180.0)


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
        self.assertEqual(widget._poster_zone(), 6)
        self.assertNotIn("poster", widget._assignments())
        self.assertEqual(widget._assignments()[0], "")
        self.assertEqual(widget._assignments()[2], "volume")
        self.assertEqual(widget._assignments()[3], "")
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


class TtCountdownWidgetTests(unittest.TestCase):
    """TT + countdown widget: filename, divider guides, tabular digits, TT color."""

    def test_widget_filename_and_asset_exist(self) -> None:
        from pigeon.np_layout import widget_filename

        name = widget_filename("tt_countdown", 1)
        self.assertEqual(name, "widget_np_01-02-03_tt_countdown.svg")
        svg = (
            Path(__file__).resolve().parents[2]
            / "pigeonAssets"
            / "nowPlaying"
            / name
        )
        self.assertTrue(svg.is_file(), msg=str(svg))

    def test_svg_has_expected_layers(self) -> None:
        import xml.etree.ElementTree as ET

        from pigeon.np_layout import widget_filename

        svg = (
            Path(__file__).resolve().parents[2]
            / "pigeonAssets"
            / "nowPlaying"
            / widget_filename("tt_countdown", 1)
        )
        root = ET.parse(svg).getroot()
        ids = {el.get("id") for el in root.iter() if el.get("id")}
        for layer in ("tt_countdown_background", "tmdb_tt", "divider", "countdown_text"):
            self.assertIn(layer, ids)
        self.assertEqual(root.get("viewBox"), "0 0 398 488")

    def test_prepare_strips_dynamic_layers(self) -> None:
        import xml.etree.ElementTree as ET

        from pigeon.np_layout import widget_filename
        from pigeon.widgets.view_circles import (
            _find_by_key,
            _prepare_tt_countdown_svg,
        )

        svg = (
            Path(__file__).resolve().parents[2]
            / "pigeonAssets"
            / "nowPlaying"
            / widget_filename("tt_countdown", 1)
        )
        root = ET.parse(svg).getroot()
        _prepare_tt_countdown_svg(root)
        for name in ("tmdb_tt", "countdown_text", "divider", "tt_countdown_background"):
            self.assertIsNone(_find_by_key(root, name), msg=name)

    def test_divider_guides_tt_and_countdown(self) -> None:
        from pigeon.np_layout import (
            TT_COUNTDOWN_DIVIDER_LOCAL,
            TT_COUNTDOWN_VIEW_H,
            TT_COUNTDOWN_VIEW_W,
            tt_countdown_time_anchor,
            tt_countdown_tt_box,
        )

        div_x, div_y, div_w, div_h = TT_COUNTDOWN_DIVIDER_LOCAL
        tt_x, tt_y, tt_w, tt_h = tt_countdown_tt_box()
        # TT bottom edge sits on the divider's top edge, centered on the widget.
        self.assertAlmostEqual(tt_y + tt_h, div_y)
        self.assertAlmostEqual(tt_x + tt_w / 2.0, TT_COUNTDOWN_VIEW_W / 2.0)
        # Countdown top line hangs from the divider's bottom edge, centered.
        ax, ay = tt_countdown_time_anchor()
        self.assertAlmostEqual(ax, div_x + div_w / 2.0)
        self.assertAlmostEqual(ay, div_y + div_h)
        self.assertLess(ay, TT_COUNTDOWN_VIEW_H)

    def test_tabular_layout_fixed_digit_cells(self) -> None:
        from pigeon.np_layout import tabular_time_layout

        widths = {"0": 54.0, "1": 31.0, "9": 50.0, ":": 18.0, "-": 38.0}
        cells_a, total_a = tabular_time_layout(
            "-1:00:00", digit_cell_w=54.0, char_widths=widths
        )
        cells_b, total_b = tabular_time_layout(
            "-0:19:19", digit_cell_w=54.0, char_widths=widths
        )
        # Same character classes → identical total width (no jitter while counting).
        self.assertAlmostEqual(total_a, total_b)
        # Every digit occupies the same fixed cell width.
        for cells in (cells_a, cells_b):
            for ch, _x, cell_w in cells:
                if ch.isdigit():
                    self.assertAlmostEqual(cell_w, 54.0)
        # Cells tile left to right with no gaps.
        x = 0.0
        for _ch, cell_x, cell_w in cells_a:
            self.assertAlmostEqual(cell_x, x)
            x += cell_w
        self.assertAlmostEqual(x, total_a)

    def test_dark_tt_is_whitened_bright_tt_unchanged(self) -> None:
        from pigeon.tmdb_tt_contrast import whiten_dark_tt_bgra

        dark = np.zeros((10, 10, 4), dtype=np.uint8)
        dark[:, :, 3] = 255  # opaque black logo
        out = whiten_dark_tt_bgra(dark)
        self.assertTrue((out[:, :, :3] == 255).all())
        self.assertTrue((out[:, :, 3] == 255).all())

        bright = np.full((10, 10, 4), 255, dtype=np.uint8)
        bright[:, :, 0] = 200  # slightly warm white
        out2 = whiten_dark_tt_bgra(bright)
        self.assertTrue(np.array_equal(out2, bright))

        transparent = np.zeros((10, 10, 4), dtype=np.uint8)
        out3 = whiten_dark_tt_bgra(transparent)
        self.assertTrue(np.array_equal(out3, transparent))

    def test_countdown_patch_is_column_stable(self) -> None:
        from pigeon.widgets.view_circles import _tt_countdown_time_patch

        a = _tt_countdown_time_patch("-1:00:00", size_px=80)
        b = _tt_countdown_time_patch("-1:11:11", size_px=80)
        self.assertEqual(a.shape, b.shape)
        self.assertGreater(a.shape[1], 0)

    def test_zone_catalog_offers_tt_countdown_in_portrait_zones(self) -> None:
        from pigeon.widgets.preferences_settings import (
            ZONE_WIDGET_CATALOG,
            preferences_widget_focus_ring,
        )

        for zone in (1, 2, 3):
            self.assertIn("tt_countdown", ZONE_WIDGET_CATALOG[zone])
            self.assertIn("tt_countdown", preferences_widget_focus_ring(zone))
            self.assertIn("tt_countdown_16x9", ZONE_WIDGET_CATALOG[zone])
            self.assertIn("tt_countdown_16x9", preferences_widget_focus_ring(zone))
        for zone in (4, 5):
            self.assertNotIn("tt_countdown", ZONE_WIDGET_CATALOG[zone])
            self.assertNotIn("tt_countdown", preferences_widget_focus_ring(zone))
            self.assertNotIn("tt_countdown_16x9", ZONE_WIDGET_CATALOG[zone])
            self.assertNotIn("tt_countdown_16x9", preferences_widget_focus_ring(zone))

    def test_tt_countdown_assignment_round_trips_normalization(self) -> None:
        from pigeon.widgets.preferences_settings import _normalize_zone_widgets

        norm = _normalize_zone_widgets(
            ("tt_countdown", "poster", "volume", "cast_info", "status_bar")
        )
        self.assertEqual(norm[0], "tt_countdown")

    def test_effective_widgets_keep_tt_countdown_when_active(self) -> None:
        from pigeon.widgets.view_circles import _effective_zone_widgets

        zones = ("tt_countdown", "poster", "volume", "cast_info", "status_bar")
        got = _effective_zone_widgets(
            has_position=True,
            cast_count=5,
            content_active=True,
            zone_widgets=zones,
        )
        self.assertEqual(got[0], "tt_countdown")
        idle = _effective_zone_widgets(
            has_position=False,
            cast_count=0,
            content_active=False,
            zone_widgets=zones,
        )
        self.assertEqual(idle[0], "")

    def test_widget_draws_tt_and_countdown_pixels(self) -> None:
        from pigeon.widgets.view_circles import ViewCirclesWidget

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        from pigeon.widgets import view_circles as vc

        vc._default_zone_widget_assignments = lambda: (  # type: ignore[method-assign]
            "tt_countdown",
            "poster",
            "volume",
            "cast_info",
            "status_bar",
        )
        tt = np.zeros((60, 240, 4), dtype=np.uint8)
        tt[:, :, 3] = 255  # opaque black logo → should be whitened
        widget.update_state(
            progress=0.25,
            elapsed_text="0:30:00",
            remaining_text="1:30:00",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            poster_bgra=np.full((80, 54, 3), 40, dtype=np.uint8),
            service_name="Peacock",
            tt_bgra=tt,
            tt_title="The Drama",
        )
        self.assertEqual(widget._assignments()[0], "tt_countdown")
        frame = widget.bgra_frame()
        self.assertIsNotNone(frame)
        from pigeon.np_layout import NOW_PLAYING_ZONES

        z1 = NOW_PLAYING_ZONES[1]
        zx, zy, zw, zh = z1.xywh
        region = frame[zy : zy + zh, zx : zx + zw]
        # The whitened TT and the countdown digits paint bright pixels in zone 1.
        self.assertGreater(int((region[:, :, :3].max(axis=2) > 200).sum()), 500)


class TtCountdown16x9WidgetTests(unittest.TestCase):
    """Wide TT + countdown (zone 6 or 7): divider guides, leading-zero TRT."""

    def test_widget_filename_and_asset_exist(self) -> None:
        from pigeon.np_layout import widget_filename

        name = widget_filename("tt_countdown_16x9", 6)
        self.assertEqual(name, "widget_np_06-07_tt_countdown.svg")
        svg = (
            Path(__file__).resolve().parents[2]
            / "pigeonAssets"
            / "nowPlaying"
            / name
        )
        self.assertTrue(svg.is_file(), msg=str(svg))

    def test_background_card_hugs_content(self) -> None:
        from pigeon.np_layout import NOW_PLAYING_ZONES
        from pigeon.widgets import view_circles as vc
        from pigeon.widgets.view_circles import ViewCirclesWidget

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        vc._default_zone_widget_assignments = lambda: (  # type: ignore[method-assign]
            "tt_countdown_16x9",
            "poster",
            "volume",
            "cast_info",
            "status_bar",
        )
        tt = np.zeros((80, 400, 4), dtype=np.uint8)
        tt[:, :, :] = (255, 255, 255, 255)
        poster = np.full((80, 54, 3), (0, 180, 80), dtype=np.uint8)
        widget.update_state(
            progress=0.25,
            elapsed_text="0:30:00",
            remaining_text="1:30:00",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            poster_bgra=poster,
            service_name="Peacock",
            tt_bgra=tt,
            tt_title="The Drama",
        )
        frame = widget.bgra_frame()
        self.assertIsNotNone(frame)
        assert frame is not None
        z6 = NOW_PLAYING_ZONES[6]
        zx, zy, zw, zh = z6.xywh
        region = frame[zy : zy + zh, zx : zx + zw, :3]
        # Hugged plate: zone corners show artwork; a black island wraps the ink.
        self.assertGreater(int(region[2, 2].max()), 12)
        self.assertGreater(int(region[2, -3].max()), 12)
        dark = region.max(axis=2) < 10
        self.assertGreater(int(dark.sum()), 400)
        self.assertLess(int(dark.sum()), int(zw * zh * 0.85))
        self.assertGreater(int((region.max(axis=2) > 200).sum()), 500)

    def test_svg_has_divider_guides(self) -> None:
        import xml.etree.ElementTree as ET

        from pigeon.np_layout import widget_filename

        svg = (
            Path(__file__).resolve().parents[2]
            / "pigeonAssets"
            / "nowPlaying"
            / widget_filename("tt_countdown_16x9", 6)
        )
        root = ET.parse(svg).getroot()
        ids = {el.get("id") for el in root.iter() if el.get("id")}
        for layer in (
            "tt_countdown_background",
            "tmdb_tt",
            "dividers",
            "top_divider",
            "left_divider",
            "right_divider",
            "horizontal_divider",
            "countdown_text",
        ):
            self.assertIn(layer, ids)
        self.assertEqual(root.get("viewBox"), "0 0 793 488")

    def test_prepare_strips_guides_and_demo_text(self) -> None:
        import xml.etree.ElementTree as ET

        from pigeon.np_layout import widget_filename
        from pigeon.widgets.view_circles import (
            _find_by_key,
            _prepare_tt_countdown_svg,
        )

        svg = (
            Path(__file__).resolve().parents[2]
            / "pigeonAssets"
            / "nowPlaying"
            / widget_filename("tt_countdown_16x9", 6)
        )
        root = ET.parse(svg).getroot()
        _prepare_tt_countdown_svg(root)
        for name in (
            "tmdb_tt",
            "countdown_text",
            "dividers",
            "top_divider",
            "left_divider",
            "right_divider",
            "horizontal_divider",
            "tt_countdown_background",
        ):
            self.assertIsNone(_find_by_key(root, name), msg=name)

    def test_dividers_bound_tt_and_trt(self) -> None:
        from pigeon.np_layout import (
            TT_COUNTDOWN_16X9_HORIZONTAL_DIVIDER_LOCAL,
            TT_COUNTDOWN_16X9_LEFT_DIVIDER_LOCAL,
            TT_COUNTDOWN_16X9_RIGHT_DIVIDER_LOCAL,
            TT_COUNTDOWN_16X9_TOP_DIVIDER_LOCAL,
            tt_countdown_16x9_time_anchor,
            tt_countdown_16x9_tt_box,
        )

        tx, ty, _tw, th = TT_COUNTDOWN_16X9_TOP_DIVIDER_LOCAL
        lx, _ly, lw, _lh = TT_COUNTDOWN_16X9_LEFT_DIVIDER_LOCAL
        rx, _ry, _rw, _rh = TT_COUNTDOWN_16X9_RIGHT_DIVIDER_LOCAL
        hx, hy, hw, hh = TT_COUNTDOWN_16X9_HORIZONTAL_DIVIDER_LOCAL
        tt_x, tt_y, tt_w, tt_h = tt_countdown_16x9_tt_box()
        self.assertAlmostEqual(tt_y, ty + th)
        self.assertAlmostEqual(tt_x, lx + lw)
        self.assertAlmostEqual(tt_x + tt_w, rx)
        self.assertAlmostEqual(tt_y + tt_h, hy)
        ax, ay = tt_countdown_16x9_time_anchor()
        self.assertAlmostEqual(ax, hx + hw / 2.0)
        self.assertAlmostEqual(ay, hy + hh)

    def test_content_group_lifts_to_top_divider(self) -> None:
        from pigeon.np_layout import (
            TT_COUNTDOWN_16X9_HORIZONTAL_DIVIDER_LOCAL,
            TT_COUNTDOWN_16X9_TOP_DIVIDER_LOCAL,
            tt_countdown_16x9_content_lift,
            tt_countdown_16x9_time_anchor,
        )

        short_h = 80.0
        trt_h = 40.0
        lift = tt_countdown_16x9_content_lift(short_h, trt_height=trt_h)
        _tx, ty, _tw, th = TT_COUNTDOWN_16X9_TOP_DIVIDER_LOCAL
        _hx, hy, _hw, hh = TT_COUNTDOWN_16X9_HORIZONTAL_DIVIDER_LOCAL
        group_top = hy - short_h - lift
        self.assertAlmostEqual(group_top, ty + th)
        _ax, ay = tt_countdown_16x9_time_anchor(lift=lift)
        self.assertAlmostEqual(ay, hy + hh - lift)
        self.assertGreater(lift, 0.0)
        # A TT that already fills the box does not lift.
        box_h = hy - (ty + th)
        self.assertAlmostEqual(tt_countdown_16x9_content_lift(box_h), 0.0)

    def test_portrait_tt_sits_beside_trt(self) -> None:
        from pigeon.np_layout import (
            TT_COUNTDOWN_16X9_VIEW_H,
            TT_COUNTDOWN_16X9_VIEW_W,
            tt_countdown_16x9_portrait_rects,
            tt_countdown_16x9_tt_is_portrait,
        )

        self.assertFalse(tt_countdown_16x9_tt_is_portrait(400, 80))
        self.assertTrue(tt_countdown_16x9_tt_is_portrait(200, 400))
        tt_r, trt_r = tt_countdown_16x9_portrait_rects(200, 400, 180, 70)
        self.assertLess(tt_r[0], 40)
        self.assertGreater(trt_r[0] + trt_r[2], TT_COUNTDOWN_16X9_VIEW_W - 40)
        self.assertLess(tt_r[0] + tt_r[2], trt_r[0])
        tt_cy = tt_r[1] + tt_r[3] / 2.0
        trt_cy = trt_r[1] + trt_r[3] / 2.0
        self.assertAlmostEqual(tt_cy, trt_cy, places=4)
        self.assertGreater(tt_r[3], TT_COUNTDOWN_16X9_VIEW_H * 0.7)

    def test_zone_1_or_2_opens_slot_6_zone_3_opens_slot_7(self) -> None:
        from pigeon.np_layout import (
            apply_tt_countdown_16x9_override,
            tt_countdown_16x9_zone,
        )

        z6 = apply_tt_countdown_16x9_override(
            ("tt_countdown_16x9", "volume", "clock", "cast_info", "status_bar")
        )
        self.assertEqual(tt_countdown_16x9_zone(z6), 6)
        self.assertEqual(z6[0], "tt_countdown_16x9")
        self.assertEqual(z6[1], "")
        self.assertEqual(z6[2], "clock")

        z7 = apply_tt_countdown_16x9_override(
            ("clock", "volume", "tt_countdown_16x9", "cast_info", "status_bar")
        )
        self.assertEqual(tt_countdown_16x9_zone(z7), 7)
        self.assertEqual(z7[0], "clock")
        self.assertEqual(z7[1], "")
        self.assertEqual(z7[2], "tt_countdown_16x9")

    def test_leading_zero_field_is_dropped(self) -> None:
        from pigeon.widgets.view_circles import format_countdown_timecode

        self.assertEqual(format_countdown_timecode("1:30:00"), "-1:30:00")
        self.assertEqual(format_countdown_timecode("1:00:00"), "-1:00:00")
        self.assertEqual(format_countdown_timecode("0:59:59"), "-59:59")
        self.assertEqual(format_countdown_timecode("10:00"), "-10:00")
        self.assertEqual(format_countdown_timecode("09:00"), "-9:00")
        self.assertEqual(format_countdown_timecode("0:09:00"), "-9:00")
        self.assertEqual(format_countdown_timecode("1:00"), "-1:00")
        self.assertEqual(format_countdown_timecode("0:45"), "-45")
        self.assertEqual(format_countdown_timecode("LIVE"), "LIVE")

    def test_widget_draws_in_zone_6(self) -> None:
        from pigeon.np_layout import NOW_PLAYING_ZONES
        from pigeon.widgets import view_circles as vc
        from pigeon.widgets.view_circles import ViewCirclesWidget

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        vc._default_zone_widget_assignments = lambda: (  # type: ignore[method-assign]
            "tt_countdown_16x9",
            "poster",
            "volume",
            "cast_info",
            "status_bar",
        )
        tt = np.zeros((80, 400, 4), dtype=np.uint8)
        tt[:, :, 3] = 255
        widget.update_state(
            progress=0.25,
            elapsed_text="0:30:00",
            remaining_text="1:30:00",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            poster_bgra=np.full((80, 54, 3), 40, dtype=np.uint8),
            service_name="Peacock",
            tt_bgra=tt,
            tt_title="The Drama",
        )
        self.assertEqual(widget._assignments()[0], "tt_countdown_16x9")
        self.assertEqual(widget._assignments()[1], "")
        frame = widget.bgra_frame()
        self.assertIsNotNone(frame)
        z6 = NOW_PLAYING_ZONES[6]
        zx, zy, zw, zh = z6.xywh
        region = frame[zy : zy + zh, zx : zx + zw]
        bright = region[:, :, :3].max(axis=2) > 200
        self.assertGreater(int(bright.sum()), 500)
        # Landscape short TT lifts to the top divider — ink sits in the upper half.
        rows = np.where(bright.any(axis=1))[0]
        self.assertGreater(len(rows), 0)
        mid = (int(rows.min()) + int(rows.max())) / 2.0
        self.assertLess(mid, zh * 0.55)

    def test_portrait_tt_draws_left_trt_right(self) -> None:
        from pigeon.np_layout import NOW_PLAYING_ZONES
        from pigeon.widgets import view_circles as vc
        from pigeon.widgets.view_circles import ViewCirclesWidget

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = ViewCirclesWidget(assets_dir=assets)
        vc._default_zone_widget_assignments = lambda: (  # type: ignore[method-assign]
            "tt_countdown_16x9",
            "poster",
            "volume",
            "cast_info",
            "status_bar",
        )
        tt = np.zeros((400, 200, 4), dtype=np.uint8)
        tt[:, :, :] = (255, 255, 255, 255)
        widget.update_state(
            progress=0.25,
            elapsed_text="0:30:00",
            remaining_text="1:30:00",
            volume_text="-22.5 dB",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            poster_bgra=np.full((80, 54, 3), 40, dtype=np.uint8),
            service_name="Peacock",
            tt_bgra=tt,
            tt_title="The Drama",
        )
        frame = widget.bgra_frame()
        self.assertIsNotNone(frame)
        z6 = NOW_PLAYING_ZONES[6]
        zx, zy, zw, zh = z6.xywh
        region = frame[zy : zy + zh, zx : zx + zw]
        bright = region[:, :, :3].max(axis=2) > 200
        left = bright[:, : zw // 2]
        right = bright[:, zw // 2 :]
        self.assertGreater(int(left.sum()), int(right.sum()))


if __name__ == "__main__":
    unittest.main()
