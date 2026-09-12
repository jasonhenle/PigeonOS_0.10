"""1280×800 settings rebuild: zones, assets, and native frames."""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

import numpy as np

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon.design import DESIGN_H, DESIGN_W  # noqa: E402
from pigeon.settings_layout import (  # noqa: E402
    DUAL_SLOT_A,
    MENU_PLATE_XYWH,
    SETTINGS_MAIN_ZONES,
    dual_slot_design,
    settings_widget_path,
    zone_center_rect,
)
from pigeon.np_layout import LEGACY_UPDATE_MARK_BGR, stamp_legacy_update_mark  # noqa: E402


class SettingsZoneTests(unittest.TestCase):
    def test_zones_fit_canvas(self) -> None:
        for zone in SETTINGS_MAIN_ZONES.values():
            x, y, w, h = zone.xywh
            self.assertGreaterEqual(x, 0, msg=f"zone {zone.index}")
            self.assertGreaterEqual(y, 0, msg=f"zone {zone.index}")
            self.assertLessEqual(x + w, DESIGN_W + 1, msg=f"zone {zone.index}")
            self.assertLessEqual(y + h, DESIGN_H + 1, msg=f"zone {zone.index}")

    def test_exit_is_top_left_header(self) -> None:
        z0 = SETTINGS_MAIN_ZONES[0]
        self.assertAlmostEqual(z0.x, 55.0)
        self.assertAlmostEqual(z0.y, 94.0)
        self.assertLess(z0.y + z0.h, SETTINGS_MAIN_ZONES[1].y)


class SettingsAssetTests(unittest.TestCase):
    def test_required_widgets_exist(self) -> None:
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        for key in (
            "background",
            "exit",
            "search",
            "dual",
            "location",
            "network",
            "password",
            "pigeon",
            "pigeon_logo",
            "list",
            "column_container",
            "device_info",
            "add_player",
            "add_audio",
            "pigeon_page",
            "ui_color_bar",
            "options_bar",
        ):
            path = settings_widget_path(key, assets_dir=assets)
            self.assertTrue(path.is_file(), msg=str(path))


class SettingsRenderTests(unittest.TestCase):
    def test_main_frame_is_design_size(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import render_settings_main_1280_bgra

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        frame = render_settings_main_1280_bgra(
            MainSettingsState(), assets_dir=assets
        )
        self.assertEqual(frame.shape[0], DESIGN_H)
        self.assertEqual(frame.shape[1], DESIGN_W)
        self.assertEqual(frame.shape[2], 4)

    def test_zone2_uses_wordmark_png(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import render_settings_main_1280_bgra

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        frame = render_settings_main_1280_bgra(
            MainSettingsState(), assets_dir=assets
        )
        zx, zy, zw, zh = SETTINGS_MAIN_ZONES[2].xywh
        roi = frame[zy + 40 : zy + 240, zx + 16 : zx + zw - 16, :3]
        cyan = (roi[:, :, 0] > 180) & (roi[:, :, 2] < 80)
        self.assertGreater(int(cyan.sum()), 80)

    def test_zone2_uses_tmdb_tt_when_playing(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            _column_card_rect,
            _paired_device_line_boxes,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        tt = np.zeros((40, 160, 4), dtype=np.uint8)
        tt[:, :, 0] = 255
        tt[:, :, 3] = 255
        state = MainSettingsState()
        state.zone2_tt_bgra = tt
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        cx, cy, cw, ch = _column_card_rect(2)
        pad = 18
        box = (cx + pad, cy + pad, max(8, cw - 2 * pad), max(8, ch - 2 * pad))
        bx, by, bw, bh = box
        roi = frame[by : by + bh, bx : bx + bw, :3]
        blue = (roi[:, :, 0] > 180) & (roi[:, :, 1] < 80) & (roi[:, :, 2] < 80)
        cyan = (roi[:, :, 0] > 180) & (roi[:, :, 2] < 80)
        self.assertGreater(int(blue.sum()), 80)
        self.assertLess(int(cyan.sum() - blue.sum()), 40)
        name_box, _ip_box = _paired_device_line_boxes(box)
        nx, ny, nw, nh = name_box
        below = frame[ny + nh : by + bh, bx : bx + bw, :3]
        blue_below = (below[:, :, 0] > 180) & (below[:, :, 1] < 80) & (below[:, :, 2] < 80)
        self.assertGreater(int(blue_below.sum()), 40)

    def test_tt_ink_flips_black_and_white_with_selection(self) -> None:
        from pigeon.widgets.settings_main_1280 import _tt_ink_for_column

        black = np.zeros((8, 8, 4), dtype=np.uint8)
        black[:, :, 3] = 255
        white = np.full((8, 8, 4), 255, dtype=np.uint8)
        blue = np.zeros((8, 8, 4), dtype=np.uint8)
        blue[:, :, 0] = 255
        blue[:, :, 3] = 255
        off = _tt_ink_for_column(black, selected=False)
        on = _tt_ink_for_column(white, selected=True)
        kept = _tt_ink_for_column(blue, selected=False)
        self.assertTrue(np.all(off[:, :, :3] == 255))
        self.assertTrue(np.all(on[:, :, :3] == 0))
        self.assertTrue(np.all(kept[:, :, 0] == 255))
        self.assertTrue(np.all(kept[:, :, 1] == 0))

    def test_pick_box3_updates_displayed_ip(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState

        st = MainSettingsState()
        st.box3_devices.picked = ("Denon", "10.0.7.116")
        st.box3_devices.active = True
        st.box3_devices.phase = "results"
        st.box3_devices.devices = (("Living room", "10.0.4.88"),)
        st.box3_devices.device_rows = (
            {
                "identifier": "aa:bb:cc:dd:ee:ff",
                "address": "10.0.4.88",
                "name": "Living room",
                "label": "Living room — 10.0.4.88",
                "looks_like_apple_tv": "false",
            },
        )
        row = st.pick_box_device(3)
        self.assertIsNotNone(row)
        self.assertEqual(st.box3_devices.picked, ("Living room", "10.0.4.88"))
        self.assertEqual(st.saved_box_device(3), ("Living room", "10.0.4.88"))

    def test_np_status_bar_can_overlay_settings_main(self) -> None:
        from pigeon.np_layout import NOW_PLAYING_ZONES
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import render_settings_main_1280_bgra
        from pigeon.widgets.view_circles import ViewCirclesWidget

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        frame = render_settings_main_1280_bgra(
            MainSettingsState(), assets_dir=assets
        )
        canvas = frame[:, :, :3].copy()
        widget = ViewCirclesWidget(assets_dir=assets)
        widget.update_state(
            progress=0.55,
            elapsed_text="40:58",
            remaining_text="-1:29:16",
            volume_text="",
            has_now_playing=True,
            has_position=True,
            content_active=True,
            content_mode="video",
            service_name="peacock",
        )
        self.assertTrue(widget.overlay_status_bar(canvas))
        zx, zy, zw, zh = NOW_PLAYING_ZONES[5].xywh
        before = frame[zy : zy + zh, zx : zx + zw, :3]
        after = canvas[zy : zy + zh, zx : zx + zw]
        self.assertGreater(int(np.mean(np.abs(after.astype(int) - before.astype(int)))), 4)

    def test_plate_covers_status_bar_track(self) -> None:
        from pigeon.np_layout import (
            NOW_PLAYING_ZONES,
            STATUS_BAR_SERVICE_LOCAL,
            STATUS_BAR_TRACK,
        )
        from pigeon.settings_layout import MENU_PLATE_XYWH

        zone = NOW_PLAYING_ZONES[5]
        track_bottom = zone.y + STATUS_BAR_TRACK[1] + STATUS_BAR_TRACK[3]
        plate_bottom = MENU_PLATE_XYWH[1] + MENU_PLATE_XYWH[3]
        self.assertGreaterEqual(plate_bottom, track_bottom + 8)
        text_baseline = zone.y + STATUS_BAR_SERVICE_LOCAL[1]
        self.assertGreater(text_baseline, plate_bottom)

    def test_zone2_ip_aligns_with_zone3(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            _column_card_rect,
            _paired_device_line_boxes,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.box2_devices.picked = ("DOWNSTAIRS APPLETV", "10.0.4.26")
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)

        def _text_box(zone_index: int) -> tuple[int, int, int, int]:
            x, y, w, h = _column_card_rect(zone_index)
            return (x + 18, y + 18, max(8, w - 36), max(8, h - 36))

        _name2, ip2 = _paired_device_line_boxes(_text_box(2))
        _name3, ip3 = _paired_device_line_boxes(_text_box(3))
        self.assertEqual(ip2[1], ip3[1])
        self.assertEqual(ip2[3], ip3[3])
        for x, y, w, h in (ip2, ip3):
            roi = frame[y : y + h, x : x + w, :3]
            self.assertGreater(int(np.count_nonzero(np.all(roi > 180, axis=2))), 40)

    def test_idle_column_card_matches_list_rect(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            _LIST_CHROME_VB,
            _column_card_rect,
            _list_rows_rect,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        frame = render_settings_main_1280_bgra(MainSettingsState(), assets_dir=assets)
        zone = SETTINGS_MAIN_ZONES[3]
        card = _column_card_rect(3)
        rows = _list_rows_rect(3)
        listed = zone_center_rect(zone, *_LIST_CHROME_VB)
        self.assertEqual(card[1], rows[1])
        self.assertEqual(card[3], rows[3])
        self.assertGreater(card[1], listed[1] + 20)
        self.assertLess(card[3], listed[3] - 40)
        zx, zy, zw, zh = zone.xywh
        below = frame[card[1] + card[3] + 8, zx + zw // 2, :3]
        # Factory UI blue plate (#4EA6F7 → BGR) shows under the card.
        self.assertGreater(int(below[0]), 60)

    def test_idle_cards_share_list_vertical_center(self) -> None:
        from pigeon.widgets.settings_main_1280 import (
            _column_card_rect,
            _list_rows_rect,
        )

        rows = _list_rows_rect(3)
        self.assertEqual(_column_card_rect(3)[1:], rows[1:])
        self.assertEqual(_column_card_rect(2)[1], rows[1])
        self.assertEqual(_column_card_rect(4)[1], rows[1])
        self.assertEqual(_column_card_rect(2)[3], rows[3])
        self.assertEqual(_column_card_rect(4)[3], rows[3])

    def test_color_tile_uses_export_clip(self) -> None:
        from pigeon.widgets.pigeon_settings import (
            _color_clip_from_root,
            default_pigeon_settings_svg_path,
        )
        import xml.etree.ElementTree as ET

        path = default_pigeon_settings_svg_path(
            Path(__file__).resolve().parents[2] / "pigeonAssets"
        )
        root = ET.parse(path).getroot()
        clip = _color_clip_from_root(root)
        self.assertAlmostEqual(clip[0], 683.18, places=1)
        self.assertAlmostEqual(clip[1], 916.95, places=1)
        self.assertGreater(clip[4], 10.0)
        self.assertNotAlmostEqual(clip[0], 675.2, places=1)

    def test_pigeon_frame_is_design_size(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.pigeon_settings import render_pigeon_settings_bgra

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        frame = render_pigeon_settings_bgra(
            MainSettingsState(), assets_dir=assets
        )
        self.assertEqual(frame.shape[0], DESIGN_H)
        self.assertEqual(frame.shape[1], DESIGN_W)
        self.assertEqual(frame.shape[2], 4)

    def test_ui_color_bar_replaces_back_and_loops(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.pigeon_settings import render_pigeon_settings_bgra
        from pigeon.widgets.ui_color_settings import ui_color_swatch_focus_ring

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        idle = MainSettingsState()
        idle.show_pigeon_settings = True
        idle_frame = render_pigeon_settings_bgra(idle, assets_dir=assets)
        z0 = SETTINGS_MAIN_ZONES[0].xywh
        idle_back = idle_frame[z0[1] : z0[1] + z0[3], z0[0] : z0[0] + z0[2], :3]
        self.assertGreater(int(np.count_nonzero(np.all(idle_back > 200, axis=2))), 40)

        state = MainSettingsState()
        state.show_pigeon_settings = True
        state.open_ui_color()
        opened = render_pigeon_settings_bgra(state, assets_dir=assets)
        picker_back = opened[z0[1] : z0[1] + z0[3], z0[0] : z0[0] + z0[2], :3]
        self.assertLess(
            int(np.count_nonzero(np.all(picker_back > 200, axis=2))),
            int(np.count_nonzero(np.all(idle_back > 200, axis=2))),
        )
        from pigeon.settings_layout import MENU_PLATE_XYWH
        from pigeon.widgets.ui_color_settings import render_ui_color_bar_bgra

        plate_y = int(round(MENU_PLATE_XYWH[1]))
        header = opened[0:plate_y, 101:1178, :3]
        self.assertGreater(int(np.count_nonzero(header.max(axis=2) > 40)), 800)
        bar_only = render_ui_color_bar_bgra(state, assets_dir=assets)
        self.assertEqual(int(bar_only[plate_y:, :, 3].max()), 0)
        from pigeon.widgets.ui_color_settings import apply_color_keys_to_state

        apply_color_keys_to_state(
            state, {"accent": "white", "ui": "blue", "button": "black"}, persist=False
        )
        state.ui_color_focus_index = 0
        start = state.ui_color_focused_id
        first_hex = str(state.theme.ui)
        state.navigate_ui_color(forward=True)
        self.assertNotEqual(state.ui_color_focused_id, start)
        self.assertNotEqual(str(state.theme.ui), first_hex)
        ring = ui_color_swatch_focus_ring("ui")
        for _ in range(len(ring) - 1):
            state.navigate_ui_color(forward=True)
        self.assertEqual(state.ui_color_focused_id, start)
        state.activate_ui_color()
        self.assertFalse(state.show_ui_color)
        closed = render_pigeon_settings_bgra(state, assets_dir=assets)
        closed_back = closed[z0[1] : z0[1] + z0[3], z0[0] : z0[0] + z0[2], :3]
        self.assertGreater(int(np.count_nonzero(np.all(closed_back > 200, axis=2))), 40)

    def test_options_bar_keeps_back_and_toggles(self) -> None:
        from pigeon.settings_layout import MENU_PLATE_XYWH
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.options_settings import options_focus_ring, toggle_option
        from pigeon.widgets.pigeon_settings import render_pigeon_settings_bgra

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        idle = MainSettingsState()
        idle.show_pigeon_settings = True
        idle_frame = render_pigeon_settings_bgra(idle, assets_dir=assets)
        z0 = SETTINGS_MAIN_ZONES[0].xywh
        idle_back = idle_frame[z0[1] : z0[1] + z0[3], z0[0] : z0[0] + z0[2], :3]
        self.assertGreater(int(np.count_nonzero(np.all(idle_back > 200, axis=2))), 40)

        state = MainSettingsState()
        state.show_pigeon_settings = True
        state.options_values = {
            "time_format": "12",
            "clock_format": "digital",
            "temp_format": "f",
            "color_format": "color",
            "idle_standby": 60,
            "clock_saver": "on",
        }
        state.show_options = True
        state.options_focus_index = 0
        opened = render_pigeon_settings_bgra(state, assets_dir=assets)
        picker_back = opened[z0[1] : z0[1] + z0[3], z0[0] : z0[0] + z0[2], :3]
        self.assertGreater(int(np.count_nonzero(np.all(picker_back > 200, axis=2))), 40)
        plate_y = int(round(MENU_PLATE_XYWH[1]))
        header = opened[0:plate_y, 101:1178, :3]
        self.assertGreater(int(np.count_nonzero(header.max(axis=2) > 40)), 800)
        from pigeon.widgets.options_settings import (
            apply_options_svg_state,
            options_bar_board,
            options_bar_xy,
            render_options_bar_bgra,
        )

        bar_only = render_options_bar_bgra(state, assets_dir=assets)
        self.assertEqual(int(bar_only[plate_y:, :, 3].max()), 0)
        back = SETTINGS_MAIN_ZONES[0]
        bx, by, bw, bh = (int(back.x), int(back.y), int(back.w), int(back.h))
        self.assertEqual(int(bar_only[by : by + bh, bx : bx + bw, 3].max()), 0)
        lit = np.where(bar_only[:, :, 3] > 0)
        self.assertGreater(int(lit[1].min()), bx + bw)
        svg_text = (assets / "settings" / "pigeon" / "widget_sp_options.svg").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("options_container", svg_text)
        self.assertNotIn("options_text", svg_text)
        self.assertNotIn("options_container1", svg_text)
        import xml.etree.ElementTree as ET

        from pigeon.widgets.main_settings import _find_by_logical_id

        svg_root = ET.parse(assets / "settings" / "pigeon" / "widget_sp_options.svg").getroot()
        sel = _find_by_logical_id(svg_root, "toggle_selector_1")
        swatch = _find_by_logical_id(svg_root, "red_swatch_button1")
        self.assertIsNotNone(sel)
        self.assertIsNotNone(swatch)
        assert sel is not None and swatch is not None
        self.assertIsNone(_find_by_logical_id(svg_root, "description_container_1"))
        self.assertIsNone(_find_by_logical_id(svg_root, "description_1"))
        opt_a = _find_by_logical_id(svg_root, "option_a_1")
        opt_b = _find_by_logical_id(svg_root, "option_b_1")
        self.assertIsNotNone(opt_a)
        self.assertIsNotNone(opt_b)
        assert opt_a is not None and opt_b is not None
        opt_a_texts = [n for n in opt_a.iter() if n.tag.endswith("text")]
        opt_b_texts = [n for n in opt_b.iter() if n.tag.endswith("text")]
        self.assertGreaterEqual(len(opt_a_texts), 2)
        self.assertGreaterEqual(len(opt_b_texts), 1)
        primary = opt_a_texts[0]
        secondary = opt_a_texts[1]
        before = (
            sel.get("x"),
            sel.get("y"),
            sel.get("width"),
            sel.get("height"),
            sel.get("stroke-width"),
            swatch.get("x"),
            swatch.get("y"),
            swatch.get("width"),
            swatch.get("height"),
            swatch.get("rx"),
            primary.get("font-size"),
            primary.get("text-anchor"),
            primary.get("transform"),
            primary.get("font-family"),
            secondary.get("font-size"),
            secondary.get("font-family"),
            opt_b_texts[0].get("font-size"),
            opt_b_texts[0].get("text-anchor"),
            opt_b_texts[0].get("transform"),
        )
        self.assertEqual(sel.get("display"), "none")
        apply_options_svg_state(svg_root, state)
        after = (
            sel.get("x"),
            sel.get("y"),
            sel.get("width"),
            sel.get("height"),
            sel.get("stroke-width"),
            swatch.get("x"),
            swatch.get("y"),
            swatch.get("width"),
            swatch.get("height"),
            swatch.get("rx"),
            primary.get("font-size"),
            primary.get("text-anchor"),
            primary.get("transform"),
            primary.get("font-family"),
            secondary.get("font-size"),
            secondary.get("font-family"),
            opt_b_texts[0].get("font-size"),
            opt_b_texts[0].get("text-anchor"),
            opt_b_texts[0].get("transform"),
        )
        self.assertEqual(before, after)
        self.assertAlmostEqual(float(swatch.get("x") or 0), float(sel.get("x") or 0), places=1)
        self.assertAlmostEqual(float(swatch.get("y") or 0), float(sel.get("y") or 0), places=1)
        self.assertEqual(sel.get("stroke-width"), "20")
        self.assertAlmostEqual(float(swatch.get("rx") or 0), float(swatch.get("height") or 0) / 2.0, places=1)
        toggle = _find_by_logical_id(svg_root, "toggle_switch1_rectangle")
        self.assertIsNotNone(toggle)
        assert toggle is not None
        self.assertGreater(float(toggle.get("y") or 0), float(swatch.get("y") or 0))
        self.assertEqual(primary.get("font-size"), "17")
        self.assertEqual(secondary.get("font-size"), "14")
        self.assertIn("Extrabold", primary.get("font-family") or "")
        self.assertIn("Semibold", secondary.get("font-family") or "")
        self.assertEqual(primary.get("text-anchor"), "end")
        self.assertEqual(opt_b_texts[0].get("text-anchor"), "start")
        self.assertEqual((opt_b_texts[0].get("fill") or "").lower(), "#000000")
        board = options_bar_board(svg_root)
        x0, _y0 = options_bar_xy(board)
        self.assertGreaterEqual(x0, float(back.x) + float(back.w))

        self.assertEqual(state.options_focused_id, "option_1")
        flipped = toggle_option(1, state.options_values, persist=False)
        self.assertEqual(flipped["time_format"], "24")
        ring = options_focus_ring()
        self.assertEqual(ring[-1], "pigeon_back")
        state.navigate_options(forward=True)
        self.assertEqual(state.options_focused_id, "option_2")
        for _ in range(len(ring) - 2):
            state.navigate_options(forward=True)
        self.assertEqual(state.options_focused_id, "pigeon_back")
        action = state.activate_options()
        self.assertEqual(action, "options_back")
        self.assertFalse(state.show_options)

    def test_color_and_options_preview_on_tile_focus(self) -> None:
        import xml.etree.ElementTree as ET

        from pigeon.settings_layout import MENU_PLATE_XYWH, settings_widget_path
        from pigeon.widgets.main_settings import MainSettingsState, _find_by_logical_id
        from pigeon.widgets.options_settings import apply_options_svg_state
        from pigeon.widgets.pigeon_settings import (
            pigeon_focus_ring,
            render_pigeon_settings_bgra,
        )
        from pigeon.widgets.ui_color_settings import (
            _BAR_BUTTON_IDS,
            apply_ui_color_bar_state,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        ring = pigeon_focus_ring()
        plate_y = int(round(MENU_PLATE_XYWH[1]))

        idle = MainSettingsState()
        idle.show_pigeon_settings = True
        idle.pigeon_focus_index = ring.index("pigeon_back")
        idle_frame = render_pigeon_settings_bgra(idle, assets_dir=assets)
        idle_header = idle_frame[0:plate_y, 192:1178, :3]
        idle_lit = int(np.count_nonzero(idle_header.max(axis=2) > 40))

        color = MainSettingsState()
        color.show_pigeon_settings = True
        color.pigeon_focus_index = ring.index("color_button")
        self.assertFalse(color.show_ui_color)
        color_frame = render_pigeon_settings_bgra(color, assets_dir=assets)
        color_header = color_frame[0:plate_y, 192:1178, :3]
        self.assertGreater(
            int(np.count_nonzero(color_header.max(axis=2) > 40)),
            idle_lit,
        )
        z0 = SETTINGS_MAIN_ZONES[0].xywh
        color_back = color_frame[z0[1] : z0[1] + z0[3], z0[0] : z0[0] + z0[2], :3]
        self.assertGreater(int(np.count_nonzero(np.all(color_back > 200, axis=2))), 40)
        # Preview is dimmed (~50%); activated picker is full strength.
        color.open_ui_color()
        active_color = render_pigeon_settings_bgra(color, assets_dir=assets)
        preview_sum = int(color_header.astype(np.int32).sum())
        active_sum = int(active_color[0:plate_y, 192:1178, :3].astype(np.int32).sum())
        self.assertGreater(active_sum, preview_sum)
        color.close_ui_color()
        color.navigate_pigeon(forward=True)
        self.assertEqual(color.pigeon_focused_id, "info_button")
        self.assertFalse(color.show_ui_color)

        picker_root = ET.parse(settings_widget_path("ui_color_bar", assets_dir=assets)).getroot()
        apply_ui_color_bar_state(picker_root, color, preview=True)
        for bid in _BAR_BUTTON_IDS.values():
            el = _find_by_logical_id(picker_root, bid)
            self.assertIsNotNone(el)
            assert el is not None
            self.assertEqual((el.get("stroke") or "").lower(), "#202020")

        options = MainSettingsState()
        options.show_pigeon_settings = True
        options.pigeon_focus_index = ring.index("general_button")
        self.assertFalse(options.show_options)
        options_frame = render_pigeon_settings_bgra(options, assets_dir=assets)
        options_header = options_frame[0:plate_y, 192:1178, :3]
        self.assertGreater(
            int(np.count_nonzero(options_header.max(axis=2) > 40)),
            idle_lit,
        )
        options_back = options_frame[z0[1] : z0[1] + z0[3], z0[0] : z0[0] + z0[2], :3]
        self.assertGreater(int(np.count_nonzero(np.all(options_back > 200, axis=2))), 40)
        options.open_options()
        active_options = render_pigeon_settings_bgra(options, assets_dir=assets)
        preview_opt_sum = int(options_header.astype(np.int32).sum())
        active_opt_sum = int(
            active_options[0:plate_y, 192:1178, :3].astype(np.int32).sum()
        )
        self.assertGreater(active_opt_sum, preview_opt_sum)
        options.close_options()
        opt_root = ET.parse(assets / "settings" / "pigeon" / "widget_sp_options.svg").getroot()
        apply_options_svg_state(opt_root, options, preview=True)
        for n in range(1, 7):
            sel = _find_by_logical_id(opt_root, f"toggle_selector_{n}")
            self.assertIsNotNone(sel)
            assert sel is not None
            self.assertEqual(sel.get("display"), "none")
        options.navigate_pigeon(forward=True)
        self.assertEqual(options.pigeon_focused_id, "reset_button")
        self.assertFalse(options.show_options)

    def test_legacy_mark_is_not_part_of_native_frame(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import render_settings_main_1280_bgra

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        frame = render_settings_main_1280_bgra(
            MainSettingsState(), assets_dir=assets
        )
        marked = frame.copy()
        stamp_legacy_update_mark(marked)
        self.assertNotEqual(
            tuple(int(v) for v in frame[0, 0, :3]),
            LEGACY_UPDATE_MARK_BGR,
        )
        self.assertEqual(
            tuple(int(v) for v in marked[0, 0, :3]),
            LEGACY_UPDATE_MARK_BGR,
        )

    def test_focused_exit_has_single_visible_label(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import render_settings_main_1280_bgra

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        frame = render_settings_main_1280_bgra(
            MainSettingsState(), assets_dir=assets
        )
        x, y, w, h = SETTINGS_MAIN_ZONES[0].xywh
        roi = frame[y : y + h, x : x + w, :3]
        # Selected capsule is white; Digital-7 EXIT is black on that fill.
        self.assertGreater(int(np.count_nonzero(np.all(roi > 200, axis=2))), 200)
        self.assertGreater(int(np.count_nonzero(np.all(roi < 40, axis=2))), 40)

    def test_background_is_clipped_to_menu_plate(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import render_settings_main_1280_bgra

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        frame = render_settings_main_1280_bgra(
            MainSettingsState(), assets_dir=assets
        )
        self.assertTrue(np.all(frame[8, 8, :3] == 0))
        x, y, w, h = MENU_PLATE_XYWH
        # Sample the plate above the dual bar / column cards (those are black chrome).
        self.assertGreater(int(frame[int(y) + 12, int(x + w * 0.5), :3].max()), 20)
        self.assertTrue(np.all(frame[int(y) - 6, int(x + w * 0.5), :3] == 0))

    def test_long_location_stays_inside_dual_slot(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import render_settings_main_1280_bgra

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.location_name = "DOWNSTAIRS MEDIA THEATER ROOM"
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        from pigeon.widgets.settings_main_1280 import _location_field_boxes

        loc_box = dual_slot_design(DUAL_SLOT_A)
        _dots_box, text_box = _location_field_boxes(loc_box)
        tx, ty, tw, th = text_box
        # Fitted label must leave the last columns of the text box mostly empty.
        tail = frame[ty : ty + th, tx + tw - 6 : tx + tw, :3]
        self.assertLess(int(np.count_nonzero(np.all(tail > 180, axis=2))), 8)
        inside = frame[ty : ty + th, tx : tx + tw, :3]
        self.assertGreater(int(inside.max()), 40)

    def test_long_device_name_stays_inside_zone(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import render_settings_main_1280_bgra

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.box2_devices.picked = ("DOWNSTAIRS DENON AV RECEIVER", "10.0.7.123")
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        zx, zy, zw, zh = SETTINGS_MAIN_ZONES[3].xywh
        gutter = frame[zy : zy + zh, min(DESIGN_W - 1, zx + zw + 2) : min(DESIGN_W, zx + zw + 6), :3]
        # Slants may color the gutter; overflowing Digital-7 would paint near-white there.
        self.assertFalse(np.any(np.all(gutter > 180, axis=2)))
        self.assertGreater(int(frame[zy : zy + zh, zx : zx + zw, :3].max()), 40)

    def test_column_container_inverts_on_focus(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import render_settings_main_1280_bgra

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        idle = MainSettingsState()
        idle.box2_devices.picked = ("APPLETV", "10.0.7.1")
        idle_frame = render_settings_main_1280_bgra(idle, assets_dir=assets)
        focused = MainSettingsState()
        focused.box2_devices.picked = ("APPLETV", "10.0.7.1")
        focused.ensure_focus_ring()
        focused.focus_index = focused.focus_ring.index("main_box2_button")
        on_frame = render_settings_main_1280_bgra(focused, assets_dir=assets)
        zx, zy, zw, zh = SETTINGS_MAIN_ZONES[3].xywh
        cx, cy = zx + zw // 2, zy + zh // 2
        self.assertLess(int(idle_frame[cy, cx, :3].max()), 80)
        self.assertGreater(int(on_frame[cy, cx, :3].min()), 180)

    def test_bright_uses_gray_background_and_black_box_strokes(self) -> None:
        from pigeon.widgets.options_settings import apply_ui_bright_bgr, ui_chrome_hex
        from pigeon.widgets.settings_main_1280 import (
            _chrome_stroke,
            _find,
            _load_widget,
            _set_fill,
            _settings_main_bg,
            _style_column_container,
            clear_settings_main_compose_cache,
        )
        from pigeon.widgets.settings_theme_background import settings_background_ui_hex
        from pigeon.widgets.ui_color_settings import hex_for_color_key, write_ui_color_keys

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        write_ui_color_keys({"ui": "bright"}, persist=False)
        clear_settings_main_compose_cache()
        try:
            gray = hex_for_color_key("ui", "gray").lower()
            self.assertEqual(settings_background_ui_hex("#4EA6F7").lower(), gray)
            self.assertEqual(_chrome_stroke().lower(), "#000000")
            self.assertEqual(ui_chrome_hex().lower(), "#000000")
            col = _load_widget("column_container", assets_dir=assets)
            _style_column_container(col, selected=False)
            self.assertEqual((_find(col, "sm_container").get("stroke") or "").lower(), "#000000")
            dual = _load_widget("dual", assets_dir=assets)
            _set_fill(_find(dual, "dual_container"), "#202020", stroke=_chrome_stroke())
            self.assertEqual((_find(dual, "dual_container").get("stroke") or "").lower(), "#000000")

            bg = _settings_main_bg(ui_hex="#4EA6F7", assets_dir=assets)
            bgr = bg[:, :, :3]
            self.assertEqual(tuple(int(v) for v in apply_ui_bright_bgr(bgr)[0, 0]), tuple(int(v) for v in bgr[0, 0]))
            self.assertGreater(int(bgr[8, 8].min()), 200)
            plate = bgr[220:620, 120:1160]
            mx = plate.max(axis=2)
            mn = plate.min(axis=2)
            slant = mx > 30
            self.assertGreater(int(slant.sum()), 2000)
            self.assertLess(float((mx - mn)[slant].mean()), 24.0)
            self.assertGreater(float(plate[slant].mean()), 40.0)
        finally:
            from pigeon.compositing import clear_bright_slant_mask

            write_ui_color_keys({"ui": "blue"}, persist=False)
            clear_bright_slant_mask()
            clear_settings_main_compose_cache()

    def test_list_row_text_uses_svg_font_size(self) -> None:
        from pigeon.widgets.settings_main_1280 import (
            _LIST_CHROME_VB,
            _LIST_DEVICE_SIZE_SVG,
            _LIST_IP_SIZE_SVG,
            _LIST_ROW_SVG,
        )

        zone = SETTINGS_MAIN_ZONES[3]
        _x, _y, w, h = zone_center_rect(zone, *_LIST_CHROME_VB)
        sy = h / _LIST_CHROME_VB[1]
        row_h = _LIST_ROW_SVG[0][3] * sy
        device_px = _LIST_DEVICE_SIZE_SVG * sy
        ip_px = _LIST_IP_SIZE_SVG * sy
        self.assertAlmostEqual(device_px, 32.0 * sy, delta=0.2)
        self.assertAlmostEqual(ip_px, 19.0 * sy, delta=0.2)
        self.assertLess(device_px, row_h + 1)
        self.assertLess(device_px, 50)

    def test_search_glyph_uses_stem_clips(self) -> None:
        from pigeon.widgets.search_spinner import render_search_glyph_bgra

        glyph = render_search_glyph_bgra(size=180, color_bgr=(255, 255, 255))
        alpha = glyph[:, :, 3]
        self.assertGreater(int((alpha > 40).sum()), 200)
        # Ring hole stays empty.
        self.assertLess(int(alpha[90, 90]), 40)
        # Opposite stems (≈45° / 225°), with gaps on the other diagonal.
        import math

        ring = []
        for ang in (45.0, 120.0, 225.0, 300.0):
            rad = math.radians(ang)
            x = int(round(90 + 48 * math.cos(rad)))
            y = int(round(90 + 48 * math.sin(rad)))
            ring.append(int(alpha[y, x]))
        self.assertGreater(ring[0], 80)
        self.assertGreater(ring[2], 80)
        self.assertLess(ring[1], 40)
        self.assertLess(ring[3], 40)

    def test_pigeon_wifi_and_update_icons_paint(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.pigeon_settings import render_pigeon_settings_bgra

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        frame = render_pigeon_settings_bgra(MainSettingsState(), assets_dir=assets)
        # WiFi button (board 870.89,1165.09 → canvas after the -20 plate shift).
        wifi = frame[478:560, 300:430, :3]
        light = np.all(wifi > 160, axis=2)
        self.assertGreater(int(light.sum()), 40)
        # Update download bar (board 1648.85,982.31).
        bar = frame[280:310, 1060:1160, :3]
        self.assertGreater(int(np.count_nonzero(np.all(bar > 180, axis=2))), 40)
        # HDMI slot line stays dark on the white plug.
        slot = frame[536:548, 698:790, :3]
        self.assertGreater(int(np.count_nonzero(np.all(slot < 40, axis=2))), 20)

    def test_list_hides_column_container_and_short_list_arrows(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            _LIST_CHROME_VB,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.box2_devices.phase = "results"
        state.box2_devices.active = True
        state.box2_devices.devices = (
            ("APPLETV", "10.0.7.1"),
            ("ROKU", "10.0.7.22"),
            ("DENON", "10.0.7.40"),
        )
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        zone = SETTINGS_MAIN_ZONES[3]
        lx, ly, lw, lh = zone_center_rect(zone, *_LIST_CHROME_VB)
        # Gap between row a and b — red slants, not a white focused container plate.
        gap_y = ly + int(round(lh * (142.52 / _LIST_CHROME_VB[1])))
        gap = frame[gap_y, lx + lw // 2, :3]
        self.assertLess(int(gap.min()), 180)
        # Arrow tip sits at the top of the list artboard; short lists omit it.
        ax = lx + int(round(171.1 * lw / _LIST_CHROME_VB[0]))
        ay = ly + int(round(10.0 * lh / _LIST_CHROME_VB[1]))
        tip = frame[ay, ax, :3]
        self.assertFalse(np.all(tip > 200))

    def test_activating_column_shows_list_widget(self) -> None:
        from pigeon.widgets.box_device_search import box_devices_with_special_rows
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            _LIST_CHROME_VB,
            _LIST_ROW_SVG,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.box2_devices.phase = "results"
        state.box2_devices.active = True
        state.box2_devices.devices = box_devices_with_special_rows(
            (("APPLETV", "10.0.7.1"), ("ROKU", "10.0.7.22"))
        )
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        zone = SETTINGS_MAIN_ZONES[3]
        lx, ly, lw, lh = zone_center_rect(zone, *_LIST_CHROME_VB)
        rx, ry, rw, rh = _LIST_ROW_SVG[0]
        # Left inset of the selected pill (fill), not the black device label.
        cx = lx + int(round((rx + 28) * lw / _LIST_CHROME_VB[0]))
        cy = ly + int(round((ry + rh * 0.5) * lh / _LIST_CHROME_VB[1]))
        self.assertGreater(int(frame[cy, cx, :3].min()), 180)
        names = [n for n, _ip in state.box2_devices.devices]
        self.assertEqual(names.count("ENTER IP"), 1)
        self.assertEqual(names.count("CANCEL"), 1)
        ax = lx + int(round(171.1 * lw / _LIST_CHROME_VB[0]))
        ay = ly + int(round(10.0 * lh / _LIST_CHROME_VB[1]))
        self.assertFalse(np.all(frame[ay, ax, :3] > 200))
        # Unused fifth pill stays off — red plate, not another ENTER IP/CANCEL.
        ex, ey, ew, eh = _LIST_ROW_SVG[4]
        unused = frame[
            ly + int(round((ey + eh * 0.5) * lh / _LIST_CHROME_VB[1])),
            lx + int(round((ex + ew * 0.5) * lw / _LIST_CHROME_VB[0])),
            :3,
        ]
        self.assertLess(int(unused.min()), 80)

    def test_device_rows_left_align_ip_and_right_align_name(self) -> None:
        from pigeon.widgets.box_device_search import box_devices_with_special_rows
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            _LIST_CHROME_VB,
            _LIST_ROW_SVG,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.box2_devices.phase = "results"
        state.box2_devices.active = True
        state.box2_devices.devices = box_devices_with_special_rows(
            (("JASON'S MACBOOK AIR", "10.0.4.28"), ("THEATER", "10.0.7.123"))
        )
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        zone = SETTINGS_MAIN_ZONES[3]
        lx, ly, lw, lh = zone_center_rect(zone, *_LIST_CHROME_VB)
        sx = lw / _LIST_CHROME_VB[0]
        sy = lh / _LIST_CHROME_VB[1]

        def _row_box(index: int) -> tuple[int, int, int, int]:
            rx, ry, rw, rh = _LIST_ROW_SVG[index]
            return (
                lx + int(round(rx * sx)),
                ly + int(round(ry * sy)),
                int(round(rw * sx)),
                int(round(rh * sy)),
            )

        def _ink_xs(index: int) -> np.ndarray:
            x, y, w, h = _row_box(index)
            band = frame[y + h // 2, x : x + w, :3]
            on = band.min(axis=1) < 80 if index == 0 else band.max(axis=1) > 180
            return np.flatnonzero(on)

        long_xs = _ink_xs(0)
        short_xs = _ink_xs(1)
        self.assertGreater(int(long_xs.size), 8)
        self.assertGreater(int(short_xs.size), 8)
        row_w = _row_box(0)[2]
        # IPs share the left column; names share the right edge even when short.
        self.assertLess(int(long_xs[0]), int(round(row_w * 0.22)))
        self.assertLess(int(short_xs[0]), int(round(row_w * 0.22)))
        self.assertGreater(int(long_xs[-1]), int(round(row_w * 0.82)))
        self.assertGreater(int(short_xs[-1]), int(round(row_w * 0.82)))
        self.assertLess(abs(int(long_xs[-1]) - int(short_xs[-1])), 24)

    def test_list_star_marks_current_device(self) -> None:
        from pigeon.widgets.box_device_search import box_devices_with_special_rows
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            _LIST_CHROME_VB,
            _LIST_NAME_X_SVG,
            _LIST_ROW_SVG,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.box2_devices.phase = "results"
        state.box2_devices.active = True
        state.box2_devices.picked = ("THEATER", "10.0.7.123")
        state.box2_devices.devices = box_devices_with_special_rows(
            (("APPLETV", "10.0.7.1"), ("THEATER", "10.0.7.123"))
        )
        state.box2_devices.row = 0
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        zone = SETTINGS_MAIN_ZONES[3]
        lx, ly, lw, lh = zone_center_rect(zone, *_LIST_CHROME_VB)
        sx = lw / _LIST_CHROME_VB[0]
        sy = lh / _LIST_CHROME_VB[1]
        rx, ry, rw, rh = _LIST_ROW_SVG[1]
        split = max(int(round(_LIST_NAME_X_SVG * sx)), int(round(rw * sx * 0.36)))
        cx = lx + int(round(rx * sx)) + split - 8
        cy = ly + int(round(ry * sy)) + int(round(rh * sy)) // 2
        sample = frame[cy - 3 : cy + 4, cx - 3 : cx + 4, :3]
        self.assertGreater(int(np.count_nonzero(sample.max(axis=2) > 180)), 6)

        state.box2_devices.row = 1
        on = render_settings_main_1280_bgra(state, assets_dir=assets)
        sample_on = on[cy - 3 : cy + 4, cx - 3 : cx + 4, :3]
        self.assertGreater(int(np.count_nonzero(np.all(sample_on < 60, axis=2))), 6)

    def test_scanning_column_draws_status_bar_then_list(self) -> None:
        from pigeon.widgets.box_device_search import box_devices_with_special_rows
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            _LIST_CHROME_VB,
            _LIST_ROW_SVG,
            _column_line_boxes,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.box2_devices.active = True
        state.box2_devices.phase = "scanning"
        state.box2_devices.scanning = True
        state.box2_devices.scan_started_mono = 0.0
        scanning = render_settings_main_1280_bgra(state, assets_dir=assets)
        name_box, bar_box = _column_line_boxes(3)
        bx, by, bw, bh = bar_box
        scan_roi = scanning[by : by + bh, bx : bx + bw, :3]
        self.assertGreater(int(np.count_nonzero(scan_roi.max(axis=2) > 40)), 80)
        nx, ny, nw, nh = name_box
        label = scanning[ny : ny + nh, nx : nx + nw, :3]
        self.assertGreater(int(np.count_nonzero(np.all(label < 60, axis=2))), 80)

        state.box2_devices.scanning = False
        state.box2_devices.phase = "results"
        state.box2_devices.devices = box_devices_with_special_rows(
            (("APPLETV", "10.0.7.1"),)
        )
        results = render_settings_main_1280_bgra(state, assets_dir=assets)
        zone = SETTINGS_MAIN_ZONES[3]
        listed = zone_center_rect(zone, *_LIST_CHROME_VB)
        lx, ly, lw, lh = listed
        sx = lw / _LIST_CHROME_VB[0]
        sy = lh / _LIST_CHROME_VB[1]
        rx, ry, rw, rh = _LIST_ROW_SVG[0]
        row = results[
            ly + int(round(ry * sy)) : ly + int(round((ry + rh) * sy)),
            lx + int(round(rx * sx)) : lx + int(round((rx + rw) * sx)),
            :3,
        ]
        self.assertGreater(int(np.count_nonzero(np.all(row > 180, axis=2))), 80)

    def test_scanning_zone4_says_finding_audio(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            _column_line_boxes,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"

        def _name_ink(zone_index: int, box_attr: str) -> np.ndarray:
            state = MainSettingsState()
            panel = getattr(state, box_attr)
            panel.active = True
            panel.phase = "scanning"
            panel.scanning = True
            frame = render_settings_main_1280_bgra(state, assets_dir=assets)
            nx, ny, nw, nh = _column_line_boxes(zone_index)[0]
            vis = np.all(frame[ny : ny + nh, nx : nx + nw, :3] < 60, axis=2)
            return vis

        players = _name_ink(3, "box2_devices")
        audio = _name_ink(4, "box3_devices")
        self.assertGreater(int(players.sum()), 80)
        self.assertGreater(int(audio.sum()), 80)
        self.assertGreater(int(np.count_nonzero(players != audio)), 40)

    def test_add_labels_match_device_info_slots(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            _column_card_rect,
            _paired_device_line_boxes,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"

        def _name_box(zone_index: int) -> tuple[int, int, int, int]:
            x, y, w, h = _column_card_rect(zone_index)
            pad = 18
            return _paired_device_line_boxes((x + pad, y + pad, w - 2 * pad, h - 2 * pad))[0]

        def _ink_ys(frame: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
            x, y, w, h = box
            vis = frame[y : y + h, x : x + w, :3].max(axis=2) > 180
            return np.where(vis.any(axis=1))[0]

        idle = render_settings_main_1280_bgra(MainSettingsState(), assets_dir=assets)
        paired_state = MainSettingsState()
        paired_state.box2_devices.picked = ("SKYNET", "10.0.0.1")
        paired = render_settings_main_1280_bgra(paired_state, assets_dir=assets)
        add_ys = _ink_ys(idle, _name_box(4))
        device_ys = _ink_ys(paired, _name_box(3))
        self.assertGreater(int(add_ys.size), 8)
        self.assertGreater(int(device_ys.size), 8)
        self.assertLess(abs(int(add_ys[0]) - int(device_ys[0])), 12)
        self.assertLess(abs(int(add_ys[-1]) - int(device_ys[-1])), 18)

    def test_scan_status_bar_advances_through_widget_cache(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsWidget
        from pigeon.widgets.settings_main_1280 import _column_line_boxes

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = MainSettingsWidget(assets_dir=assets)
        st = widget.state
        st.ensure_focus_ring()
        st.focus_index = st.focus_ring.index("main_box2_button")
        st.start_box_device_scan(2)
        now = time.monotonic()
        st.box2_devices.scan_started_mono = now - 0.4
        early = widget.bgra_frame().copy()
        st.box2_devices.scan_started_mono = now - 14.0
        late = widget.bgra_frame()
        _name, bar = _column_line_boxes(3)
        row_y = bar[1] + bar[3] // 2
        x0, x1 = bar[0], bar[0] + bar[2]
        early_dark = int(np.count_nonzero(np.all(early[row_y, x0:x1, :3] < 60, axis=1)))
        late_dark = int(np.count_nonzero(np.all(late[row_y, x0:x1, :3] < 60, axis=1)))
        self.assertGreater(late_dark, early_dark + 20)

    def test_location_picker_uses_three_room_cards(self) -> None:
        from pigeon.settings_layout import DUAL_SLOT_A
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            _column_card_rect,
            _location_field_boxes,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.enter_location_picker()
        state.location_slots = (("a", "ROOM 1"), ("b", "Bedroom"), ("c", "ROOM 3"))
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        z2 = SETTINGS_MAIN_ZONES[2].xywh
        z4 = SETTINGS_MAIN_ZONES[4].xywh
        self.assertEqual(SETTINGS_MAIN_ZONES[2].h, SETTINGS_MAIN_ZONES[3].h)
        self.assertEqual(SETTINGS_MAIN_ZONES[3].h, SETTINGS_MAIN_ZONES[4].h)
        self.assertGreater(int(frame[z2[1] + z2[3] // 2, z2[0] + z2[2] // 2, :3].max()), 10)
        self.assertGreater(int(frame[z4[1] + z4[3] // 2, z4[0] + z4[2] // 2, :3].max()), 10)
        # Room cards are in the focus ring so they can be activated.
        self.assertIn(
            state.focused_id,
            ("main_box1_button", "main_box2_button", "main_box3_button"),
        )
        loc_x, loc_y, loc_w, loc_h = dual_slot_design(DUAL_SLOT_A)
        loc_mid = frame[loc_y + loc_h // 2, loc_x + loc_w // 2, :3]
        # Location slot is idle while a room card holds focus.
        self.assertLess(int(loc_mid.max()), 80)
        focused_zone = {"main_box1_button": 2, "main_box2_button": 3, "main_box3_button": 4}[
            state.focused_id
        ]
        for zone_index in (2, 3, 4):
            cx, cy, cw, ch = _column_card_rect(zone_index)
            card = frame[cy + ch // 2, cx + cw // 2, :3]
            if zone_index == focused_zone:
                self.assertGreater(int(card.min()), 180)
            else:
                self.assertLess(int(card.max()), 80)
        dots_box, _text = _location_field_boxes(dual_slot_design(DUAL_SLOT_A))
        dx, dy, dw, dh = dots_box
        dots = frame[dy : dy + dh, dx : dx + dw, :3]
        self.assertGreater(int(np.count_nonzero(np.all(dots < 40, axis=2))), 20)
        red = (dots[:, :, 2] > 140) & (dots[:, :, 1] < 90) & (dots[:, :, 0] < 90)
        self.assertEqual(int(red.sum()), 0)

    def test_columns_sit_above_plate_bottom(self) -> None:
        from pigeon.settings_layout import menu_plate_chrome_bottom

        plate_bottom = menu_plate_chrome_bottom()
        for zone in (SETTINGS_MAIN_ZONES[2], SETTINGS_MAIN_ZONES[3], SETTINGS_MAIN_ZONES[4]):
            self.assertLess(zone.y + zone.h, plate_bottom - 8)

    def test_sign_out_text_uses_zone_1b(self) -> None:
        from pigeon.settings_layout import DUAL_SLOT_B
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_keyboard import KeyboardState
        from pigeon.widgets.settings_main_1280 import render_settings_main_1280_bgra

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.selected_wifi_ssid = "HENLI"
        state.keyboard = KeyboardState(target="wifi_logout", buffer="")
        state.ensure_focus_ring()
        if "main_dual_network_button" in state.focus_ring:
            state.focus_index = state.focus_ring.index("main_dual_network_button")
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        x, y, w, h = dual_slot_design(DUAL_SLOT_B)
        roi = frame[y : y + h, x : x + w, :3]
        self.assertGreater(int(np.count_nonzero(np.all(roi < 40, axis=2))), 40)
        z0 = SETTINGS_MAIN_ZONES[0].xywh
        z1 = SETTINGS_MAIN_ZONES[1].xywh
        yes_no = frame[
            z0[1] : z0[1] + z0[3],
            z1[0] + z1[2] // 2 - 160 : z1[0] + z1[2] // 2 + 160,
            :3,
        ]
        self.assertGreater(int(np.count_nonzero(np.all(yes_no > 200, axis=2))), 80)

    def test_sign_out_yes_no_navigation_repaints(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_keyboard import KeyboardMode, open_keyboard
        from pigeon.widgets.settings_main_1280 import render_settings_main_1280_bgra

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.selected_wifi_ssid = "HENLI"
        state.keyboard = open_keyboard(
            target="wifi_logout",
            mode=KeyboardMode.YES_NO,
            assets_dir=assets,
        )
        yes = render_settings_main_1280_bgra(state, assets_dir=assets)
        state.navigate(forward=True)
        no = render_settings_main_1280_bgra(state, assets_dir=assets)
        z0 = SETTINGS_MAIN_ZONES[0].xywh
        z1 = SETTINGS_MAIN_ZONES[1].xywh
        band = (
            slice(z0[1], z0[1] + z0[3]),
            slice(z1[0] + z1[2] // 2 - 160, z1[0] + z1[2] // 2 + 160),
            slice(0, 3),
        )
        self.assertGreater(int(np.count_nonzero(yes[band] != no[band])), 80)
        self.assertGreaterEqual(len(state.keyboard.focus_ring), 2)

    def test_wifi_logo_is_stroked_not_filled(self) -> None:
        from pigeon.settings_layout import DUAL_SLOT_B
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            _network_field_boxes,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.selected_wifi_ssid = "HENLI"
        state.wifi_level = 3
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        wifi_box, _text = _network_field_boxes(dual_slot_design(DUAL_SLOT_B))
        x, y, w, h = wifi_box
        roi = frame[y : y + h, x : x + w, :3]
        # Factory UI blue (#4EA6F7 → BGR ≈ 247,166,78).
        blue = (roi[:, :, 0] > 140) & (roi[:, :, 1] > 100) & (roi[:, :, 2] < 120)
        self.assertGreater(int(blue.sum()), 30)
        self.assertLess(float(blue.mean()), 0.28)


class SettingsKeyboard1280Tests(unittest.TestCase):
    def test_native_keyboards_use_shared_bottom_row(self) -> None:
        from pigeon.widgets.settings_keyboard import (
            KeyAction,
            KeyboardMode,
            open_keyboard,
            render_keyboard_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        network = open_keyboard(target="network", assets_dir=assets)
        letters = {k.char for k in network.focus_ring if k.action == KeyAction.CHAR}
        self.assertTrue({"q", "w", "e", "p"}.issubset(letters))
        self.assertTrue(network.include_bottom_row)
        self.assertTrue(any(k.action == KeyAction.GO for k in network.focus_ring))
        bottom_ids = [
            k.button_id
            for k in network.focus_ring
            if k.button_id
            in {
                "lower_cancel",
                "lower_SYM",
                "lower_123",
                "lower_space",
                "lower_del",
                "lower_enter",
            }
        ]
        self.assertEqual(
            bottom_ids,
            ["lower_cancel", "lower_SYM", "lower_123", "lower_space", "lower_del", "lower_enter"],
        )
        frame = render_keyboard_bgra(network, assets_dir=assets)
        self.assertEqual(frame.shape[0], DESIGN_H)
        self.assertEqual(frame.shape[1], DESIGN_W)
        self.assertGreater(int((frame[:, :, 3] > 10).sum()), 8000)

        pin = open_keyboard(target="pin", assets_dir=assets)
        self.assertEqual(pin.mode, KeyboardMode.NUMERIC_PIN)
        self.assertTrue(pin.include_bottom_row)
        self.assertTrue(any(k.action == KeyAction.GO for k in pin.focus_ring))
        self.assertTrue({k.char for k in pin.focus_ring if k.action == KeyAction.CHAR} >= set("0123456789"))

        ip = open_keyboard(target="device_ip", assets_dir=assets)
        self.assertEqual(ip.mode, KeyboardMode.NUMERIC_IP)
        self.assertFalse(ip.include_bottom_row)
        chars = {k.char for k in ip.focus_ring if k.action == KeyAction.CHAR}
        self.assertTrue(set("0123456789.").issubset(chars))
        self.assertTrue(any(k.action == KeyAction.GO for k in ip.focus_ring))
        self.assertTrue(any(k.action == KeyAction.CANCEL for k in ip.focus_ring))
        self.assertTrue(any(k.action == KeyAction.DELETE for k in ip.focus_ring))
        digit_keys = {
            k.char: k.button_id
            for k in ip.focus_ring
            if k.action == KeyAction.CHAR and k.char.isdigit()
        }
        self.assertEqual(set(digit_keys), set("0123456789"))
        self.assertNotEqual(digit_keys["8"], digit_keys["9"])

    def test_ip_keyboard_keeps_8_and_9_apart(self) -> None:
        import xml.etree.ElementTree as ET

        from pigeon.widgets.main_settings import _find_by_logical_id, keyboard_svg_path
        from pigeon.widgets.settings_keyboard import (
            _center_integrated_pad_labels,
            open_keyboard,
            render_keyboard_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        root = ET.parse(keyboard_svg_path("keyboard_ip.svg", assets_dir=assets)).getroot()
        eight = _find_by_logical_id(root, "numeric_8")
        nine = _find_by_logical_id(root, "numeric_9")
        self.assertIsNotNone(eight)
        self.assertIsNotNone(nine)
        assert eight is not None and nine is not None
        self.assertIn("8", "".join(eight.itertext()))
        self.assertIn("9", "".join(nine.itertext()))
        self.assertNotIn("9", "".join(eight.itertext()))
        self.assertNotIn("8", "".join(nine.itertext()))
        _center_integrated_pad_labels(root)
        self.assertIn("8", "".join(eight.itertext()))
        self.assertIn("9", "".join(nine.itertext()))
        eight_icon = _find_by_logical_id(root, "numeric_8_icon")
        nine_icon = _find_by_logical_id(root, "numeric_9_icon")
        self.assertIsNotNone(eight_icon)
        self.assertIsNotNone(nine_icon)
        assert eight_icon is not None and nine_icon is not None
        self.assertNotEqual(eight_icon.get("transform"), nine_icon.get("transform"))

        state = open_keyboard(target="device_ip", assets_dir=assets)
        frame = render_keyboard_bgra(state, assets_dir=assets)
        self.assertGreater(int((frame[:, :, 3] > 10).sum()), 4000)

    def test_upper_and_lower_qwerty_share_key_origin(self) -> None:
        from pigeon.widgets.settings_keyboard import (
            KeyboardMode,
            _CLUSTER_KEY_ROW_Y,
            _CLUSTER_SCALE,
            _cluster_xy,
            open_keyboard,
            render_keyboard_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        lower = open_keyboard(target="network", assets_dir=assets)
        self.assertEqual(lower.mode, KeyboardMode.QWERTY_LOWER)
        upper = open_keyboard(target="network", assets_dir=assets)
        upper.set_mode(KeyboardMode.QWERTY_UPPER, assets_dir=assets)
        lf = render_keyboard_bgra(lower, assets_dir=assets)
        uf = render_keyboard_bgra(upper, assets_dir=assets)
        _cx, cy = _cluster_xy()
        row_h = int(round(_CLUSTER_KEY_ROW_Y * _CLUSTER_SCALE))
        lower_mask = lf[cy : cy + row_h, :, 3] > 10
        upper_mask = uf[cy : cy + row_h, :, 3] > 10
        self.assertTrue(lower_mask.any())
        self.assertTrue(upper_mask.any())
        lx = int(np.where(lower_mask.any(axis=0))[0][0])
        ux = int(np.where(upper_mask.any(axis=0))[0][0])
        ly = int(np.where(lower_mask.any(axis=1))[0][0])
        uy = int(np.where(upper_mask.any(axis=1))[0][0])
        self.assertLessEqual(abs(lx - ux), 2)
        self.assertLessEqual(abs(ly - uy), 2)

    def test_shift_glyph_shapes_stay_visible(self) -> None:
        import xml.etree.ElementTree as ET

        from pigeon.widgets.main_settings import (
            SettingsTheme,
            _find_by_logical_id,
            keyboard_svg_path,
        )
        from pigeon.widgets.settings_keyboard import (
            KeyAction,
            apply_keyboard_selection,
            open_keyboard,
            render_keyboard_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        root = ET.parse(keyboard_svg_path("keyboard_lower.svg", assets_dir=assets)).getroot()
        shift = _find_by_logical_id(root, "lower_shift")
        self.assertIsNotNone(shift)
        assert shift is not None
        apply_keyboard_selection(
            root,
            focused_button_id="lower_q",
            theme=SettingsTheme(),
            button_ids={"lower_shift", "lower_q"},
        )
        polys = [n for n in shift.iter() if n.tag.endswith("polygon")]
        stems = [
            n
            for n in shift.iter()
            if n.tag.endswith("rect") and float(n.get("rx") or 0) < 8.0
        ]
        self.assertTrue(polys)
        self.assertTrue(stems)
        for node in polys + stems:
            fill = (node.get("fill") or "").lower()
            self.assertIn(fill, ("#fff", "#ffffff", "white"))

        state = open_keyboard(target="network", assets_dir=assets)
        self.assertTrue(any(k.action == KeyAction.SHIFT for k in state.focus_ring))
        frame = render_keyboard_bgra(state, assets_dir=assets)
        from pigeon.widgets.settings_keyboard import _CLUSTER_SCALE, _cluster_xy

        cx, cy = _cluster_xy()
        sx = cx + int(round(112 * _CLUSTER_SCALE))
        sy = cy + int(round(152 * _CLUSTER_SCALE))
        roi = frame[sy + 8 : sy + 58, sx + 20 : sx + 140, :3]
        luma = roi.mean(axis=2)
        self.assertGreater(float(luma.max() - luma.min()), 40)

    def test_selected_key_letter_uses_ui_color(self) -> None:
        import xml.etree.ElementTree as ET

        from pigeon.widgets.main_settings import (
            COLOR_UI_DEFAULT,
            SettingsTheme,
            _find_by_logical_id,
            _iter_style_fill_stroke,
            keyboard_svg_path,
        )
        from pigeon.widgets.settings_keyboard import apply_keyboard_selection

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        root = ET.parse(keyboard_svg_path("keyboard_lower.svg", assets_dir=assets)).getroot()
        apply_keyboard_selection(
            root,
            focused_button_id="lower_q",
            theme=SettingsTheme(ui=COLOR_UI_DEFAULT, selected="#FFFFFF"),
            button_ids={"lower_q", "lower_w"},
        )
        q = _find_by_logical_id(root, "lower_q")
        w = _find_by_logical_id(root, "lower_w")
        assert q is not None and w is not None
        q_text = next(n for n in q.iter() if n.tag.endswith("text"))
        w_text = next(n for n in w.iter() if n.tag.endswith("text"))
        q_fill, _ = _iter_style_fill_stroke(q_text)
        w_fill, _ = _iter_style_fill_stroke(w_text)
        self.assertEqual((q_fill or "").lower(), COLOR_UI_DEFAULT.lower())
        self.assertEqual((w_fill or "").lower(), "#ffffff")

    def test_bottom_row_mode_defaults(self) -> None:
        from pigeon.widgets.settings_keyboard import (
            KeyAction,
            KeyboardMode,
            _bottom_row_keys,
            _bottom_row_mode_labels,
        )

        self.assertEqual(_bottom_row_mode_labels(KeyboardMode.NUMERIC_ALL), ("SYM", "abc"))
        self.assertEqual(_bottom_row_mode_labels(KeyboardMode.NUMERIC_PIN), ("SYM", "abc"))
        self.assertEqual(_bottom_row_mode_labels(KeyboardMode.SYMBOLIC), ("abc", "123"))
        self.assertEqual(_bottom_row_mode_labels(KeyboardMode.QWERTY_LOWER), ("SYM", "123"))
        self.assertEqual(_bottom_row_mode_labels(KeyboardMode.QWERTY_UPPER), ("SYM", "123"))
        numeric = {k.button_id: k.action for k in _bottom_row_keys(KeyboardMode.NUMERIC_ALL)}
        self.assertEqual(numeric["lower_SYM"], KeyAction.MODE_SYM)
        self.assertEqual(numeric["lower_123"], KeyAction.MODE_ABC)
        self.assertNotIn(KeyAction.MODE_123, numeric.values())
        symbolic = {k.button_id: k.action for k in _bottom_row_keys(KeyboardMode.SYMBOLIC)}
        self.assertEqual(symbolic["lower_SYM"], KeyAction.MODE_ABC)
        self.assertEqual(symbolic["lower_123"], KeyAction.MODE_123)
        self.assertNotIn(KeyAction.MODE_SYM, symbolic.values())
        letters = {k.button_id: k.action for k in _bottom_row_keys(KeyboardMode.QWERTY_LOWER)}
        self.assertEqual(letters["lower_SYM"], KeyAction.MODE_SYM)
        self.assertEqual(letters["lower_123"], KeyAction.MODE_123)
        self.assertNotIn(KeyAction.MODE_ABC, letters.values())

    def test_pin_shows_authentication_code_and_enter(self) -> None:
        from pigeon.settings_layout import DUAL_SLOT_A, dual_slot_design
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_keyboard import KeyAction
        from pigeon.widgets.settings_main_1280 import (
            _auth_code_box,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.open_keyboard("pin", assets_dir=assets)
        kb = state.keyboard
        self.assertIsNotNone(kb)
        self.assertTrue(any(k.action == KeyAction.GO for k in kb.focus_ring))
        kb.buffer = "0035"
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        box = _auth_code_box(dual_slot_design(DUAL_SLOT_A))
        x, y, w, h = box
        roi = frame[y : y + h, x : x + w, :3]
        self.assertGreater(int(np.count_nonzero(roi.max(axis=2) > 180)), 80)

    def test_keyboard_sits_between_box1_and_status_bar(self) -> None:
        from pigeon.np_layout import NOW_PLAYING_ZONES, STATUS_BAR_TRACK
        from pigeon.settings_layout import DUAL_SLOT_A, MENU_PLATE_XYWH, dual_slot_design
        from pigeon.widgets.settings_keyboard import (
            _CLUSTER_GAP_ABOVE_STATUS,
            _CLUSTER_GAP_AFTER_BOX1,
            _CLUSTER_NARROW_PX,
            _CLUSTER_SCALE,
            _CLUSTER_VB,
            _cluster_wh,
            _cluster_xy,
            KeyboardMode,
            open_keyboard,
            render_keyboard_bgra,
        )

        x, y = _cluster_xy()
        _cw, ch = _cluster_wh()
        box = dual_slot_design(DUAL_SLOT_A)
        box1_bottom = box[1] + box[3]
        track_top = NOW_PLAYING_ZONES[5].y + STATUS_BAR_TRACK[1]
        band_top = box1_bottom + _CLUSTER_GAP_AFTER_BOX1
        band_bottom = track_top - _CLUSTER_GAP_ABOVE_STATUS
        mid = band_top + (band_bottom - band_top - ch) * 0.5
        self.assertAlmostEqual(y, mid, delta=2)
        self.assertGreater(y, box1_bottom)
        bottom = y + ch
        self.assertLessEqual(bottom, track_top - 4)
        self.assertAlmostEqual(_CLUSTER_VB[2] * _CLUSTER_SCALE, _CLUSTER_VB[2] - _CLUSTER_NARROW_PX, delta=0.5)

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        kb = open_keyboard(target="location", assets_dir=assets)
        self.assertEqual(kb.mode, KeyboardMode.QWERTY_UPPER)
        frame = render_keyboard_bgra(kb, assets_dir=assets)
        zx, zy, zw, zh = (
            int(round(NOW_PLAYING_ZONES[5].x)),
            int(round(track_top)),
            int(round(NOW_PLAYING_ZONES[5].w)),
            int(round(STATUS_BAR_TRACK[3])),
        )
        self.assertEqual(int(frame[zy : zy + zh, zx : zx + zw, 3].max()), 0)
        lit = np.where(frame[:, :, 3] > 10)
        self.assertGreater(int(lit[1].size), 0)
        plate_l = int(round(MENU_PLATE_XYWH[0]))
        plate_r = int(round(MENU_PLATE_XYWH[0] + MENU_PLATE_XYWH[2]))
        self.assertGreaterEqual(int(lit[1].min()) - plate_l, 6)
        self.assertGreaterEqual(plate_r - int(lit[1].max()), 6)

    def test_keyboard_focus_reuse_matches_full_raster(self) -> None:
        from pigeon.widgets.settings_keyboard import (
            KeyboardMode,
            clear_keyboard_render_caches,
            keyboard_overlay_cached,
            open_keyboard,
            render_keyboard_bgra,
            warm_keyboard_idle,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        clear_keyboard_render_caches()
        kb = open_keyboard(target="network", assets_dir=assets)
        self.assertEqual(kb.mode, KeyboardMode.QWERTY_LOWER)
        first = render_keyboard_bgra(kb, assets_dir=assets)
        again = render_keyboard_bgra(kb, assets_dir=assets)
        self.assertTrue(np.array_equal(first, again))
        warm_keyboard_idle(kb, assets_dir=assets)
        kb.navigate(forward=True)
        neighbor = render_keyboard_bgra(kb, assets_dir=assets)
        self.assertFalse(np.array_equal(first, neighbor))
        neighbor_again = render_keyboard_bgra(kb, assets_dir=assets)
        self.assertTrue(np.array_equal(neighbor, neighbor_again))
        self.assertTrue(keyboard_overlay_cached(kb, assets_dir=assets))
        kb.navigate(forward=False)
        back = render_keyboard_bgra(kb, assets_dir=assets)
        self.assertTrue(np.array_equal(first, back))

    def test_keyboard_adjacent_layer_matches_full_composite(self) -> None:
        from pigeon.widgets.settings_keyboard import (
            _composite_keyboard_layers,
            clear_keyboard_render_caches,
            open_keyboard,
            render_keyboard_bgra,
            warm_keyboard_idle,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        clear_keyboard_render_caches()
        kb = open_keyboard(target="network", assets_dir=assets)
        warm_keyboard_idle(kb, assets_dir=assets)
        kb.navigate(forward=True)
        adjacent = render_keyboard_bgra(kb, assets_dir=assets)
        full = _composite_keyboard_layers(
            kb, assets_dir=assets, focused_button_id=kb.focused.button_id
        )
        delta = np.max(np.abs(adjacent.astype(np.int16) - full.astype(np.int16)))
        self.assertLessEqual(int(delta), 2)

    def test_settings_main_dirty_zones_match_full_redraw(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            clear_settings_main_compose_cache,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        clear_settings_main_compose_cache()
        state = MainSettingsState()
        state.ensure_focus_ring()
        render_settings_main_1280_bgra(state, assets_dir=assets)
        state.navigate(forward=True)
        patched = render_settings_main_1280_bgra(state, assets_dir=assets)
        clear_settings_main_compose_cache()
        full = render_settings_main_1280_bgra(state, assets_dir=assets)
        self.assertTrue(np.array_equal(patched, full))

    def test_keyboard_hides_zones_2_to_4(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_main_1280 import (
            _column_card_rect,
            clear_settings_main_compose_cache,
            render_settings_main_1280_bgra,
        )
        from pigeon.widgets.ui_color_settings import write_ui_color_keys

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        write_ui_color_keys({"ui": "blue"}, persist=False)
        clear_settings_main_compose_cache()
        state = MainSettingsState()
        state.open_keyboard("location", assets_dir=assets)
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        for zone_index in (2, 3, 4):
            cx, cy, cw, ch = _column_card_rect(zone_index)
            card = frame[cy + ch // 2, cx + cw // 2, :3]
            # Blue plate shows through — no dark column card.
            self.assertGreater(int(card[0]), 80)

    def test_location_rename_starts_grey_and_first_key_replaces(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState, location_room_label
        from pigeon.widgets.settings_keyboard import activate_key, focus_first_letter

        self.assertEqual(location_room_label("NEST3", slot_index=3), "ROOM 3")
        self.assertEqual(location_room_label("nest 2", slot_index=2), "ROOM 2")
        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.location_name = "NEST3"
        state.renaming_location_slot = 3
        state.open_keyboard("location", assets_dir=assets)
        kb = state.keyboard
        self.assertIsNotNone(kb)
        self.assertEqual(kb.buffer, "")
        self.assertEqual(kb.initial_text, "ROOM 3")
        focus_first_letter(kb, assets_dir=assets)
        ch = kb.focused.char
        activate_key(kb, assets_dir=assets)
        self.assertEqual(kb.buffer, ch.upper() if not kb.supports_lowercase else ch)
        self.assertNotIn("NEST", kb.buffer.upper())
        self.assertNotIn("ROOM", kb.buffer.upper())

    def test_text_entry_cursor_sits_in_location_field(self) -> None:
        from pigeon.settings_layout import DUAL_SLOT_A, dual_slot_design
        from pigeon.widgets.main_settings import MainSettingsState, _draw_text_entry_cursor
        from pigeon.widgets.settings_main_1280 import (
            _location_field_boxes,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.open_keyboard("location", assets_dir=assets)
        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        _draw_text_entry_cursor(frame, state, force=True)
        _dots, box = _location_field_boxes(dual_slot_design(DUAL_SLOT_A))
        x, y, w, h = box
        band = frame[y + 8 : y + h - 8, x : x + w, :3]
        dark = np.all(band < 40, axis=2)
        cols = np.where(dark.any(axis=0))[0]
        self.assertGreater(int(cols.size), 0)
        self.assertGreater(int(cols[0]), 8)
        self.assertLess(int(cols[-1]), w - 4)

    def test_location_picker_room_activate_switches(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsState, MainSettingsWidget

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = MainSettingsWidget(assets_dir=assets)
        st = widget.state
        st.enter_location_picker()
        st.location_slots = (("a", "FAMILYROOM"), ("b", "BEDROOM"), ("c", "ROOM 3"))
        st.focus_index = st.focus_ring.index("main_box2_button")
        action = widget.activate()
        self.assertIn(action, ("location_switch", "keyboard_open:location"))
        if action == "location_switch":
            self.assertEqual(st.location_name, "BEDROOM")

    def test_activating_letter_updates_visible_field(self) -> None:
        from pigeon.settings_layout import DUAL_SLOT_A, dual_slot_design
        from pigeon.widgets.main_settings import MainSettingsState
        from pigeon.widgets.settings_keyboard import KeyAction, activate_key, focus_first_letter
        from pigeon.widgets.settings_main_1280 import (
            _location_field_boxes,
            render_settings_main_1280_bgra,
        )

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        state = MainSettingsState()
        state.open_keyboard("location", assets_dir=assets)
        kb = state.keyboard
        self.assertIsNotNone(kb)
        focus_first_letter(kb, assets_dir=assets)
        self.assertEqual(kb.focused.action, KeyAction.CHAR)
        self.assertTrue(kb.focused.char)
        before = str(kb.buffer)
        self.assertEqual(activate_key(kb, assets_dir=assets), "typing")
        self.assertEqual(kb.buffer, before + kb.focused.char)

        frame = render_settings_main_1280_bgra(state, assets_dir=assets)
        _dots, box = _location_field_boxes(dual_slot_design(DUAL_SLOT_A))
        x, y, w, h = box
        roi = frame[y : y + h, x : x + w, :3]
        self.assertGreater(int(np.count_nonzero(roi.min(axis=2) > 180)), 80)
        self.assertGreater(int(np.count_nonzero(roi.max(axis=2) < 80)), 20)

    def test_focus_cache_key_matches_prewarm_lookup(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsWidget

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        widget = MainSettingsWidget(assets_dir=assets)
        widget.state.ensure_focus_ring()
        self.assertEqual(
            widget._focus_cache_key(),
            widget._focus_key_for_state(widget.state),
        )
        widget.navigate(forward=True)
        self.assertEqual(
            widget._focus_cache_key(),
            widget._focus_key_for_state(widget.state),
        )
        tt = np.zeros((8, 8, 4), dtype=np.uint8)
        widget.state.zone2_tt_bgra = tt
        before = widget._structure_sig()
        widget.state.zone2_tt_bgra = np.zeros((8, 8, 4), dtype=np.uint8)
        self.assertEqual(before, widget._structure_sig())


    def test_ui_color_picker_order_and_looks(self) -> None:
        from pigeon.widgets.main_settings import COLOR_UI_DEFAULT, MainSettingsState
        from pigeon.widgets.ui_color_settings import (
            _BAR_BUTTON_IDS,
            apply_color_keys_to_state,
            current_ui_picker_key,
            hex_for_color_key,
            theme_from_color_keys,
            ui_color_swatch_focus_ring,
            write_ui_color_keys,
        )
        from pigeon.compositing import swap_achromatic_black_white_bgr

        ring = ui_color_swatch_focus_ring("ui")
        self.assertEqual(
            ring,
            ("blue", "orange", "yellow", "green", "gray", "dark", "bright"),
        )
        self.assertEqual(tuple(_BAR_BUTTON_IDS), ring)

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        svg = (assets / "settings" / "pigeon" / "widget_sp_ui_color_zone0.svg").read_text(
            encoding="utf-8"
        )
        for bid in _BAR_BUTTON_IDS.values():
            self.assertIn(f'id="{bid}"', svg)
        self.assertNotIn("red_swatch_button", svg)
        self.assertNotIn("purple_swatch_button", svg)

        mapped = write_ui_color_keys({"ui": "red"}, persist=False)
        self.assertEqual(mapped["ui"], "dark")
        self.assertEqual(current_ui_picker_key(), "dark")
        self.assertEqual(hex_for_color_key("ui", "dark"), COLOR_UI_DEFAULT)
        self.assertEqual(hex_for_color_key("ui", "bright"), COLOR_UI_DEFAULT)
        self.assertEqual(theme_from_color_keys({"ui": "bright"}).ui, COLOR_UI_DEFAULT)

        state = MainSettingsState()
        apply_color_keys_to_state(
            state, {"accent": "white", "ui": "blue", "button": "black"}, persist=False
        )
        state.show_ui_color = True
        state.ui_color_nav = "swatches"
        state.ui_color_active_class = "ui"
        state.ui_color_focus_index = 0
        self.assertEqual(state.ui_color_focused_id, "blue")
        for _ in range(5):
            state.navigate_ui_color(forward=True)
        self.assertEqual(state.ui_color_focused_id, "dark")
        self.assertEqual(current_ui_picker_key(), "dark")
        state.navigate_ui_color(forward=True)
        self.assertEqual(state.ui_color_focused_id, "bright")
        self.assertEqual(current_ui_picker_key(), "bright")

        frame = np.zeros((2, 3, 3), dtype=np.uint8)
        frame[0, 0] = (0, 0, 0)
        frame[0, 1] = (255, 255, 255)
        frame[0, 2] = (247, 166, 78)  # BGR of #4EA6F7
        swapped = swap_achromatic_black_white_bgr(frame)
        self.assertEqual(tuple(int(v) for v in swapped[0, 0]), (255, 255, 255))
        self.assertEqual(tuple(int(v) for v in swapped[0, 1]), (0, 0, 0))
        self.assertEqual(tuple(int(v) for v in swapped[0, 2]), (247, 166, 78))
        gray = np.full((1, 1, 3), 147, dtype=np.uint8)  # NP chrome / idle text
        gray_out = swap_achromatic_black_white_bgr(gray)
        self.assertEqual(tuple(int(v) for v in gray_out[0, 0]), (0, 0, 0))
        # 24% artwork wash over black (white tank / highlights) must stay a
        # soft invert, not snap to ink — that was the black "brush strokes".
        wash = np.full((1, 1, 3), 55, dtype=np.uint8)
        wash_out = swap_achromatic_black_white_bgr(wash)
        self.assertGreater(int(wash_out[0, 0, 0]), 150)
        # Saturated stage light at the same dim wash (chroma ≥ 28) used to
        # stay dark and cut a hard silhouette against the inverted gray.
        spot = np.zeros((1, 1, 3), dtype=np.uint8)
        spot[0, 0] = (8, 24, 56)  # BGR orange-red, y ≈ 32
        spot_out = swap_achromatic_black_white_bgr(spot)
        self.assertGreater(int(spot_out[0, 0].min()), 180)

        from pigeon.compositing import (
            clear_bright_artwork_mask,
            clear_bright_slant_mask,
            restore_bright_artwork_pixels,
            stamp_bright_artwork_mask,
        )
        from pigeon.design import DESIGN_H, DESIGN_W

        clear_bright_artwork_mask()
        clear_bright_slant_mask()
        canvas = np.zeros((DESIGN_H, DESIGN_W, 3), dtype=np.uint8)
        canvas[40:80, 40:80] = 40
        stamp_bright_artwork_mask(np.full((40, 40), 255, dtype=np.uint8), 40, 40)
        restored = restore_bright_artwork_pixels(
            canvas, swap_achromatic_black_white_bgr(canvas)
        )
        self.assertEqual(int(restored[60, 60, 0]), 40)
        self.assertEqual(int(restored[0, 0, 0]), 255)
        clear_bright_artwork_mask()
        write_ui_color_keys({"ui": "blue"}, persist=False)


class SettingsMainNetworkSsidTests(unittest.TestCase):
    def test_live_ssid_shows_when_location_record_is_empty(self) -> None:
        from unittest.mock import patch

        from pigeon.widgets.main_settings import MainSettingsState

        state = MainSettingsState()
        with (
            patch("pigeon.app_state.read_location_wifi", return_value=None),
            patch("pigeon.wifi_scan.current_connected_ssid", return_value="HENLI"),
        ):
            state.refresh_network_ssid()
        self.assertEqual(state.displayed_wifi_ssid(), "HENLI")
        self.assertTrue(state.wifi_configured)

    def test_logout_does_not_readopt_live_ssid(self) -> None:
        from unittest.mock import patch

        from pigeon.widgets.main_settings import MainSettingsState

        state = MainSettingsState()
        state.selected_wifi_ssid = ""
        state.wifi_logged_out = True
        with (
            patch("pigeon.app_state.read_location_wifi", return_value=None),
            patch("pigeon.wifi_scan.current_connected_ssid", return_value="HENLI"),
        ):
            state.refresh_network_ssid()
        self.assertEqual(state.displayed_wifi_ssid(), "")
        self.assertFalse(state.wifi_configured)

    def test_location_wifi_fills_empty_selected(self) -> None:
        from unittest.mock import patch

        from pigeon.widgets.main_settings import MainSettingsState

        state = MainSettingsState()
        with (
            patch(
                "pigeon.app_state.read_location_wifi",
                return_value={"ssid": "NEST", "password": "x"},
            ),
            patch("pigeon.wifi_scan.current_connected_ssid", return_value=""),
        ):
            state.refresh_network_ssid()
        self.assertEqual(state.selected_wifi_ssid, "NEST")
        self.assertEqual(state.displayed_wifi_ssid(), "NEST")

    def test_reload_location_wifi_drops_previous_location_ssid(self) -> None:
        from unittest.mock import patch

        from pigeon.widgets.main_settings import MainSettingsState

        state = MainSettingsState()
        state.selected_wifi_ssid = "OLDNET"
        with (
            patch("pigeon.app_state.read_location_wifi", return_value=None),
            patch("pigeon.wifi_scan.current_connected_ssid", return_value=""),
        ):
            state.reload_location_wifi()
        self.assertEqual(state.selected_wifi_ssid, "")
        self.assertEqual(state.displayed_wifi_ssid(), "")


if __name__ == "__main__":
    unittest.main()
