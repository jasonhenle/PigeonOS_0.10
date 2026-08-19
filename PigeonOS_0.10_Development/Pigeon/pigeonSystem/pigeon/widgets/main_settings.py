"""
Pigeon 0.8 main settings — SVG chrome from ``settings_0.8/settings_main.svg``.

Renders the primary settings menu (800×480 BGRA) with left/right focus navigation
and selection recoloring. Text entry opens the shared settings keyboard overlay
(``settings_keyboard.py``).
"""

from __future__ import annotations

import io
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pigeon.compositing import alpha_blend_bgra_over_bgr
from pigeon.design import DESIGN_H, DESIGN_W
from pigeon.font_paths import resolve_digital7_font, resolve_ui_font_medium
from pigeon.widgets.box_device_search import (
    BOX_DEVICE_ROW_CANCEL,
    BOX_DEVICE_ROW_ENTER_IP,
    BoxDevicePanelState,
    ManualDeviceEntry,
    _BOX_DEVICE_ROW_COUNT,
    _BOX_SCAN_MAX_DURATION_S,
    _BOX_SCAN_ROTATION_DPS,
    box_devices_with_special_rows,
    box_search_center_svg,
    device_row_from_pick,
    is_special_device_row,
    scan_lan_devices,
)
from pigeon.widgets.box_device_pairing import BoxPairingSession

# Illustrator export quirk: row 2 device art is named ``device1_text_<suffix>`` at y≈290.
_BOX_RESULT_ROW_Y_SVG: tuple[float, ...] = (264.0, 290.0, 316.0, 342.0, 368.0)
_BOX_RESULT_ROW_FONT_SIZE_SVG = 20.0
_BOX_RESULT_ROW_GAP_SVG = 6.0
_BOX_COLUMN_INNER_SVG: dict[int, tuple[float, float]] = {
    1: (60.0, 250.0),
    2: (310.0, 495.0),
    3: (555.0, 740.0),
}
_BOX_LOCATION_TEXT_Y_SVG: dict[int, float] = {1: 325.6104, 2: 325.704, 3: 325.6104}
_BOX_LOCATION_TEXT_FONT_SVG = 48.0
_LOCATION_SLOT_DEFAULT_NAMES: tuple[str, ...] = ("nest 1", "nest 2", "nest 3")
_BOX_DEVICE_NAME_Y_SVG = 319.8984
_BOX_DEVICE_IP_Y_SVG = 359.6094
_BOX_DEVICE_NAME_FONT_SVG = 48.1346
_BOX_DEVICE_IP_FONT_SVG = 42.1177

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

_MATRIX_RE = re.compile(
    r"matrix\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)",
    re.IGNORECASE,
)
_TRANSLATE_RE = re.compile(
    r"translate\(\s*([-\d.]+)(?:[,\s]+([-\d.]+))?\s*\)",
    re.IGNORECASE,
)

# --- Colors (settingInstructions_0.8.0) ---
COLOR_SELECTED = "#FFFFFF"
COLOR_DESELECTED = "#000013"
COLOR_UI_DEFAULT = "#ff0013"
COLOR_ACCENT_DEFAULT = "#FFFFFF"
COLOR_INACTIVE = "#404040"
COLOR_VERSION_TEXT = "#000000"
COLOR_LEGACY_GREEN = "#02e900"
_SEARCH_GLYPH_BGR = (0, 0, 0)
_WIFI_SEARCH_GLYPH_BGR = (255, 255, 255)

_ARTBOARD_H = 480.0
_WIFI_RADII_SVG: tuple[float, float, float] = (18.668, 29.008, 39.145)
_WIFI_STROKE_SVG = 5.0
_WIFI_FAIL_SIZE_SVG = 42.0
_COLOR_GRAY_BGR = (128, 128, 128)
_COLOR_GRAY_RGBA = (128, 128, 128, 255)
_TEXT_ENTRY_FIELDS: dict[str, dict[str, float | str | bool]] = {
    "location": {
        "text_id": "main_dual_location_text",
        "x0_svg": 82.0,
        "x1_svg": 383.5,
        "baseline_y_svg": 184.05,
        "font_size_svg": 35.0,
        "font": "digital7",
        "uppercase_only": True,
        "password_mask": False,
    },
    "network": {
        "text_id": "main_dual_network_name_text",
        "x0_svg": 417.0,
        "x1_svg": 717.8,
        "baseline_y_svg": 184.05,
        "font_size_svg": 35.0,
        "font": "sharp_medium",
        "uppercase_only": False,
        "password_mask": True,
        "placeholder": "password",
        "placeholder_font_size_svg": 30.0,
        "placeholder_baseline_offset_px": -1,
    },
    "pin": {
        "text_id": "main_dual_network_name_text",
        "x0_svg": 417.0,
        "x1_svg": 717.8,
        "baseline_y_svg": 184.05,
        "font_size_svg": 35.0,
        "font": "digital7",
        "uppercase_only": True,
        "password_mask": False,
        "placeholder": "ENTER PIN",
    },
    "device_ip": {
        "text_id": "main_dual_network_name_text",
        "x0_svg": 417.0,
        "x1_svg": 717.8,
        "baseline_y_svg": 184.05,
        "font_size_svg": 35.0,
        "font": "digital7",
        "uppercase_only": False,
        "password_mask": False,
    },
    "device_name": {
        "text_id": "main_dual_location_text",
        "x0_svg": 82.0,
        "x1_svg": 383.5,
        "baseline_y_svg": 184.05,
        "font_size_svg": 35.0,
        "font": "digital7",
        "uppercase_only": True,
        "password_mask": False,
        "placeholder": "NEW DEVICE",
    },
}
_KEYBOARD_HIDE_WHEN_OPEN: tuple[str, ...] = (
    "main_exit_group",
    "main_box1",
    "main_box2",
    "main_box3",
    "main_box1_device_group",
    "main_box2_device_group",
    "main_box3_device_group",
    "main_box1_location_group",
    "main_box2_location_group",
    "main_box3_location_group",
    "main_box1_container",
    "main_box2_container",
    "main_box3_container",
    "main_box1_search_icon",
    "main_box2_search_icon",
    "main_box3_search_icon",
)

# Keyboard SVG stubs (full systems not implemented yet).
KEYBOARD_SVG_NAMES: tuple[str, ...] = (
    "keyboard_bottom_row.svg",
    "keyboard_qwerty_lower.svg",
    "keyboard_qwerty_upper.svg",
    "keyboard_numeric_all.svg",
    "keyboard_numeric_pin.svg",
    "keyboard_numeric_ip.svg",
    "keyboard_yes_no.svg",
    "keyboard_symbolic.svg",
)
# Specified in instructions but not in the current GFX export set.
KEYBOARD_NUMERIC_IP_SVG = "keyboard_numeric_ip.svg"

_HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")
_AI_SUFFIX_RE = re.compile(r"_\d{20,}_?$")

# Accent outlines hidden on settings_main tiles (exit / back accents stay).
_HIDDEN_MAIN_ACCENT_IDS = frozenset(
    {
        "main_dual_accent",
        "main_03.02_dual_button_accent",
        "main_box1_accent",
        "main_box2_accent",
        "main_box3_accent",
    }
)

# Primary nav ring (left/right). Network picker joins only when visible.
_PRIMARY_FOCUS_CANDIDATES: tuple[str, ...] = (
    "main_exit_button",
    "main_dual_location_button",
    "main_dual_network_button",
    "main_box1_button",
    "main_box2_button",
    "main_box3_button",
)

_ACTIVATE_ACTIONS: dict[str, str] = {
    "main_dual_location_button": "focus_location",
    "main_dual_network_button": "focus_network",
    "main_box1_button": "focus_box1",
    "main_box2_button": "focus_box2",
    "main_box3_button": "focus_box3",
    "main_box2_device_results": "pick_box2_device",
    "main_box3_device_results": "pick_box3_device",
    "main_exit_button": "exit",
    "main_network_picker_button": "focus_network_picker",
    "main_box2_add_search_icon": "wifi_search",
}

# WiFi onboarding (instructions + search glyph under ``main_instructions``).
_WIFI_SEARCH_CENTER_SVG = (400.446, 298.466)
_WIFI_SEARCH_RING_RADIUS_SVG = 32.247
_WIFI_SEARCH_RING_STROKE_SVG = 9.0
_WIFI_SEARCH_PATCH_RADIUS_PX = 52
_WIFI_SCAN_MIN_DURATION_S = 0.0
_WIFI_SCAN_MAX_DURATION_S = 55.0
_WIFI_SCAN_ROTATION_DPS = 720.0
_WIFI_SCAN_CACHE_TTL_S = 120.0
_BOX_SCAN_CACHE_TTL_S = 120.0
# Location switch: show centered search spinner only if reload takes this long.
_LOCATION_SWITCH_SPINNER_DELAY_S = 2.0
_LOCATION_SWITCH_ROTATION_DPS = _WIFI_SCAN_ROTATION_DPS
_DUAL_LOCATION_TEXT_X0_SVG = 82.0
_DUAL_LOCATION_TEXT_X1_SVG = 395.0
_DUAL_NETWORK_TEXT_X0_SVG = 417.0
_DUAL_NETWORK_TEXT_X1_SVG = 717.8
_DUAL_LOCATION_ICON_RADIUS_SVG = 6.921
_DUAL_LOCATION_TEXT_ICON_GAP_SVG = 10.0
_DUAL_LOCATION_WIFI_CENTER_SVG = (111.133, 172.5)
_DUAL_LOCATION_ICON_IDS: tuple[str, ...] = (
    "main_dual_locaion1_icon",
    "main_dual_locaion2_icon",
    "main_dual_locaion3_icon",
)
_PICKER_TEXT_CENTER_X_SVG = 400.0
_WIFI_ONBOARDING_FOCUS: tuple[str, ...] = (
    "main_exit_button",
    "main_dual_location_button",
    "main_dual_network_button",
    "main_box2_add_search_icon",
)

# Button → associated contrast layers (logical ids; AI suffixes tolerated).
_FOCUS_ASSOCIATED: dict[str, tuple[str, ...]] = {
    "main_dual_location_button": (
        "main_dual_location_text",
        "main_dual_locaion1_icon",
        "main_dual_locaion2_icon",
        "main_dual_locaion3_icon",
    ),
    "main_dual_network_button": (
        "main_dual_network_name_text",
    ),
    "main_box1_button": (),
    "main_box2_button": (),
    "main_box3_button": (),
    "main_exit_button": (
        "main_exit_text",
        "main_exit_icon",
    ),
    "main_network_picker_button": (),
    "main_box2_add_search_icon": ("main_box2_+_icon",),
}

_PICKER_ROW_MINI_BUTTONS: tuple[str, ...] = (
    "main_network_picker_row1_mini_button",
    "main_network_picker_row2_mini_button",
    "main_network_picker_row3_mini_button",
)
_PICKER_ROW_TEXTS: tuple[str, ...] = (
    "main_network_picker_row1_text",
    "main_network_picker_row2_text",
    "main_network_picker_row3_text",
)
_PICKER_ROW_LOCK_GROUPS: tuple[str, ...] = (
    "main_network_picker_row1_lock_info",
    "main_network_picker_row22_lock_info",
    "main_network_picker_lock3_icon",
)

_HIDE_ALWAYS_LOGICAL: tuple[str, ...] = (
    "keyboardtemp",
)

# Baked stripe groups in settings_main (containerN) and settings_pigeon (menu_container_N).
_BACKGROUND_CONTAINERS: tuple[str, ...] = tuple(
    [f"container{i}" for i in range(1, 8)]
    + [f"menu_container_{i}" for i in range(1, 7)]
)

# Launch-visible layer groups (``settingInstructions_0.8.0``).
_LAUNCH_VISIBLE_LOGICAL: frozenset[str] = frozenset(
    {
        "settings_background",
        "main_exit_group",
        "main_dual",
        "main_box1_device_group",
        "main_box2_device_group",
        "main_box3_device_group",
    }
)

_BOX_LOCATION_GROUPS: tuple[str, ...] = (
    "main_box1_location_group",
    "main_box2_location_group",
    "main_box3_location_group",
)

# Rounded menu container from ``settings_main.svg`` clip path (800×480 artboard).
_MENU_CONTAINER_BBOX: tuple[int, int, int, int] = (22, 110, 777, 426)
_MENU_CONTAINER_RADIUS_PX: int = 20

# Box panel chrome (container, search, results) hidden until a box is opened.
_BOX_CHROME_PREFIXES: tuple[str, ...] = (
    "main_box1_container",
    "main_box1_search_results",
    "main_box1_search_icon",
    "main_box2_container",
    "main_box2_search_results",
    "main_box2_search_icon",
    "main_box3_container",
    "main_box3_search_results",
    "main_box3_search_icon",
)
_BOX_CHROME_NUM_RE = re.compile(r"^main_box([123])_")
# Rounded column chrome (button + accent) — always visible at launch.
_BOX_CONTAINER_LOGICALS: frozenset[str] = frozenset(
    {
        "main_box1_container",
        "main_box2_container",
        "main_box3_container",
    }
)
_BOX_DEVICE_GROUPS: dict[int, str] = {
    1: "main_box1_device_group",
    2: "main_box2_device_group",
    3: "main_box3_device_group",
}
_BOX_ROOT_LOGICALS: tuple[str, ...] = ("main_box1", "main_box2", "main_box3")
_BOX_BUTTON_LOGICALS: frozenset[str] = frozenset(
    {"main_box1_button", "main_box2_button", "main_box3_button"}
)

_BUTTON_FILL_CANDIDATES = frozenset(
    {
        COLOR_SELECTED.lower(),
        COLOR_DESELECTED.lower(),
        COLOR_LEGACY_GREEN.lower(),
        "#ffffff",
        "#000013",
        "#000000",
        "#202020",
        "#231f20",  # Illustrator keyboard button default
        "white",
        "black",
    }
)
_CONTRAST_SWAP_CANDIDATES = frozenset(
    {
        COLOR_SELECTED.lower(),
        COLOR_DESELECTED.lower(),
        COLOR_INACTIVE.lower(),
        COLOR_LEGACY_GREEN.lower(),
        "#ffffff",
        "#000013",
        "#000000",
        "#202020",
        "#404040",
        "white",
        "black",
    }
)

_UI_BRAND_COLORS = frozenset({COLOR_UI_DEFAULT.lower(), "#ff0013"})


def _theme_swatch_hexes() -> frozenset[str]:
    """Lazy import — ui_color palette expands button/accent swap candidates."""
    try:
        from pigeon.widgets.ui_color_settings import THEME_SWATCH_HEXES

        return THEME_SWATCH_HEXES
    except Exception:
        return frozenset()


def _is_ui_brand_color(hex_color: str | None) -> bool:
    if not hex_color:
        return False
    return hex_color.lower() in (_UI_BRAND_COLORS | _ui_recolor_hexes())


@dataclass(frozen=True)
class _ContainerStripeSpec:
    x_svg: float
    y_svg: float
    width_svg: float
    height_svg: float
    matrix: tuple[float, float, float, float, float, float]
    fill_hex: str


@dataclass(frozen=True)
class _WifiIconLayout:
    """Hex clip + ring geometry from ``settings_main.svg`` (PyMuPDF ignores clip-path)."""

    clip_polygon_svg: tuple[tuple[float, float], ...]
    cx_svg: float
    cy_svg: float
    fail_text_xy_svg: tuple[float, float] | None = None
    focus_logical: str = "main_dual_network_button"
    picker_row: bool = False
    picker_slot: int | None = None


@dataclass(frozen=True)
class SettingsTheme:
    """User-themeable colors for main settings."""

    ui: str = COLOR_UI_DEFAULT
    selected: str = COLOR_SELECTED
    deselected: str = COLOR_DESELECTED
    inactive: str = COLOR_INACTIVE
    accent: str = COLOR_ACCENT_DEFAULT


class MainSettingsFocus(IntEnum):
    """Index into the live focus ring (see ``MainSettingsState.focus_ring``)."""

    FIRST = 0


@dataclass
class MainSettingsState:
    """Interactive state for the main settings SVG menu."""

    focus_index: int = 0
    theme: SettingsTheme = field(default_factory=SettingsTheme)
    wifi_level: int = 3  # stub 0–3
    location_name: str = "nest 1"
    selected_wifi_ssid: str = ""
    pending_wifi_ssid: str = ""
    wifi_password: str = ""
    network_password_error: bool = False
    pending_network_password: str = ""
    wifi_connecting: bool = False
    wifi_connect_started_mono: float = 0.0
    network_name: str = ""  # legacy alias; kept for cache sig compatibility
    version_string: str = "0.8.0"
    show_network_picker: bool = False
    network_picker_row: int = 0
    network_picker_scroll: int = 0
    network_picker_arrow: str = ""
    show_instructions: bool = False
    wifi_onboarding: bool = False
    wifi_scanning: bool = False
    wifi_scan_started_mono: float = 0.0
    wifi_scan_angle_deg: float = 0.0
    wifi_networks: tuple[str, ...] = ()
    show_box1_panel: bool = False
    show_box2_panel: bool = False
    show_box3_panel: bool = False
    show_location_picker: bool = False
    # Up to three (id, name) slots shown in box1–3 while picking/renaming.
    location_slots: tuple[tuple[str, str], ...] = ()
    renaming_location_id: str = ""
    renaming_location_slot: int = 0  # 1–3 while keyboard is open for a slot
    # True while applying a location change (devices / LEDs / runtime reload).
    location_switching: bool = False
    location_switch_started_mono: float = 0.0
    location_switch_angle_deg: float = 0.0
    box2_devices: BoxDevicePanelState = field(default_factory=BoxDevicePanelState)
    box3_devices: BoxDevicePanelState = field(default_factory=BoxDevicePanelState)
    box_pairing: BoxPairingSession | None = None
    manual_device_entry: ManualDeviceEntry | None = None
    box2_ip_invalid: bool = False
    box3_ip_invalid: bool = False
    show_pigeon_settings: bool = False
    pigeon_focus_index: int = 0
    # Silent GitHub poll when opening settings_pigeon (badge without popup).
    pigeon_needs_update_prefetch: bool = False
    # Status LEDs for tiles 6–9 (None → derive wifi/metadata; hdmi/audio follow toggles).
    pigeon_metadata_ok: bool | None = None
    pigeon_hdmi_ok: bool = False
    pigeon_audio_ok: bool = False
    # User toggles for WIFI / METADATA / HDMI / AUDIO (persisted in state.json).
    source_wifi_on: bool = True
    source_metadata_on: bool = True
    source_hdmi_on: bool = True
    source_audio_on: bool = True
    show_preferences: bool = False
    # Metadata inspector ([4]): pages 0=player, 1=hdmi, 2=pigeon.
    show_metadata_debug: bool = False
    metadata_debug_page: int = 0
    # "zones" = navigation A; "widgets" = navigation B after a zone is activated.
    preferences_nav: str = "zones"
    preferences_focus_index: int = 0
    preferences_active_zone: int = 0  # 1–5 while in widgets nav; else 0
    preferences_zone_widgets: tuple[str, str, str, str, str] = (
        "clock",
        "poster",
        "volume",
        "cast_info",
        "now_playing",
    )
    # Live playback progress 0..1 for prefs circular now-playing; None → idle demo.
    preferences_np_progress: float | None = None
    # When True, prefs preview uses live poster / volume / cast / bar (not SVG demos).
    preferences_live_content: bool = False
    preferences_poster_bgra: object | None = None  # np.ndarray | None
    preferences_volume: str | None = None
    preferences_volume_fraction: float | None = None
    preferences_incoming: str | None = None
    preferences_config: str | None = None
    preferences_cast: tuple[tuple[str, str], ...] | None = None
    preferences_elapsed_text: str | None = None
    preferences_remaining_text: str | None = None
    preferences_service_name: str | None = None
    preferences_content_mode: str | None = None  # video | music
    preferences_song_title: str | None = None
    preferences_album_title: str | None = None
    preferences_artist_title: str | None = None
    # System color page (settings_pigeon_ui_color) — opened from preferences color.
    show_ui_color: bool = False
    # "classes" = accent/ui/button/back; "swatches" = colors within active class.
    ui_color_nav: str = "classes"
    ui_color_focus_index: int = 0
    ui_color_active_class: str = ""  # accent | ui | button while in swatch nav
    ui_color_accent_key: str = "white"
    ui_color_ui_key: str = "red"
    ui_color_button_key: str = "black"
    show_update_popup: bool = False
    update_popup_focus_index: int = 0
    update_available: bool = False
    update_checking: bool = False
    update_applying: bool = False
    update_progress: float = 0.0  # 0..1 while applying
    update_local_version: str = ""
    update_remote_version: str | None = None
    update_changelog: str = ""
    update_error: str | None = None
    update_github_branch: str | None = None
    spinner_glyph_capture: bool = False
    # Focus ring rebuilt when panel visibility changes.
    focus_ring: tuple[str, ...] = field(default_factory=tuple)
    # Text-entry keyboard overlay (None = closed).
    keyboard: object | None = None

    @property
    def keyboard_open(self) -> bool:
        return self.keyboard is not None

    @property
    def wifi_configured(self) -> bool:
        return bool(str(self.selected_wifi_ssid or "").strip())

    def needs_wifi_setup(self) -> bool:
        """True until the user has chosen a WiFi network (SSID)."""
        return not self.wifi_configured

    def _box_panel(self, box_num: int) -> BoxDevicePanelState:
        return self.box2_devices if box_num == 2 else self.box3_devices

    def box_has_saved_device(self, box_num: int) -> bool:
        return self.saved_box_device(box_num) is not None

    def saved_box_device(self, box_num: int) -> tuple[str, str] | None:
        """Return the currently paired device for a box column, if any."""
        panel = self._box_panel(box_num)
        if panel.picked is not None:
            return panel.picked
        try:
            from pigeon.app_state import read_saved_av_receiver, read_saved_streaming_device

            row = read_saved_streaming_device() if box_num == 2 else read_saved_av_receiver()
            if not row:
                return None
            name = str(row.get("name") or row.get("label") or "Device").strip()
            ip = str(row.get("address") or "").strip()
            if name or ip:
                return (name or ip, ip or name)
        except Exception:
            pass
        return None

    def restore_box_device_panel(self, box_num: int) -> None:
        """Leave search/results and return the column to its last idle/device state."""
        panel = self._box_panel(box_num)
        saved = self.saved_box_device(box_num)
        panel.active = False
        panel.phase = "idle"
        panel.scanning = False
        panel.scan_angle_deg = 0.0
        panel.devices = ()
        panel.device_rows = ()
        panel.scroll = 0
        panel.row = 0
        panel.arrow = ""
        panel.picked = saved
        if box_num == 2:
            self.show_box2_panel = False
        elif box_num == 3:
            self.show_box3_panel = False
        self.ensure_focus_ring()
        btn = f"main_box{box_num}_button"
        if btn in self.focus_ring:
            self.focus_index = self.focus_ring.index(btn)

    def device_row_matches_saved(self, box_num: int, row: dict[str, str]) -> bool:
        saved = self.saved_box_device(box_num)
        if saved is None:
            return False
        saved_name, saved_ip = saved
        row_name = str(row.get("name") or row.get("label") or "").strip()
        row_ip = str(row.get("address") or "").strip()
        if row_ip and saved_ip and row_ip == saved_ip:
            return True
        return bool(row_name) and row_name.lower() == saved_name.lower() and row_ip == saved_ip

    def load_saved_box_devices(self) -> None:
        try:
            from pigeon.app_state import read_saved_av_receiver, read_saved_streaming_device

            stream = read_saved_streaming_device()
            if stream:
                name = str(stream.get("name") or stream.get("label") or "Device").strip()
                ip = str(stream.get("address") or "").strip()
                self.box2_devices.picked = (name, ip)
                self.box2_ip_invalid = False
            avr = read_saved_av_receiver()
            if avr:
                name = str(avr.get("name") or avr.get("label") or "Device").strip()
                ip = str(avr.get("address") or "").strip()
                self.box3_devices.picked = (name, ip)
                self.box3_ip_invalid = False
        except Exception:
            pass

    def reset_box_device_panel(self, box_num: int) -> None:
        panel = self._box_panel(box_num)
        panel.active = False
        panel.phase = "idle"
        panel.scanning = False
        panel.scan_angle_deg = 0.0
        panel.devices = ()
        panel.device_rows = ()
        panel.scroll = 0
        panel.row = 0
        panel.arrow = ""
        if box_num == 2:
            self.show_box2_panel = False
        elif box_num == 3:
            self.show_box3_panel = False

    def start_manual_device_entry(self, box_num: int) -> None:
        panel = self._box_panel(box_num)
        panel.active = False
        panel.phase = "idle"
        panel.scanning = False
        panel.devices = ()
        panel.device_rows = ()
        panel.scroll = 0
        panel.row = 0
        self.manual_device_entry = ManualDeviceEntry(box_num=int(box_num), step="ip")
        if box_num == 2:
            self.show_box2_panel = False
        elif box_num == 3:
            self.show_box3_panel = False

    def finish_manual_device_entry(self, *, name: str, ip: str, ip_valid: bool) -> None:
        entry = self.manual_device_entry
        if entry is None:
            return
        box_num = int(entry.box_num)
        panel = self._box_panel(box_num)
        clean_name = str(name or "Device").strip() or "Device"
        clean_ip = str(ip or "").strip()
        panel.picked = (clean_name, clean_ip)
        panel.active = False
        panel.phase = "idle"
        panel.scanning = False
        if box_num == 2:
            self.box2_ip_invalid = not ip_valid
            self.show_box2_panel = True
        elif box_num == 3:
            self.box3_ip_invalid = not ip_valid
            self.show_box3_panel = True
        self.manual_device_entry = None
        try:
            from pigeon.app_state import write_saved_av_receiver, write_saved_streaming_device

            row = {"name": clean_name, "label": clean_name, "address": clean_ip}
            if box_num == 2:
                write_saved_streaming_device(row)
            elif box_num == 3:
                write_saved_av_receiver(row)
        except Exception:
            pass
        self.ensure_focus_ring()
        btn = f"main_box{box_num}_button"
        if btn in self.focus_ring:
            self.focus_index = self.focus_ring.index(btn)

    def box_device_results_locked(self) -> int | None:
        for box in (2, 3):
            if self._box_panel(box).results_locked:
                return box
        return None

    def refresh_location_slots(self) -> None:
        """Load (and pad to three) location slots for the dual-location picker."""
        from pigeon.app_state import add_empty_location_v2, read_all_locations_v2

        locs = read_all_locations_v2()
        while len(locs) < 3:
            add_empty_location_v2(_LOCATION_SLOT_DEFAULT_NAMES[len(locs)])
            locs = read_all_locations_v2()
        slots: list[tuple[str, str]] = []
        for i, loc in enumerate(locs[:3]):
            lid = str(loc.get("id") or "").strip()
            name = str(loc.get("name") or "").strip() or _LOCATION_SLOT_DEFAULT_NAMES[i]
            if lid:
                slots.append((lid, name))
        self.location_slots = tuple(slots)

    def location_slot(self, box_num: int) -> tuple[str, str] | None:
        idx = int(box_num) - 1
        if 0 <= idx < len(self.location_slots):
            return self.location_slots[idx]
        return None

    def enter_location_picker(self) -> None:
        self.show_network_picker = False
        self.close_update_popup()
        self.close_preferences()
        self.show_pigeon_settings = False
        self.show_location_picker = True
        self.renaming_location_id = ""
        self.renaming_location_slot = 0
        self.show_box1_panel = False
        self.show_box2_panel = False
        self.show_box3_panel = False
        for box_num in (2, 3):
            panel = self._box_panel(box_num)
            panel.active = False
            panel.scanning = False
            if panel.phase == "scanning":
                panel.phase = "idle"
        self.refresh_location_slots()
        self.ensure_focus_ring()
        if "main_dual_location_button" in self.focus_ring:
            self.focus_index = self.focus_ring.index("main_dual_location_button")

    def exit_location_picker(self) -> None:
        self.show_location_picker = False
        self.renaming_location_id = ""
        self.renaming_location_slot = 0
        self.ensure_focus_ring()
        if "main_dual_location_button" in self.focus_ring:
            self.focus_index = self.focus_ring.index("main_dual_location_button")

    def begin_rename_location_slot(self, box_num: int) -> bool:
        slot = self.location_slot(box_num)
        if slot is None:
            self.refresh_location_slots()
            slot = self.location_slot(box_num)
        if slot is None:
            return False
        lid, name = slot
        self.renaming_location_id = lid
        self.renaming_location_slot = int(box_num)
        self.location_name = name
        return True

    def select_location_slot(self, box_num: int) -> bool:
        """Make the nest in ``box_num`` the active location (dual-location picker)."""
        if self.location_switching:
            return False
        slot = self.location_slot(box_num)
        if slot is None:
            self.refresh_location_slots()
            slot = self.location_slot(box_num)
        if slot is None:
            return False
        lid, name = slot
        try:
            from pigeon.app_state import set_current_location_id

            if not set_current_location_id(str(lid)):
                return False
        except Exception:
            return False
        self.location_name = str(name or "").strip() or self.location_name
        self.renaming_location_id = ""
        self.renaming_location_slot = 0
        try:
            self.load_saved_box_devices()
        except Exception:
            pass
        return True

    def begin_location_switching(self) -> None:
        """Mark a location reload in progress (spinner appears after a short delay)."""
        self.location_switching = True
        self.location_switch_started_mono = time.monotonic()
        self.location_switch_angle_deg = 0.0

    def finish_location_switching(self) -> None:
        self.location_switching = False
        self.location_switch_angle_deg = 0.0

    def location_switch_spinner_visible(self) -> bool:
        if not self.location_switching:
            return False
        return (time.monotonic() - float(self.location_switch_started_mono)) >= float(
            _LOCATION_SWITCH_SPINNER_DELAY_S
        )

    def begin_rename_current_location(self) -> bool:
        """Prepare rename keyboard for the currently active location."""
        try:
            from pigeon.app_state import read_current_location_id, read_current_location_name

            lid = str(read_current_location_id() or "").strip()
            name = str(read_current_location_name() or "").strip()
        except Exception:
            lid, name = "", ""
        if not lid:
            # Fall back to slot 1 if current id is missing.
            slot = self.location_slot(1)
            if slot is None:
                self.refresh_location_slots()
                slot = self.location_slot(1)
            if slot is None:
                return False
            lid, name = slot
        self.renaming_location_id = lid
        self.renaming_location_slot = 0
        for box_num in (1, 2, 3):
            slot = self.location_slot(box_num)
            if slot is not None and str(slot[0]) == str(lid):
                self.renaming_location_slot = int(box_num)
                if not name:
                    name = str(slot[1] or "")
                break
        self.location_name = str(name or "").strip() or "Room"
        return True

    def ensure_focus_ring(self) -> None:
        locked_box = self.box_device_results_locked()
        if locked_box is not None:
            self.focus_ring = (f"main_box{locked_box}_device_results",)
            self.focus_index = 0
            return
        if not self.wifi_configured and self.show_network_picker:
            self.focus_ring = ("main_network_picker_button",)
            self.focus_index = 0
            return
        if not self.wifi_configured:
            ring = list(_WIFI_ONBOARDING_FOCUS)
            self.focus_ring = tuple(ring)
        else:
            ring = list(_PRIMARY_FOCUS_CANDIDATES)
            if self.show_network_picker:
                # Insert after dual network when present.
                if "main_dual_network_button" in ring:
                    i = ring.index("main_dual_network_button") + 1
                    ring.insert(i, "main_network_picker_button")
                else:
                    ring.append("main_network_picker_button")
            self.focus_ring = tuple(ring)
        if not self.focus_ring:
            self.focus_ring = ("main_exit_button",)
        self.focus_index = int(self.focus_index) % len(self.focus_ring)

    @property
    def focused_id(self) -> str:
        self.ensure_focus_ring()
        return self.focus_ring[int(self.focus_index) % len(self.focus_ring)]

    @property
    def pigeon_focused_id(self) -> str:
        from pigeon.widgets.pigeon_settings import (
            normalize_pigeon_focus_id,
            pigeon_focus_ring,
        )

        ring = pigeon_focus_ring()
        return normalize_pigeon_focus_id(
            ring[int(self.pigeon_focus_index) % len(ring)]
        )

    def enter_pigeon_settings(self) -> None:
        from pigeon.widgets.pigeon_settings import pigeon_focus_ring
        from pigeon.widgets.ui_color_settings import load_persisted_theme_into_state

        self.show_pigeon_settings = True
        self.show_box1_panel = False
        self.close_update_popup()
        self.close_preferences()
        self.close_ui_color()
        self.close_metadata_debug()
        load_persisted_theme_into_state(self)
        try:
            from pigeon.source_toggles import apply_toggles_to_settings_state

            apply_toggles_to_settings_state(self)
        except Exception:
            pass
        try:
            from pigeon.hdmi_ocr import hdmi_capture_available, probe_hdmi_presence

            self.pigeon_hdmi_ok = hdmi_capture_available()
            probe_hdmi_presence(force=True)
        except Exception:
            pass
        ring = pigeon_focus_ring()
        # Land on COLOR (first selectable tile); BACK remains in the ring.
        if "color_button" in ring:
            self.pigeon_focus_index = ring.index("color_button")
        elif "general_button" in ring:
            self.pigeon_focus_index = ring.index("general_button")
        else:
            self.pigeon_focus_index = 0
        # Prefetch update availability so the badge is ready without opening the popup.
        self.pigeon_needs_update_prefetch = True

    def open_metadata_debug(self) -> None:
        """Open the metadata inspector ([4]) on its first page (player)."""
        if not self.show_pigeon_settings:
            self.enter_pigeon_settings()
        self.close_update_popup()
        self.close_preferences()
        self.close_ui_color()
        self.show_metadata_debug = True
        self.metadata_debug_page = 0
        try:
            from pigeon.hdmi_ocr import probe_hdmi_presence

            probe_hdmi_presence(force=True)
        except Exception:
            pass

    def close_metadata_debug(self) -> None:
        self.show_metadata_debug = False
        self.metadata_debug_page = 0

    def navigate_metadata_debug(self, *, forward: bool = True) -> None:
        """Cycle player → hdmi → pigeon, wrapping in both directions."""
        step = 1 if forward else -1
        self.metadata_debug_page = (int(self.metadata_debug_page) + step) % 3

    def exit_pigeon_settings(self) -> None:
        self.close_update_popup()
        self.close_preferences()
        self.close_metadata_debug()
        self.show_pigeon_settings = False
        self.pigeon_needs_update_prefetch = False
        self.ensure_focus_ring()
        if "main_box1_button" in self.focus_ring:
            self.focus_index = self.focus_ring.index("main_box1_button")

    def open_preferences(self) -> None:
        """Open the now-playing zone widget selector (prefs / settings)."""
        from pigeon.widgets.preferences_settings import (
            preferences_zone_focus_ring,
            read_now_playing_zone_widgets,
        )

        self.show_preferences = True
        self.preferences_nav = "zones"
        self.preferences_active_zone = 0
        self.preferences_zone_widgets = read_now_playing_zone_widgets()
        ring = preferences_zone_focus_ring()
        # Land on zone1 — first editable target.
        self.preferences_focus_index = ring.index("zone1") if "zone1" in ring else 0
        try:
            from pigeon.hdmi_ocr import hdmi_capture_available, probe_hdmi_presence

            self.pigeon_hdmi_ok = hdmi_capture_available()
            probe_hdmi_presence(force=True)
        except Exception:
            pass

    def close_preferences(self) -> None:
        self.close_ui_color()
        self.show_preferences = False
        self.preferences_nav = "zones"
        self.preferences_active_zone = 0
        self.preferences_focus_index = 0

    def open_ui_color(self) -> None:
        """Open the system color customizer (from preferences color control)."""
        from pigeon.widgets.ui_color_settings import (
            apply_color_keys_to_state,
            read_ui_color_keys,
            ui_color_class_focus_ring,
        )

        keys = read_ui_color_keys()
        apply_color_keys_to_state(self, keys, persist=False)
        self.show_ui_color = True
        self.ui_color_nav = "classes"
        self.ui_color_active_class = ""
        ring = ui_color_class_focus_ring()
        self.ui_color_focus_index = ring.index("accent") if "accent" in ring else 0

    def close_ui_color(self) -> None:
        self.show_ui_color = False
        self.ui_color_nav = "classes"
        self.ui_color_active_class = ""
        self.ui_color_focus_index = 0

    @property
    def ui_color_focused_id(self) -> str:
        from pigeon.widgets.ui_color_settings import (
            ui_color_class_focus_ring,
            ui_color_swatch_focus_ring,
        )

        if str(self.ui_color_nav or "") == "swatches":
            ring = ui_color_swatch_focus_ring(str(self.ui_color_active_class or ""))
        else:
            ring = ui_color_class_focus_ring()
        if not ring:
            return "back"
        return ring[int(self.ui_color_focus_index) % len(ring)]

    def navigate_ui_color(self, *, forward: bool = True) -> None:
        from pigeon.widgets.ui_color_settings import (
            apply_color_keys_to_state,
            ui_color_class_focus_ring,
            ui_color_swatch_focus_ring,
        )

        step = 1 if forward else -1
        if str(self.ui_color_nav or "") == "swatches":
            cls = str(self.ui_color_active_class or "")
            ring = ui_color_swatch_focus_ring(cls)
            if not ring:
                return
            self.ui_color_focus_index = (
                int(self.ui_color_focus_index) + step
            ) % len(ring)
            focused = ring[self.ui_color_focus_index]
            keys = {
                "accent": self.ui_color_accent_key,
                "ui": self.ui_color_ui_key,
                "button": self.ui_color_button_key,
            }
            if cls in keys:
                keys[cls] = focused
                # Live preview theme while browsing swatches (persist on activate).
                apply_color_keys_to_state(self, keys, persist=False)
            return
        ring = ui_color_class_focus_ring()
        if not ring:
            return
        self.ui_color_focus_index = (
            int(self.ui_color_focus_index) + step
        ) % len(ring)

    def activate_ui_color(self) -> str:
        """Handle activate on the system color page."""
        from pigeon.widgets.preferences_settings import preferences_zone_focus_ring
        from pigeon.widgets.ui_color_settings import (
            apply_color_keys_to_state,
            ui_color_class_focus_ring,
            ui_color_swatch_focus_ring,
        )

        focused = self.ui_color_focused_id
        if str(self.ui_color_nav or "") != "swatches":
            if focused == "back":
                self.close_ui_color()
                # Return to preferences with the color control focused.
                ring = preferences_zone_focus_ring()
                if "color" in ring:
                    self.preferences_focus_index = ring.index("color")
                self.preferences_nav = "zones"
                return "ui_color_back"
            if focused in ("accent", "ui", "button"):
                self.ui_color_nav = "swatches"
                self.ui_color_active_class = focused
                ring = ui_color_swatch_focus_ring(focused)
                current = {
                    "accent": self.ui_color_accent_key,
                    "ui": self.ui_color_ui_key,
                    "button": self.ui_color_button_key,
                }.get(focused, ring[0] if ring else "")
                if current in ring:
                    self.ui_color_focus_index = ring.index(current)
                else:
                    self.ui_color_focus_index = 0
                return f"ui_color_class:{focused}"
            return "ui_color_noop"

        # Swatch activate — commit color and return focus to the class label.
        cls = str(self.ui_color_active_class or "")
        keys = {
            "accent": self.ui_color_accent_key,
            "ui": self.ui_color_ui_key,
            "button": self.ui_color_button_key,
        }
        if cls in keys and focused:
            keys[cls] = focused
        apply_color_keys_to_state(self, keys, persist=True)
        self.ui_color_nav = "classes"
        self.ui_color_active_class = ""
        class_ring = ui_color_class_focus_ring()
        if cls in class_ring:
            self.ui_color_focus_index = class_ring.index(cls)
        else:
            self.ui_color_focus_index = 0
        return f"ui_color_swatch:{cls}:{focused}"

    @property
    def preferences_focused_id(self) -> str:
        from pigeon.widgets.preferences_settings import (
            preferences_widget_focus_ring,
            preferences_zone_focus_ring,
        )

        if str(self.preferences_nav or "") == "widgets":
            ring = preferences_widget_focus_ring(int(self.preferences_active_zone))
        else:
            ring = preferences_zone_focus_ring()
        if not ring:
            return "exit"
        return ring[int(self.preferences_focus_index) % len(ring)]

    def navigate_preferences(self, *, forward: bool = True) -> None:
        from pigeon.widgets.preferences_settings import (
            preferences_widget_focus_ring,
            preferences_zone_focus_ring,
            write_now_playing_zone_widgets,
        )

        step = 1 if forward else -1
        if str(self.preferences_nav or "") == "widgets":
            zone = int(self.preferences_active_zone)
            ring = preferences_widget_focus_ring(zone)
            if not ring:
                return
            self.preferences_focus_index = (
                int(self.preferences_focus_index) + step
            ) % len(ring)
            focused = ring[self.preferences_focus_index]
            # Live-assign the focused widget (skip BACK). Preview only — persist on activate.
            if focused != "exit" and 1 <= zone <= 5:
                widgets = list(self.preferences_zone_widgets)
                widgets[zone - 1] = focused
                self.preferences_zone_widgets = (
                    widgets[0],
                    widgets[1],
                    widgets[2],
                    widgets[3],
                    widgets[4],
                )
                write_now_playing_zone_widgets(
                    self.preferences_zone_widgets, persist=False
                )
            return
        ring = preferences_zone_focus_ring()
        if not ring:
            return
        self.preferences_focus_index = (
            int(self.preferences_focus_index) + step
        ) % len(ring)

    def activate_preferences(self) -> str:
        """Handle activate while the preferences selector is open."""
        from pigeon.widgets.preferences_settings import (
            preferences_widget_focus_ring,
            write_now_playing_zone_widgets,
        )

        focused = self.preferences_focused_id
        if str(self.preferences_nav or "") != "widgets":
            if focused == "exit":
                self.close_preferences()
                return "preferences_exit"
            if focused == "color":
                self.open_ui_color()
                return "preferences_color"
            if focused.startswith("zone"):
                try:
                    zone = int(focused.replace("zone", ""))
                except ValueError:
                    return "preferences_noop"
                self.preferences_nav = "widgets"
                self.preferences_active_zone = zone
                ring = preferences_widget_focus_ring(zone)
                current = (
                    self.preferences_zone_widgets[zone - 1]
                    if 1 <= zone <= 5
                    else "exit"
                )
                if current in ring:
                    self.preferences_focus_index = ring.index(current)
                else:
                    self.preferences_focus_index = 0
                return f"preferences_zone:{zone}"
            return "preferences_noop"

        # Widget navigation — BACK always returns to pigeon settings.
        if focused == "exit":
            write_now_playing_zone_widgets(self.preferences_zone_widgets)
            self.close_preferences()
            return "preferences_exit"
        # Confirm focused widget, persist, and return to zone navigation.
        zone = int(self.preferences_active_zone)
        if 1 <= zone <= 5 and focused and focused != "exit":
            widgets = list(self.preferences_zone_widgets)
            widgets[zone - 1] = focused
            self.preferences_zone_widgets = (
                widgets[0],
                widgets[1],
                widgets[2],
                widgets[3],
                widgets[4],
            )
        write_now_playing_zone_widgets(self.preferences_zone_widgets)
        self.preferences_nav = "zones"
        self.preferences_active_zone = 0
        from pigeon.widgets.preferences_settings import preferences_zone_focus_ring

        zring = preferences_zone_focus_ring()
        zid = f"zone{zone}"
        self.preferences_focus_index = zring.index(zid) if zid in zring else 0
        return f"preferences_widget:{focused}"

    def open_update_popup(self) -> None:
        """Show the GitHub update popup and always start a fresh check.

        Prior poll results are cleared so the Update button never reuses a
        cached available / up-to-date decision.
        """
        from pigeon.widgets.update_popup import update_popup_focus_ring

        self.show_update_popup = True
        self.update_applying = False
        self.update_progress = 0.0
        self.update_error = None
        self.update_local_version = self.version_string
        # Drop cache — settings_pigeon Update always re-queries GitHub.
        self.update_available = False
        self.update_remote_version = None
        self.update_github_branch = None
        self.update_checking = True
        self.update_changelog = "Checking GitHub for updates…"
        ring = update_popup_focus_ring(update_available=False)
        self.update_popup_focus_index = ring.index("now") if "now" in ring else 0

    def close_update_popup(self) -> None:
        self.show_update_popup = False
        self.update_checking = False
        self.update_applying = False
        self.update_progress = 0.0
        self.update_popup_focus_index = 0

    def navigate_update_popup(self, *, forward: bool = True) -> None:
        from pigeon.widgets.update_popup import update_popup_focus_ring

        ring = update_popup_focus_ring(update_available=bool(self.update_available))
        if not ring:
            return
        step = 1 if forward else -1
        self.update_popup_focus_index = (int(self.update_popup_focus_index) + step) % len(ring)

    @property
    def update_popup_focused_choice(self) -> str:
        from pigeon.widgets.update_popup import update_popup_focus_ring

        ring = update_popup_focus_ring(update_available=bool(self.update_available))
        if not ring:
            return "now"
        return ring[int(self.update_popup_focus_index) % len(ring)]

    def navigate_pigeon(self, *, forward: bool = True) -> None:
        from pigeon.widgets.pigeon_settings import pigeon_focus_ring

        if self.show_metadata_debug:
            self.navigate_metadata_debug(forward=forward)
            return
        if self.show_ui_color:
            self.navigate_ui_color(forward=forward)
            return
        if self.show_preferences:
            self.navigate_preferences(forward=forward)
            return
        if self.show_update_popup:
            self.navigate_update_popup(forward=forward)
            return
        ring = pigeon_focus_ring()
        step = 1 if forward else -1
        self.pigeon_focus_index = (int(self.pigeon_focus_index) + step) % len(ring)

    def navigate_picker_row(self, *, forward: bool) -> bool:
        """Move selection among picker rows; wraps at the ends of the full list."""
        if not self.show_network_picker:
            return False
        n = len(self.wifi_networks)
        if n <= 0:
            return False
        visible = len(_PICKER_ROW_TEXTS)
        step = 1 if forward else -1
        cur = max(0, min(int(self.network_picker_absolute_row), n - 1))
        abs_row = (cur + step) % n
        max_scroll = max(0, n - visible)
        scroll = int(self.network_picker_scroll)
        if abs_row < scroll:
            scroll = abs_row
        elif abs_row >= scroll + visible:
            scroll = abs_row - visible + 1
        scroll = max(0, min(scroll, max_scroll))
        self.network_picker_scroll = scroll
        self.network_picker_row = abs_row - scroll
        self.network_picker_arrow = ""
        return True

    def navigate_picker_scroll(self, *, up: bool) -> bool:
        """Deprecated: vertical picker scroll disabled (left/right only)."""
        return False

    @property
    def network_picker_can_scroll_up(self) -> bool:
        return int(self.network_picker_scroll) > 0

    @property
    def network_picker_can_scroll_down(self) -> bool:
        n = len(self.wifi_networks)
        max_scroll = max(0, n - len(_PICKER_ROW_TEXTS))
        return int(self.network_picker_scroll) < max_scroll

    @property
    def network_picker_absolute_row(self) -> int:
        return int(self.network_picker_scroll) + int(self.network_picker_row)

    def start_box_device_scan(self, box_num: int) -> None:
        panel = self._box_panel(box_num)
        panel.active = True
        panel.phase = "scanning"
        panel.scanning = True
        panel.scan_started_mono = time.monotonic()
        panel.scan_angle_deg = 0.0
        panel.devices = ()
        panel.device_rows = ()
        panel.scroll = 0
        panel.row = 0
        panel.arrow = ""
        # Keep panel.picked so abort/cancel can restore the prior device view.
        if box_num == 2:
            self.show_box2_panel = True
        elif box_num == 3:
            self.show_box3_panel = True

    def abort_box_device_scan_ui(self, box_num: int) -> bool:
        """Instantly dismiss the scanning UI and restore the prior idle/device screen."""
        panel = self._box_panel(box_num)
        if not panel.scanning:
            return False
        self.restore_box_device_panel(box_num)
        return True

    def complete_box_device_scan(
        self,
        box_num: int,
        scan_result: tuple[tuple[tuple[str, str], ...], tuple[dict[str, str], ...]] | None = None,
    ) -> None:
        panel = self._box_panel(box_num)
        panel.scanning = False
        panel.phase = "results"
        if scan_result and len(scan_result) == 2:
            devices, device_rows = scan_result
            saved = self.saved_box_device(box_num)
            if saved and devices:
                dev_list = list(devices)
                row_list = list(device_rows)
                match_idx: int | None = None
                saved_name, saved_ip = saved
                for i, (name, ip) in enumerate(dev_list):
                    if ip == saved_ip or (
                        name.lower() == saved_name.lower() and ip == saved_ip
                    ):
                        match_idx = i
                        break
                if match_idx is not None and match_idx > 0:
                    dev_item = dev_list.pop(match_idx)
                    dev_list.insert(0, dev_item)
                    if match_idx < len(row_list):
                        row_item = row_list.pop(match_idx)
                        row_list.insert(0, row_item)
                devices = tuple(dev_list)
                device_rows = tuple(row_list)
            panel.devices = box_devices_with_special_rows(devices)
            panel.device_rows = device_rows
        else:
            panel.devices = box_devices_with_special_rows(())
            panel.device_rows = ()
        panel.scroll = 0
        panel.row = 0
        panel.arrow = ""
        self.ensure_focus_ring()

    def navigate_box_device_row(self, box_num: int, *, forward: bool) -> bool:
        """Move selection among device rows; wraps at the ends of the full list."""
        panel = self._box_panel(box_num)
        if not panel.results_locked:
            return False
        n = len(panel.devices)
        if n <= 0:
            return False
        visible = int(_BOX_DEVICE_ROW_COUNT)
        step = 1 if forward else -1
        cur = max(0, min(int(panel.absolute_row()), n - 1))
        abs_row = (cur + step) % n
        max_scroll = panel.max_scroll()
        scroll = int(panel.scroll)
        if abs_row < scroll:
            scroll = abs_row
        elif abs_row >= scroll + visible:
            scroll = abs_row - visible + 1
        scroll = max(0, min(scroll, max_scroll))
        panel.scroll = scroll
        panel.row = abs_row - scroll
        panel.arrow = ""
        return True

    def navigate_box_device_scroll(self, box_num: int, *, up: bool) -> bool:
        """Deprecated: vertical device-list scroll disabled (left/right only)."""
        return False

    def pick_box_device(self, box_num: int) -> dict[str, str] | None:
        panel = self._box_panel(box_num)
        if not panel.results_locked:
            return None
        row = device_row_from_pick(
            panel.devices,
            panel.device_rows,
            scroll=int(panel.scroll),
            row=int(panel.row),
        )
        if row is None:
            return None
        special = str(row.get("special") or "")
        if special == "cancel":
            self.restore_box_device_panel(box_num)
            return None
        if special == "enter_ip":
            self.start_manual_device_entry(box_num)
            self.ensure_focus_ring()
            return None
        if self.device_row_matches_saved(box_num, row):
            self.restore_box_device_panel(box_num)
            return None
        panel.active = False
        panel.phase = "idle"
        panel.scanning = False
        panel.devices = ()
        panel.device_rows = ()
        panel.scroll = 0
        panel.row = 0
        panel.arrow = ""
        if box_num == 2:
            self.show_box2_panel = False
        elif box_num == 3:
            self.show_box3_panel = False
        self.ensure_focus_ring()
        btn = f"main_box{box_num}_button"
        if btn in self.focus_ring:
            self.focus_index = self.focus_ring.index(btn)
        return row

    def clear_box_pairing(self) -> None:
        self.box_pairing = None

    def start_box_pairing(self, box_num: int, row: dict[str, str]) -> None:
        dn = str(row.get("name") or row.get("label") or row.get("address") or "Device")
        self.box_pairing = BoxPairingSession(box_num=int(box_num), row=dict(row), device_name=dn)

    def navigate(self, *, forward: bool = True) -> None:
        if self.keyboard is not None:
            self.keyboard.navigate(forward=forward)
            return
        locked = self.box_device_results_locked()
        if locked is not None:
            self.navigate_box_device_row(locked, forward=forward)
            return
        if self.show_network_picker and not self.wifi_configured:
            self.navigate_picker_row(forward=forward)
            return
        if (
            self.show_network_picker
            and self.focused_id == "main_network_picker_button"
            and self.navigate_picker_row(forward=forward)
        ):
            return
        self.ensure_focus_ring()
        n = len(self.focus_ring)
        step = 1 if forward else -1
        self.focus_index = (int(self.focus_index) + step) % n

    def open_keyboard(
        self,
        target: str,
        *,
        assets_dir: Path | str | None = None,
        trigger_button: str | None = None,
    ) -> None:
        from pigeon.widgets.settings_keyboard import focus_first_letter, focus_keyboard_go, open_keyboard

        buffer_prefill = ""
        if target == "location":
            initial = ""
            buffer_prefill = self.location_name
        elif target == "network":
            initial = ""
            buffer_prefill = self.wifi_password
        elif target == "pin":
            initial = ""
        elif target == "device_name":
            entry = self.manual_device_entry
            initial = str(entry.name or "") if entry is not None else ""
            if initial:
                buffer_prefill = initial
                initial = ""
        else:
            initial = ""
        mode_override = None
        if target == "device_ip":
            from pigeon.widgets.settings_keyboard import KeyboardMode

            mode_override = KeyboardMode.NUMERIC_IP
        elif target == "device_name":
            from pigeon.widgets.settings_keyboard import KeyboardMode

            mode_override = KeyboardMode.QWERTY_UPPER
        elif target == "wifi_logout":
            from pigeon.widgets.settings_keyboard import KeyboardMode

            mode_override = KeyboardMode.YES_NO
        self.keyboard = open_keyboard(
            target=target,
            initial_text=initial,
            buffer=buffer_prefill,
            theme=self.theme,
            assets_dir=assets_dir,
            mode=mode_override,
        )
        for box_num in (2, 3):
            panel = self._box_panel(box_num)
            panel.scanning = False
        if target == "network" or trigger_button == "main_dual_network_button":
            focus_keyboard_go(self.keyboard, assets_dir=assets_dir)
        elif trigger_button == "main_dual_location_button":
            focus_first_letter(self.keyboard, assets_dir=assets_dir)
        elif target == "device_ip":
            from pigeon.widgets.settings_keyboard import KeyboardMode, focus_numeric_one

            self.keyboard.set_mode(KeyboardMode.NUMERIC_IP, assets_dir=assets_dir)
            focus_numeric_one(self.keyboard, assets_dir=assets_dir)
        elif target == "device_name":
            focus_first_letter(self.keyboard, assets_dir=assets_dir)
        elif target == "wifi_logout":
            from pigeon.widgets.settings_keyboard import focus_yes_no_yes

            focus_yes_no_yes(self.keyboard, assets_dir=assets_dir)
        else:
            focus_first_letter(self.keyboard, assets_dir=assets_dir)

    def close_keyboard(self, *, commit: bool = False) -> tuple[str, str]:
        """Close keyboard. Returns ``(target, buffer_text)``."""
        kb = self.keyboard
        if kb is None:
            return "", ""
        target = str(getattr(kb, "target", "") or "")
        buffer = str(getattr(kb, "buffer", "") or "")
        if commit:
            if target == "location":
                self.location_name = buffer
            elif target == "network":
                self.wifi_password = buffer
                self.network_name = buffer
        self.keyboard = None
        return target, buffer

    def enter_wifi_onboarding(self) -> None:
        if self.wifi_configured:
            return
        self.wifi_onboarding = True
        self.show_instructions = False
        self.show_network_picker = False
        self.wifi_scanning = False
        self.wifi_networks = ()
        self.ensure_focus_ring()
        if "main_dual_network_button" in self.focus_ring:
            self.focus_index = self.focus_ring.index("main_dual_network_button")

    def start_wifi_scan(self) -> None:
        if self.wifi_scanning:
            return
        self.wifi_scanning = True
        self.wifi_scan_started_mono = time.monotonic()
        self.wifi_scan_angle_deg = 0.0
        self.show_network_picker = False
        self.wifi_networks = ()

    def complete_wifi_scan(self, networks: tuple[str, ...] | None = None) -> None:
        from pigeon.wifi_scan import filter_scan_results_for_picker

        self.wifi_scanning = False
        self.wifi_networks = filter_scan_results_for_picker(networks or ())
        self.show_network_picker = True
        self.network_picker_row = 0
        self.network_picker_scroll = 0
        self.ensure_focus_ring()
        if "main_network_picker_button" in self.focus_ring:
            self.focus_index = self.focus_ring.index("main_network_picker_button")

    def select_wifi_network(self, ssid: str) -> None:
        self.pending_wifi_ssid = str(ssid or "").strip()
        self.network_password_error = False
        self.wifi_onboarding = False
        self.show_instructions = False
        self.show_network_picker = False
        self.wifi_scanning = False
        self.wifi_networks = ()
        self.ensure_focus_ring()

def decode_svg_id(raw_id: str) -> str:
    """Decode Illustrator XML id encoding (``_x5F_`` → ``_``, ``_x30_`` → ``0``, …)."""
    if not raw_id:
        return ""
    out: list[str] = []
    i = 0
    n = len(raw_id)
    while i < n:
        if raw_id.startswith("_x5F_", i):
            out.append("_")
            i += 5
            continue
        if i + 5 <= n and raw_id[i] == "_" and raw_id[i + 1] == "x" and raw_id[i + 4] == "_":
            hx = raw_id[i + 2 : i + 4]
            try:
                out.append(chr(int(hx, 16)))
                i += 5
                continue
            except ValueError:
                pass
        out.append(raw_id[i])
        i += 1
    return "".join(out)


def encode_svg_id(logical_id: str) -> str:
    """Encode a logical layer id for Illustrator-style SVG ``id`` attributes."""
    if not logical_id:
        return ""
    body = logical_id
    if body.startswith("0"):
        body = "_x30_" + body[1:]

    def _enc_char(ch: str) -> str:
        if ch.isalnum() or ch in ".-|":
            return ch
        if ch == "_":
            return "_x5F_"
        return f"_x{ord(ch):02X}_"

    return "".join(_enc_char(c) for c in body)


def _strip_ai_uniqueness(decoded_id: str) -> str:
    """Drop Illustrator uniqueness suffixes like ``_0000016089…``."""
    return _AI_SUFFIX_RE.sub("", decoded_id)


@lru_cache(maxsize=16384)
def _normalize_logical(raw_or_logical: str) -> str:
    """Cache Illustrator id decode — each apply/raster normalizes the same ids thousands of times."""
    return _strip_ai_uniqueness(decode_svg_id(raw_or_logical))


def default_main_settings_svg_path(assets_dir: Path | str | None = None) -> Path:
    """Resolve ``settings_main.svg`` (override with ``PIGEON_MAIN_SETTINGS_SVG``)."""
    env = os.environ.get("PIGEON_MAIN_SETTINGS_SVG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if assets_dir is not None:
        return Path(assets_dir) / "settings_0.8" / "settings_main.svg"
    pigeon_root = Path(__file__).resolve().parents[3]
    return pigeon_root / "pigeonAssets" / "settings_0.8" / "settings_main.svg"


def keyboard_svg_path(
    name: str,
    *,
    assets_dir: Path | str | None = None,
) -> Path:
    """Resolve a keyboard SVG under ``settings_0.8/`` (stub helper)."""
    base = Path(assets_dir) if assets_dir is not None else Path(__file__).resolve().parents[3] / "pigeonAssets"
    return Path(base) / "settings_0.8" / name


# Full-frame registration PNG for the box1 pigeon mark (replaces the SVG wing polygon).
_BOX1_PIGEON_LOGO_PNG = "settings_main_box1_pigeon_logo.png"
_BOX1_PIGEON_LOGO_CACHE: dict[tuple[str, float, int, int], np.ndarray] = {}


def box1_pigeon_logo_png_path(*, assets_dir: Path | str | None = None) -> Path:
    """Resolve the full-screen box1 pigeon logo under ``settings_0.8/``."""
    base = Path(assets_dir) if assets_dir is not None else Path(__file__).resolve().parents[3] / "pigeonAssets"
    return Path(base) / "settings_0.8" / _BOX1_PIGEON_LOGO_PNG


def _hide_box1_wing_logo_polygon(root: ET.Element) -> None:
    """Drop the Illustrator wing polygon; left/right rules stay for chrome."""
    icon = _find_by_logical_id(root, "main_box1_pigeon_logo_icon")
    if icon is None:
        return
    for el in icon.iter():
        if el.tag.endswith("polygon"):
            _set_visible(el, False)


def _load_box1_pigeon_logo_overlay_bgra(
    *,
    assets_dir: Path | str | None = None,
) -> np.ndarray | None:
    """Load + scale the registration PNG to the design canvas (BGRA)."""
    path = box1_pigeon_logo_png_path(assets_dir=assets_dir)
    if not path.is_file():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    key = (str(path.resolve()), float(mtime), int(DESIGN_W), int(DESIGN_H))
    hit = _BOX1_PIGEON_LOGO_CACHE.get(key)
    if hit is not None:
        return hit
    src = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if src is None or src.size == 0:
        return None
    if src.ndim == 2:
        src = cv2.cvtColor(src, cv2.COLOR_GRAY2BGRA)
    elif src.shape[2] == 3:
        src = cv2.cvtColor(src, cv2.COLOR_BGR2BGRA)
    elif src.shape[2] != 4:
        return None
    if src.shape[0] != DESIGN_H or src.shape[1] != DESIGN_W:
        src = cv2.resize(src, (DESIGN_W, DESIGN_H), interpolation=cv2.INTER_AREA)
    _BOX1_PIGEON_LOGO_CACHE.clear()
    _BOX1_PIGEON_LOGO_CACHE[key] = src
    return src


def _draw_box1_pigeon_logo_overlay(
    bgra: np.ndarray,
    *,
    assets_dir: Path | str | None = None,
    ui_hex: str = COLOR_UI_DEFAULT,
) -> None:
    """Composite the full-frame box1 pigeon mark, tinted brand red like the SVG icons."""
    overlay = _load_box1_pigeon_logo_overlay_bgra(assets_dir=assets_dir)
    if overlay is None or overlay.shape[:2] != bgra.shape[:2]:
        return
    if not np.any(overlay[:, :, 3]):
        return
    tinted = overlay.copy()
    b, g, r = _hex_to_bgr(ui_hex or COLOR_UI_DEFAULT)
    tinted[:, :, 0] = b
    tinted[:, :, 1] = g
    tinted[:, :, 2] = r
    blended = _composite_bgra_over_bgra(bgra, tinted)
    bgra[:] = blended


def _parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    parents: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in parent:
            parents[child] = parent
    return parents


def _is_descendant_of(el: ET.Element, ancestor: ET.Element, parents: dict[ET.Element, ET.Element]) -> bool:
    cur: ET.Element | None = el
    while cur is not None:
        if cur is ancestor:
            return True
        cur = parents.get(cur)
    return False


def _needs_wifi_setup(state: MainSettingsState) -> bool:
    return state.needs_wifi_setup()


def _wifi_onboarding_add_search_group(root: ET.Element) -> ET.Element | None:
    return _find_by_logical_id(root, "main_box2_add_search_icon")


def _is_onboarding_search_group(
    root: ET.Element,
    search_group: ET.Element,
    parents: dict[ET.Element, ET.Element],
) -> bool:
    add_group = _wifi_onboarding_add_search_group(root)
    return add_group is not None and _is_descendant_of(search_group, add_group, parents)


def _find_box_root_group(root: ET.Element, box_num: int) -> ET.Element | None:
    want = f"main_box{box_num}"
    for el in root.iter():
        if not el.tag.endswith("g"):
            continue
        if _normalize_logical(el.get("id") or "") == want:
            return el
    return None


def _set_box_columns_visible(root: ET.Element, visible: bool) -> None:
    """Show/hide the three main box column groups (``main_box1`` … ``main_box3``)."""
    for i in (1, 2, 3):
        _set_visible(_find_box_root_group(root, i), visible)


def _apply_wifi_onboarding_search_layers(root: ET.Element, state: MainSettingsState) -> None:
    """
    WiFi discovery chrome under ``main_instructions``.

    Idle onboarding: ``main_box2_search_icon`` + ``main_box2_+_icon``.
    Scanning: only the search glyph (triangles + ellipse strokes via overlay spin).
    """
    plus_group = _find_by_logical_id(root, "main_box2_+_icon")
    search_group = _wifi_onboarding_search_group(root)
    welcome_el = _find_by_logical_id(root, "welcome_to_pigeon_find_your_wifi_text")
    _set_visible(welcome_el, False)
    scanning = bool(state.wifi_scanning or state.wifi_connecting)
    capture = bool(getattr(state, "spinner_glyph_capture", False))
    if scanning or capture:
        _set_visible(plus_group, False)
        if search_group is not None:
            _set_visible(search_group, True)
        return
    _set_visible(plus_group, True)
    if search_group is not None:
        _set_visible(search_group, True)


def _hide_wifi_onboarding_search_svg_glyphs(root: ET.Element) -> None:
    """Hide leftover clip-path circles. Stroke arcs + filled triangles stay in the SVG."""
    search_group = _wifi_onboarding_search_group(root)
    if search_group is None:
        return
    for el in search_group.iter():
        if el.tag.endswith("circle"):
            _set_visible(el, False)


def _hide_wifi_onboarding_search_svg_circles(root: ET.Element) -> None:
    """Backward-compatible alias."""
    _hide_wifi_onboarding_search_svg_glyphs(root)


_SVG_ARC_PATH_RE = re.compile(
    r"M\s*([-\d.]+)(?:\s*,\s*|\s+)([-\d.]+)\s*"
    r"A\s*([-\d.]+)(?:\s*,\s*|\s+)([-\d.]+)\s+"
    r"([-\d.]+)\s+(\d)\s+(\d)\s+"
    r"([-\d.]+)(?:\s*,\s*|\s+)([-\d.]+)",
    re.IGNORECASE,
)


def _svg_arc_center_and_angles(
    x1: float,
    y1: float,
    rx: float,
    ry: float,
    phi_deg: float,
    fa: int,
    fs: int,
    x2: float,
    y2: float,
) -> tuple[float, float, float, float] | None:
    """Endpoint-parameterized SVG arc → center + start/end degrees (0=east, y-down)."""
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-6 or ry < 1e-6:
        return None
    phi = math.radians(phi_deg)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx = (x1 - x2) / 2.0
    dy = (y1 - y2) / 2.0
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = 0.0 if den <= 1e-12 else math.sqrt(max(0.0, num / den))
    if fa == fs:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = coef * -ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2.0
    start = math.degrees(math.atan2(y1 - cy, x1 - cx)) % 360.0
    end = math.degrees(math.atan2(y2 - cy, x2 - cx)) % 360.0
    return cx, cy, start, end


def _wedge_clip_corners(
    cx: float,
    cy: float,
    radius: float,
    start_deg: float,
    end_deg: float,
    *,
    clockwise: bool,
    pad: float,
) -> tuple[tuple[float, float], ...]:
    """Pie wedge covering a stroked arc so OpenCV can clip a ring to that sweep."""
    sweep = (end_deg - start_deg) % 360.0 if clockwise else (start_deg - end_deg) % 360.0
    if sweep <= 0.0:
        sweep = 360.0
    n = max(12, int(sweep / 4.0) + 1)
    outer = radius + pad
    pts: list[tuple[float, float]] = [(cx, cy)]
    step = sweep / n if clockwise else -sweep / n
    for i in range(n + 1):
        ang = math.radians(start_deg + step * i)
        pts.append((cx + outer * math.cos(ang), cy + outer * math.sin(ang)))
    return tuple(pts)


def _path_stroke_width(el: ET.Element) -> float:
    style = el.get("style") or ""
    sw_m = re.search(r"stroke-width\s*:\s*([-\d.]+)", style, re.IGNORECASE)
    if sw_m:
        return float(sw_m.group(1))
    try:
        return float(el.get("stroke-width") or 8.0)
    except ValueError:
        return 8.0


def _arc_spec_from_search_path(el: ET.Element) -> _OnboardingSearchArcSpec | None:
    """Stroke-only ellipse arc path (no clip-path) → spinner overlay spec."""
    parsed = _SVG_ARC_PATH_RE.search(el.get("d") or "")
    if parsed is None:
        return None
    x1, y1 = float(parsed.group(1)), float(parsed.group(2))
    rx, ry = float(parsed.group(3)), float(parsed.group(4))
    phi = float(parsed.group(5))
    fa, fs = int(parsed.group(6)), int(parsed.group(7))
    x2, y2 = float(parsed.group(8)), float(parsed.group(9))
    converted = _svg_arc_center_and_angles(x1, y1, rx, ry, phi, fa, fs, x2, y2)
    if converted is None:
        return None
    cx, cy, start_deg, end_deg = converted
    radius = (abs(rx) + abs(ry)) / 2.0
    stroke_w = _path_stroke_width(el)
    corners = _wedge_clip_corners(
        cx,
        cy,
        radius,
        start_deg,
        end_deg,
        clockwise=bool(fs),
        pad=stroke_w,
    )
    return _OnboardingSearchArcSpec(
        cx_svg=cx,
        cy_svg=cy,
        radius_svg=radius,
        stroke_svg=stroke_w,
        clip_corners_svg=corners,
    )


def _discover_search_arc_specs_in_group(
    root: ET.Element,
    search_group: ET.Element,
) -> tuple[_OnboardingSearchArcSpec, ...]:
    """Ring arcs from a search-icon subtree (path strokes, or legacy clip-path circles)."""
    parents = _parent_map(root)
    if _is_subtree_hidden(search_group, parents):
        return ()
    specs: list[_OnboardingSearchArcSpec] = []
    for el in search_group.iter():
        if not el.tag.endswith("g"):
            continue
        logical = _normalize_logical(el.get("id") or "")
        if "eplipse" not in logical and "ellipse" not in logical:
            continue
        rect_el: ET.Element | None = None
        circle_el: ET.Element | None = None
        path_el: ET.Element | None = None
        for node in el.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "rect" and node.get("width") and node.get("height"):
                rect_el = node
            if tag == "circle" and node.get("cx") and node.get("r"):
                circle_el = node
            if tag == "path" and "A" in (node.get("d") or "").upper():
                path_el = node
        if path_el is not None:
            spec = _arc_spec_from_search_path(path_el)
            if spec is not None:
                specs.append(spec)
            continue
        if rect_el is None or circle_el is None:
            continue
        try:
            x = float(rect_el.get("x") or 0.0)
            y = float(rect_el.get("y") or 0.0)
            w = float(rect_el.get("width") or 0.0)
            h = float(rect_el.get("height") or 0.0)
        except ValueError:
            continue
        matrix = _parse_svg_matrix(rect_el.get("transform"))
        if matrix is None:
            corners = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
        else:
            corners = _transform_rect_corners_svg(x, y, w, h, matrix)
        cx, cy, radius, stroke_w, _ = _circle_stroke_from_element(circle_el)
        specs.append(
            _OnboardingSearchArcSpec(
                cx_svg=cx,
                cy_svg=cy,
                radius_svg=radius,
                stroke_svg=stroke_w,
                clip_corners_svg=corners,
            )
        )
    return tuple(specs)


def _discover_onboarding_search_arc_specs(root: ET.Element) -> tuple[_OnboardingSearchArcSpec, ...]:
    """Clip-masked ring arcs for onboarding ``main_box2_search_icon``."""
    search_group = _wifi_onboarding_search_group(root)
    if search_group is None:
        return ()
    return _discover_search_arc_specs_in_group(root, search_group)


def _find_box_column_search_icon(root: ET.Element, box_num: int) -> ET.Element | None:
    """Column ``main_box{N}_search_icon`` (skip WiFi onboarding art under add-search)."""
    parents = _parent_map(root)
    add_group = _wifi_onboarding_add_search_group(root)
    box_root = _find_box_root_group(root, box_num)
    want = f"main_box{box_num}_search_icon"
    for el in _find_all_by_logical_id(root, want):
        if add_group is not None and _is_descendant_of(el, add_group, parents):
            continue
        if box_root is not None and not _is_descendant_of(el, box_root, parents):
            continue
        return el
    for el in _find_all_by_logical_id(root, want):
        if add_group is not None and _is_descendant_of(el, add_group, parents):
            continue
        return el
    return None


def _discover_box_search_arc_specs(
    root: ET.Element,
    box_num: int,
) -> tuple[_OnboardingSearchArcSpec, ...]:
    """Clip-masked ring arcs for ``main_box{N}_search_icon`` column spinners."""
    search_group = _find_box_column_search_icon(root, box_num)
    if search_group is None:
        return ()
    return _discover_search_arc_specs_in_group(root, search_group)


def _discover_onboarding_search_triangle_specs(
    root: ET.Element,
) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Filled arrow triangles for onboarding ``main_box2_search_icon``."""
    search_group = _wifi_onboarding_search_group(root)
    if search_group is None:
        return ()
    parents = _parent_map(root)
    if _is_subtree_hidden(search_group, parents):
        return ()
    specs: list[tuple[tuple[float, float], ...]] = []
    for el in search_group.iter():
        if not el.tag.endswith("polygon"):
            continue
        pts = _parse_svg_points(el.get("points") or "")
        if pts:
            specs.append(pts)
    return tuple(specs)


def _draw_onboarding_search_triangle_overlays(
    bgra: np.ndarray,
    specs: tuple[tuple[tuple[float, float], ...], ...],
) -> None:
    for pts in specs:
        px_pts = np.array([_svg_to_px(x, y) for x, y in pts], dtype=np.int32)
        mask = np.zeros(bgra.shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, [px_pts], 255)
        _composite_stroke_mask(bgra, mask, _SEARCH_GLYPH_BGR)


def _draw_onboarding_search_arc_stroke(
    bgra: np.ndarray,
    spec: _OnboardingSearchArcSpec,
    *,
    color_bgr: tuple[int, int, int],
) -> None:
    cx, cy = _svg_to_px(spec.cx_svg, spec.cy_svg)
    radius = _svg_radius_to_px(spec.radius_svg)
    stroke = max(1, int(round(_svg_scale(spec.stroke_svg))))
    clip_pts = np.array(
        [_svg_to_px(x, y) for x, y in spec.clip_corners_svg],
        dtype=np.int32,
    )
    clip_mask = np.zeros(bgra.shape[:2], dtype=np.uint8)
    cv2.fillPoly(clip_mask, [clip_pts], 255)
    ring_mask = np.zeros(bgra.shape[:2], dtype=np.uint8)
    cv2.circle(ring_mask, (cx, cy), radius, 255, stroke, lineType=cv2.LINE_AA)
    ring_mask = cv2.bitwise_and(ring_mask, clip_mask)
    _composite_stroke_mask(bgra, ring_mask, color_bgr)


def _draw_onboarding_search_arc_overlays(
    bgra: np.ndarray,
    specs: tuple[_OnboardingSearchArcSpec, ...],
) -> None:
    for spec in specs:
        _draw_onboarding_search_arc_stroke(bgra, spec, color_bgr=_SEARCH_GLYPH_BGR)


def _draw_onboarding_search_arc_overlays_on_patch(
    patch: np.ndarray,
    specs: tuple[_OnboardingSearchArcSpec, ...],
    *,
    origin_x0: int,
    origin_y0: int,
    color_bgr: tuple[int, int, int] = _SEARCH_GLYPH_BGR,
) -> None:
    """Draw clipped arcs into a cropped glyph patch (offset-local coordinates)."""
    if patch.size == 0:
        return
    for spec in specs:
        cx, cy = _svg_to_px(spec.cx_svg, spec.cy_svg)
        cx -= origin_x0
        cy -= origin_y0
        radius = _svg_radius_to_px(spec.radius_svg)
        stroke = max(1, int(round(_svg_scale(spec.stroke_svg))))
        clip_pts = np.array(
            [(_svg_to_px(x, y)[0] - origin_x0, _svg_to_px(x, y)[1] - origin_y0) for x, y in spec.clip_corners_svg],
            dtype=np.int32,
        )
        clip_mask = np.zeros(patch.shape[:2], dtype=np.uint8)
        cv2.fillPoly(clip_mask, [clip_pts], 255)
        ring_mask = np.zeros(patch.shape[:2], dtype=np.uint8)
        cv2.circle(ring_mask, (cx, cy), radius, 255, stroke, lineType=cv2.LINE_AA)
        ring_mask = cv2.bitwise_and(ring_mask, clip_mask)
        _composite_stroke_mask(patch, ring_mask, color_bgr)


def _mask_wifi_search_glyph_patch(patch: np.ndarray) -> np.ndarray:
    """Keep glyph pixels (black fill/stroke) so rotation never includes background art."""
    out = patch.copy()
    out[:, :, 3] = np.where(out[:, :, 3] > 0, 255, 0).astype(np.uint8)
    return out


_SEARCH_SPINNER_STEPS = 36


def _precompute_rotated_patches(
    patch: np.ndarray,
    *,
    steps: int = _SEARCH_SPINNER_STEPS,
) -> tuple[np.ndarray, ...]:
    """Pre-render spinner rotations so animation avoids per-frame warpAffine."""
    ph, pw = patch.shape[:2]
    center = (pw * 0.5, ph * 0.5)
    frames: list[np.ndarray] = []
    step_deg = 360.0 / max(1, steps)
    for i in range(steps):
        angle = i * step_deg
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        frames.append(
            cv2.warpAffine(
                patch,
                matrix,
                (pw, ph),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(0, 0, 0, 0),
            )
        )
    return tuple(frames)


def _rotated_patch_for_angle(
    frames: tuple[np.ndarray, ...],
    angle_deg: float,
) -> np.ndarray:
    if not frames:
        raise ValueError("spinner frame cache is empty")
    step_deg = 360.0 / len(frames)
    idx = int(round(float(angle_deg) / step_deg)) % len(frames)
    return frames[idx]


def _blit_spinner_patch(frame: np.ndarray, patch: np.ndarray, *, cx: int, cy: int) -> None:
    ph, pw = patch.shape[:2]
    x0 = cx - pw // 2
    y0 = cy - ph // 2
    x1 = x0 + pw
    y1 = y0 + ph
    fx0 = max(0, x0)
    fy0 = max(0, y0)
    fx1 = min(frame.shape[1], x1)
    fy1 = min(frame.shape[0], y1)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    sx0 = fx0 - x0
    sy0 = fy0 - y0
    sx1 = sx0 + (fx1 - fx0)
    sy1 = sy0 + (fy1 - fy0)
    region = frame[fy0:fy1, fx0:fx1]
    strip = patch[sy0:sy1, sx0:sx1]
    alpha = strip[:, :, 3:4].astype(np.float32) / 255.0
    fg = strip[:, :, :3].astype(np.float32)
    bg = region[:, :, :3].astype(np.float32)
    blended = np.clip(fg * alpha + bg * (1.0 - alpha), 0, 255).astype(np.uint8)
    out_a = np.clip(
        alpha * 255.0 + (1.0 - alpha) * region[:, :, 3:4].astype(np.float32),
        0,
        255,
    ).astype(np.uint8)
    region[:, :, :3] = blended
    region[:, :, 3:4] = out_a


def _mask_search_glyph_patch(patch: np.ndarray, *, include_dark: bool) -> np.ndarray:
    """Isolate spinner glyph pixels (stroked rings and filled arrow triangles)."""
    out = patch.copy()
    b = out[:, :, 0].astype(np.int16)
    g = out[:, :, 1].astype(np.int16)
    r = out[:, :, 2].astype(np.int16)
    mx = np.maximum(np.maximum(r, g), b)
    bright = (b > 180) & (g > 180) & (r > 180)
    if include_dark:
        is_glyph = bright | ((mx > 18) & (mx < 210))
    else:
        is_glyph = bright
    out[:, :, 3] = np.where(is_glyph, 255, 0).astype(np.uint8)
    return out


def _set_svg_text_centered(el: ET.Element, x_svg: float, y_svg: float) -> None:
    """Place SVG ``<text>`` at ``(x, y)`` with middle/middle anchoring for rasterize."""
    el.set("transform", f"matrix(1 0 0 1 {x_svg} {y_svg})")
    el.set("text-anchor", "middle")
    el.set("dominant-baseline", "middle")
    style = el.get("style") or ""
    style = re.sub(r"text-anchor\s*:\s*[^;]+;?\s*", "", style, flags=re.IGNORECASE)
    style = re.sub(r"dominant-baseline\s*:\s*[^;]+;?\s*", "", style, flags=re.IGNORECASE)
    style = style.strip().rstrip(";")
    if style:
        el.set("style", style)
    elif "style" in el.attrib:
        el.attrib.pop("style")


def _set_svg_text_horiz_centered(el: ET.Element, x_svg: float, y_svg: float) -> None:
    """Horizontally center text while preserving the original SVG baseline ``y``."""
    el.set("transform", f"matrix(1 0 0 1 {x_svg} {y_svg})")
    el.set("text-anchor", "middle")
    el.attrib.pop("dominant-baseline", None)
    style = el.get("style") or ""
    style = re.sub(r"text-anchor\s*:\s*[^;]+;?\s*", "", style, flags=re.IGNORECASE)
    style = re.sub(r"dominant-baseline\s*:\s*[^;]+;?\s*", "", style, flags=re.IGNORECASE)
    style = style.strip().rstrip(";")
    if style:
        el.set("style", style)
    elif "style" in el.attrib:
        el.attrib.pop("style")


def _set_svg_text_right_aligned(el: ET.Element, x_svg: float, y_svg: float) -> None:
    el.set("transform", f"matrix(1 0 0 1 {x_svg} {y_svg})")
    el.set("text-anchor", "end")
    el.attrib.pop("dominant-baseline", None)
    style = el.get("style") or ""
    style = re.sub(r"text-anchor\s*:\s*[^;]+;?\s*", "", style, flags=re.IGNORECASE)
    style = style.strip().rstrip(";")
    if style:
        el.set("style", style)
    elif "style" in el.attrib:
        el.attrib.pop("style")


def _set_svg_text_left_aligned(el: ET.Element, x_svg: float, y_svg: float) -> None:
    el.set("transform", f"matrix(1 0 0 1 {x_svg} {y_svg})")
    el.set("text-anchor", "start")
    el.attrib.pop("dominant-baseline", None)
    style = el.get("style") or ""
    style = re.sub(r"text-anchor\s*:\s*[^;]+;?\s*", "", style, flags=re.IGNORECASE)
    style = style.strip().rstrip(";")
    if style:
        el.set("style", style)
    elif "style" in el.attrib:
        el.attrib.pop("style")


def _result_row_font_size_px() -> int:
    return max(8, int(round(_svg_scale(_BOX_RESULT_ROW_FONT_SIZE_SVG))))


def _truncate_text_to_width(text: str, *, max_width_px: int, font: ImageFont.FreeTypeFont) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    if probe.textlength(raw, font=font) <= max_width_px:
        return raw
    ell = "..."
    if probe.textlength(ell, font=font) >= max_width_px:
        return ell
    lo, hi = 0, len(raw)
    best = ell
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = raw[:mid].rstrip() + ell
        if probe.textlength(cand, font=font) <= max_width_px:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _result_row_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return _field_font({"font": "digital7"}, _result_row_font_size_px())


def _text_width_px(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> float:
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    return float(probe.textlength(str(text or ""), font=font))


def _px_to_svg_x(px: float) -> float:
    return px * 800.0 / float(DESIGN_W)


def _format_box_result_device_name(name: str, *, box_num: int, ip_width_svg: float = 0.0) -> str:
    bounds = _BOX_COLUMN_INNER_SVG.get(box_num)
    if bounds is None:
        bounds = (280.0, 418.0)
    x0, x1 = bounds
    gap_svg = _BOX_RESULT_ROW_GAP_SVG if ip_width_svg > 0.0 else 0.0
    max_w_svg = max(8.0, (x1 - x0) - gap_svg - ip_width_svg - 8.0)
    max_w = max(24, int(round(max_w_svg * DESIGN_W / 800.0)))
    font = _result_row_font()
    return _truncate_text_to_width(name, max_width_px=max_w, font=font)


def _apply_box_result_row_text_layout(
    dev_el: ET.Element | None,
    ip_el: ET.Element | None,
    *,
    name: str,
    ip: str,
    row: int,
    box_num: int,
) -> None:
    if row < 1 or row > len(_BOX_RESULT_ROW_Y_SVG):
        return
    bounds = _BOX_COLUMN_INNER_SVG.get(box_num)
    if bounds is None:
        return
    x0, x1 = bounds
    center_svg = (x0 + x1) * 0.5
    y_svg = _BOX_RESULT_ROW_Y_SVG[row - 1]
    font = _result_row_font()
    gap_svg = _BOX_RESULT_ROW_GAP_SVG

    if ip_el is None or not str(ip or "").strip():
        display = _format_box_result_device_name(name, box_num=box_num)
        if dev_el is not None:
            _set_text_content(dev_el, display)
            _set_svg_text_horiz_centered(dev_el, center_svg, y_svg)
        return

    ip_display = str(ip)
    ip_w_svg = _px_to_svg_x(_text_width_px(ip_display, font))
    dev_display = _format_box_result_device_name(name, box_num=box_num, ip_width_svg=ip_w_svg)
    dev_w_svg = _px_to_svg_x(_text_width_px(dev_display, font))
    total_svg = dev_w_svg + gap_svg + ip_w_svg
    start_svg = center_svg - total_svg * 0.5
    dev_right_svg = start_svg + dev_w_svg
    ip_left_svg = dev_right_svg + gap_svg

    if dev_el is not None:
        _set_text_content(dev_el, dev_display)
        _set_svg_text_right_aligned(dev_el, dev_right_svg, y_svg)
    if ip_el is not None:
        _set_text_content(ip_el, ip_display)
        _set_svg_text_left_aligned(ip_el, ip_left_svg, y_svg)


def _center_wifi_plus_icon(root: ET.Element) -> None:
    """Anchor ``main_box2_+_icon`` on the search ring center."""
    plus_group = _find_by_logical_id(root, "main_box2_+_icon")
    if plus_group is None:
        return
    cx, cy = _WIFI_SEARCH_CENTER_SVG
    for el in plus_group.iter():
        if el.tag.endswith("text"):
            _set_svg_text_centered(el, cx, cy)


def _draw_wifi_onboarding_search_overlays(
    bgra: np.ndarray,
    state: MainSettingsState,
    arc_specs: tuple[_OnboardingSearchArcSpec, ...],
    triangle_specs: tuple[tuple[tuple[float, float], ...], ...] = (),
) -> None:
    if not _needs_wifi_setup(state) or state.show_network_picker:
        return
    if not (state.wifi_scanning or state.wifi_connecting):
        return
    if (state.wifi_scanning or state.wifi_connecting) and not bool(
        getattr(state, "spinner_glyph_capture", False)
    ):
        return
    _draw_onboarding_search_arc_overlays(bgra, arc_specs)
    _draw_onboarding_search_triangle_overlays(bgra, triangle_specs)


def _wifi_onboarding_search_group(root: ET.Element) -> ET.Element | None:
    """Search rings nested under ``main_box2_add_search_icon`` (WiFi onboarding art)."""
    add_group = _find_by_logical_id(root, "main_box2_add_search_icon")
    if add_group is None:
        return None
    for child in add_group:
        if not child.tag.endswith("g"):
            continue
        logical = _normalize_logical(child.get("id") or "")
        if logical == "main_box2_search_icon" or logical.startswith("main_box2_search_icon_"):
            return child
    return None


def _find_by_id(root: ET.Element, layer_id: str) -> ET.Element | None:
    for el in root.iter():
        if el.get("id") == layer_id:
            return el
    return None


# Per-tree logical-id → elements. Built once; apply_state does hundreds of lookups
# on a ~3.7k-node SVG, and naive O(n) scans made each Pi invalidate multi-second.
_LOGICAL_INDEX_BY_ROOT: dict[int, tuple[ET.Element, dict[str, list[ET.Element]]]] = {}


def _invalidate_logical_index(root: ET.Element) -> None:
    _LOGICAL_INDEX_BY_ROOT.pop(id(root), None)


def _logical_id_index(root: ET.Element) -> dict[str, list[ET.Element]]:
    rid = id(root)
    hit = _LOGICAL_INDEX_BY_ROOT.get(rid)
    if hit is not None and hit[0] is root:
        return hit[1]
    index: dict[str, list[ET.Element]] = {}
    for el in root.iter():
        raw = el.get("id") or ""
        if not raw:
            continue
        index.setdefault(_normalize_logical(raw), []).append(el)
    if len(_LOGICAL_INDEX_BY_ROOT) >= 8:
        _LOGICAL_INDEX_BY_ROOT.clear()
    _LOGICAL_INDEX_BY_ROOT[rid] = (root, index)
    return index


def _find_by_logical_id(root: ET.Element, logical_id: str) -> ET.Element | None:
    """Match exact encoded id, raw logical id, or AI-suffixed variants."""
    want = _normalize_logical(logical_id)
    hits = _logical_id_index(root).get(want)
    if hits:
        return hits[0]
    encoded = encode_svg_id(logical_id)
    for el in root.iter():
        raw = el.get("id") or ""
        if not raw:
            continue
        if raw == encoded or raw == logical_id:
            return el
        if raw.startswith(encoded + "_") and _AI_SUFFIX_RE.search("_" + raw[len(encoded) + 1 :]):
            return el
    return None


def _set_svg_text_font_size_px(el: ET.Element, size_px: int) -> None:
    targets = [el] if el.tag.endswith("text") else [n for n in el.iter() if n.tag.endswith("text")]
    for node in targets:
        style = node.get("style") or ""
        node.set("style", _rewrite_style_prop(style, "font-size", f"{max(6, int(size_px))}px"))


def _box_column_inner_svg(box_num: int) -> tuple[float, float]:
    return _BOX_COLUMN_INNER_SVG.get(box_num, (310.0, 495.0))


def _layout_box_device_label(
    el: ET.Element | None,
    *,
    text: str,
    box_num: int,
    kind: str,
) -> None:
    """Center device name/IP inside a box column and shrink to fit."""
    raw = str(text or "").strip()
    if el is None or not raw:
        return
    x0, x1 = _box_column_inner_svg(box_num)
    center_svg = (x0 + x1) * 0.5
    y_svg = _BOX_DEVICE_NAME_Y_SVG if kind == "name" else _BOX_DEVICE_IP_Y_SVG
    font_svg = _BOX_DEVICE_NAME_FONT_SVG if kind == "name" else _BOX_DEVICE_IP_FONT_SVG
    max_w = max(24, int(round((x1 - x0 - 8.0) * DESIGN_W / 800.0)))
    start_px = _field_font_size_px(font_svg)
    size_px, font = _fit_text_font_size(
        raw,
        lambda px: _field_font({"font": "digital7"}, px),
        max_width_px=max_w,
        start_px=start_px,
    )
    display = _truncate_text_to_width(raw, max_width_px=max_w, font=font)
    _set_text_content(el, display)
    for node in el.iter():
        if not node.tag.endswith("text"):
            continue
        _set_svg_text_horiz_centered(node, center_svg, y_svg)
        _set_svg_text_font_size_px(node, size_px)


def _apply_box_device_group_layout(
    root: ET.Element,
    box_num: int,
    *,
    name: str = "",
    ip: str = "",
) -> None:
    group = _find_box_device_group(root, box_num)
    if group is None:
        return
    name_el: ET.Element | None = None
    ip_el: ET.Element | None = None
    for el in group.iter():
        logical = _normalize_logical(el.get("id") or "")
        if not _is_device_label_logical(logical):
            continue
        if logical.endswith("_device_name_text"):
            name_el = el
        elif logical.endswith("_device_ip_text"):
            ip_el = el
    if name:
        _layout_box_device_label(name_el, text=name, box_num=box_num, kind="name")
    if ip:
        _layout_box_device_label(ip_el, text=ip, box_num=box_num, kind="ip")


def _dual_location_text_bounds_svg(root: ET.Element | None = None) -> tuple[float, float]:
    """Text span for the location field: right of nest icons, left of the ``|`` divider."""
    x1 = _DUAL_LOCATION_TEXT_X1_SVG
    icon_right = _DUAL_LOCATION_TEXT_X0_SVG
    if root is not None:
        for lid in _DUAL_LOCATION_ICON_IDS:
            el = _find_by_logical_id(root, lid)
            if el is None or not el.tag.endswith("circle"):
                continue
            try:
                cx = float(el.get("cx") or 0.0)
                r = float(el.get("r") or _DUAL_LOCATION_ICON_RADIUS_SVG)
            except (TypeError, ValueError):
                continue
            icon_right = max(icon_right, cx + r)
    else:
        icon_right = 128.26 + _DUAL_LOCATION_ICON_RADIUS_SVG
    return icon_right + _DUAL_LOCATION_TEXT_ICON_GAP_SVG, x1


def _dual_location_text_bounds_px(root: ET.Element | None = None) -> tuple[int, int]:
    x0, x1 = _dual_location_text_bounds_svg(root)
    return int(round(x0 * DESIGN_W / 800.0)), int(round(x1 * DESIGN_W / 800.0))


def _find_all_by_logical_id(root: ET.Element, logical_id: str) -> list[ET.Element]:
    want = _normalize_logical(logical_id)
    indexed = _logical_id_index(root).get(want)
    if indexed:
        return list(indexed)
    hits: list[ET.Element] = []
    for el in root.iter():
        raw = el.get("id") or ""
        if not raw:
            continue
        decoded = decode_svg_id(raw)
        if decoded.startswith(want + "_") and _AI_SUFFIX_RE.search(decoded[len(want) :]):
            hits.append(el)
    return hits


def _set_visible(el: ET.Element | None, visible: bool) -> None:
    """Toggle visibility on ``el``.

    Hide marks only the subtree root — ``_prune_display_none`` drops the whole
    branch before PyMuPDF (which ignores inherited ``display``).

    Show clears ``display:none`` on the root and descendants that actually have
    it (Illustrator often bakes ``display:none`` into child ``style``). Skipping
    clean nodes keeps focus navigation cheap on Pi.
    """
    if el is None:
        return
    if not visible:
        el.set("display", "none")
        return
    for node in (el, *(child for child in el.iter() if child is not el)):
        had_attr = "display" in node.attrib
        style = node.get("style") or ""
        had_style = "display:" in style
        if not had_attr and not had_style:
            continue
        if had_attr:
            node.attrib.pop("display", None)
        if had_style:
            cleaned = re.sub(r"display\s*:\s*[^;]+;?\s*", "", style, flags=re.IGNORECASE)
            cleaned = cleaned.strip().rstrip(";")
            if cleaned:
                node.set("style", cleaned)
            elif "style" in node.attrib:
                node.attrib.pop("style")


def _prune_display_none(root: ET.Element) -> None:
    """Remove ``display:none`` subtrees before rasterize (PyMuPDF ignores display)."""
    parents = _parent_map(root)
    to_remove = [el for el in root.iter() if el.get("display") == "none"]
    for el in to_remove:
        parent = parents.get(el)
        if parent is not None:
            try:
                parent.remove(el)
            except ValueError:
                pass


def _remove_by_logical_id(root: ET.Element, logical_id: str) -> None:
    """Remove nodes (PyMuPDF ignores display:none on some groups)."""
    parents = _parent_map(root)
    for el in list(_find_all_by_logical_id(root, logical_id)):
        parent = parents.get(el)
        if parent is not None:
            try:
                parent.remove(el)
            except ValueError:
                pass


def _norm_hex(color: str | None) -> str | None:
    if not color:
        return None
    c = color.strip().lower()
    if c in ("none", "transparent"):
        return c
    if c in ("white", "#fff"):
        return "#ffffff"
    if c in ("black", "#000"):
        return "#000000"
    if c.startswith("url("):
        return None
    if not c.startswith("#") and _HEX_RE.match(f"#{c}"):
        c = f"#{c}"
    if c.startswith("#") and len(c) == 4:
        c = f"#{c[1]}{c[1]}{c[2]}{c[2]}{c[3]}{c[3]}"
    if c.startswith("#") and len(c) == 7:
        return c
    return c


def _rewrite_style_prop(style: str, prop: str, value: str) -> str:
    pat = re.compile(rf"{re.escape(prop)}\s*:\s*[^;\"']+", re.IGNORECASE)
    if pat.search(style):
        return pat.sub(f"{prop}:{value}", style)
    if style.strip():
        return f"{style.rstrip().rstrip(';')};{prop}:{value}"
    return f"{prop}:{value}"


def _set_paint(node: ET.Element, *, fill: str | None = None, stroke: str | None = None) -> None:
    style = node.get("style") or ""
    if fill is not None:
        if "fill:" in style:
            style = _rewrite_style_prop(style, "fill", fill)
        node.set("fill", fill)
    if stroke is not None:
        if "stroke:" in style:
            style = _rewrite_style_prop(style, "stroke", stroke)
        node.set("stroke", stroke)
    if style:
        node.set("style", style)


def _iter_style_fill_stroke(node: ET.Element) -> tuple[str | None, str | None]:
    fill = _norm_hex(node.get("fill"))
    stroke = _norm_hex(node.get("stroke"))
    style = node.get("style") or ""
    if style:
        fm = re.search(r"fill\s*:\s*([^;\"']+)", style, re.IGNORECASE)
        sm = re.search(r"stroke\s*:\s*([^;\"']+)", style, re.IGNORECASE)
        if fm:
            fill = _norm_hex(fm.group(1).strip()) or fill
        if sm:
            stroke = _norm_hex(sm.group(1).strip()) or stroke
    return fill, stroke


def _style_prop(style: str | None, prop: str) -> str | None:
    if not style:
        return None
    m = re.search(rf"{re.escape(prop)}\s*:\s*([^;\"']+)", style, re.IGNORECASE)
    if not m:
        return None
    return m.group(1).strip().strip("'\"")


def _apply_button_fill(group: ET.Element | None, *, selected: bool, theme: SettingsTheme) -> None:
    if group is None:
        return
    fill = theme.selected if selected else theme.deselected
    for node in group.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in ("path", "rect", "polygon", "circle", "ellipse"):
            continue
        # Skip nested accent strokes that live under a *_button group by mistake.
        nid = _normalize_logical(node.get("id") or "")
        if nid.endswith("_accent") or "_accent_" in nid:
            continue
        cur_fill, _ = _iter_style_fill_stroke(node)
        if cur_fill is None or cur_fill in ("none", "transparent"):
            # Dual location/network use filled paths; still paint if attribute missing but path exists.
            if tag == "path" and (node.get("d") or "d" in (node.attrib or {})):
                if cur_fill in ("none", "transparent"):
                    continue
            else:
                continue
        if cur_fill in _BUTTON_FILL_CANDIDATES or cur_fill in (
            theme.selected.lower(),
            theme.deselected.lower(),
        ) or cur_fill in _theme_swatch_hexes():
            _set_paint(node, fill=fill)


def _apply_direct_glyph_contrast(
    group: ET.Element | None,
    *,
    selected: bool,
    theme: SettingsTheme,
    disabled: bool = False,
) -> None:
    """Paint glyphs directly: white selected, black deselected, gray disabled."""
    if group is None:
        return
    if disabled:
        color = theme.inactive
    else:
        color = theme.selected if selected else theme.deselected
    for node in group.iter():
        nid = _normalize_logical(node.get("id") or "")
        if nid.endswith("_accent") or "_accent_" in nid:
            continue
        tag = node.tag.rsplit("}", 1)[-1]
        fill, stroke = _iter_style_fill_stroke(node)
        if tag.endswith("text") and fill in (None, "none", "transparent"):
            fill = "#ffffff"
        swap = _CONTRAST_SWAP_CANDIDATES | _theme_swatch_hexes()
        if (
            fill
            and fill not in ("none", "transparent")
            and fill in swap
            and not _is_ui_brand_color(fill)
        ):
            _set_paint(node, fill=color)
        if (
            stroke
            and stroke not in ("none", "transparent")
            and stroke in swap
            and not _is_ui_brand_color(stroke)
        ):
            _set_paint(node, stroke=color)


def _apply_contrast_paint(
    group: ET.Element | None,
    *,
    selected: bool,
    theme: SettingsTheme,
    muted_deselected: bool = False,
) -> None:
    """Contrasting text/icons on buttons (white on black, black on white). Leaves accent alone.

    When ``muted_deselected`` is True (binary yes/no / later/now choices), deselected
    labels use grey (``theme.inactive``) on the black button instead of white.
    """
    if group is None:
        return
    if selected:
        contrast = theme.deselected
    elif muted_deselected:
        contrast = theme.inactive
    else:
        contrast = theme.selected
    swap = _CONTRAST_SWAP_CANDIDATES | _theme_swatch_hexes()
    for node in group.iter():
        nid = _normalize_logical(node.get("id") or "")
        if nid.endswith("_accent") or "_accent_" in nid:
            continue
        tag = node.tag.rsplit("}", 1)[-1]
        fill, stroke = _iter_style_fill_stroke(node)
        if tag.endswith("text") and fill in (None, "none", "transparent"):
            fill = "#ffffff"
        if (
            fill
            and fill not in ("none", "transparent")
            and fill in swap
            and not _is_ui_brand_color(fill)
        ):
            _set_paint(node, fill=contrast)
        if (
            stroke
            and stroke not in ("none", "transparent")
            and stroke in swap
            and not _is_ui_brand_color(stroke)
        ):
            _set_paint(node, stroke=contrast)


def _apply_accent_paint(group: ET.Element | None, accent: str) -> None:
    if group is None:
        return
    accent_swap = frozenset(
        {
            "#ffffff",
            "white",
            COLOR_ACCENT_DEFAULT.lower(),
            accent.lower(),
        }
    ) | _theme_swatch_hexes()
    for node in group.iter():
        fill, stroke = _iter_style_fill_stroke(node)
        if fill and fill not in ("none", "transparent"):
            if fill in accent_swap:
                _set_paint(node, fill=accent)
        if stroke and stroke not in ("none", "transparent"):
            if stroke in accent_swap:
                _set_paint(node, stroke=accent)


def _ui_recolor_hexes() -> frozenset[str]:
    """Hexes that count as the configurable UI/brand color on icon layers."""
    try:
        from pigeon.widgets.ui_color_settings import UI_SWATCH_HEXES

        return _UI_BRAND_COLORS | UI_SWATCH_HEXES
    except Exception:
        return _UI_BRAND_COLORS | {"red"}


def _apply_ui_brand_paint(group: ET.Element | None, ui_hex: str) -> None:
    """Recolor UI/brand fills/strokes under an icon group to ``theme.ui``.

    Child geometry often has no id (e.g. box2 play triangle polygon) — paint by
    current brand/swatch hex, not by requiring each node to be named ``*_icon``.
    """
    if group is None:
        return
    ui = (ui_hex or COLOR_UI_DEFAULT).lower()
    candidates = _ui_recolor_hexes() | {ui}
    for node in group.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in ("path", "rect", "polygon", "polyline", "circle", "ellipse", "line"):
            continue
        fill, stroke = _iter_style_fill_stroke(node)
        if fill and fill not in ("none", "transparent") and fill in candidates and fill != ui:
            _set_paint(node, fill=ui_hex)
        if (
            stroke
            and stroke not in ("none", "transparent")
            and stroke in candidates
            and stroke != ui
        ):
            _set_paint(node, stroke=ui_hex)


def _set_text_content(el: ET.Element | None, text: str) -> None:
    if el is None:
        return
    # Prefer nested <text> if this is a group.
    texts = [n for n in el.iter() if n.tag.endswith("text")]
    targets = texts if texts else ([el] if el.tag.endswith("text") else [])
    for t in targets:
        if len(t):
            for child in list(t):
                if child.tag.endswith("tspan"):
                    child.text = text
                    child.tail = None
            if not any(c.tag.endswith("tspan") for c in t):
                t.text = text
        else:
            t.text = text


def _hide_add_search_glyphs(root: ET.Element) -> None:
    """Hide plus/arrow search art so it cannot bleed through PIN / overlay screens."""
    for lid in (
        "main_box1_search_icon",
        "main_box2_search_icon",
        "main_box3_search_icon",
        "main_box2_add_search_icon",
        "main_box2_+_icon",
        "welcome_to_pigeon_group",
    ):
        _set_visible(_find_by_logical_id(root, lid), False)
    search_group = _wifi_onboarding_search_group(root)
    if search_group is not None:
        _set_visible(search_group, False)
    for box_num in (1, 2, 3):
        _hide_box_search_animation_layers(root, box_num)


def _apply_keyboard_layer_visibility(root: ET.Element, state: MainSettingsState) -> None:
    """While the keyboard is open, hide chrome under the overlay."""
    if not state.keyboard_open and not state.wifi_connecting:
        return
    kb_target = ""
    if state.keyboard is not None:
        kb_target = str(getattr(state.keyboard, "target", "") or "")
    pairing_pin = kb_target == "pin" and state.box_pairing is not None
    wifi_logout = kb_target == "wifi_logout"
    manual_entry = state.manual_device_entry is not None
    device_entry = kb_target in ("device_ip", "device_name") or manual_entry
    password_flow = kb_target == "network" or state.wifi_connecting
    if wifi_logout:
        for lid in (
            "main_exit_group",
            "main_network_picker",
            "welcome_to_pigeon_find_your_wifi_text",
            "welcome_to_pigeon_group",
            "main_box1_text",
            "main_box1_device_group",
            "main_box2_device_group",
            "main_box3_device_group",
            "main_box1_container",
            "main_box2_container",
            "main_box3_container",
            "main_box2_add_search_icon",
            "main_box2_+_icon",
        ):
            _set_visible(_find_by_logical_id(root, lid), False)
        _hide_add_search_glyphs(root)
        for lid in _DUAL_LOCATION_ICON_IDS:
            _set_visible(_find_by_logical_id(root, lid), False)
        _set_visible(_find_by_logical_id(root, "main_dual_network_wifi_group"), False)
        _set_visible(_find_by_logical_id(root, "main_instructions"), True)
        nest_el = _find_by_logical_id(root, "rename_your_nest_text")
        if nest_el is not None:
            _set_text_content(nest_el, _wifi_logout_instruction_text(state))
            _force_layer_white(nest_el)
            _set_visible(nest_el, True)
        return
    if pairing_pin or device_entry:
        for lid in (
            "main_exit_group",
            "main_instructions",
            "main_network_picker",
            "welcome_to_pigeon_find_your_wifi_text",
            "main_box1_text",
            "main_box1_device_group",
            "main_box2_device_group",
            "main_box3_device_group",
            "main_box1",
            "main_box2",
            "main_box3",
            "main_box1_container",
            "main_box2_container",
            "main_box3_container",
            "main_box2_add_search_icon",
        ):
            _set_visible(_find_by_logical_id(root, lid), False)
        _hide_add_search_glyphs(root)
        for lid in _DUAL_LOCATION_ICON_IDS:
            _set_visible(_find_by_logical_id(root, lid), False)
        _set_visible(_find_by_logical_id(root, "main_dual_network_wifi_group"), False)
        if pairing_pin:
            _set_visible(_find_by_logical_id(root, "main_instructions"), True)
            _set_visible(_find_by_logical_id(root, "welcome_to_pigeon_group"), False)
            nest_el = _find_by_logical_id(root, "rename_your_nest_text")
            if nest_el is not None:
                _set_text_content(nest_el, _pairing_instruction_text(state))
                _force_layer_white(nest_el)
                _set_visible(nest_el, True)
        return
    if password_flow:
        for lid in (
            "main_exit_group",
            "main_instructions",
            "main_network_picker",
            "rename_your_nest_text",
            "welcome_to_pigeon_find_your_wifi_text",
            "main_box1",
            "main_box2",
            "main_box3",
            "main_box2_add_search_icon",
            "main_box1_text",
        ):
            _set_visible(_find_by_logical_id(root, lid), False)
        if state.keyboard_open and kb_target == "network":
            for lid in _DUAL_LOCATION_ICON_IDS:
                _set_visible(_find_by_logical_id(root, lid), False)
        return
    # Location rename / default keyboard: suppress every box column layer under the overlay.
    for lid in _KEYBOARD_HIDE_WHEN_OPEN:
        _set_visible(_find_by_logical_id(root, lid), False)
    _set_box_columns_visible(root, False)
    for box_num in (1, 2, 3):
        _set_visible(_find_by_logical_id(root, _BOX_LOCATION_GROUPS[box_num - 1]), False)
        _set_visible(_find_by_logical_id(root, _BOX_DEVICE_GROUPS[box_num]), False)
        _set_visible(_find_by_logical_id(root, f"main_box{box_num}_container"), False)
        _set_visible(_find_by_logical_id(root, f"main_box{box_num}_search_icon"), False)
        _set_box_search_results_visible(root, box_num, False)
        _hide_box_search_animation_layers(root, box_num)


def _text_field_spec(target: str) -> dict[str, float | str | bool] | None:
    return _TEXT_ENTRY_FIELDS.get(target)


def _location_text_is_grayed(state: MainSettingsState) -> bool:
    """True when dual-location shows placeholder gray (replace mode, not append)."""
    if state.show_instructions:
        return True
    name = str(state.location_name or "").strip().lower()
    return name in ("", "nest 1", "nest1")


def _wifi_logout_instruction_text(state: MainSettingsState) -> str:
    ssid = str(state.selected_wifi_ssid or "").strip() or "network"
    return f"log out of {ssid}?"


def _pairing_instruction_text(state: MainSettingsState) -> str:
    sess = state.box_pairing
    if sess is None:
        return "PAIR COMPANION (1 of 2)"
    if sess.step == "airplay_pin":
        return "PAIR AIRPLAY (2 of 2)"
    return "PAIR COMPANION (1 of 2)"


@lru_cache(maxsize=4)
def _load_digital7(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = resolve_digital7_font()
    if not path:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(path, max(6, size_px))
    except OSError:
        return ImageFont.load_default()


def _field_font_size_px(font_size_svg: float) -> int:
    return max(6, int(round(font_size_svg * DESIGN_H / _ARTBOARD_H)))


def _fit_text_font_size(
    text: str,
    font_fn,
    *,
    max_width_px: int,
    start_px: int,
) -> tuple[int, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    size = max(6, int(start_px))
    font = font_fn(size)
    while size > 6 and probe.textlength(text, font=font) > max_width_px:
        size -= 1
        font = font_fn(size)
    return size, font


def _draw_centered_field_text(
    draw: ImageDraw.ImageDraw,
    *,
    x0_px: int,
    x1_px: int,
    baseline_px: int,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
) -> None:
    if not text:
        return
    bbox = draw.textbbox((0, 0), text, font=font, anchor="ls")
    tw = bbox[2] - bbox[0]
    cx = (x0_px + x1_px) // 2
    draw.text((cx - tw // 2, baseline_px), text, font=font, fill=fill, anchor="ls")


def _entry_content_font_size_px(
    spec: dict[str, float | str | bool],
    *,
    buffer: str,
    display: str,
    max_width_px: int | None = None,
) -> int:
    if display == "incorrect password" and max_width_px is not None:
        start = min(_field_font_size_px(float(spec["font_size_svg"])) - 10, 18)
        font_fn = lambda s: _field_font(spec, s)
        size, _font = _fit_text_font_size(
            display, font_fn, max_width_px=max_width_px, start_px=max(8, start)
        )
        return max(8, min(size, 18))
    placeholder = str(spec.get("placeholder", "") or "")
    is_placeholder = (
        not buffer
        and spec.get("password_mask")
        and display == placeholder
    )
    if is_placeholder:
        ph_size = spec.get("placeholder_font_size_svg")
        if ph_size is not None:
            return _field_font_size_px(float(ph_size))
        return max(6, _field_font_size_px(float(spec["font_size_svg"])) - 5)
    return _field_font_size_px(float(spec["font_size_svg"]))


@lru_cache(maxsize=4)
def _load_sharp_medium(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = resolve_ui_font_medium()
    if not path:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(path, max(6, size_px))
    except OSError:
        return ImageFont.load_default()


def _field_font(spec: dict[str, float | str | bool], size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if spec.get("font") == "sharp_medium":
        return _load_sharp_medium(size_px)
    return _load_digital7(size_px)


def _password_mask_display(buffer: str) -> str:
    """Reveal only the most recently typed character (e.g. ``**g``)."""
    if not buffer:
        return ""
    if len(buffer) == 1:
        return buffer
    return ("*" * (len(buffer) - 1)) + buffer[-1]


def _asterisk_font_for_password_mask(
    draw: ImageDraw.ImageDraw,
    spec: dict[str, float | str | bool],
    *,
    size_px: int,
    visible: str,
    base_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Return a font sized so ``*`` matches the visible character height (~2× body size)."""
    vis_bbox = draw.textbbox((0, 0), visible or "0", font=base_font, anchor="ls")
    vis_h = max(1, vis_bbox[3] - vis_bbox[1])
    target = max(size_px + 4, int(round(size_px * 2.0)))
    best = _field_font(spec, target)
    best_bbox = draw.textbbox((0, 0), "*", font=best, anchor="ls")
    best_h = best_bbox[3] - best_bbox[1]
    for candidate in range(target, max(size_px, target - 8), -1):
        trial = _field_font(spec, candidate)
        trial_bbox = draw.textbbox((0, 0), "*", font=trial, anchor="ls")
        trial_h = trial_bbox[3] - trial_bbox[1]
        if trial_h <= vis_h:
            best = trial
            best_h = trial_h
            break
        best = trial
        best_h = trial_h
    if best_h > vis_h * 1.15:
        for candidate in range(target - 1, size_px, -1):
            trial = _field_font(spec, candidate)
            trial_bbox = draw.textbbox((0, 0), "*", font=trial, anchor="ls")
            trial_h = trial_bbox[3] - trial_bbox[1]
            if trial_h <= vis_h * 1.05:
                return trial
    return best


def _password_mask_layout(
    draw: ImageDraw.ImageDraw,
    *,
    cx: int,
    baseline_px: int,
    display: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    spec: dict[str, float | str | bool],
    size_px: int,
) -> tuple[int, int, int, int, int, int, int]:
    """
    Layout ``***x`` with enlarged asterisks.

    Returns ``(x0, mask_y, mask_w, vis_w, cursor_x, text_top, text_bottom)``.
    """
    if not display or "*" not in display:
        bbox = draw.textbbox((0, 0), display, font=font, anchor="ls")
        tw = bbox[2] - bbox[0]
        top = int(baseline_px + bbox[1])
        bot = int(baseline_px + bbox[3])
        x0 = cx - tw // 2
        return x0, baseline_px, tw, 0, x0 + tw + 2, top, bot

    visible = display[-1]
    mask_str = display[:-1]
    vis_bbox = draw.textbbox((0, 0), visible, font=font, anchor="ls")
    vis_w = vis_bbox[2] - vis_bbox[0]
    vis_center_y = baseline_px + (vis_bbox[1] + vis_bbox[3]) * 0.5

    ast_font = _asterisk_font_for_password_mask(
        draw, spec, size_px=size_px, visible=visible, base_font=font
    )
    mask_bbox = draw.textbbox((0, 0), mask_str, font=ast_font, anchor="ls")
    mask_w = mask_bbox[2] - mask_bbox[0]
    mask_center_offset = (mask_bbox[1] + mask_bbox[3]) * 0.5
    mask_y = int(round(vis_center_y - mask_center_offset))

    total_w = mask_w + vis_w
    x0 = cx - total_w // 2
    top = int(min(mask_y + mask_bbox[1], baseline_px + vis_bbox[1]))
    bot = int(max(mask_y + mask_bbox[3], baseline_px + vis_bbox[3]))
    return x0, mask_y, mask_w, vis_w, x0 + total_w + 2, top, bot


def _draw_password_mask_text(
    draw: ImageDraw.ImageDraw,
    *,
    cx: int,
    baseline_px: int,
    display: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    spec: dict[str, float | str | bool],
    size_px: int,
    fill: tuple[int, int, int, int],
) -> tuple[int, int, int]:
    """
    Draw ``***x`` with enlarged, vertically centered asterisks.

    Returns ``(cursor_x, text_top, text_bottom)`` for the insertion caret.
    """
    if not display or "*" not in display:
        bbox = draw.textbbox((0, 0), display, font=font, anchor="ls")
        tw = bbox[2] - bbox[0]
        draw.text((cx - tw // 2, baseline_px), display, font=font, fill=fill, anchor="ls")
        top = baseline_px + bbox[1]
        bot = baseline_px + bbox[3]
        return cx - tw // 2 + tw + 2, int(top), int(bot)

    visible = display[-1]
    mask_str = display[:-1]
    ast_font = _asterisk_font_for_password_mask(
        draw, spec, size_px=size_px, visible=visible, base_font=font
    )
    x0, mask_y, mask_w, _vis_w, cursor_x, top, bot = _password_mask_layout(
        draw,
        cx=cx,
        baseline_px=baseline_px,
        display=display,
        font=font,
        spec=spec,
        size_px=size_px,
    )
    draw.text((x0, mask_y), mask_str, font=ast_font, fill=fill, anchor="ls")
    draw.text((x0 + mask_w, baseline_px), visible, font=font, fill=fill, anchor="ls")
    return cursor_x, top, bot


def _entry_display_text(
    *,
    buffer: str,
    initial: str,
    spec: dict[str, float | str | bool],
    state: MainSettingsState | None = None,
) -> str:
    if (
        state is not None
        and state.network_password_error
        and spec.get("password_mask")
        and not buffer
    ):
        return "incorrect password"
    if buffer:
        if spec.get("password_mask"):
            return _password_mask_display(buffer)
        if spec.get("uppercase_only"):
            return buffer.upper()
        return buffer
    placeholder = str(spec.get("placeholder", "") or "")
    if spec.get("password_mask") and placeholder:
        return placeholder
    if initial:
        if spec.get("uppercase_only"):
            return initial.upper()
        return initial
    return ""


def _entry_field_bounds_px(target: str) -> tuple[int, int]:
    """Pixel bounds for the active dual-bar field (matches ``_draw_text_entry_content``)."""
    if target in ("location", "device_name"):
        return _dual_location_text_bounds_px()
    if target in ("network", "pin", "device_ip"):
        return (
            int(round(_DUAL_NETWORK_TEXT_X0_SVG * DESIGN_W / 800.0)),
            int(round(_DUAL_NETWORK_TEXT_X1_SVG * DESIGN_W / 800.0)),
        )
    spec = _text_field_spec(target)
    if spec is None:
        return 0, DESIGN_W
    return (
        int(round(float(spec["x0_svg"]) * DESIGN_W / 800.0)),
        int(round(float(spec["x1_svg"]) * DESIGN_W / 800.0)),
    )


def _entry_cursor_display(
    *,
    buffer: str,
    initial: str,
    spec: dict[str, float | str | bool],
    state: MainSettingsState | None = None,
) -> str:
    """Rendered string used to place the caret at the end of the editable text."""
    if (
        state is not None
        and state.network_password_error
        and spec.get("password_mask")
        and not buffer
    ):
        return "incorrect password"
    edit = buffer if buffer else initial
    if not edit:
        return ""
    if spec.get("password_mask"):
        return _password_mask_display(edit)
    if spec.get("uppercase_only"):
        return edit.upper()
    return edit


def _clamp_field_cursor_x(cursor_x: int, *, x0_px: int, x1_px: int) -> int:
    return max(x0_px + 2, min(x1_px - 2, int(cursor_x)))


def _draw_text_entry_content(bgra: np.ndarray, state: MainSettingsState) -> None:
    """Paint centered dual-bar text while the keyboard is open (no cursor)."""
    kb = state.keyboard
    if kb is None and not state.wifi_connecting:
        return
    target = ""
    buffer = ""
    initial = ""
    if kb is not None:
        target = str(getattr(kb, "target", "") or "")
        buffer = str(getattr(kb, "buffer", "") or "")
        initial = str(getattr(kb, "initial_text", "") or "")
    if state.wifi_connecting and not target:
        target = "network"
    if target == "wifi_logout" and state.keyboard_open:
        baseline_px = int(round(float(_TEXT_ENTRY_FIELDS["location"]["baseline_y_svg"]) * DESIGN_H / _ARTBOARD_H))
        loc_x0, loc_x1 = _dual_location_text_bounds_px()
        rgb = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGBA)
        img = Image.fromarray(rgb)
        draw = ImageDraw.Draw(img)
        ssid = str(state.selected_wifi_ssid or "").strip()
        if ssid:
            loc_spec = _TEXT_ENTRY_FIELDS["location"]
            ssid_size = _entry_content_font_size_px(
                loc_spec,
                buffer=ssid,
                display=ssid,
                max_width_px=max(24, loc_x1 - loc_x0 - 56),
            )
            ssid_font = _field_font(loc_spec, ssid_size)
            _draw_centered_field_text(
                draw,
                x0_px=loc_x0,
                x1_px=loc_x1,
                baseline_px=baseline_px,
                text=ssid,
                font=ssid_font,
                fill=(255, 255, 255, 255),
            )
        bgra[:] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)
        return
    spec = _text_field_spec(target)
    if spec is None:
        return
    baseline_px = int(round(float(spec["baseline_y_svg"]) * DESIGN_H / _ARTBOARD_H))
    rgb = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGBA)
    img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(img)

    if state.wifi_connecting and not state.keyboard_open:
        loc_x0, loc_x1 = _dual_location_text_bounds_px()
        net_x0 = int(round(_DUAL_NETWORK_TEXT_X0_SVG * DESIGN_W / 800.0))
        net_x1 = int(round(_DUAL_NETWORK_TEXT_X1_SVG * DESIGN_W / 800.0))
        loc_spec = _TEXT_ENTRY_FIELDS["location"]
        loc_name = str(state.location_name or "nest 1").strip() or "nest 1"
        loc_size = _entry_content_font_size_px(
            loc_spec,
            buffer=loc_name,
            display=loc_name.upper(),
            max_width_px=max(24, loc_x1 - loc_x0 - 16),
        )
        loc_font = _field_font(loc_spec, loc_size)
        _draw_centered_field_text(
            draw,
            x0_px=loc_x0,
            x1_px=loc_x1,
            baseline_px=baseline_px,
            text=loc_name.upper(),
            font=loc_font,
            fill=(255, 255, 255, 255),
        )
        ssid = str(state.pending_wifi_ssid or state.selected_wifi_ssid or "").strip()
        if ssid:
            net_spec = _TEXT_ENTRY_FIELDS["network"]
            ssid_size = _entry_content_font_size_px(
                net_spec,
                buffer=ssid,
                display=ssid,
                max_width_px=max(24, net_x1 - net_x0 - 16),
            )
            ssid_font = _field_font(net_spec, ssid_size)
            _draw_centered_field_text(
                draw,
                x0_px=net_x0,
                x1_px=net_x1,
                baseline_px=baseline_px,
                text=ssid,
                font=ssid_font,
                fill=_COLOR_GRAY_RGBA,
            )
        bgra[:] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)
        return

    if target == "network" and state.keyboard_open:
        ssid = str(state.pending_wifi_ssid or state.selected_wifi_ssid or "").strip()
        if ssid:
            loc_x0, loc_x1 = _dual_location_text_bounds_px()
            loc_spec = _TEXT_ENTRY_FIELDS["location"]
            ssid_size = _entry_content_font_size_px(
                loc_spec,
                buffer=ssid,
                display=ssid,
                max_width_px=max(24, loc_x1 - loc_x0 - 56),
            )
            ssid_font = _field_font(loc_spec, ssid_size)
            ssid_fill = _COLOR_GRAY_RGBA if state.network_password_error else (255, 255, 255, 255)
            _draw_centered_field_text(
                draw,
                x0_px=loc_x0,
                x1_px=loc_x1,
                baseline_px=baseline_px,
                text=ssid.upper(),
                font=ssid_font,
                fill=ssid_fill,
            )
    elif target == "location":
        loc_x0, loc_x1 = _dual_location_text_bounds_px()
        loc_spec = _TEXT_ENTRY_FIELDS["location"]
        loc_display = _entry_display_text(buffer=buffer, initial=initial, spec=loc_spec, state=state)
        if loc_display:
            loc_size = _entry_content_font_size_px(
                loc_spec,
                buffer=buffer,
                display=loc_display,
                max_width_px=max(24, loc_x1 - loc_x0 - 16),
            )
            loc_font = _field_font(loc_spec, loc_size)
            loc_fill = (
                _COLOR_GRAY_RGBA
                if (not buffer and _location_text_is_grayed(state))
                else (255, 255, 255, 255)
            )
            _draw_centered_field_text(
                draw,
                x0_px=loc_x0,
                x1_px=loc_x1,
                baseline_px=baseline_px,
                text=loc_display,
                font=loc_font,
                fill=loc_fill,
            )
        bgra[:] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)
        return

    if kb is None and state.manual_device_entry is not None:
        entry = state.manual_device_entry
        loc_x0, loc_x1 = _dual_location_text_bounds_px()
        net_x0 = int(round(_DUAL_NETWORK_TEXT_X0_SVG * DESIGN_W / 800.0))
        net_x1 = int(round(_DUAL_NETWORK_TEXT_X1_SVG * DESIGN_W / 800.0))
        loc_spec = _TEXT_ENTRY_FIELDS["device_name"]
        net_spec = _TEXT_ENTRY_FIELDS["device_ip"]
        loc_font = _field_font(loc_spec, _entry_content_font_size_px(
            loc_spec, buffer="", display="NEW DEVICE", max_width_px=max(24, loc_x1 - loc_x0 - 16)
        ))
        _draw_centered_field_text(
            draw,
            x0_px=loc_x0,
            x1_px=loc_x1,
            baseline_px=baseline_px,
            text="NEW DEVICE",
            font=loc_font,
            fill=_COLOR_GRAY_RGBA,
        )
        ip_text = str(entry.ip or "").strip()
        if ip_text:
            ip_size = _entry_content_font_size_px(
                net_spec, buffer=ip_text, display=ip_text, max_width_px=max(24, net_x1 - net_x0 - 16)
            )
            ip_font = _field_font(net_spec, ip_size)
            _draw_centered_field_text(
                draw,
                x0_px=net_x0,
                x1_px=net_x1,
                baseline_px=baseline_px,
                text=ip_text,
                font=ip_font,
                fill=(255, 255, 255, 255),
            )
        bgra[:] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)
        return

    if target == "pin":
        loc_x0, loc_x1 = _dual_location_text_bounds_px()
        net_x0 = int(round(_DUAL_NETWORK_TEXT_X0_SVG * DESIGN_W / 800.0))
        net_x1 = int(round(_DUAL_NETWORK_TEXT_X1_SVG * DESIGN_W / 800.0))
        device_name = ""
        if state.box_pairing is not None:
            device_name = str(state.box_pairing.device_name or "").strip().upper()
        if device_name:
            loc_spec = _TEXT_ENTRY_FIELDS["location"]
            loc_size = _entry_content_font_size_px(
                loc_spec,
                buffer=device_name,
                display=device_name,
                max_width_px=max(24, loc_x1 - loc_x0 - 16),
            )
            loc_font = _field_font(loc_spec, loc_size)
            _draw_centered_field_text(
                draw,
                x0_px=loc_x0,
                x1_px=loc_x1,
                baseline_px=baseline_px,
                text=device_name,
                font=loc_font,
                fill=(255, 255, 255, 255),
            )
        pin_spec = _TEXT_ENTRY_FIELDS["pin"]
        pin_display = _entry_display_text(buffer=buffer, initial=initial, spec=pin_spec, state=state)
        if pin_display:
            pin_size = _entry_content_font_size_px(
                pin_spec,
                buffer=buffer,
                display=pin_display,
                max_width_px=max(24, net_x1 - net_x0 - 16),
            )
            pin_font = _field_font(pin_spec, pin_size)
            pin_fill = _COLOR_GRAY_RGBA if not buffer else (255, 255, 255, 255)
            _draw_centered_field_text(
                draw,
                x0_px=net_x0,
                x1_px=net_x1,
                baseline_px=baseline_px,
                text=pin_display,
                font=pin_font,
                fill=pin_fill,
            )
        bgra[:] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)
        return

    if target == "device_ip":
        loc_x0, loc_x1 = _dual_location_text_bounds_px()
        net_x0 = int(round(_DUAL_NETWORK_TEXT_X0_SVG * DESIGN_W / 800.0))
        net_x1 = int(round(_DUAL_NETWORK_TEXT_X1_SVG * DESIGN_W / 800.0))
        loc_spec = _TEXT_ENTRY_FIELDS["device_name"]
        loc_font = _field_font(loc_spec, _entry_content_font_size_px(
            loc_spec, buffer="", display="NEW DEVICE", max_width_px=max(24, loc_x1 - loc_x0 - 16)
        ))
        _draw_centered_field_text(
            draw,
            x0_px=loc_x0,
            x1_px=loc_x1,
            baseline_px=baseline_px,
            text="NEW DEVICE",
            font=loc_font,
            fill=_COLOR_GRAY_RGBA,
        )
        ip_spec = _TEXT_ENTRY_FIELDS["device_ip"]
        ip_display = _entry_display_text(buffer=buffer, initial=initial, spec=ip_spec, state=state)
        if ip_display:
            ip_size = _entry_content_font_size_px(
                ip_spec, buffer=buffer, display=ip_display, max_width_px=max(24, net_x1 - net_x0 - 16)
            )
            ip_font = _field_font(ip_spec, ip_size)
            _draw_centered_field_text(
                draw,
                x0_px=net_x0,
                x1_px=net_x1,
                baseline_px=baseline_px,
                text=ip_display,
                font=ip_font,
                fill=(255, 255, 255, 255),
            )
        bgra[:] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)
        return

    if target == "device_name":
        entry = state.manual_device_entry
        loc_x0, loc_x1 = _dual_location_text_bounds_px()
        net_x0 = int(round(_DUAL_NETWORK_TEXT_X0_SVG * DESIGN_W / 800.0))
        net_x1 = int(round(_DUAL_NETWORK_TEXT_X1_SVG * DESIGN_W / 800.0))
        name_spec = _TEXT_ENTRY_FIELDS["device_name"]
        name_display = _entry_display_text(buffer=buffer, initial=initial, spec=name_spec, state=state)
        if name_display:
            name_size = _entry_content_font_size_px(
                name_spec, buffer=buffer, display=name_display, max_width_px=max(24, loc_x1 - loc_x0 - 16)
            )
            name_font = _field_font(name_spec, name_size)
            name_fill = _COLOR_GRAY_RGBA if (not buffer and name_display == "NEW DEVICE") else (255, 255, 255, 255)
            _draw_centered_field_text(
                draw,
                x0_px=loc_x0,
                x1_px=loc_x1,
                baseline_px=baseline_px,
                text=name_display,
                font=name_font,
                fill=name_fill,
            )
        ip_text = str(entry.ip or "").strip() if entry is not None else ""
        if ip_text:
            ip_spec = _TEXT_ENTRY_FIELDS["device_ip"]
            ip_size = _entry_content_font_size_px(
                ip_spec, buffer=ip_text, display=ip_text, max_width_px=max(24, net_x1 - net_x0 - 16)
            )
            ip_font = _field_font(ip_spec, ip_size)
            _draw_centered_field_text(
                draw,
                x0_px=net_x0,
                x1_px=net_x1,
                baseline_px=baseline_px,
                text=ip_text,
                font=ip_font,
                fill=(255, 255, 255, 255),
            )
        bgra[:] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)
        return

    if target != "network":
        bgra[:] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)
        return

    x0_px = int(round(float(spec["x0_svg"]) * DESIGN_W / 800.0))
    x1_px = int(round(float(spec["x1_svg"]) * DESIGN_W / 800.0))
    display = _entry_display_text(buffer=buffer, initial=initial, spec=spec, state=state)
    placeholder = str(spec.get("placeholder", "") or "")
    max_field_w = max(24, x1_px - x0_px - 8)
    size_px = _entry_content_font_size_px(
        spec, buffer=buffer, display=display, max_width_px=max_field_w
    )
    font = _field_font(spec, size_px)
    cx = (x0_px + x1_px) // 2
    if display and state.keyboard_open:
        is_password_error = (
            state.network_password_error
            and spec.get("password_mask")
            and display == "incorrect password"
        )
        is_placeholder = (
            not buffer
            and spec.get("password_mask")
            and display == placeholder
        )
        is_initial_gray = not buffer and bool(initial) and not spec.get("password_mask")
        fill = _COLOR_GRAY_RGBA if (is_placeholder or is_initial_gray or is_password_error) else (255, 255, 255, 255)
        text_y = baseline_px
        if is_placeholder:
            text_y += int(spec.get("placeholder_baseline_offset_px", 0))
        use_mask_center = bool(spec.get("password_mask") and buffer and "*" in display)
        if use_mask_center:
            _draw_password_mask_text(
                draw,
                cx=cx,
                baseline_px=baseline_px,
                display=display,
                font=font,
                spec=spec,
                size_px=size_px,
                fill=fill,
            )
        else:
            _draw_centered_field_text(
                draw,
                x0_px=x0_px,
                x1_px=x1_px,
                baseline_px=text_y,
                text=display,
                font=font,
                fill=fill,
            )
    bgra[:] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)


def _centered_field_cursor_x(
    draw: ImageDraw.ImageDraw,
    *,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    x0_px: int,
    x1_px: int,
) -> int:
    """Insertion caret immediately after the last rendered character."""
    if not text:
        return x0_px + 8
    bbox = draw.textbbox((0, 0), text, font=font, anchor="ls")
    tw = bbox[2] - bbox[0]
    cx = (x0_px + x1_px) // 2
    return cx - tw // 2 + tw + 1


def _draw_text_entry_cursor(bgra: np.ndarray, state: MainSettingsState) -> None:
    """Blinking insertion caret for the active dual-bar text field."""
    kb = state.keyboard
    if kb is None:
        return
    if int(time.monotonic() * 2) % 2:
        return
    target = str(getattr(kb, "target", "") or "")
    spec = _text_field_spec(target)
    if spec is None:
        return
    buffer = str(getattr(kb, "buffer", "") or "")
    initial = str(getattr(kb, "initial_text", "") or "")
    x0_px, x1_px = _entry_field_bounds_px(target)
    baseline_px = int(round(float(spec["baseline_y_svg"]) * DESIGN_H / _ARTBOARD_H))
    display = _entry_cursor_display(
        buffer=buffer, initial=initial, spec=spec, state=state
    )
    placeholder = str(spec.get("placeholder", "") or "")
    max_field_w = max(24, x1_px - x0_px - 8)
    content_display = _entry_display_text(
        buffer=buffer, initial=initial, spec=spec, state=state
    )
    size_px = _entry_content_font_size_px(
        spec, buffer=buffer, display=content_display, max_width_px=max_field_w
    )
    font = _field_font(spec, size_px)
    rgb = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGBA)
    img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(img)
    cx = (x0_px + x1_px) // 2
    is_password_error = (
        state.network_password_error
        and spec.get("password_mask")
        and display == "incorrect password"
    )
    use_mask_center = bool(
        spec.get("password_mask") and buffer and display and "*" in display and not is_password_error
    )
    text_y = baseline_px
    if display and not is_password_error:
        if use_mask_center:
            _x0, _my, _mw, _vw, cursor_x, top, bot = _password_mask_layout(
                draw,
                cx=cx,
                baseline_px=baseline_px,
                display=display,
                font=font,
                spec=spec,
                size_px=size_px,
            )
        else:
            if not buffer and spec.get("password_mask") and display == placeholder:
                text_y += int(spec.get("placeholder_baseline_offset_px", 0))
            cursor_x = _centered_field_cursor_x(
                draw, text=display, font=font, x0_px=x0_px, x1_px=x1_px
            )
            if display:
                bbox = draw.textbbox((0, 0), display, font=font, anchor="ls")
                top = text_y + bbox[1]
                bot = text_y + bbox[3]
            else:
                top = text_y - size_px + 4
                bot = text_y + 2
        cursor_x = _clamp_field_cursor_x(cursor_x, x0_px=x0_px, x1_px=x1_px)
    else:
        cursor_x = _clamp_field_cursor_x(x0_px + 8, x0_px=x0_px, x1_px=x1_px)
        top = text_y - size_px + 4
        bot = text_y + 2
    draw.line([(cursor_x, top), (cursor_x, bot)], fill=(255, 255, 255, 255), width=2)
    bgra[:] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)


def _layer_class(logical_id: str) -> str | None:
    for cls in ("_text", "_icon", "_button", "_accent", "_group", "_container"):
        if logical_id.endswith(cls) or f"{cls}_" in logical_id:
            # Prefer the terminal class token.
            if logical_id.endswith(cls):
                return cls
    for cls in ("_text", "_icon", "_button", "_accent", "_group", "_container"):
        if logical_id.endswith(cls):
            return cls
    return None


def _svg_scale(value: float) -> float:
    return value * DESIGN_H / _ARTBOARD_H


def _svg_to_px(x_svg: float, y_svg: float) -> tuple[int, int]:
    x = int(round(x_svg * DESIGN_W / 800.0))
    y = int(round(y_svg * DESIGN_H / _ARTBOARD_H))
    return x, y


def _svg_radius_to_px(radius_svg: float) -> int:
    return max(1, int(round(_svg_scale(radius_svg))))


def _hex_to_bgr(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#").lower()
    if len(h) == 3:
        h = f"{h[0]}{h[0]}{h[1]}{h[1]}{h[2]}{h[2]}"
    if len(h) != 6:
        return (255, 255, 255)
    return (int(h[2:4], 16), int(h[4:6], 16), int(h[0:2], 16))


def _hide_svg_wifi_icons(root: ET.Element) -> None:
    """Remove WiFi circles / fail badge from SVG (redrawn with star clip after rasterize)."""
    parents = _parent_map(root)
    to_remove: list[ET.Element] = []
    for el in root.iter():
        logical = _normalize_logical(el.get("id") or "")
        if re.search(r"wifi[123]_icon", logical) or "wifi_fail_icon" in logical:
            to_remove.append(el)
    for el in to_remove:
        parent = parents.get(el)
        if parent is not None:
            parent.remove(el)


def _hide_box_column_search_svg_circles(root: ET.Element) -> None:
    """Hide leftover clip-path ring circles. Stroke arc paths rasterize correctly."""
    parents = _parent_map(root)
    for box_num in (1, 2, 3):
        search_icon = _find_box_column_search_icon(root, box_num)
        if search_icon is None or _is_subtree_hidden(search_icon, parents):
            continue
        for el in search_icon.iter():
            if el.tag.endswith("circle"):
                _set_visible(el, False)


def _collect_box_search_arc_overlays(
    root: ET.Element,
    state: MainSettingsState,
    *,
    focused_logical: str,
) -> list[_BoxSearchArcOverlay]:
    """Arc specs + stroke colors for visible idle box search icons.

    Path-stroked ellipses rasterize correctly; only legacy clip-path circles
    need an OpenCV redraw. Skip entirely while a keyboard overlay is up so the
    add/search arrows cannot bleed through the PIN pad.
    """
    if state.keyboard_open:
        return []
    parents = _parent_map(root)
    overlays: list[_BoxSearchArcOverlay] = []
    for box_num in (1, 2, 3):
        search_icon = _find_box_column_search_icon(root, box_num)
        if search_icon is None or _is_subtree_hidden(search_icon, parents):
            continue
        if not any(el.tag.endswith("circle") for el in search_icon.iter()):
            continue
        panel = state._box_panel(box_num) if box_num in (2, 3) else None
        scanning = bool(panel is not None and panel.scanning and panel.active)
        # Rotating spinner path draws its own glyph; skip static arcs.
        if scanning and not bool(getattr(state, "spinner_glyph_capture", False)):
            continue
        specs = _discover_search_arc_specs_in_group(root, search_icon)
        if not specs:
            continue
        selected = focused_logical == f"main_box{box_num}_button" or scanning
        color_bgr = _hex_to_bgr("#202020" if selected else "#ffffff")
        overlays.append(_BoxSearchArcOverlay(specs=specs, color_bgr=color_bgr))
    return overlays


def _draw_box_search_arc_overlays(
    bgra: np.ndarray,
    overlays: list[_BoxSearchArcOverlay],
) -> None:
    for overlay in overlays:
        for spec in overlay.specs:
            _draw_onboarding_search_arc_stroke(bgra, spec, color_bgr=overlay.color_bgr)


def _svg_id_index(root: ET.Element) -> dict[str, ET.Element]:
    return {raw: el for el in root.iter() if (raw := el.get("id"))}


def _parse_matrix_translate(transform: str | None) -> tuple[float, float] | None:
    if not transform:
        return None
    m = _MATRIX_RE.search(transform)
    if m:
        return float(m.group(5)), float(m.group(6))
    t = _TRANSLATE_RE.search(transform)
    if t:
        return float(t.group(1)), float(t.group(2) or 0.0)
    return None


def _wifi_star_clip_polygon(group: ET.Element, id_index: dict[str, ET.Element]) -> tuple[tuple[float, float], ...]:
    """Illustrator ``<Star>`` clip mask → polygon points (the visible wifi wedge)."""
    xlink = f"{{{XLINK_NS}}}"
    for cp in group.iter():
        if not cp.tag.endswith("clipPath"):
            continue
        for child in cp:
            if child.tag.endswith("use"):
                href = child.get(f"{xlink}href") or child.get("href") or ""
                ref = href.lstrip("#")
                target = id_index.get(ref)
                if target is not None and target.get("points"):
                    pts = _parse_svg_points(target.get("points"))
                    if pts:
                        return pts
            if child.tag.endswith("polygon") and child.get("points"):
                pts = _parse_svg_points(child.get("points"))
                if pts:
                    return pts
    for poly in group.iter():
        if poly.tag.endswith("polygon") and poly.get("points"):
            pts = _parse_svg_points(poly.get("points"))
            if len(pts) >= 3:
                return pts
    return ()


def _wifi_circle_center(group: ET.Element) -> tuple[float, float] | None:
    for el in group.iter():
        if not el.tag.endswith("circle") or not el.get("cx"):
            continue
        logical = _normalize_logical(el.get("id") or "")
        if re.search(r"wifi[123]_icon", logical) or "wifi" in logical:
            return float(el.get("cx")), float(el.get("cy"))
    for el in group.iter():
        if el.tag.endswith("circle") and el.get("cx"):
            return float(el.get("cx")), float(el.get("cy"))
    return None


def _wifi_fail_text_xy(group: ET.Element) -> tuple[float, float] | None:
    for el in group.iter():
        logical = _normalize_logical(el.get("id") or "")
        if "wifi_fail_icon" not in logical:
            continue
        for text in el.iter():
            if text.tag.endswith("text"):
                pos = _parse_matrix_translate(text.get("transform"))
                if pos is not None:
                    return pos
    return None


def _discover_wifi_icon_layouts(root: ET.Element) -> list[_WifiIconLayout]:
    """Parse each wifi stack's star clip + circle center from ``settings_main.svg``."""
    id_index = _svg_id_index(root)
    parents = _parent_map(root)
    layouts: list[_WifiIconLayout] = []
    for group in root.iter():
        if not group.tag.endswith("g"):
            continue
        if _is_subtree_hidden(group, parents):
            continue
        logical = _normalize_logical(group.get("id") or "")
        is_dual = logical.endswith("_wifi_group")
        is_picker = "wifi_info" in logical and "network_picker" in logical
        if not is_dual and not is_picker:
            continue
        clip = _wifi_star_clip_polygon(group, id_index)
        center = _wifi_circle_center(group)
        if not clip or center is None:
            continue
        cx, cy = center
        if is_dual:
            cy -= 3.0
            focus_logical = "main_dual_network_button"
            picker_row = False
            picker_slot = None
        else:
            focus_logical = "main_network_picker_button"
            picker_row = True
            picker_slot = None
            m = re.search(r"network_picker_row(\d+)_wifi", logical)
            if m:
                picker_slot = int(m.group(1)) - 1
        layouts.append(
            _WifiIconLayout(
                clip_polygon_svg=clip,
                cx_svg=cx,
                cy_svg=cy,
                fail_text_xy_svg=_wifi_fail_text_xy(group),
                focus_logical=focus_logical,
                picker_row=picker_row,
                picker_slot=picker_slot,
            )
        )
    return layouts


def _composite_stroke_mask(
    dst: np.ndarray,
    mask: np.ndarray,
    color_bgr: tuple[int, int, int],
) -> None:
    """Paint ``color_bgr`` where ``mask`` is non-zero (opaque). Avoids full-frame float blends."""
    sel = mask > 0
    if not np.any(sel):
        return
    dst[sel, 0] = color_bgr[0]
    dst[sel, 1] = color_bgr[1]
    dst[sel, 2] = color_bgr[2]
    dst[sel, 3] = 255


@lru_cache(maxsize=4)
def _load_wifi_fail_font(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    from pigeon.font_paths import resolve_ui_font_extrabold

    path = resolve_ui_font_extrabold()
    if not path:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(path, max(8, size_px))
    except OSError:
        return ImageFont.load_default()


def _draw_wifi_fail_badge(
    bgra: np.ndarray,
    layout: _WifiIconLayout,
    *,
    color_bgr: tuple[int, int, int],
    clip_mask: np.ndarray,
) -> None:
    if layout.fail_text_xy_svg is None:
        return
    x, y = _svg_to_px(*layout.fail_text_xy_svg)
    size_px = max(8, int(round(_svg_scale(_WIFI_FAIL_SIZE_SVG))))
    font = _load_wifi_fail_font(size_px)
    text = "!"

    pad = max(48, size_px * 2)
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(bgra.shape[1], x + pad)
    y1 = min(bgra.shape[0], y + pad)
    patch_h, patch_w = y1 - y0, x1 - x0
    if patch_h <= 0 or patch_w <= 0:
        return

    rgb = np.zeros((patch_h, patch_w, 4), dtype=np.uint8)
    img = Image.fromarray(rgb, mode="RGBA")
    draw = ImageDraw.Draw(img)
    r, g, b = color_bgr[2], color_bgr[1], color_bgr[0]
    draw.text((x - x0, y - y0), text, font=font, fill=(r, g, b, 255), anchor="ls")
    glyph = np.asarray(img)
    glyph_mask = glyph[:, :, 3]

    region_clip = clip_mask[y0:y1, x0:x1]
    combined = cv2.bitwise_and(glyph_mask, region_clip)
    if not np.any(combined > 0):
        return

    sub = bgra[y0:y1, x0:x1]
    alpha = combined.astype(np.float32) / 255.0
    alpha3 = alpha[..., np.newaxis]
    fg = np.array(color_bgr, dtype=np.float32)
    bg = sub[:, :, :3].astype(np.float32)
    sub[:, :, :3] = np.clip(fg * alpha3 + bg * (1.0 - alpha3), 0, 255).astype(np.uint8)
    sub[:, :, 3] = 255


def _draw_wifi_icon_overlay(
    bgra: np.ndarray,
    layout: _WifiIconLayout,
    *,
    level: int,
    active_bgr: tuple[int, int, int],
) -> None:
    cx, cy = _svg_to_px(layout.cx_svg, layout.cy_svg)
    radii = [_svg_radius_to_px(r) for r in _WIFI_RADII_SVG]
    stroke = max(1, int(round(_svg_scale(_WIFI_STROKE_SVG))))

    clip_pts = np.array(
        [_svg_to_px(x, y) for x, y in layout.clip_polygon_svg],
        dtype=np.int32,
    )
    clip_mask = np.zeros(bgra.shape[:2], dtype=np.uint8)
    cv2.fillPoly(clip_mask, [clip_pts], 255)

    if level == 0:
        _draw_wifi_fail_badge(bgra, layout, color_bgr=active_bgr, clip_mask=clip_mask)
        return

    for i, radius in enumerate(radii):
        color = active_bgr if i < level else _COLOR_GRAY_BGR
        ring_mask = np.zeros(bgra.shape[:2], dtype=np.uint8)
        cv2.circle(ring_mask, (cx, cy), radius, 255, stroke, lineType=cv2.LINE_AA)
        ring_mask = cv2.bitwise_and(ring_mask, clip_mask)
        _composite_stroke_mask(bgra, ring_mask, color)


def _draw_wifi_overlays(
    bgra: np.ndarray,
    state: MainSettingsState,
    layouts: list[_WifiIconLayout],
    *,
    focused_logical: str,
) -> None:
    del focused_logical  # WiFi icons stay brand red regardless of nav focus.
    default_level = max(0, min(3, int(state.wifi_level)))
    active_bgr = _hex_to_bgr(state.theme.ui)
    scroll = max(0, int(state.network_picker_scroll))
    kb_target = ""
    if state.keyboard is not None:
        kb_target = str(getattr(state.keyboard, "target", "") or "")
    kb_network = state.keyboard_open and kb_target == "network"
    kb_logout = state.keyboard_open and kb_target == "wifi_logout"
    dual_layout: _WifiIconLayout | None = None
    for layout in layouts:
        if layout.focus_logical == "main_dual_network_button":
            dual_layout = layout
            break
    # Dual-network CTA shows "RENAME" while picking nests — hide the Wi‑Fi logo
    # (SVG group is already display:none; overlays must skip too).
    rename_mode = bool(state.show_location_picker) and not _needs_wifi_setup(state)
    for layout in layouts:
        if layout.picker_row and not state.show_network_picker:
            continue
        if layout.focus_logical == "main_dual_network_button":
            if kb_logout or rename_mode:
                continue
            if not state.wifi_configured and not state.wifi_connecting:
                continue
            if kb_network:
                continue
        level = default_level
        if layout.picker_row and layout.picker_slot is not None:
            abs_idx = scroll + int(layout.picker_slot)
            if abs_idx >= len(state.wifi_networks):
                continue
            level = max(1, min(3, 3 - abs_idx))
        _draw_wifi_icon_overlay(bgra, layout, level=level, active_bgr=active_bgr)
    if (kb_network or kb_logout) and dual_layout is not None:
        loc_layout = _WifiIconLayout(
            clip_polygon_svg=dual_layout.clip_polygon_svg,
            cx_svg=_DUAL_LOCATION_WIFI_CENTER_SVG[0],
            cy_svg=_DUAL_LOCATION_WIFI_CENTER_SVG[1],
            fail_text_xy_svg=(
                _DUAL_LOCATION_WIFI_CENTER_SVG[0],
                _DUAL_LOCATION_WIFI_CENTER_SVG[1] - 18.0,
            ),
            focus_logical="main_dual_location_button",
            picker_row=False,
        )
        _draw_wifi_icon_overlay(bgra, loc_layout, level=default_level, active_bgr=active_bgr)


@dataclass(frozen=True)
class _OnboardingSearchArcSpec:
    """Single clipped ring arc from search-icon art (box columns + onboarding)."""

    cx_svg: float
    cy_svg: float
    radius_svg: float
    stroke_svg: float
    clip_corners_svg: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class _BoxSearchArcOverlay:
    """Idle box2/box3 search arcs redrawn after rasterize (PyMuPDF drops clip-path)."""

    specs: tuple[_OnboardingSearchArcSpec, ...]
    color_bgr: tuple[int, int, int]


@dataclass(frozen=True)
class _StarMaskedCircleSpec:
    """Circle stroke clipped by a hex/star polygon from the SVG (search + pigeon logo)."""

    mask_polygon_svg: tuple[tuple[float, float], ...]
    cx_svg: float
    cy_svg: float
    radius_svg: float
    stroke_svg: float
    default_stroke_hex: str
    focus_button: str | None = None
    circle_el_id: int | None = None


def _parse_svg_points(raw: str) -> tuple[tuple[float, float], ...]:
    nums = [float(x) for x in re.split(r"[\s,]+", raw.strip()) if x]
    if len(nums) < 6:
        return ()
    return tuple((nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2))


def _circle_stroke_from_element(el: ET.Element) -> tuple[float, float, float, float, str]:
    cx = float(el.get("cx") or 0.0)
    cy = float(el.get("cy") or 0.0)
    radius = float(el.get("r") or 0.0)
    _, stroke = _iter_style_fill_stroke(el)
    style = el.get("style") or ""
    sw_m = re.search(r"stroke-width\s*:\s*([-\d.]+)", style, re.IGNORECASE)
    if sw_m:
        stroke_w = float(sw_m.group(1))
    else:
        try:
            stroke_w = float(el.get("stroke-width") or 1.0)
        except ValueError:
            stroke_w = 1.0
    stroke_hex = stroke if stroke and stroke.startswith("#") else "#202020"
    return cx, cy, radius, stroke_w, stroke_hex


def _ancestor_search_icon(el: ET.Element, parents: dict[ET.Element, ET.Element]) -> ET.Element | None:
    cur: ET.Element | None = el
    while cur is not None:
        logical = _normalize_logical(cur.get("id") or "")
        if logical.endswith("_search_icon"):
            return cur
        cur = parents.get(cur)
    return None


def _box_focus_button(el: ET.Element, parents: dict[ET.Element, ET.Element]) -> str | None:
    cur: ET.Element | None = el
    while cur is not None:
        logical = _normalize_logical(cur.get("id") or "")
        for box in ("main_box1", "main_box2", "main_box3"):
            if logical == box:
                return f"{box}_button"
            if logical.startswith(f"{box}_"):
                if (
                    logical.startswith(f"{box}_search_icon")
                    or logical.startswith(f"{box}_search_results")
                    or logical.startswith(f"{box}_container")
                    or logical == f"{box}_add_search_icon"
                ):
                    break
                return f"{box}_button"
        cur = parents.get(cur)
    return None


def _discover_star_masked_circles(root: ET.Element) -> list[_StarMaskedCircleSpec]:
    """Pair pigeon-logo hex clips with circle strokes (search spinners use a separate path)."""
    parents = _parent_map(root)
    specs: list[_StarMaskedCircleSpec] = []

    for el in root.iter():
        logical = _normalize_logical(el.get("id") or "")
        if not logical.endswith("_pigeon_logo_icon"):
            continue
        if _is_subtree_hidden(el, parents):
            continue
        hex_pts: tuple[tuple[float, float], ...] | None = None
        circles: list[ET.Element] = []
        for sub in el.iter():
            if sub.tag.endswith("polygon") and sub.get("points"):
                pts = _parse_svg_points(sub.get("points") or "")
                if len(pts) == 6:
                    hex_pts = pts
            if sub.tag.endswith("circle") and sub.get("cx") and sub.get("r"):
                fill, stroke = _iter_style_fill_stroke(sub)
                if stroke in ("none", "transparent") and fill in ("none", "transparent"):
                    continue
                circles.append(sub)
        if hex_pts is None or not circles:
            continue
        focus_button = _box_focus_button(el, parents)
        for circle_el in circles:
            cx, cy, radius, stroke_w, stroke_hex = _circle_stroke_from_element(circle_el)
            specs.append(
                _StarMaskedCircleSpec(
                    mask_polygon_svg=hex_pts,
                    cx_svg=cx,
                    cy_svg=cy,
                    radius_svg=radius,
                    stroke_svg=stroke_w,
                    default_stroke_hex=stroke_hex,
                    focus_button=focus_button,
                    circle_el_id=id(circle_el),
                )
            )
    return specs


def _hide_star_masked_svg_circles(root: ET.Element, specs: list[_StarMaskedCircleSpec]) -> None:
    """Hide circle strokes replaced by star/hex clip overlays.

    Prefer live element ids from this render's discover pass. Also hide every
    stroked circle under ``*_pigeon_logo_icon`` when any star specs exist for
    that focus button, so a stale/cross-tree id never leaves unclipped rings.
    """
    hide_ids = {spec.circle_el_id for spec in specs if spec.circle_el_id is not None}
    focus_buttons = {spec.focus_button for spec in specs if spec.focus_button}
    for el in root.iter():
        if el.tag.endswith("circle") and id(el) in hide_ids:
            _set_visible(el, False)
    if not specs:
        return
    parents = _parent_map(root)
    for el in root.iter():
        logical = _normalize_logical(el.get("id") or "")
        if not logical.endswith("_pigeon_logo_icon"):
            continue
        focus = _box_focus_button(el, parents)
        if focus_buttons and focus not in focus_buttons:
            continue
        for sub in el.iter():
            if not sub.tag.endswith("circle"):
                continue
            fill, stroke = _iter_style_fill_stroke(sub)
            if stroke in ("none", "transparent") and fill in ("none", "transparent"):
                continue
            _set_visible(sub, False)


def _hide_box_search_animation_layers(root: ET.Element, box_num: int) -> None:
    """Force-hide search spinner art for a column (SVG vectors + group)."""
    search_icon = _find_box_column_search_icon(root, box_num)
    if search_icon is not None:
        _set_visible(search_icon, False)
        _hide_box_search_icon_vectors(search_icon)
    # Belt-and-suspenders: hide any leftover search triangle/ellipse under this box.
    prefix = f"main_box{box_num}_search_"
    for el in root.iter():
        logical = _normalize_logical(el.get("id") or "")
        if not logical.startswith(prefix):
            continue
        if "results" in logical:
            continue
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("circle", "polygon", "g") or "triangle" in logical or "eplipse" in logical or "ellipse" in logical:
            _set_visible(el, False)


def _stroke_bgr_for_star_spec(
    spec: _StarMaskedCircleSpec,
    *,
    state: MainSettingsState,
    focused_logical: str,
) -> tuple[int, int, int]:
    theme = state.theme
    if spec.focus_button and len(spec.mask_polygon_svg) == 6:
        m = re.match(r"main_box([23])_button", spec.focus_button)
        if m is not None:
            panel = state._box_panel(int(m.group(1)))
            if not panel.scanning and not panel.active:
                return _hex_to_bgr(theme.ui or COLOR_UI_DEFAULT)
    if spec.focus_button:
        m = re.match(r"main_box([23])_button", spec.focus_button)
        if m is not None:
            panel = state._box_panel(int(m.group(1)))
            if panel.scanning or panel.active:
                return _hex_to_bgr(theme.selected)
        selected = focused_logical == spec.focus_button
        contrast = theme.selected if selected else theme.deselected
        return _hex_to_bgr(contrast)
    return _hex_to_bgr(spec.default_stroke_hex)


def _draw_star_masked_circle_stroke(
    bgra: np.ndarray,
    spec: _StarMaskedCircleSpec,
    *,
    color_bgr: tuple[int, int, int],
) -> None:
    cx, cy = _svg_to_px(spec.cx_svg, spec.cy_svg)
    radius = _svg_radius_to_px(spec.radius_svg)
    stroke = max(1, int(round(_svg_scale(spec.stroke_svg))))
    clip_pts = np.array(
        [_svg_to_px(x, y) for x, y in spec.mask_polygon_svg],
        dtype=np.int32,
    )
    clip_mask = np.zeros(bgra.shape[:2], dtype=np.uint8)
    cv2.fillPoly(clip_mask, [clip_pts], 255)
    ring_mask = np.zeros(bgra.shape[:2], dtype=np.uint8)
    cv2.circle(ring_mask, (cx, cy), radius, 255, stroke, lineType=cv2.LINE_AA)
    ring_mask = cv2.bitwise_and(ring_mask, clip_mask)
    _composite_stroke_mask(bgra, ring_mask, color_bgr)


def _draw_star_masked_circle_overlays(
    bgra: np.ndarray,
    state: MainSettingsState,
    specs: list[_StarMaskedCircleSpec],
    *,
    focused_logical: str,
) -> None:
    if state.keyboard_open or state.manual_device_entry is not None:
        return
    for spec in specs:
        if spec.focus_button:
            m = re.match(r"main_box([23])_button", spec.focus_button)
            if m is not None:
                panel = state._box_panel(int(m.group(1)))
                if panel.scanning or panel.results_locked:
                    continue
        color = _stroke_bgr_for_star_spec(spec, state=state, focused_logical=focused_logical)
        _draw_star_masked_circle_stroke(bgra, spec, color_bgr=color)


def _is_subtree_hidden(el: ET.Element, parents: dict[ET.Element, ET.Element]) -> bool:
    cur: ET.Element | None = el
    while cur is not None:
        if cur.get("display") == "none":
            return True
        style = cur.get("style") or ""
        if re.search(r"display\s*:\s*none", style, re.IGNORECASE):
            return True
        cur = parents.get(cur)
    return False


def _active_background_container(state: MainSettingsState, focused: str) -> str:
    """Which diagonal-stripe ``containerN`` group matches the focused box button."""
    if state.show_network_picker or _needs_wifi_setup(state):
        return "container1"
    if focused == "main_box3_button":
        return "container3"
    if focused == "main_box2_button":
        return "container2"
    if focused == "main_box1_button":
        return "container1"
    return "container1"


def _apply_background_container(
    root: ET.Element,
    state: MainSettingsState,
    *,
    focused: str,
) -> None:
    """Legacy no-op: stripe backgrounds come from ``settings_background.svg`` now."""
    del state, focused
    for cid in _BACKGROUND_CONTAINERS:
        _set_visible(_find_by_logical_id(root, cid), False)


def _style_has_display_none(style: str | None) -> bool:
    return bool(style and re.search(r"display\s*:\s*none", style, re.IGNORECASE))


def _parse_svg_matrix(transform: str | None) -> tuple[float, float, float, float, float, float] | None:
    if not transform:
        return None
    m = _MATRIX_RE.search(transform)
    if not m:
        return None
    return tuple(float(m.group(i)) for i in range(1, 7))


def _transform_rect_corners_svg(
    x: float,
    y: float,
    w: float,
    h: float,
    matrix: tuple[float, float, float, float, float, float],
) -> tuple[tuple[float, float], ...]:
    a, b, c, d, e, f = matrix
    corners = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
    out: list[tuple[float, float]] = []
    for px, py in corners:
        out.append((a * px + c * py + e, b * px + d * py + f))
    return tuple(out)


@lru_cache(maxsize=1)
def _menu_container_mask() -> np.ndarray:
    """Inside=255 mask for the red menu container (PyMuPDF inverts SVG clip-path)."""
    from PIL import Image, ImageDraw

    mask = Image.new("L", (DESIGN_W, DESIGN_H), 0)
    draw = ImageDraw.Draw(mask)
    x0, y0, x1, y1 = _MENU_CONTAINER_BBOX
    draw.rounded_rectangle((x0, y0, x1, y1), radius=_MENU_CONTAINER_RADIUS_PX, fill=255)
    return np.asarray(mask, dtype=np.uint8)


_CONTAINER_BG_PLATE_CACHE: dict[tuple[object, ...], np.ndarray] = {}


def _container_stripe_cache_key(stripes: tuple[_ContainerStripeSpec, ...]) -> tuple[object, ...]:
    return tuple(
        (
            round(s.x_svg, 3),
            round(s.y_svg, 3),
            round(s.width_svg, 3),
            round(s.height_svg, 3),
            tuple(round(v, 5) for v in s.matrix),
            s.fill_hex,
        )
        for s in stripes
    )


def _draw_container_background_bgra(
    bgra: np.ndarray,
    stripes: tuple[_ContainerStripeSpec, ...] | None = None,
    *,
    ui_hex: str | None = None,
    assets_dir: Path | str | None = None,
) -> None:
    """Paint black frame + UI-tinted theme slants (shared settings_background.svg).

    ``stripes`` is ignored (legacy baked SVG discovery); kept for call-site compat.
    """
    del stripes  # embedded chrome SVG stripes are no longer used
    from pigeon.widgets.settings_theme_background import (
        draw_settings_theme_background_bgra,
    )

    draw_settings_theme_background_bgra(
        bgra,
        ui_hex=ui_hex or COLOR_UI_DEFAULT,
        clip_mask=_menu_container_mask(),
        assets_dir=assets_dir,
    )


def _disable_embedded_settings_background_layers(root: ET.Element) -> None:
    """Hide/remove baked stripe + canvas background groups from chrome SVGs.

    All settings menus paint the shared ``settings_background.svg`` plate in code;
    embedded Illustrator stripe/background layers must never rasterize.
    """
    for cid in _BACKGROUND_CONTAINERS:
        el = _find_by_logical_id(root, cid)
        if el is not None:
            _set_visible(el, False)
    # Catch any leftover stripe groups by logical id prefix.
    for el in root.iter():
        logical = _normalize_logical(el.get("id") or "")
        if logical in ("background", "settings_background") or logical.startswith(
            ("container", "menu_container_")
        ):
            if el.tag.endswith("g") or el.tag.endswith("rect"):
                _set_visible(el, False)
    _hide_container_stripe_rects(root)
    _remove_canvas_background_rect(root)
    # Illustrator exports sometimes bake the canvas + diagonal stripes into
    # plain ``Layer_NN`` groups (e.g. settings_pigeon_ui_color 0815): sweep the
    # whole tree for slanted stripe rects and full-canvas plates so the shared
    # code-drawn theme background stays visible underneath.
    parents = _parent_map(root)
    for el in list(root.iter()):
        if not el.tag.endswith("rect"):
            continue
        transform = el.get("transform") or _style_prop(el.get("style") or "", "transform")
        is_slant_stripe = _parse_svg_slant_transform(transform) is not None
        try:
            w = float(el.get("width") or 0.0)
            h = float(el.get("height") or 0.0)
        except ValueError:
            w = h = 0.0
        is_canvas_plate = w >= 790.0 and h >= 470.0
        if is_slant_stripe or is_canvas_plate:
            parent = parents.get(el)
            if parent is not None:
                parent.remove(el)
    for lid in ("settings_background", "background"):
        el = _find_by_logical_id(root, lid)
        if el is not None:
            _set_visible(el, False)

def _composite_bgra_over_bgra(base: np.ndarray, overlay: np.ndarray) -> np.ndarray:
    """Alpha-composite overlay onto base. Fast paths for empty / fully opaque pixels."""
    a = overlay[:, :, 3]
    if not np.any(a):
        return base
    out = base.copy()
    opaque = a == 255
    if np.any(opaque):
        out[opaque, :3] = overlay[opaque, :3]
        out[opaque, 3] = 255
    partial = (a > 0) & (a < 255)
    if np.any(partial):
        fg = overlay[partial].astype(np.float32)
        bg = out[partial].astype(np.float32)
        alpha = fg[:, 3:4] * (1.0 / 255.0)
        inv = 1.0 - alpha
        blended = fg[:, :3] * alpha + bg[:, :3] * inv
        out_a = np.clip(fg[:, 3] + bg[:, 3] * inv[:, 0], 0, 255)
        out[partial, :3] = blended
        out[partial, 3] = out_a
    return out.astype(np.uint8)


def _discover_container_stripe_specs(root: ET.Element, container_id: str) -> tuple[_ContainerStripeSpec, ...]:
    container = _find_by_logical_id(root, container_id)
    if container is None:
        return ()
    specs: list[_ContainerStripeSpec] = []
    for el in container.iter():
        if not el.tag.endswith("rect"):
            continue
        style = el.get("style") or ""
        if _style_has_display_none(style) or el.get("display") == "none":
            continue
        transform = el.get("transform") or _style_prop(style, "transform")
        matrix = _parse_svg_matrix(transform)
        if matrix is None:
            continue
        fill = _style_prop(style, "fill") or el.get("fill") or "#ff0013"
        if not fill.startswith("#"):
            continue
        try:
            specs.append(
                _ContainerStripeSpec(
                    x_svg=float(el.get("x") or 0.0),
                    y_svg=float(el.get("y") or 0.0),
                    width_svg=float(el.get("width") or 0.0),
                    height_svg=float(el.get("height") or 0.0),
                    matrix=matrix,
                    fill_hex=fill.lower(),
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(specs)


def _parse_svg_slant_transform(
    transform: str | None,
) -> tuple[float, float, float, float, float, float] | None:
    """Parse ``matrix(...)`` or Illustrator ``translate(...) rotate(...)`` CTMs."""
    matrix = _parse_svg_matrix(transform)
    if matrix is not None:
        return matrix
    if not transform:
        return None
    m = _TRANSLATE_RE.search(transform)
    if not m:
        return None
    rot = re.search(r"rotate\(\s*([-\d.]+)\s*\)", transform, re.IGNORECASE)
    if rot is None:
        return None
    import math

    tx = float(m.group(1))
    ty = float(m.group(2) or 0.0)
    rad = math.radians(float(rot.group(1)))
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    return (cos_a, sin_a, -sin_a, cos_a, tx, ty)


def _hide_container_stripe_rects(
    root: ET.Element,
    container_ids: tuple[str, ...] | None = None,
) -> None:
    """Remove diagonal stripe rects and dim overlay plates before rasterize."""
    for cid in container_ids if container_ids is not None else _BACKGROUND_CONTAINERS:
        container = _find_by_logical_id(root, cid)
        if container is None:
            continue
        parents = _parent_map(root)
        for el in list(container.iter()):
            if not el.tag.endswith("rect"):
                continue
            style = el.get("style") or ""
            transform = el.get("transform") or _style_prop(style, "transform")
            if _parse_svg_slant_transform(transform) is not None:
                parent = parents.get(el)
                if parent is not None:
                    parent.remove(el)
                continue
            fill = _style_prop(style, "fill") or el.get("fill") or ""
            opacity = _style_prop(style, "opacity") or ""
            if _norm_hex(fill) == "#202020" or opacity.startswith("0.5"):
                parent = parents.get(el)
                if parent is not None:
                    parent.remove(el)


def _remove_canvas_background_rect(root: ET.Element) -> None:
    """Drop the full-canvas rect so PyMuPDF leaves transparency for compositing."""
    bg_group = _find_by_logical_id(root, "background")
    if bg_group is None:
        return
    parents = _parent_map(root)
    for el in list(bg_group.iter()):
        if el.tag.endswith("rect"):
            parent = parents.get(el)
            if parent is not None:
                parent.remove(el)


def _is_box_chrome_logical(logical: str) -> bool:
    if logical in _BOX_CONTAINER_LOGICALS:
        return True
    return any(
        logical == prefix or logical.startswith(f"{prefix}_")
        for prefix in _BOX_CHROME_PREFIXES
    )


def _is_device_label_logical(logical: str) -> bool:
    """Primary device name / IP labels inside a box column."""
    if not logical:
        return False
    if logical.endswith("_device_name_text") or logical.endswith("_device_ip_text"):
        return True
    return logical in ("main_device_name_text", "main_device_ip_text")


def _find_box_device_group(root: ET.Element, box_num: int) -> ET.Element | None:
    want = _BOX_DEVICE_GROUPS.get(box_num, "")
    if not want:
        return None
    hit = _find_by_logical_id(root, want)
    if hit is not None:
        return hit
    prefix = f"main_box{box_num}_device_group"
    for el in root.iter():
        logical = _normalize_logical(el.get("id") or "")
        if logical == prefix or logical.startswith(f"{prefix}_"):
            return el
    return None


def _apply_box1_host_device_labels(root: ET.Element) -> None:
    """Show this Pigeon host name + LAN IP in box1 device chrome."""
    from pigeon.local_ip import local_ipv4_address

    ip = local_ipv4_address() or ""
    _apply_box_device_group_layout(root, 1, name="pigeon", ip=ip)


def _apply_box_device_group_labels(
    root: ET.Element,
    state: MainSettingsState,
    box_num: int,
) -> None:
    """Populate box2/box3 device_group name + IP after the user picks a LAN device."""
    if box_num not in (2, 3):
        return
    panel = state._box_panel(box_num)
    picked = state.saved_box_device(box_num)
    if picked is None:
        return
    if panel.picked is None:
        panel.picked = picked
    name, ip = picked
    ip_invalid = state.box2_ip_invalid if box_num == 2 else state.box3_ip_invalid
    _apply_box_device_group_layout(root, box_num, name=name, ip=ip)
    group = _find_box_device_group(root, box_num)
    if group is None:
        return
    _apply_box_pigeon_logo_icon_styles(root, box_num, theme=state.theme)
    if ip_invalid:
        for el in group.iter():
            logical = _normalize_logical(el.get("id") or "")
            if logical.endswith("_device_ip_text"):
                for node in el.iter():
                    if node.tag.endswith("text"):
                        _set_paint(node, fill=COLOR_UI_DEFAULT)


def _apply_box_pigeon_logo_icon_styles(
    root: ET.Element,
    box_num: int,
    *,
    theme: SettingsTheme,
) -> None:
    """Paint hex-clip ring strokes / speaker polygons with ``theme.ui``."""
    icon = _find_by_logical_id(root, f"main_box{box_num}_pigeon_logo_icon")
    if icon is None:
        return
    ui = theme.ui or COLOR_UI_DEFAULT
    edge = theme.deselected or COLOR_DESELECTED
    for el in icon.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "circle":
            _set_paint(el, fill="none", stroke=ui)
        elif tag == "line":
            _set_paint(el, fill="none", stroke=ui)
        elif tag == "polygon":
            _set_paint(el, fill=ui, stroke=edge)


def _apply_box_device_text_contrast(
    root: ET.Element,
    *,
    box_num: int,
    selected: bool,
    theme: SettingsTheme,
) -> None:
    """Selected box pill is white → black text; deselected dark → white text."""
    contrast = theme.deselected if selected else theme.selected
    group = _find_box_device_group(root, box_num)
    if group is None:
        return
    for el in group.iter():
        logical = _normalize_logical(el.get("id") or "")
        if not _is_device_label_logical(logical):
            continue
        for node in el.iter():
            if node.tag.endswith("text"):
                _set_paint(node, fill=contrast)


def _box_shows_location_group(
    state: MainSettingsState,
    box_num: int,
    panel: BoxDevicePanelState,
) -> bool:
    if box_num not in (2, 3):
        return False
    if panel.results_locked or (panel.active and panel.phase == "scanning"):
        return False
    if _needs_wifi_setup(state) or state.manual_device_entry is not None:
        return False
    if state.box_has_saved_device(box_num) and not panel.active:
        return False
    if box_num == 2:
        panel_open = bool(state.show_box2_panel or state.box2_devices.active)
    else:
        panel_open = bool(state.show_box3_panel or state.box3_devices.active)
    if panel.active:
        return False
    return panel_open


def _box_location_text_bounds_svg(box_num: int) -> tuple[float, float]:
    return _box_column_inner_svg(box_num)


def _box_location_label_for_slot(state: MainSettingsState, box_num: int) -> str:
    """Label shown inside a box location group (picker slots or current nest)."""
    default = _LOCATION_SLOT_DEFAULT_NAMES[max(0, min(2, int(box_num) - 1))]
    if state.show_location_picker:
        slot = state.location_slot(box_num)
        if slot is not None:
            return str(slot[1] or "").strip() or default
        return default
    return str(state.location_name or default).strip() or default


def _apply_box_location_group_labels(
    root: ET.Element,
    state: MainSettingsState,
    box_num: int,
) -> None:
    """Center and truncate nest/location label inside box1–3 column chrome."""
    if box_num not in (1, 2, 3):
        return
    group = _find_by_logical_id(root, f"main_box{box_num}_location_group")
    if group is None:
        return
    label = _box_location_label_for_slot(state, box_num)
    x0, x1 = _box_location_text_bounds_svg(box_num)
    center_svg = (x0 + x1) * 0.5
    y_svg = _BOX_LOCATION_TEXT_Y_SVG.get(box_num, 325.7)
    max_w = max(24, int(round((x1 - x0 - 8.0) * DESIGN_W / 800.0)))
    font_size_px = _field_font_size_px(_BOX_LOCATION_TEXT_FONT_SVG)
    font = _field_font({"font": "digital7"}, font_size_px)
    display = _truncate_text_to_width(label, max_width_px=max_w, font=font)
    for el in group.iter():
        logical = _normalize_logical(el.get("id") or "")
        if not (logical.endswith("_loation1_text") or logical.endswith("_location1_text")):
            continue
        _set_text_content(el, display)
        for node in el.iter():
            if node.tag.endswith("text"):
                _set_svg_text_horiz_centered(node, center_svg, y_svg)


def _apply_box_location_text_contrast(
    root: ET.Element,
    *,
    box_num: int,
    selected: bool,
    theme: SettingsTheme,
) -> None:
    group = _find_by_logical_id(root, f"main_box{box_num}_location_group")
    if group is None:
        return
    for el in group.iter():
        logical = _normalize_logical(el.get("id") or "")
        if el.tag.endswith("text") or logical.endswith("_loation1_text") or logical.endswith(
            "_location1_text"
        ):
            _apply_contrast_paint(el, selected=selected, theme=theme)


def _box_num_from_chrome_logical(logical: str) -> int | None:
    m = _BOX_CHROME_NUM_RE.match(logical)
    return int(m.group(1)) if m else None


def _location_display_text(state: MainSettingsState) -> str:
    """Dual-bar location label; shows pending/selected SSID while entering network password."""
    kb = state.keyboard
    if state.keyboard_open and kb is not None:
        target = str(getattr(kb, "target", "") or "")
        if target == "network":
            ssid = str(state.pending_wifi_ssid or state.selected_wifi_ssid or "").strip()
            if ssid:
                return ssid
    return state.location_name


def _force_layer_white(el: ET.Element) -> None:
    tag = el.tag.rsplit("}", 1)[-1]
    if tag not in ("path", "rect", "polygon", "circle", "ellipse", "text"):
        return
    fill, stroke = _iter_style_fill_stroke(el)
    if fill and fill not in ("none", "transparent"):
        _set_paint(el, fill="#ffffff")
    if stroke and stroke not in ("none", "transparent"):
        _set_paint(el, stroke="#ffffff")


def _apply_wifi_search_glyph_layers(search_group: ET.Element) -> None:
    """WiFi search art: ellipse paths are stroke-only; triangles are fill-only."""
    for el in search_group.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        fill, stroke = _iter_style_fill_stroke(el)
        if tag == "polygon":
            _set_paint(el, fill=fill or "#ffffff", stroke="none")
        elif tag in ("circle", "path"):
            _set_paint(el, fill="none", stroke=stroke or "#ffffff")


def _hide_wifi_search_glyphs(root: ET.Element) -> None:
    """Hide only the onboarding search rings (``+`` stays static while scanning)."""
    search_group = _wifi_onboarding_search_group(root)
    if search_group is not None:
        _set_visible(search_group, False)


def _apply_wifi_search_icon_styles(
    root: ET.Element,
    *,
    focused: str,
    theme: SettingsTheme,
) -> None:
    """Onboarding search rings stay stroke-only; ``+`` follows selection contrast."""
    add_group = _find_by_logical_id(root, "main_box2_add_search_icon")
    if add_group is None:
        return
    selected = focused == "main_box2_add_search_icon"
    search_group = _wifi_onboarding_search_group(root)
    if search_group is not None:
        _apply_wifi_search_glyph_layers(search_group)
    _center_wifi_plus_icon(root)
    plus_group = _find_by_logical_id(root, "main_box2_+_icon")
    if plus_group is not None:
        for el in plus_group.iter():
            logical = _normalize_logical(el.get("id") or "")
            if logical.startswith("main_box2_+_icon") or el is plus_group:
                _apply_direct_glyph_contrast(el, selected=selected, theme=theme)


def _apply_picker_network_labels(root: ET.Element, state: MainSettingsState) -> None:
    if not state.show_network_picker:
        return
    names = state.wifi_networks
    scroll = max(0, int(state.network_picker_scroll))
    for i, text_id in enumerate(_PICKER_ROW_TEXTS):
        idx = scroll + i
        label = names[idx] if idx < len(names) else ""
        # Digital-7 has limited lowercase coverage — uppercase keeps SSIDs visible on Pi.
        display = str(label).upper() if label else ""
        for text_el in _find_all_by_logical_id(root, text_id):
            _set_text_content(text_el, display)
            targets = [text_el] if text_el.tag.endswith("text") else [
                n for n in text_el.iter() if n.tag.endswith("text")
            ]
            for target in targets:
                pos = _parse_matrix_translate(target.get("transform"))
                y_svg = pos[1] if pos is not None else 270.3023 + i * 48.0
                _set_svg_text_horiz_centered(target, _PICKER_TEXT_CENTER_X_SVG, y_svg)


def _hide_wifi_onboarding_chrome_for_picker(root: ET.Element) -> None:
    """Hide search/label layers once the network picker replaces onboarding art."""
    for lid in (
        "rename_your_nest_text",
        "main_box2_add_search_icon",
        "main_box1_text",
        "welcome_to_pigeon_find_your_wifi_text",
    ):
        _set_visible(_find_by_logical_id(root, lid), False)


def _hide_all_box_chrome(root: ET.Element) -> None:
    """Hide panel chrome (search results, search icons, etc.) for all three columns."""
    parents = _parent_map(root)
    add_group = _find_by_logical_id(root, "main_box2_add_search_icon")
    for el in root.iter():
        if not el.tag.endswith("g"):
            continue
        logical = _normalize_logical(el.get("id") or "")
        if logical in _BOX_CONTAINER_LOGICALS:
            continue
        if add_group is not None and _is_descendant_of(el, add_group, parents):
            continue
        if _is_box_chrome_logical(logical):
            _set_visible(el, False)
    for gid in _BOX_LOCATION_GROUPS:
        _set_visible(_find_by_logical_id(root, gid), False)


def _network_field_text(state: MainSettingsState) -> str | None:
    """Dual-network label text, or ``None`` to preserve SVG placeholder art."""
    if state.show_location_picker and not _needs_wifi_setup(state):
        return "RENAME"
    if state.wifi_configured:
        return "CONNECTED"
    if state.wifi_connecting or state.keyboard_open:
        return None
    return "CONNECT TO WIFI"


def _network_field_font_size_svg(display: str) -> float | None:
    """Disconnected label uses the same size as the location field."""
    return None


def _dual_bar_pill_center_y_px() -> int:
    baseline = float(_TEXT_ENTRY_FIELDS["location"]["baseline_y_svg"])
    return int(round(baseline * DESIGN_H / _ARTBOARD_H))


def _draw_dual_bar_network_prompt(bgra: np.ndarray, state: MainSettingsState) -> None:
    """Paint the disconnected dual-network CTA with Digital-7 sizing and vertical centering."""
    if (
        state.wifi_configured
        or state.wifi_connecting
        or state.keyboard_open
        or state.show_network_picker
    ):
        return
    label = _network_field_text(state)
    if label != "CONNECT TO WIFI":
        return
    net_x0 = int(round(_DUAL_NETWORK_TEXT_X0_SVG * DESIGN_W / 800.0))
    net_x1 = int(round(_DUAL_NETWORK_TEXT_X1_SVG * DESIGN_W / 800.0))
    loc_spec = _TEXT_ENTRY_FIELDS["location"]
    display = label.upper()
    start_px = _field_font_size_px(float(loc_spec["font_size_svg"]))
    size_px, font = _fit_text_font_size(
        display,
        lambda s: _field_font(loc_spec, s),
        max_width_px=max(24, net_x1 - net_x0 - 16),
        start_px=start_px,
    )
    text_y = _dual_bar_pill_center_y_px()
    rgb = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGBA)
    img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(img)
    _draw_centered_field_text(
        draw,
        x0_px=net_x0,
        x1_px=net_x1,
        baseline_px=text_y,
        text=display,
        font=font,
        fill=_COLOR_GRAY_RGBA,
    )
    bgra[:] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)


def _apply_wifi_onboarding_visibility(root: ET.Element, state: MainSettingsState) -> None:
    """Hide box columns until WiFi is configured; show instructions search chrome."""
    no_wifi = _needs_wifi_setup(state)

    if no_wifi:
        _set_box_columns_visible(root, False)
        _set_visible(_find_by_logical_id(root, "main_instructions"), False)
        _set_visible(_find_by_logical_id(root, "rename_your_nest_text"), False)
        add_visible = False
        _set_visible(
            _find_by_logical_id(root, "main_box2_add_search_icon"),
            state.wifi_scanning or state.wifi_connecting,
        )
        _hide_all_box_chrome(root)
    else:
        _set_visible(_find_by_logical_id(root, "welcome_to_pigeon_group"), False)
        _set_visible(_find_by_logical_id(root, "welcome_to_pigeon_find_your_wifi_text"), False)
        _set_visible(_find_by_logical_id(root, "main_box2_add_search_icon"), False)
        _set_visible(_find_by_logical_id(root, "main_box1_text"), False)
        show_rename_hint = bool(state.show_instructions or state.show_location_picker)
        nest_el = _find_by_logical_id(root, "rename_your_nest_text")
        if nest_el is not None and state.show_location_picker:
            _set_text_content(nest_el, "select your nest")
            _force_layer_white(nest_el)
        _set_visible(nest_el, show_rename_hint)
        _set_box_columns_visible(root, True)


def _finalize_wifi_onboarding_layers(root: ET.Element, state: MainSettingsState) -> None:
    """Re-apply onboarding chrome after ``main_instructions`` visibility is toggled."""
    if not _needs_wifi_setup(state) or state.show_network_picker:
        return
    _set_visible(_find_by_logical_id(root, "rename_your_nest_text"), False)
    _set_visible(
        _find_by_logical_id(root, "main_box1_text"),
        not (state.wifi_scanning or state.wifi_connecting),
    )
    _apply_wifi_onboarding_search_layers(root, state)


def _apply_scene_layer_visibility(root: ET.Element, state: MainSettingsState) -> None:
    """
    Default view shows only launch layers (exit, dual bar, box device groups, background).

    Location groups, instructions, network picker, and box panel chrome open via state flags.
    """
    focused = "" if state.keyboard_open else state.focused_id
    _apply_wifi_onboarding_visibility(root, state)
    no_wifi = _needs_wifi_setup(state)

    show_search_chrome = no_wifi and (state.wifi_scanning or state.wifi_connecting)
    _set_visible(_find_by_logical_id(root, "main_instructions"), show_search_chrome)
    _set_visible(_find_by_logical_id(root, "welcome_to_pigeon_find_your_wifi_text"), False)
    if not no_wifi:
        _set_visible(_find_by_logical_id(root, "welcome_to_pigeon_group"), False)
        _set_visible(_find_by_logical_id(root, "main_box2_add_search_icon"), False)
    _set_visible(_find_by_logical_id(root, "main_network_picker"), state.show_network_picker)
    _finalize_wifi_onboarding_layers(root, state)

    if state.show_network_picker:
        _hide_wifi_onboarding_chrome_for_picker(root)

    location_picker = bool(state.show_location_picker) and not no_wifi
    # Panel chrome (search / results) — not used for the dual-location rename picker.
    panel_open = {
        1: False if no_wifi else bool(state.show_box1_panel),
        2: False
        if no_wifi or state.manual_device_entry is not None
        else bool(state.show_box2_panel or state.box2_devices.active),
        3: False
        if no_wifi or state.manual_device_entry is not None
        else bool(state.show_box3_panel or state.box3_devices.active),
    }
    for i, gid in enumerate(_BOX_LOCATION_GROUPS, start=1):
        if location_picker:
            show_loc = True
        else:
            show_loc = panel_open.get(i, False)
            if i in (2, 3):
                panel = state._box_panel(i)
                if panel.active:
                    show_loc = False
        _set_visible(_find_by_logical_id(root, gid), show_loc)

    parents = _parent_map(root)
    add_group = _find_by_logical_id(root, "main_box2_add_search_icon")
    # Walk indexed chrome ids only — avoid normalizing every <g> in the 3.7k-node tree.
    for logical, els in _logical_id_index(root).items():
        if logical in _BOX_CONTAINER_LOGICALS:
            show = not no_wifi and state.manual_device_entry is None
            for el in els:
                _set_visible(el, show)
            continue
        if not _is_box_chrome_logical(logical):
            continue
        box_num = _box_num_from_chrome_logical(logical)
        show = False if location_picker else bool(box_num and panel_open.get(box_num, False))
        for el in els:
            if add_group is not None and _is_descendant_of(el, add_group, parents):
                continue
            _set_visible(el, show)

    if location_picker:
        # Match Illustrator dual-location visibility: location + container on; device/search off.
        for box_num in (1, 2, 3):
            _set_visible(_find_by_logical_id(root, _BOX_DEVICE_GROUPS[box_num]), False)
            _set_visible(_find_by_logical_id(root, f"main_box{box_num}_search_icon"), False)
            _set_box_search_results_visible(root, box_num, False)
            _hide_box_search_animation_layers(root, box_num)
            _set_visible(_find_by_logical_id(root, _BOX_LOCATION_GROUPS[box_num - 1]), True)
            _set_visible(
                _find_by_logical_id(root, f"main_box{box_num}_container"),
                True,
            )
    else:
        for box_num in (2, 3):
            _apply_box_device_panel_layers(
                root, state, box_num, theme=state.theme, focused=focused
            )
        # Box1 has no LAN device search — keep its search spinner art permanently off.
        _hide_box_search_animation_layers(root, 1)
        # Restore host device chrome after leaving the location rename picker.
        if not no_wifi and state.manual_device_entry is None:
            _set_visible(_find_by_logical_id(root, "main_box1_device_group"), True)


def _parse_svg_text_y_svg(el: ET.Element) -> float | None:
    """Read the ``y`` translate from a text element's ``transform`` matrix."""
    targets = [el] if el.tag.endswith("text") else [n for n in el.iter() if n.tag.endswith("text")]
    for target in targets:
        transform = target.get("transform") or ""
        m = _MATRIX_RE.search(transform)
        if m:
            return float(m.group(6))
    return None


def _is_box_results_device_logical(logical: str, box_num: int) -> bool:
    prefix = f"main_box{box_num}_device"
    if not logical.startswith(prefix) or "text" not in logical:
        return False
    if any(
        token in logical
        for token in ("device_group", "device_name", "device_ip", "search_results_device")
    ):
        return False
    return True


def _find_box_result_device_layer(
    root: ET.Element,
    box_num: int,
    row: int,
) -> ET.Element | None:
    """Locate the device-name text layer for a results row (1-based)."""
    if row < 1 or row > len(_BOX_RESULT_ROW_Y_SVG):
        return None
    target_y = _BOX_RESULT_ROW_Y_SVG[row - 1]
    best: ET.Element | None = None
    best_dy = 999.0
    for el in root.iter():
        logical = _normalize_logical(el.get("id") or "")
        if not _is_box_results_device_logical(logical, box_num):
            continue
        y = _parse_svg_text_y_svg(el)
        if y is None:
            continue
        dy = abs(y - target_y)
        if dy < best_dy:
            best_dy = dy
            best = el
    return best if best_dy <= 2.5 else None


def _discover_box_search_triangle_specs(
    root: ET.Element,
    box_num: int,
) -> tuple[tuple[tuple[float, float], ...], ...]:
    search_icon = _find_box_column_search_icon(root, box_num)
    if search_icon is None:
        return ()
    specs: list[tuple[tuple[float, float], ...]] = []
    for el in search_icon.iter():
        if not el.tag.endswith("polygon"):
            continue
        logical = _normalize_logical(el.get("id") or "")
        if "triangle" not in logical:
            continue
        pts = _parse_svg_points(el.get("points") or "")
        if pts:
            specs.append(pts)
    return tuple(specs)


def _draw_star_spec_on_patch(
    patch: np.ndarray,
    spec: _StarMaskedCircleSpec,
    *,
    origin_x0: int,
    origin_y0: int,
    color_bgr: tuple[int, int, int],
) -> None:
    cx, cy = _svg_to_px(spec.cx_svg, spec.cy_svg)
    cx -= origin_x0
    cy -= origin_y0
    radius = _svg_radius_to_px(spec.radius_svg)
    stroke = max(1, int(round(_svg_scale(spec.stroke_svg))))
    clip_pts = np.array(
        [(_svg_to_px(x, y)[0] - origin_x0, _svg_to_px(x, y)[1] - origin_y0) for x, y in spec.mask_polygon_svg],
        dtype=np.int32,
    )
    clip_mask = np.zeros(patch.shape[:2], dtype=np.uint8)
    cv2.fillPoly(clip_mask, [clip_pts], 255)
    ring_mask = np.zeros(patch.shape[:2], dtype=np.uint8)
    cv2.circle(ring_mask, (cx, cy), radius, 255, stroke, lineType=cv2.LINE_AA)
    ring_mask = cv2.bitwise_and(ring_mask, clip_mask)
    _composite_stroke_mask(patch, ring_mask, color_bgr)


def _draw_triangle_specs_on_patch(
    patch: np.ndarray,
    specs: tuple[tuple[tuple[float, float], ...], ...],
    *,
    origin_x0: int,
    origin_y0: int,
    color_bgr: tuple[int, int, int],
) -> None:
    for pts in specs:
        px_pts = np.array(
            [(_svg_to_px(x, y)[0] - origin_x0, _svg_to_px(x, y)[1] - origin_y0) for x, y in pts],
            dtype=np.int32,
        )
        mask = np.zeros(patch.shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, [px_pts], 255)
        _composite_stroke_mask(patch, mask, color_bgr)


def _box_search_star_specs(
    specs: list[_StarMaskedCircleSpec],
    box_num: int,
) -> list[_StarMaskedCircleSpec]:
    want = f"main_box{box_num}_button"
    return [spec for spec in specs if spec.focus_button == want]


def _find_box_result_layer(
    root: ET.Element,
    box_num: int,
    kind: str,
    row: int,
) -> ET.Element | None:
    """Find device/ip text or mini button for a results row (1-based)."""
    if kind == "device":
        return _find_box_result_device_layer(root, box_num, row)
    stem = f"main_box{box_num}_{kind}{row}"
    if kind == "mini":
        stem = f"main_box{box_num}_mini{row}_button"
    for el in root.iter():
        logical = _normalize_logical(el.get("id") or "")
        if logical == stem or logical.startswith(f"{stem}_"):
            return el
    return None


def _hide_vertical_scroll_arrows(root: ET.Element) -> None:
    """Up/down scroll glyphs are unused — main settings navigates left/right only."""
    for lid in ("main_network_picker_up_icon", "main_network_picker_down_icon"):
        _set_visible(_find_by_logical_id(root, lid), False)
    for box_num in (2, 3):
        for direction in ("up", "down"):
            _set_visible(_find_box_scroll_arrow(root, box_num, direction), False)


def _set_box_search_results_visible(root: ET.Element, box_num: int, visible: bool) -> None:
    """Toggle the search-results subtree (rows + mini buttons; no scroll arrows)."""
    prefixes = (f"main_box{box_num}_search_results",)
    for el in root.iter():
        logical = _normalize_logical(el.get("id") or "")
        if any(logical.startswith(prefix) for prefix in prefixes):
            _set_visible(el, visible)
    for direction in ("up", "down"):
        _set_visible(_find_box_scroll_arrow(root, box_num, direction), False)


def _apply_box_search_icon_glyph_styles(
    search_icon: ET.Element | None,
    *,
    color: str = "#202020",
) -> None:
    """Ellipses: stroke only. Arrow triangles and ``+``: fill only."""
    if search_icon is None:
        return
    for el in search_icon.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        logical = _normalize_logical(el.get("id") or "")
        if tag in ("circle", "path"):
            _set_paint(el, fill="none", stroke=color)
        elif tag == "polygon" and "triangle" in logical:
            _set_paint(el, fill=color, stroke="none")
        elif tag == "text":
            _set_paint(el, fill=color, stroke="none")


def _set_box_search_plus_visible(search_icon: ET.Element | None, visible: bool) -> None:
    if search_icon is None:
        return
    for el in search_icon.iter():
        if el.tag.endswith("text"):
            _set_visible(el, visible)


def _show_box_search_icon_vectors(search_icon: ET.Element | None) -> None:
    """Show filled triangles/plus and stroke arcs; hide leftover clip-path circles."""
    if search_icon is None:
        return
    for el in search_icon.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("polygon", "path", "text"):
            _set_visible(el, True)
        elif tag == "circle":
            _set_visible(el, False)


def _hide_box_search_icon_vectors(search_icon: ET.Element | None) -> None:
    """Hide raw SVG vectors; spinner/search art is drawn as overlays."""
    if search_icon is None:
        return
    for el in search_icon.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("circle", "polygon", "path"):
            _set_visible(el, False)


def _apply_box_search_icon_variant(
    search_icon: ET.Element | None,
    *,
    selected: bool,
    show_plus: bool = True,
) -> None:
    """Paint box column add/search art (black glyphs on white pill, white on black).

    Plus and triangles are fills; ellipse arcs are strokes. Keep groups present
    and only recolor. Leftover clip-path circles stay hidden.
    """
    if search_icon is None:
        return
    color = "#202020" if selected else "#ffffff"
    _apply_box_search_icon_glyph_styles(search_icon, color=color)
    for el in search_icon.iter():
        if el is search_icon:
            continue
        logical = _normalize_logical(el.get("id") or "")
        tag = el.tag.rsplit("}", 1)[-1]
        if "ellipse" in logical or "eplipse" in logical or tag == "path":
            _set_visible(el, True)
        if tag == "circle":
            _set_visible(el, False)
    _set_box_search_plus_visible(search_icon, bool(show_plus))


def _apply_box_result_text_style(
    el: ET.Element | None,
    *,
    selected: bool,
    theme: SettingsTheme,
) -> None:
    """Selected row: black on white mini; unselected: white on dark column."""
    if el is None:
        return
    fill = theme.deselected if selected else theme.selected
    targets = [el] if el.tag.endswith("text") else [n for n in el.iter() if n.tag.endswith("text")]
    for hit in targets:
        _set_paint(hit, fill=fill)


def _find_box_scroll_arrow(root: ET.Element, box_num: int, direction: str) -> ET.Element | None:
    stem = f"main_box{box_num}_{direction}_icon"
    for el in root.iter():
        logical = _normalize_logical(el.get("id") or "")
        if logical == stem or logical.startswith(f"{stem}_"):
            return el
    return None


def _apply_scroll_arrow_glyph(
    el: ET.Element | None,
    *,
    selected: bool,
    disabled: bool,
    theme: SettingsTheme,
) -> None:
    _apply_direct_glyph_contrast(el, selected=selected, disabled=disabled, theme=theme)


def _apply_network_picker_arrows(
    root: ET.Element,
    state: MainSettingsState,
    *,
    theme: SettingsTheme,
) -> None:
    _hide_vertical_scroll_arrows(root)


def _box_nav_button_selected(
    state: MainSettingsState,
    box_num: int,
    *,
    focused: str,
) -> bool:
    logical = f"main_box{box_num}_button"
    locked = state.box_device_results_locked()
    if locked is not None:
        return False
    if state.manual_device_entry is not None:
        return False
    if box_num in (2, 3):
        panel = state._box_panel(box_num)
        if panel.scanning and panel.active:
            return True
    if state.keyboard_open:
        return False
    if focused == logical:
        return True
    # While picking nests, also highlight the currently active location.
    if state.show_location_picker:
        try:
            from pigeon.app_state import read_current_location_id

            cur = str(read_current_location_id() or "").strip()
        except Exception:
            cur = ""
        if cur:
            slot = state.location_slot(box_num)
            if slot is not None and str(slot[0]) == cur:
                return True
    return False


def _apply_box_column_nav_fills(
    root: ET.Element,
    state: MainSettingsState,
    *,
    focused: str,
    theme: SettingsTheme,
) -> None:
    """Always paint box1–3 nav pills (virtual results focus skips the main ring loop)."""
    scanning_box: int | None = None
    for box_num in (2, 3):
        panel = state._box_panel(box_num)
        if panel.scanning and panel.active:
            scanning_box = box_num
            break
    for box_num in (1, 2, 3):
        logical = f"main_box{box_num}_button"
        if scanning_box is not None:
            selected = box_num == scanning_box
        else:
            selected = _box_nav_button_selected(state, box_num, focused=focused)
        button_el = _find_by_logical_id(root, logical)
        _apply_button_fill(button_el, selected=selected, theme=theme)
        for hit in _find_all_by_logical_id(root, logical):
            _apply_button_fill(hit, selected=selected, theme=theme)
        _apply_box_device_text_contrast(
            root, box_num=box_num, selected=selected, theme=theme
        )
        _apply_box_location_text_contrast(
            root, box_num=box_num, selected=selected, theme=theme
        )


def _apply_dual_bar_nav_fills(
    root: ET.Element,
    state: MainSettingsState,
    *,
    focused: str,
    theme: SettingsTheme,
) -> None:
    """Dual-bar pills stay deselected while browsing box device search results."""
    results_locked = state.box_device_results_locked() is not None
    for logical in ("main_dual_location_button", "main_dual_network_button"):
        if results_locked or state.show_network_picker or state.keyboard_open:
            selected = False
        else:
            selected = focused == logical
        button_el = _find_by_logical_id(root, logical)
        _apply_button_fill(button_el, selected=selected, theme=theme)
        for hit in _find_all_by_logical_id(root, logical):
            _apply_button_fill(hit, selected=selected, theme=theme)
        for assoc in _FOCUS_ASSOCIATED.get(logical, ()):
            for assoc_el in _find_all_by_logical_id(root, assoc):
                cls = _layer_class(_normalize_logical(assoc_el.get("id") or assoc))
                if cls == "_accent":
                    continue
                _apply_contrast_paint(assoc_el, selected=selected, theme=theme)


def _apply_box_device_panel_layers(
    root: ET.Element,
    state: MainSettingsState,
    box_num: int,
    *,
    theme: SettingsTheme,
    focused: str = "",
) -> None:
    panel = state._box_panel(box_num)
    device_group = _find_box_device_group(root, box_num)
    location_group = _find_by_logical_id(root, f"main_box{box_num}_location_group")
    search_icon = _find_box_column_search_icon(root, box_num)
    has_device = state.box_has_saved_device(box_num)
    capture = bool(getattr(state, "spinner_glyph_capture", False))

    if panel.results_locked:
        _set_visible(device_group, False)
        _set_visible(location_group, False)
        _hide_box_search_animation_layers(root, box_num)
        _set_box_search_results_visible(root, box_num, True)

        names = panel.devices
        scroll = max(0, int(panel.scroll))
        row_idx = max(0, min(_BOX_DEVICE_ROW_COUNT - 1, int(panel.row)))

        for i in range(1, _BOX_DEVICE_ROW_COUNT + 1):
            for kind in ("device", "ip", "mini"):
                layer = _find_box_result_layer(root, box_num, kind, i)
                if layer is not None:
                    _set_visible(layer, False)

        for i in range(1, _BOX_DEVICE_ROW_COUNT + 1):
            idx = scroll + (i - 1)
            has_data = 0 <= idx < len(names)
            name, ip = names[idx] if has_data else ("", "")
            show_ip = has_data and name not in (BOX_DEVICE_ROW_ENTER_IP, BOX_DEVICE_ROW_CANCEL)
            selected_row = has_data and (i - 1) == row_idx

            dev_el = _find_box_result_device_layer(root, box_num, i)
            ip_el = _find_box_result_layer(root, box_num, "ip", i)
            mini_el = _find_box_result_layer(root, box_num, "mini", i)

            if dev_el is not None:
                _set_visible(dev_el, has_data)
                if has_data:
                    display_name = name.upper() if name else ""
                    _apply_box_result_row_text_layout(
                        dev_el, ip_el if show_ip else None, name=display_name, ip=ip, row=i, box_num=box_num
                    )
                    _apply_box_result_text_style(dev_el, selected=selected_row, theme=theme)
            if ip_el is not None:
                _set_visible(ip_el, show_ip)
                if show_ip:
                    if dev_el is None:
                        _apply_box_result_row_text_layout(
                            None, ip_el, name=name, ip=ip, row=i, box_num=box_num
                        )
                    _apply_box_result_text_style(ip_el, selected=selected_row, theme=theme)
            if mini_el is not None:
                _set_visible(mini_el, selected_row)
                if selected_row:
                    _apply_button_fill(mini_el, selected=True, theme=theme)
                    for hit in _find_all_by_logical_id(root, mini_el.get("id") or ""):
                        _apply_button_fill(hit, selected=True, theme=theme)

        for direction in ("up", "down"):
            _set_visible(_find_box_scroll_arrow(root, box_num, direction), False)
        return
    elif panel.active and panel.phase == "scanning":
        _set_visible(device_group, False)
        _set_visible(location_group, False)
        _set_box_search_results_visible(root, box_num, False)
        if search_icon is not None:
            _set_visible(search_icon, capture)
            if capture:
                _apply_box_search_icon_variant(search_icon, selected=True, show_plus=False)
            _hide_box_search_icon_vectors(search_icon)
        else:
            _hide_box_search_animation_layers(root, box_num)
        return
    elif has_device:
        show_loc = _box_shows_location_group(state, box_num, panel)
        _set_visible(device_group, True)
        _set_visible(location_group, show_loc)
        _hide_box_search_animation_layers(root, box_num)
        _set_box_search_results_visible(root, box_num, False)
        return
    else:
        show_loc = _box_shows_location_group(state, box_num, panel)
        _set_visible(device_group, False)
        _set_visible(location_group, show_loc)
        _set_box_search_results_visible(root, box_num, False)
        if search_icon is not None:
            _set_visible(search_icon, True)
            icon_selected = focused == f"main_box{box_num}_button" or (
                panel.scanning and panel.active
            )
            _apply_box_search_icon_variant(
                search_icon,
                selected=icon_selected,
                show_plus=not (panel.scanning and panel.active),
            )
            if panel.scanning and panel.active:
                _hide_box_search_icon_vectors(search_icon)
            else:
                _show_box_search_icon_vectors(search_icon)
        return


def _box_button_fill_selected(state: MainSettingsState, box_num: int, focused: str) -> bool:
    return focused == f"main_box{box_num}_button"


def _apply_network_picker_rows(
    root: ET.Element,
    state: MainSettingsState,
    *,
    theme: SettingsTheme,
) -> None:
    """Per-row white/black pills inside the picker (outer container stays dark)."""
    if not state.show_network_picker:
        return
    names = state.wifi_networks
    scroll = max(0, int(state.network_picker_scroll))
    row_idx = max(0, min(len(_PICKER_ROW_MINI_BUTTONS) - 1, int(state.network_picker_row)))
    for i, mini_id in enumerate(_PICKER_ROW_MINI_BUTTONS):
        has_network = (scroll + i) < len(names)
        selected = has_network and i == row_idx
        mini_el = _find_by_logical_id(root, mini_id)
        _set_visible(mini_el, has_network)
        if has_network:
            _apply_button_fill(mini_el, selected=selected, theme=theme)
            for hit in _find_all_by_logical_id(root, mini_id):
                _apply_button_fill(hit, selected=selected, theme=theme)

    for i, text_id in enumerate(_PICKER_ROW_TEXTS):
        has_network = (scroll + i) < len(names)
        selected = has_network and i == row_idx
        for text_el in _find_all_by_logical_id(root, text_id):
            _set_visible(text_el, has_network)
            if has_network:
                _apply_contrast_paint(text_el, selected=selected, theme=theme)

    for i, lock_id in enumerate(_PICKER_ROW_LOCK_GROUPS):
        has_network = (scroll + i) < len(names)
        selected = has_network and i == row_idx
        for lock_el in _find_all_by_logical_id(root, lock_id):
            _set_visible(lock_el, has_network)
            if has_network:
                _apply_contrast_paint(lock_el, selected=selected, theme=theme)


def discover_focus_ring_in_svg(root: ET.Element, state: MainSettingsState) -> tuple[str, ...]:
    """Build a focus ring from candidates that exist in the SVG tree."""
    state.ensure_focus_ring()
    present: list[str] = []
    for logical in state.focus_ring:
        if logical.endswith("_device_results"):
            present.append(logical)
            continue
        if _find_by_logical_id(root, logical) is not None:
            present.append(logical)
    if not present:
        # Absolute fallback.
        for logical in ("main_exit_button", *_PRIMARY_FOCUS_CANDIDATES):
            if _find_by_logical_id(root, logical) is not None and logical not in present:
                present.append(logical)
    return tuple(present) if present else ("main_exit_button",)


def apply_main_settings_svg_state(root: ET.Element, state: MainSettingsState) -> None:
    """Mutate SVG tree: selection fills, contrast text/icons, accents, visibility."""
    theme = state.theme
    state.ensure_focus_ring()

    for hid in _HIDE_ALWAYS_LOGICAL:
        _remove_by_logical_id(root, hid)

    picker = _find_by_logical_id(root, "main_network_picker")
    _set_visible(picker, bool(state.show_network_picker))
    _apply_scene_layer_visibility(root, state)
    _apply_keyboard_layer_visibility(root, state)

    kb_target = ""
    if state.keyboard is not None:
        kb_target = str(getattr(state.keyboard, "target", "") or "")

    if _needs_wifi_setup(state):
        _hide_wifi_onboarding_search_svg_circles(root)

    if state.wifi_scanning:
        _hide_wifi_search_glyphs(root)
        _set_visible(_find_by_logical_id(root, "main_box2_+_icon"), False)
        _set_visible(_find_by_logical_id(root, "welcome_to_pigeon_find_your_wifi_text"), False)

    _apply_picker_network_labels(root, state)
    _apply_box1_host_device_labels(root)
    for box_num in (2, 3):
        _apply_box_device_group_labels(root, state, box_num)
    for box_num in (1, 2, 3):
        _apply_box_location_group_labels(root, state, box_num)

    # Apply theme accent globally to *_accent layers (selection does not change accent).
    # Prefer the logical-id index over a full-tree normalize walk on every paint.
    for logical, els in _logical_id_index(root).items():
        if logical.endswith("_accent") or re.search(r"_accent(_|$)", logical):
            hide = logical in _HIDDEN_MAIN_ACCENT_IDS
            for el in els:
                if hide:
                    _set_visible(el, False)
                else:
                    _apply_accent_paint(el, theme.accent)
        if logical.endswith("_icon") or "_icon_" in logical:
            for el in els:
                _apply_ui_brand_paint(el, theme.ui)

    entry = state.manual_device_entry
    pairing_pin = state.box_pairing is not None and kb_target == "pin"
    wifi_logout_kb = state.keyboard_open and kb_target == "wifi_logout"
    if entry is not None:
        for lid in _DUAL_LOCATION_ICON_IDS:
            _set_visible(_find_by_logical_id(root, lid), False)
        _set_visible(_find_by_logical_id(root, "main_dual_network_wifi_group"), False)
    if pairing_pin or wifi_logout_kb:
        for lid in _DUAL_LOCATION_ICON_IDS:
            _set_visible(_find_by_logical_id(root, lid), False)
        _set_visible(_find_by_logical_id(root, "main_dual_network_wifi_group"), False)
        _hide_add_search_glyphs(root)
    hide_loc_svg = (
        (state.keyboard_open and kb_target in ("location", "network", "device_name", "pin", "wifi_logout"))
        or state.wifi_connecting
        or entry is not None
        or pairing_pin
    )
    if hide_loc_svg:
        _set_visible(_find_by_logical_id(root, "main_dual_location_text"), False)
    else:
        loc_el = _find_by_logical_id(root, "main_dual_location_text")
        _set_text_content(loc_el, state.location_name)
        loc_x0, loc_x1 = _dual_location_text_bounds_svg(root)
        baseline = float(_TEXT_ENTRY_FIELDS["location"]["baseline_y_svg"])
        if loc_el is not None:
            for node in loc_el.iter():
                if node.tag.endswith("text"):
                    _set_svg_text_horiz_centered(node, (loc_x0 + loc_x1) * 0.5, baseline)
        _set_visible(loc_el, True)
    hide_net_svg = (
        (state.keyboard_open and kb_target in ("network", "device_ip", "pin", "location", "wifi_logout"))
        or state.wifi_connecting
        or entry is not None
        or pairing_pin
    )
    if state.show_location_picker and not _needs_wifi_setup(state):
        # Dual-network becomes the RENAME CTA while picking a nest.
        _set_visible(_find_by_logical_id(root, "main_dual_network_wifi_group"), False)
    if hide_net_svg:
        _set_visible(_find_by_logical_id(root, "main_dual_network_name_text"), False)
    else:
        net_el = _find_by_logical_id(root, "main_dual_network_name_text")
        net_text = _network_field_text(state)
        if net_text == "CONNECT TO WIFI":
            _set_visible(net_el, False)
        elif net_text is not None:
            display = net_text.upper() if not state.wifi_configured or net_text == "RENAME" else net_text
            if net_text == "RENAME":
                display = "RENAME"
            _set_text_content(net_el, display)
            cx = (_DUAL_NETWORK_TEXT_X0_SVG + _DUAL_NETWORK_TEXT_X1_SVG) * 0.5
            baseline = float(_TEXT_ENTRY_FIELDS["location"]["baseline_y_svg"])
            for node in net_el.iter():
                if node.tag.endswith("text"):
                    fit_size = _network_field_font_size_svg(display)
                    if fit_size is not None:
                        _set_svg_text_font_size_px(node, _field_font_size_px(fit_size))
                    _set_svg_text_horiz_centered(node, cx, baseline)
                    if state.wifi_configured and display == "CONNECTED":
                        _set_paint(node, fill="#FFFFFF")
                    elif display == "RENAME":
                        _set_paint(node, fill="#FFFFFF")
                    elif not state.wifi_configured:
                        _set_paint(node, fill="#808080")
            _set_visible(net_el, True)
        else:
            _set_visible(net_el, False)
    for ver_id in ("text_pigeonVersion", "main_pigeon_version_text", "pigeon_version_text"):
        ver_el = _find_by_logical_id(root, ver_id)
        if ver_el is not None:
            _set_text_content(ver_el, state.version_string)
            _apply_contrast_paint(ver_el, selected=True, theme=SettingsTheme(
                selected=COLOR_VERSION_TEXT,
                deselected=COLOR_VERSION_TEXT,
            ))
            # Force black regardless of selection.
            for node in ver_el.iter():
                fill, stroke = _iter_style_fill_stroke(node)
                if fill and fill not in ("none", "transparent"):
                    _set_paint(node, fill=COLOR_VERSION_TEXT)
                if stroke and stroke not in ("none", "transparent"):
                    _set_paint(node, stroke=COLOR_VERSION_TEXT)

    focused = "" if state.keyboard_open else state.focused_id
    ring = discover_focus_ring_in_svg(root, state)
    if not state.keyboard_open and focused not in ring and ring:
        # Snap to a ring entry that exists.
        state.focus_ring = ring
        state.focus_index = 0
        focused = state.focused_id

    _apply_background_container(root, state, focused=focused or state.focused_id)
    _hide_vertical_scroll_arrows(root)
    _apply_network_picker_rows(root, state, theme=theme)
    _apply_network_picker_arrows(root, state, theme=theme)

    for logical in ring:
        selected = (not state.keyboard_open) and logical == focused
        if state.show_network_picker and logical in (
            "main_dual_location_button",
            "main_dual_network_button",
        ):
            selected = False
        if logical in ("main_box1_button", "main_box2_button", "main_box3_button"):
            continue
        if logical in ("main_dual_location_button", "main_dual_network_button"):
            continue
        if logical == "main_network_picker_button":
            # Outer picker shell stays dark; row/arrow styling handled separately.
            continue
        if logical == "main_box2_add_search_icon":
            _apply_wifi_search_icon_styles(root, focused=focused, theme=theme)
            continue
        button_el = _find_by_logical_id(root, logical)
        _apply_button_fill(button_el, selected=selected, theme=theme)

        # Also paint direct path children named the same logical button.
        for hit in _find_all_by_logical_id(root, logical):
            _apply_button_fill(hit, selected=selected, theme=theme)

        for assoc in _FOCUS_ASSOCIATED.get(logical, ()):
            for assoc_el in _find_all_by_logical_id(root, assoc):
                cls = _layer_class(_normalize_logical(assoc_el.get("id") or assoc))
                if cls == "_accent":
                    continue
                _apply_contrast_paint(assoc_el, selected=selected, theme=theme)

        # Exit text lives under main_exit_icon; ensure EXIT contrast.
        if logical == "main_exit_button":
            for assoc_el in _find_all_by_logical_id(root, "main_exit_text"):
                _apply_contrast_paint(assoc_el, selected=selected, theme=theme)

    _apply_box_column_nav_fills(root, state, focused=focused, theme=theme)
    _apply_dual_bar_nav_fills(root, state, focused=focused, theme=theme)


_SVG_TREE_TEMPLATES: dict[tuple[str, int], ET.Element] = {}
_SVG_TREE_TEMPLATE_MAX = 2
# Stable art discovers (wifi / onboarding) keyed by SVG path+mtime.
# Star-masked pigeon-logo rings are NOT cached: discovery depends on layer
# visibility after apply, and specs carry per-tree element ids used to hide circles.
_SVG_GEOMETRY_CACHE: dict[tuple[str, int], dict[str, object]] = {}


def _svg_geometry_bundle(path: Path, root: ET.Element) -> dict[str, object]:
    key = (str(path.resolve()), path.stat().st_mtime_ns)
    hit = _SVG_GEOMETRY_CACHE.get(key)
    if hit is not None:
        return hit
    bundle = {
        "onboarding_arcs": _discover_onboarding_search_arc_specs(root),
        "onboarding_tris": _discover_onboarding_search_triangle_specs(root),
        "wifi_layouts": _discover_wifi_icon_layouts(root),
    }
    if len(_SVG_GEOMETRY_CACHE) >= 4:
        _SVG_GEOMETRY_CACHE.clear()
    _SVG_GEOMETRY_CACHE[key] = bundle
    return bundle


def _svg_tree_from_path(path: Path) -> ET.Element:
    import copy

    path = Path(path)
    key = (str(path.resolve()), path.stat().st_mtime_ns)
    template = _SVG_TREE_TEMPLATES.get(key)
    if template is None:
        tree = ET.parse(path)
        root = tree.getroot()
        # Native 800×480 artboard — matches pigeon.design canvas (full bleed, no letterbox).
        root.set("viewBox", "0 0 800 480")
        root.set("width", str(DESIGN_W))
        root.set("height", str(DESIGN_H))
        if len(_SVG_TREE_TEMPLATES) >= _SVG_TREE_TEMPLATE_MAX:
            _SVG_TREE_TEMPLATES.clear()
        _SVG_TREE_TEMPLATES[key] = root
        template = root
    return copy.deepcopy(template)


def _rasterize_svg_tree(root: ET.Element) -> np.ndarray:
    """Return BGRA uint8 (DESIGN_H × DESIGN_W) with Digital-7 / Sharp Sans labels."""
    from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra

    return rasterize_settings_svg_bgra(root, width=DESIGN_W, height=DESIGN_H)


def render_main_settings_bgra(
    state: MainSettingsState | None = None,
    *,
    svg_path: Path | str | None = None,
    assets_dir: Path | str | None = None,
    skip_text_entry: bool = False,
) -> np.ndarray:
    """Load settings_main.svg, apply ``state``, return 800×480 BGRA.

    ``skip_text_entry`` leaves dual-bar field text undrawn so callers can cache
    chrome and patch the buffer without a full SVG re-raster.
    """
    if svg_path is not None:
        path = Path(svg_path)
    else:
        path = default_main_settings_svg_path(assets_dir)
    if not path.is_file():
        raise FileNotFoundError(f"main settings SVG not found: {path}")

    st = state if state is not None else MainSettingsState()
    st.ensure_focus_ring()
    root = _svg_tree_from_path(path)
    # Narrow focus ring to layers that exist in this SVG.
    present = discover_focus_ring_in_svg(root, st)
    st.focus_ring = present
    st.focus_index = int(st.focus_index) % max(1, len(present))
    apply_main_settings_svg_state(root, st)
    focused_logical = "" if st.keyboard_open else st.focused_id
    _disable_embedded_settings_background_layers(root)
    # Discover after apply so hidden pigeon-logo icons are skipped, and circle
    # element ids match this tree for hide + OpenCV star-clip redraw.
    star_specs = _discover_star_masked_circles(root)
    geom = _svg_geometry_bundle(path, root)
    onboarding_arc_specs = geom["onboarding_arcs"]  # type: ignore[assignment]
    onboarding_triangle_specs = geom["onboarding_tris"]  # type: ignore[assignment]
    wifi_layouts = geom["wifi_layouts"]  # type: ignore[assignment]
    # Box search rings: path strokes rasterize; leftover clip-path circles are hidden.
    box_search_arc_overlays = _collect_box_search_arc_overlays(
        root, st, focused_logical=focused_logical
    )
    parents = _parent_map(root)
    box1_logo_el = _find_by_logical_id(root, "main_box1_pigeon_logo_icon")
    draw_box1_pigeon_logo = (
        box1_logo_el is not None and not _is_subtree_hidden(box1_logo_el, parents)
    )
    _hide_box1_wing_logo_polygon(root)
    _hide_svg_wifi_icons(root)
    _hide_star_masked_svg_circles(root, star_specs)
    _hide_box_column_search_svg_circles(root)
    _prune_display_none(root)
    ui_bgra = _rasterize_svg_tree(root)
    bg_bgra = np.zeros((DESIGN_H, DESIGN_W, 4), dtype=np.uint8)
    bg_bgra[:, :, :3] = 0
    bg_bgra[:, :, 3] = 255
    assets_root = assets_dir if assets_dir is not None else path.parent.parent
    _draw_container_background_bgra(
        bg_bgra,
        ui_hex=st.theme.ui,
        assets_dir=assets_root,
    )
    bgra = _composite_bgra_over_bgra(bg_bgra, ui_bgra)
    if draw_box1_pigeon_logo:
        _draw_box1_pigeon_logo_overlay(
            bgra, assets_dir=assets_root, ui_hex=st.theme.ui
        )
    _draw_wifi_overlays(bgra, st, wifi_layouts, focused_logical=focused_logical)
    _draw_star_masked_circle_overlays(bgra, st, star_specs, focused_logical=focused_logical)
    _draw_box_search_arc_overlays(bgra, box_search_arc_overlays)
    _draw_wifi_onboarding_search_overlays(
        bgra, st, onboarding_arc_specs, onboarding_triangle_specs
    )
    if st.keyboard_open or st.wifi_connecting or st.manual_device_entry is not None:
        if not skip_text_entry:
            _draw_text_entry_content(bgra, st)
    if not skip_text_entry:
        _draw_dual_bar_network_prompt(bgra, st)
    return bgra


class MainSettingsWidget:
    """Cached main-settings compositor (similar to ``NowPlayingScreenWidget``)."""

    def __init__(
        self,
        *,
        assets_dir: Path | str | None = None,
        svg_path: Path | str | None = None,
        state: MainSettingsState | None = None,
    ) -> None:
        self._assets_dir = Path(assets_dir) if assets_dir is not None else None
        self._svg_path = Path(svg_path) if svg_path is not None else None
        self._state = state if state is not None else MainSettingsState()
        if self._state.version_string in ("", "0.8.0"):
            try:
                from pigeon.version import version_string

                self._state.version_string = version_string()
            except Exception:
                pass
        try:
            from pigeon.widgets.ui_color_settings import load_persisted_theme_into_state

            load_persisted_theme_into_state(self._state)
        except Exception:
            pass
        if self._state.needs_wifi_setup():
            self._state.wifi_onboarding = False
        try:
            from pigeon.app_state import read_current_location_name, read_location_wifi

            wifi = read_location_wifi()
            if wifi is not None:
                self._state.selected_wifi_ssid = wifi["ssid"]
                self._state.wifi_password = wifi.get("password", "")
            self._state.location_name = read_current_location_name()
        except Exception:
            pass
        self._state.load_saved_box_devices()
        self._state.ensure_focus_ring()
        self._cached_bgra: np.ndarray | None = None
        self._cached_sig: tuple[object, ...] | None = None
        self._cached_main_bgra: np.ndarray | None = None
        self._cached_main_sig: tuple[object, ...] | None = None
        self._cached_kb_bgra: np.ndarray | None = None
        self._cached_kb_sig: tuple[object, ...] | None = None
        # Main chrome under the keyboard (no field text) — typing patches text only.
        self._cached_kb_chrome_bgra: np.ndarray | None = None
        self._cached_kb_chrome_sig: tuple[object, ...] | None = None
        # Per-key keyboard overlays for the active mode — Left/Right revisits skip PyMuPDF.
        self._kb_focus_frame_cache: dict[tuple[object, ...], np.ndarray] = {}
        self._kb_composed_cache: dict[tuple[object, ...], np.ndarray] = {}
        self._kb_cache_mode: object | None = None
        self._kb_prewarm_inflight: bool = False
        # Opaque-flag cache for keyboard overlays (avoid float blend when possible).
        self._kb_frame_opaque: dict[tuple[object, ...], bool] = {}
        self._wifi_search_glyph_cache: np.ndarray | None = None
        self._wifi_scan_result: tuple[str, ...] | None = None
        self._wifi_scan_cache: tuple[tuple[str, ...], float] | None = None
        self._box_scan_result: dict[int, tuple[tuple[tuple[str, str], ...], tuple[dict[str, str], ...]] | None] = {}
        self._box_scan_cache: dict[int, tuple[tuple[tuple[str, str], ...], tuple[dict[str, str], ...], float]] = {}
        self._box_search_glyph_cache: dict[int, np.ndarray | None] = {}
        self._wifi_search_rotated_cache: tuple[np.ndarray, ...] | None = None
        self._location_switch_spinner_frames: tuple[np.ndarray, ...] | None = None
        self._location_switch_spinner_tried = False
        self._box_search_rotated_cache: dict[int, tuple[np.ndarray, ...]] = {}
        self._box_scan_pending: set[int] = set()
        self._wifi_prefetch_inflight: bool = False
        self._box_prefetch_inflight: set[int] = set()
        self._pre_scan_main_bgra: dict[int, np.ndarray] = {}
        self._last_tick_mono: float = time.monotonic()
        # Per-focus bitmaps for the current structure — left/right nav revisits are free.
        self._focus_frame_cache: dict[tuple[object, ...], np.ndarray] = {}
        self._focus_cache_structure: tuple[object, ...] | None = None
        self._prewarm_all_inflight: bool = False
        # Kick full-ring prewarm only after the first live paint for a structure.
        self._want_prewarm_after_paint: bool = False
        # Avoid repeated full-frame alpha.min() when the current paste is known opaque.
        self._paste_fully_opaque: bool | None = None
        # Warm LAN IP off the first settings paint (hostname/ipconfig can take tens of ms).
        try:
            from pigeon.local_ip import local_ipv4_address

            local_ipv4_address()
        except Exception:
            pass

    @property
    def state(self) -> MainSettingsState:
        return self._state

    def invalidate(self) -> None:
        self._cached_bgra = None
        self._cached_sig = None
        self._cached_main_bgra = None
        self._cached_main_sig = None
        self._cached_kb_bgra = None
        self._cached_kb_sig = None
        self._cached_kb_chrome_bgra = None
        self._cached_kb_chrome_sig = None
        self._kb_focus_frame_cache.clear()
        self._kb_composed_cache.clear()
        self._kb_frame_opaque.clear()
        self._kb_cache_mode = None
        self._kb_prewarm_inflight = False
        self._focus_frame_cache.clear()
        self._focus_cache_structure = None
        self._prewarm_all_inflight = False
        self._want_prewarm_after_paint = True
        self._paste_fully_opaque = None

    def frame_cache_token(self) -> tuple[object, ...]:
        """Stable token for skip-cache while the settings bitmap is unchanged."""
        kb_open = self._state.keyboard is not None
        return (
            self._cached_main_sig,
            self._cached_kb_sig,
            round(float(self._state.wifi_scan_angle_deg), 0)
            if self._state.wifi_scanning or self._state.wifi_connecting
            else 0,
            round(float(self._state.box2_devices.scan_angle_deg), 0)
            if self._state.box2_devices.scanning
            else 0,
            round(float(self._state.box3_devices.scan_angle_deg), 0)
            if self._state.box3_devices.scanning
            else 0,
            round(float(self._state.location_switch_angle_deg), 0)
            if self._state.location_switch_spinner_visible()
            else 0,
            # Drive caret blink without forcing a full redraw every wake.
            int(time.monotonic() * 2) % 2 if kb_open else 0,
            1 if kb_open else 0,
            # Prefs live overlays (HH:MM / NP rings / content) refresh each
            # second without busting the heavy SVG structure cache.
            (
                datetime.now().strftime("%H%M%S"),
                int(round(float(self._state.preferences_np_progress) * 100))
                if self._state.preferences_np_progress is not None
                else -1,
                1 if self._state.preferences_live_content else 0,
                str(self._state.preferences_volume or ""),
                round(float(self._state.preferences_volume_fraction or 0.0), 3),
                str(self._state.preferences_elapsed_text or ""),
                str(self._state.preferences_remaining_text or ""),
                str(self._state.preferences_service_name or ""),
                str(self._state.preferences_song_title or ""),
                str(self._state.preferences_album_title or ""),
                str(self._state.preferences_artist_title or ""),
                id(self._state.preferences_poster_bgra)
                if self._state.preferences_poster_bgra is not None
                else 0,
                tuple(self._state.preferences_cast or ()),
            )
            if self._state.show_preferences
            else (),
        )

    def _clear_keyboard_focus_caches(self) -> None:
        self._kb_focus_frame_cache.clear()
        self._kb_composed_cache.clear()
        self._kb_frame_opaque.clear()
        self._kb_cache_mode = None
        self._kb_prewarm_inflight = False
        self._cached_kb_bgra = None
        self._cached_kb_sig = None

    def _invalidate_keyboard_cache(self) -> None:
        """Drop composed output; keep per-key kb bitmaps for Left/Right reuse."""
        self._cached_kb_bgra = None
        self._cached_kb_sig = None
        self._cached_bgra = None
        self._cached_sig = None
        self._paste_fully_opaque = None

    def _keyboard_chrome_sig(self) -> tuple[object, ...]:
        """Main chrome under the keyboard — excludes typing buffer."""
        st = self._state
        th = st.theme
        kb = st.keyboard
        kb_main: tuple[object, ...] = ()
        if kb is not None:
            kb_main = (
                getattr(kb, "mode", None),
                str(getattr(kb, "target", "")),
                str(getattr(kb, "initial_text", "")),
                bool(getattr(kb, "supports_lowercase", True)),
            )
        return (
            th.ui,
            th.selected,
            th.deselected,
            th.inactive,
            th.accent,
            int(st.wifi_level),
            st.location_name,
            st.wifi_password,
            st.selected_wifi_ssid,
            st.pending_wifi_ssid,
            bool(st.network_password_error),
            bool(st.wifi_connecting),
            st.version_string,
            bool(st.show_network_picker),
            bool(st.show_box1_panel),
            bool(st.show_box2_panel),
            bool(st.show_box3_panel),
            bool(st.show_location_picker),
            bool(st.location_switching),
            bool(st.location_switch_spinner_visible()),
            round(float(st.location_switch_angle_deg) / 10.0)
            if st.location_switch_spinner_visible()
            else -1,
            bool(st.show_pigeon_settings),
            bool(st.show_update_popup),
            bool(st.update_available),
            bool(st.update_checking),
            bool(st.update_applying),
            round(float(st.update_progress), 3),
            str(st.update_local_version or ""),
            str(st.update_remote_version or ""),
            str(st.update_changelog or ""),
            str(st.update_error or ""),
            int(st.update_popup_focus_index),
            bool(st.keyboard_open),
            str(self._svg_path or ""),
            str(self._assets_dir or ""),
            kb_main,
            None
            if st.box_pairing is None
            else (
                int(st.box_pairing.box_num),
                str(st.box_pairing.step),
                str(st.box_pairing.session_key),
                str(st.box_pairing.device_name),
            ),
        )

    def _main_with_text_from_chrome(self) -> np.ndarray:
        """Copy keyboard chrome and paint current field text (no SVG raster)."""
        assert self._cached_kb_chrome_bgra is not None
        frame = self._cached_kb_chrome_bgra.copy()
        _draw_text_entry_content(frame, self._state)
        _draw_dual_bar_network_prompt(frame, self._state)
        return frame

    def _compose_keyboard_over_main(
        self, main_frame: np.ndarray, kb_frame: np.ndarray, kb_sig: tuple[object, ...] | None
    ) -> np.ndarray:
        """Alpha-compose keyboard overlay; opaque overlays skip float blend."""
        opaque = None if kb_sig is None else self._kb_frame_opaque.get(kb_sig)
        if opaque is None:
            opaque = int(kb_frame[:, :, 3].min()) == 255
            if kb_sig is not None:
                self._kb_frame_opaque[kb_sig] = opaque
        out = main_frame.copy()
        if opaque:
            out[:, :, :3] = kb_frame[:, :, :3]
            out[:, :, 3] = 255
            return out
        base_bgr = out[:, :, :3]
        blended = alpha_blend_bgra_over_bgr(base_bgr, kb_frame)
        alpha = kb_frame[:, :, 3:4].astype(np.float32) / 255.0
        out_a = np.clip(
            alpha * 255.0 + (1.0 - alpha) * out[:, :, 3:4].astype(np.float32),
            0,
            255,
        ).astype(np.uint8)
        return np.dstack([blended, out_a[:, :, 0]])

    def _keyboard_overlay_sig_for(
        self,
        mode: object,
        focus_index: int,
        *,
        supports_lowercase: bool = True,
        target: str = "",
    ) -> tuple[object, ...]:
        # Include supports_lowercase/target so location (uppercase-only, no Shift)
        # frames are never reused for network password (mixed case + Shift).
        return (
            mode,
            int(focus_index),
            bool(supports_lowercase),
            str(target or ""),
            str(self._assets_dir or ""),
        )

    def _store_kb_frame(self, kb_sig: tuple[object, ...], frame: np.ndarray) -> None:
        if kb_sig not in self._kb_focus_frame_cache:
            self._kb_focus_frame_cache[kb_sig] = frame
            if len(self._kb_focus_frame_cache) > 28:
                oldest = next(iter(self._kb_focus_frame_cache))
                if oldest != kb_sig:
                    self._kb_focus_frame_cache.pop(oldest, None)

    def _store_kb_composed(
        self, compose_key: tuple[object, ...], frame: np.ndarray
    ) -> None:
        if compose_key not in self._kb_composed_cache:
            self._kb_composed_cache[compose_key] = frame
            if len(self._kb_composed_cache) > 28:
                oldest = next(iter(self._kb_composed_cache))
                if oldest != compose_key:
                    self._kb_composed_cache.pop(oldest, None)

    def _metadata_debug_sig(self) -> tuple[object, ...]:
        """Metadata inspector page + a live-data token (repaint on change)."""
        st = self._state
        if not st.show_metadata_debug:
            return (False,)
        try:
            from pigeon.widgets.metadata_debug import metadata_debug_data

            data_token = hash(repr(metadata_debug_data()))
        except Exception:
            data_token = 0
        return (True, int(st.metadata_debug_page), data_token)

    def _main_state_sig(self) -> tuple[object, ...]:
        st = self._state
        if st.show_pigeon_settings or st.show_preferences:
            try:
                from pigeon.hdmi_ocr import hdmi_capture_available, probe_hdmi_presence

                st.pigeon_hdmi_ok = hdmi_capture_available()
                probe_hdmi_presence()
            except Exception:
                pass
        th = st.theme
        kb = st.keyboard
        kb_main: tuple[object, ...] = ()
        if kb is not None:
            kb_main = (
                getattr(kb, "mode", None),
                str(getattr(kb, "buffer", "")),
                str(getattr(kb, "target", "")),
                str(getattr(kb, "initial_text", "")),
                bool(getattr(kb, "supports_lowercase", True)),
            )
        return (
            int(st.focus_index) if not st.keyboard_open else -1,
            st.focus_ring,
            th.ui,
            th.selected,
            th.deselected,
            th.inactive,
            th.accent,
            int(st.wifi_level),
            st.location_name,
            st.wifi_password,
            st.selected_wifi_ssid,
            st.pending_wifi_ssid,
            bool(st.network_password_error),
            bool(st.wifi_connecting),
            st.version_string,
            bool(st.show_network_picker),
            int(st.network_picker_row),
            int(st.network_picker_scroll),
            str(st.network_picker_arrow),
            bool(st.show_instructions),
            bool(st.wifi_onboarding),
            bool(st.wifi_scanning),
            st.wifi_networks,
            bool(st.show_box1_panel),
            bool(st.show_box2_panel),
            bool(st.show_box3_panel),
            bool(st.show_location_picker),
            bool(st.location_switching),
            bool(st.location_switch_spinner_visible()),
            round(float(st.location_switch_angle_deg) / 10.0)
            if st.location_switch_spinner_visible()
            else -1,
            tuple(st.location_slots),
            str(st.renaming_location_id),
            int(st.renaming_location_slot),
            tuple(st.box2_devices.devices),
            st.box2_devices.phase,
            bool(st.box2_devices.scanning),
            int(st.box2_devices.scroll),
            int(st.box2_devices.row),
            str(st.box2_devices.arrow),
            st.box2_devices.picked,
            tuple(st.box3_devices.devices),
            st.box3_devices.phase,
            bool(st.box3_devices.scanning),
            int(st.box3_devices.scroll),
            int(st.box3_devices.row),
            str(st.box3_devices.arrow),
            st.box3_devices.picked,
            bool(st.show_pigeon_settings),
            int(st.pigeon_focus_index),
            bool(st.pigeon_needs_update_prefetch),
            st.pigeon_metadata_ok,
            bool(st.pigeon_hdmi_ok),
            bool(st.pigeon_audio_ok),
            bool(st.source_wifi_on),
            bool(st.source_metadata_on),
            bool(st.source_hdmi_on),
            bool(st.source_audio_on),
            bool(st.wifi_configured),
            bool(st.show_preferences),
            self._metadata_debug_sig(),
            str(st.preferences_nav or ""),
            int(st.preferences_focus_index),
            int(st.preferences_active_zone),
            tuple(st.preferences_zone_widgets),
            bool(st.show_ui_color),
            str(st.ui_color_nav or ""),
            int(st.ui_color_focus_index),
            str(st.ui_color_active_class or ""),
            str(st.ui_color_accent_key or ""),
            str(st.ui_color_ui_key or ""),
            str(st.ui_color_button_key or ""),
            str(st.theme.ui),
            str(st.theme.accent),
            str(st.theme.deselected),
            bool(st.show_update_popup),
            bool(st.update_available),
            bool(st.update_checking),
            bool(st.update_applying),
            round(float(st.update_progress), 3),
            str(st.update_local_version or ""),
            str(st.update_remote_version or ""),
            str(st.update_changelog or ""),
            str(st.update_error or ""),
            int(st.update_popup_focus_index),
            bool(st.keyboard_open),
            str(self._svg_path or ""),
            str(self._assets_dir or ""),
            kb_main,
            None if st.box_pairing is None else (
                int(st.box_pairing.box_num),
                str(st.box_pairing.step),
                str(st.box_pairing.session_key),
                str(st.box_pairing.device_name),
            ),
        )

    def _keyboard_overlay_sig(self) -> tuple[object, ...] | None:
        kb = self._state.keyboard
        if kb is None:
            return None
        return self._keyboard_overlay_sig_for(
            getattr(kb, "mode", None),
            int(getattr(kb, "focus_index", 0)),
            supports_lowercase=bool(getattr(kb, "supports_lowercase", True)),
            target=str(getattr(kb, "target", "") or ""),
        )

    def _structure_sig(self) -> tuple[object, ...]:
        """State that forces a full SVG rebuild (everything except pure focus indices)."""
        st = self._state
        th = st.theme
        kb = st.keyboard
        kb_main: tuple[object, ...] = ()
        if kb is not None:
            kb_main = (
                getattr(kb, "mode", None),
                # Buffer is Pillow-patched on chrome — not SVG structure.
                str(getattr(kb, "target", "")),
                str(getattr(kb, "initial_text", "")),
                bool(getattr(kb, "supports_lowercase", True)),
            )
        return (
            st.focus_ring,
            th.ui,
            th.selected,
            th.deselected,
            th.inactive,
            th.accent,
            int(st.wifi_level),
            st.location_name,
            st.wifi_password,
            st.selected_wifi_ssid,
            st.pending_wifi_ssid,
            bool(st.network_password_error),
            bool(st.wifi_connecting),
            st.version_string,
            bool(st.show_network_picker),
            int(st.network_picker_scroll),
            str(st.network_picker_arrow),
            bool(st.show_instructions),
            bool(st.wifi_onboarding),
            bool(st.wifi_scanning),
            st.wifi_networks,
            bool(st.show_box1_panel),
            bool(st.show_box2_panel),
            bool(st.show_box3_panel),
            bool(st.show_location_picker),
            bool(st.location_switching),
            bool(st.location_switch_spinner_visible()),
            round(float(st.location_switch_angle_deg) / 10.0)
            if st.location_switch_spinner_visible()
            else -1,
            tuple(st.location_slots),
            str(st.renaming_location_id),
            int(st.renaming_location_slot),
            tuple(st.box2_devices.devices),
            st.box2_devices.phase,
            bool(st.box2_devices.scanning),
            int(st.box2_devices.scroll),
            st.box2_devices.picked,
            tuple(st.box3_devices.devices),
            st.box3_devices.phase,
            bool(st.box3_devices.scanning),
            int(st.box3_devices.scroll),
            st.box3_devices.picked,
            bool(st.show_pigeon_settings),
            bool(st.show_preferences),
            str(st.preferences_nav or ""),
            int(st.preferences_active_zone),
            # Zone widget assignments are focus-key only (change every widget nav).
            bool(st.show_ui_color),
            str(st.ui_color_nav or ""),
            str(st.ui_color_active_class or ""),
            str(st.ui_color_accent_key or ""),
            str(st.ui_color_ui_key or ""),
            str(st.ui_color_button_key or ""),
            str(st.theme.ui),
            str(st.theme.accent),
            str(st.theme.deselected),
            bool(st.show_update_popup),
            bool(st.update_available),
            bool(st.update_checking),
            bool(st.update_applying),
            round(float(st.update_progress), 3),
            str(st.update_remote_version or ""),
            str(st.update_changelog or ""),
            str(st.update_error or ""),
            bool(st.keyboard_open),
            str(self._svg_path or ""),
            str(self._assets_dir or ""),
            kb_main,
            None
            if st.box_pairing is None
            else (
                int(st.box_pairing.box_num),
                str(st.box_pairing.step),
                str(st.box_pairing.session_key),
                str(st.box_pairing.device_name),
            ),
        )

    def _focus_cache_key(self) -> tuple[object, ...]:
        st = self._state
        return (
            int(st.focus_index) if not st.keyboard_open else -1,
            int(st.network_picker_row),
            int(st.box2_devices.row),
            str(st.box2_devices.arrow),
            int(st.box3_devices.row),
            str(st.box3_devices.arrow),
            int(st.pigeon_focus_index),
            int(st.preferences_focus_index) if st.show_preferences else -1,
            str(st.preferences_nav or "") if st.show_preferences else "",
            int(st.preferences_active_zone) if st.show_preferences else 0,
            tuple(st.preferences_zone_widgets) if st.show_preferences else (),
            int(st.ui_color_focus_index) if st.show_ui_color else -1,
            str(st.ui_color_nav or "") if st.show_ui_color else "",
            str(st.ui_color_active_class or "") if st.show_ui_color else "",
            str(st.ui_color_accent_key or "") if st.show_ui_color else "",
            str(st.ui_color_ui_key or "") if st.show_ui_color else "",
            str(st.ui_color_button_key or "") if st.show_ui_color else "",
            int(st.update_popup_focus_index) if st.show_update_popup else -1,
            bool(st.show_update_popup),
        )

    def _store_focus_frame(self, frame: np.ndarray) -> None:
        structure = self._structure_sig()
        if structure != self._focus_cache_structure:
            self._focus_frame_cache.clear()
            self._focus_cache_structure = structure
        key = self._focus_cache_key()
        if key not in self._focus_frame_cache:
            self._focus_frame_cache[key] = frame.copy()
            # Bound memory: keep recent focus + list-row bitmaps (WiFi 3 / box 5).
            if len(self._focus_frame_cache) > 20:
                oldest = next(iter(self._focus_frame_cache))
                if oldest != key:
                    self._focus_frame_cache.pop(oldest, None)

    def _list_row_count(self) -> int | None:
        """Visible selection rows when WiFi picker or box results are locked."""
        st = self._state
        if st.show_network_picker and not st.wifi_configured:
            return len(_PICKER_ROW_TEXTS)
        for box_num in (2, 3):
            if st._box_panel(box_num).results_locked:
                return int(_BOX_DEVICE_ROW_COUNT)
        return None

    def _current_list_row(self) -> int | None:
        st = self._state
        if st.show_network_picker and not st.wifi_configured:
            return int(st.network_picker_row)
        for box_num in (2, 3):
            panel = st._box_panel(box_num)
            if panel.results_locked:
                return int(panel.row)
        return None

    def _apply_list_row(self, st: MainSettingsState, row: int) -> None:
        if st.show_network_picker and not st.wifi_configured:
            st.network_picker_row = int(row)
            st.network_picker_arrow = ""
            return
        for box_num in (2, 3):
            panel = st._box_panel(box_num)
            if panel.results_locked:
                panel.row = int(row)
                panel.arrow = ""
                return

    def _focus_key_for_state(self, st: MainSettingsState) -> tuple[object, ...]:
        return (
            int(st.focus_index) if not st.keyboard_open else -1,
            int(st.network_picker_row),
            int(st.box2_devices.row),
            str(st.box2_devices.arrow),
            int(st.box3_devices.row),
            str(st.box3_devices.arrow),
            int(st.pigeon_focus_index),
            int(st.preferences_focus_index) if st.show_preferences else -1,
            str(st.preferences_nav or "") if st.show_preferences else "",
            int(st.preferences_active_zone) if st.show_preferences else 0,
            tuple(st.preferences_zone_widgets) if st.show_preferences else (),
            int(st.ui_color_focus_index) if st.show_ui_color else -1,
            str(st.ui_color_nav or "") if st.show_ui_color else "",
            str(st.ui_color_active_class or "") if st.show_ui_color else "",
            str(st.ui_color_accent_key or "") if st.show_ui_color else "",
            str(st.ui_color_ui_key or "") if st.show_ui_color else "",
            str(st.ui_color_button_key or "") if st.show_ui_color else "",
            int(st.update_popup_focus_index) if st.show_update_popup else -1,
            bool(st.show_update_popup),
        )

    def navigate(self, forward: bool = True) -> None:
        st = self._state
        if st.keyboard is not None:
            st.navigate(forward=forward)
            kb = st.keyboard
            mode = getattr(kb, "mode", None) if kb is not None else None
            if self._kb_cache_mode is None:
                self._kb_cache_mode = mode
            elif self._kb_cache_mode != mode:
                self._clear_keyboard_focus_caches()
                self._kb_cache_mode = mode
            kb_sig = self._keyboard_overlay_sig()
            # Soft reuse: keep per-key overlays; only drop composed PhotoImage input.
            self._cached_bgra = None
            self._cached_sig = None
            self._paste_fully_opaque = None
            if kb_sig is not None and kb_sig in self._kb_focus_frame_cache:
                self._cached_kb_bgra = self._kb_focus_frame_cache[kb_sig]
                self._cached_kb_sig = kb_sig
            else:
                self._cached_kb_bgra = None
                self._cached_kb_sig = None
            self._prewarm_neighbor_keyboard(forward=forward)
            return
        if st.show_pigeon_settings:
            st.navigate_pigeon(forward=forward)
            # Soft nav — reuse per-focus pigeon bitmaps like main chrome.
            self._cached_bgra = None
            self._cached_sig = None
            self._paste_fully_opaque = None
            if st.show_metadata_debug:
                # Inspector pages render live; a grid focus bitmap here would
                # be served by bgra_frame() as the "current" frame and make
                # arrow presses appear to exit to the settings grid.
                self._cached_main_bgra = None
                self._cached_main_sig = None
                return
            focus_key = self._focus_cache_key()
            structure = self._structure_sig()
            if (
                structure == self._focus_cache_structure
                and focus_key in self._focus_frame_cache
            ):
                self._cached_main_bgra = self._focus_frame_cache[focus_key]
                self._cached_main_sig = self._main_state_sig()
            else:
                self._cached_main_bgra = None
                self._cached_main_sig = None
            self._prewarm_neighbor_focus(forward=forward)
            return
        st.navigate(forward=forward)
        # Drop composed frame so PhotoImage refreshes; reuse focus bitmap when available.
        self._cached_bgra = None
        self._cached_sig = None
        self._paste_fully_opaque = None
        focus_key = self._focus_cache_key()
        structure = self._structure_sig()
        if structure != self._focus_cache_structure:
            # Scroll / list content changed — refill row bitmaps after the next live paint.
            self._want_prewarm_after_paint = True
        if (
            structure == self._focus_cache_structure
            and focus_key in self._focus_frame_cache
        ):
            self._cached_main_bgra = self._focus_frame_cache[focus_key]
            self._cached_main_sig = self._main_state_sig()
        else:
            self._cached_main_bgra = None
            self._cached_main_sig = None
        self.prefetch_scans_for_focus(st.focused_id)
        self._prewarm_neighbor_focus(forward=forward)

    def prewarm_focus_ring(self) -> None:
        """Rasterize every focus / list-row target for the current structure off-thread."""
        import copy
        import threading

        if self._prewarm_all_inflight:
            return
        st = self._state
        if st.keyboard_open:
            return
        structure = self._structure_sig()
        if st.show_pigeon_settings and st.show_ui_color:
            from pigeon.widgets.ui_color_settings import (
                render_ui_color_settings_bgra,
                ui_color_class_focus_ring,
                ui_color_swatch_focus_ring,
            )

            if str(st.ui_color_nav or "") == "swatches":
                ring = ui_color_swatch_focus_ring(str(st.ui_color_active_class or ""))
            else:
                ring = ui_color_class_focus_ring()
            n = len(ring)
            if n <= 1:
                return
            missing = []
            for idx in range(n):
                probe = copy.deepcopy(st)
                probe.ui_color_focus_index = idx
                if self._focus_key_for_state(probe) not in self._focus_frame_cache:
                    missing.append(idx)
            if not missing:
                return
            self._prewarm_all_inflight = True
            state_snap = copy.deepcopy(st)
            assets_dir = self._assets_dir
            cache = self._focus_frame_cache
            struct_ref = structure

            def _work_ui_color() -> None:
                try:
                    for idx in missing:
                        if self._focus_cache_structure not in (None, struct_ref):
                            return
                        state_snap.ui_color_focus_index = idx
                        if str(state_snap.ui_color_nav or "") == "swatches":
                            focused = ring[idx % n]
                            cls = str(state_snap.ui_color_active_class or "")
                            if cls == "accent":
                                state_snap.ui_color_accent_key = focused
                            elif cls == "ui":
                                state_snap.ui_color_ui_key = focused
                            elif cls == "button":
                                state_snap.ui_color_button_key = focused
                            from pigeon.widgets.ui_color_settings import (
                                theme_from_color_keys,
                            )

                            state_snap.theme = theme_from_color_keys(
                                {
                                    "accent": state_snap.ui_color_accent_key,
                                    "ui": state_snap.ui_color_ui_key,
                                    "button": state_snap.ui_color_button_key,
                                },
                                base=state_snap.theme,
                            )
                        key = self._focus_key_for_state(state_snap)
                        if key in cache:
                            continue
                        try:
                            frame = render_ui_color_settings_bgra(
                                state_snap, assets_dir=assets_dir
                            )
                        except Exception:
                            return
                        if self._focus_cache_structure not in (None, struct_ref):
                            return
                        if self._focus_cache_structure is None:
                            self._focus_cache_structure = struct_ref
                        cache[key] = frame
                finally:
                    self._prewarm_all_inflight = False

            import threading as _threading

            _threading.Thread(
                target=_work_ui_color, name="ui-color-prewarm-all", daemon=True
            ).start()
            return
        if st.show_pigeon_settings and st.show_preferences:
            from pigeon.widgets.preferences_settings import (
                preferences_widget_focus_ring,
                preferences_zone_focus_ring,
                render_preferences_settings_bgra,
            )

            if str(st.preferences_nav or "") == "widgets":
                ring = preferences_widget_focus_ring(int(st.preferences_active_zone))
            else:
                ring = preferences_zone_focus_ring()
            n = len(ring)
            if n <= 1:
                return
            missing = []
            for idx in range(n):
                probe = copy.deepcopy(st)
                probe.preferences_focus_index = idx
                if self._focus_key_for_state(probe) not in self._focus_frame_cache:
                    missing.append(idx)
            if not missing:
                return
            self._prewarm_all_inflight = True
            state_snap = copy.deepcopy(st)
            assets_dir = self._assets_dir
            cache = self._focus_frame_cache
            struct_ref = structure

            def _work_prefs() -> None:
                try:
                    for idx in missing:
                        if self._focus_cache_structure not in (None, struct_ref):
                            return
                        state_snap.preferences_focus_index = idx
                        # Mirror live widget assignment while prewarming widget nav.
                        if str(state_snap.preferences_nav or "") == "widgets":
                            focused = ring[idx % n]
                            zone = int(state_snap.preferences_active_zone)
                            if focused != "exit" and 1 <= zone <= 5:
                                widgets = list(state_snap.preferences_zone_widgets)
                                widgets[zone - 1] = focused
                                state_snap.preferences_zone_widgets = (
                                    widgets[0],
                                    widgets[1],
                                    widgets[2],
                                    widgets[3],
                                    widgets[4],
                                )
                        key = self._focus_key_for_state(state_snap)
                        if key in cache:
                            continue
                        try:
                            frame = render_preferences_settings_bgra(
                                state_snap, assets_dir=assets_dir
                            )
                        except Exception:
                            return
                        if self._focus_cache_structure not in (None, struct_ref):
                            return
                        if self._focus_cache_structure is None:
                            self._focus_cache_structure = struct_ref
                        cache[key] = frame
                finally:
                    self._prewarm_all_inflight = False

            import threading as _threading

            _threading.Thread(
                target=_work_prefs, name="prefs-prewarm-all", daemon=True
            ).start()
            return
        if st.show_pigeon_settings:
            from pigeon.widgets.pigeon_settings import pigeon_focus_ring, render_pigeon_settings_bgra

            ring = pigeon_focus_ring()
            n = len(ring)
            if n <= 1:
                return
            missing = []
            for idx in range(n):
                probe = copy.deepcopy(st)
                probe.pigeon_focus_index = idx
                if self._focus_key_for_state(probe) not in self._focus_frame_cache:
                    missing.append(idx)
            if not missing:
                return
            self._prewarm_all_inflight = True
            state_snap = copy.deepcopy(st)
            assets_dir = self._assets_dir
            cache = self._focus_frame_cache
            struct_ref = structure

            def _work_pigeon() -> None:
                try:
                    for idx in missing:
                        if self._focus_cache_structure not in (None, struct_ref):
                            return
                        state_snap.pigeon_focus_index = idx
                        key = self._focus_key_for_state(state_snap)
                        if key in cache:
                            continue
                        try:
                            frame = render_pigeon_settings_bgra(
                                state_snap, assets_dir=assets_dir
                            )
                        except Exception:
                            return
                        if self._focus_cache_structure not in (None, struct_ref):
                            return
                        if self._focus_cache_structure is None:
                            self._focus_cache_structure = struct_ref
                        cache[key] = frame
                finally:
                    self._prewarm_all_inflight = False

            threading.Thread(
                target=_work_pigeon, name="pigeon-submenu-prewarm", daemon=True
            ).start()
            return
        row_count = self._list_row_count()
        # Search results: focus ring is length 1 — prewarm selection rows instead.
        if row_count is not None:
            cur = self._current_list_row() or 0
            # Prefer rows ahead of the selection so the next Left/Right is ready first.
            order = list(range(cur + 1, row_count)) + list(range(0, cur))
            missing: list[int] = []
            for row in order:
                snap_probe = copy.deepcopy(st)
                self._apply_list_row(snap_probe, row)
                key = self._focus_key_for_state(snap_probe)
                if key not in self._focus_frame_cache:
                    missing.append(row)
            if not missing:
                return
            self._prewarm_all_inflight = True
            state_snap = copy.deepcopy(st)
            svg_path = self._svg_path
            assets_dir = self._assets_dir
            cache = self._focus_frame_cache
            struct_ref = structure

            def _work_rows() -> None:
                try:
                    for row in missing:
                        if self._focus_cache_structure not in (None, struct_ref):
                            return
                        self._apply_list_row(state_snap, row)
                        key = self._focus_key_for_state(state_snap)
                        if key in cache:
                            continue
                        try:
                            frame = render_main_settings_bgra(
                                state_snap,
                                svg_path=svg_path,
                                assets_dir=assets_dir,
                            )
                        except Exception:
                            return
                        if self._focus_cache_structure not in (None, struct_ref):
                            return
                        if self._focus_cache_structure is None:
                            self._focus_cache_structure = struct_ref
                        cache[key] = frame
                finally:
                    self._prewarm_all_inflight = False

            threading.Thread(
                target=_work_rows, name="pigeon-settings-prewarm-rows", daemon=True
            ).start()
            return

        if not st.focus_ring:
            return
        n = len(st.focus_ring)
        if n <= 1:
            return
        self._prewarm_all_inflight = True
        state_snap = copy.deepcopy(st)
        svg_path = self._svg_path
        assets_dir = self._assets_dir
        cache = self._focus_frame_cache
        struct_ref = structure

        def _work() -> None:
            try:
                for idx in range(n):
                    if self._focus_cache_structure not in (None, struct_ref):
                        return
                    state_snap.focus_index = idx
                    key = self._focus_key_for_state(state_snap)
                    if key in cache:
                        continue
                    try:
                        frame = render_main_settings_bgra(
                            state_snap,
                            svg_path=svg_path,
                            assets_dir=assets_dir,
                        )
                    except Exception:
                        return
                    if self._focus_cache_structure not in (None, struct_ref):
                        return
                    if self._focus_cache_structure is None:
                        self._focus_cache_structure = struct_ref
                    cache[key] = frame
            finally:
                self._prewarm_all_inflight = False

        threading.Thread(target=_work, name="pigeon-settings-prewarm-all", daemon=True).start()

    def _prewarm_neighbor_focus(self, *, forward: bool) -> None:
        """Rasterize the next focus/list-row target off-thread for the following keypress."""
        import copy
        import threading

        structure = self._structure_sig()
        if structure != self._focus_cache_structure:
            return
        st = self._state
        if st.keyboard_open:
            return

        if st.show_pigeon_settings and st.show_ui_color:
            from pigeon.widgets.ui_color_settings import (
                render_ui_color_settings_bgra,
                theme_from_color_keys,
                ui_color_class_focus_ring,
                ui_color_swatch_focus_ring,
            )

            if str(st.ui_color_nav or "") == "swatches":
                ring = ui_color_swatch_focus_ring(str(st.ui_color_active_class or ""))
            else:
                ring = ui_color_class_focus_ring()
            n = len(ring)
            if n <= 0:
                return
            nxt = (int(st.ui_color_focus_index) + (1 if forward else -1)) % n
            snap = copy.deepcopy(st)
            snap.ui_color_focus_index = nxt
            focused = ring[nxt]
            if str(snap.ui_color_nav or "") == "swatches":
                cls = str(snap.ui_color_active_class or "")
                if cls == "accent":
                    snap.ui_color_accent_key = focused
                elif cls == "ui":
                    snap.ui_color_ui_key = focused
                elif cls == "button":
                    snap.ui_color_button_key = focused
                snap.theme = theme_from_color_keys(
                    {
                        "accent": snap.ui_color_accent_key,
                        "ui": snap.ui_color_ui_key,
                        "button": snap.ui_color_button_key,
                    },
                    base=snap.theme,
                )
            key_probe = self._focus_key_for_state(snap)
            if key_probe in self._focus_frame_cache:
                return
            assets = self._assets_dir
            cache = self._focus_frame_cache
            struct_ref = structure

            def _work_ui_color_n() -> None:
                try:
                    frame = render_ui_color_settings_bgra(snap, assets_dir=assets)
                except Exception:
                    return
                if self._focus_cache_structure != struct_ref:
                    return
                cache.setdefault(key_probe, frame)

            threading.Thread(
                target=_work_ui_color_n, name="ui-color-prewarm-n", daemon=True
            ).start()
            return

        if st.show_pigeon_settings and st.show_preferences:
            from pigeon.widgets.preferences_settings import (
                preferences_widget_focus_ring,
                preferences_zone_focus_ring,
                render_preferences_settings_bgra,
            )

            if str(st.preferences_nav or "") == "widgets":
                ring = preferences_widget_focus_ring(int(st.preferences_active_zone))
            else:
                ring = preferences_zone_focus_ring()
            n = len(ring)
            if n <= 0:
                return
            nxt = (int(st.preferences_focus_index) + (1 if forward else -1)) % n
            snap = copy.deepcopy(st)
            snap.preferences_focus_index = nxt
            focused = ring[nxt]
            zone = int(snap.preferences_active_zone)
            if (
                str(snap.preferences_nav or "") == "widgets"
                and focused != "exit"
                and 1 <= zone <= 5
            ):
                widgets = list(snap.preferences_zone_widgets)
                widgets[zone - 1] = focused
                snap.preferences_zone_widgets = (
                    widgets[0],
                    widgets[1],
                    widgets[2],
                    widgets[3],
                    widgets[4],
                )
            key_probe = self._focus_key_for_state(snap)
            if key_probe in self._focus_frame_cache:
                return
            assets = self._assets_dir
            cache = self._focus_frame_cache
            struct_ref = structure

            def _work_prefs_n() -> None:
                try:
                    frame = render_preferences_settings_bgra(snap, assets_dir=assets)
                except Exception:
                    return
                if self._focus_cache_structure != struct_ref:
                    return
                cache.setdefault(key_probe, frame)

            threading.Thread(
                target=_work_prefs_n, name="prefs-prewarm-n", daemon=True
            ).start()
            return

        if st.show_pigeon_settings:
            from pigeon.widgets.pigeon_settings import pigeon_focus_ring, render_pigeon_settings_bgra

            ring = pigeon_focus_ring()
            n = len(ring)
            nxt = (int(st.pigeon_focus_index) + (1 if forward else -1)) % n
            snap = copy.deepcopy(st)
            snap.pigeon_focus_index = nxt
            key_probe = self._focus_key_for_state(snap)
            if key_probe in self._focus_frame_cache:
                return
            assets = self._assets_dir
            cache = self._focus_frame_cache
            struct_ref = structure

            def _work_pigeon_n() -> None:
                try:
                    frame = render_pigeon_settings_bgra(snap, assets_dir=assets)
                except Exception:
                    return
                if self._focus_cache_structure != struct_ref:
                    return
                cache.setdefault(key_probe, frame)

            threading.Thread(
                target=_work_pigeon_n, name="pigeon-submenu-prewarm-n", daemon=True
            ).start()
            return

        row_count = self._list_row_count()
        if row_count is not None:
            cur = self._current_list_row()
            if cur is None:
                return
            nxt = int(cur) + (1 if forward else -1)
            if nxt < 0 or nxt >= row_count:
                return
            snap = copy.deepcopy(st)
            self._apply_list_row(snap, nxt)
            key_probe = self._focus_key_for_state(snap)
            if key_probe in self._focus_frame_cache:
                return
            assets = self._assets_dir
            svg = self._svg_path
            cache = self._focus_frame_cache
            struct_ref = structure

            def _work_row() -> None:
                try:
                    frame = render_main_settings_bgra(snap, svg_path=svg, assets_dir=assets)
                except Exception:
                    return
                if self._focus_cache_structure != struct_ref:
                    return
                cache.setdefault(key_probe, frame)

            threading.Thread(
                target=_work_row, name="pigeon-settings-prewarm-row", daemon=True
            ).start()
            return

        if not st.focus_ring:
            return
        n = len(st.focus_ring)
        nxt = (int(st.focus_index) + (1 if forward else -1)) % n
        snap = copy.deepcopy(st)
        snap.focus_index = nxt
        key_probe = self._focus_key_for_state(snap)
        if key_probe in self._focus_frame_cache:
            return
        assets = self._assets_dir
        svg = self._svg_path
        cache = self._focus_frame_cache
        struct_ref = structure

        def _work() -> None:
            try:
                frame = render_main_settings_bgra(snap, svg_path=svg, assets_dir=assets)
            except Exception:
                return
            if self._focus_cache_structure != struct_ref:
                return
            cache.setdefault(key_probe, frame)

        threading.Thread(target=_work, name="pigeon-settings-prewarm", daemon=True).start()

    def prewarm_keyboard_focus(self) -> None:
        """Rasterize upcoming keyboard keys off-thread (all keys for small pads)."""
        import copy
        import threading

        if self._kb_prewarm_inflight:
            return
        st = self._state
        kb = st.keyboard
        if kb is None or not kb.focus_ring:
            return
        mode = getattr(kb, "mode", None)
        supports_lower = bool(getattr(kb, "supports_lowercase", True))
        kb_target = str(getattr(kb, "target", "") or "")
        if self._kb_cache_mode not in (None, mode):
            self._clear_keyboard_focus_caches()
        self._kb_cache_mode = mode
        n = len(kb.focus_ring)
        cur = int(kb.focus_index) % n
        # Small pads (PIN/IP/yes-no): warm everything. QWERTY: a short lookahead window.
        budget = n if n <= 16 else 5
        order = [(cur + i) % n for i in range(1, budget + 1)]
        missing = [
            idx
            for idx in order
            if self._keyboard_overlay_sig_for(
                mode,
                idx,
                supports_lowercase=supports_lower,
                target=kb_target,
            )
            not in self._kb_focus_frame_cache
        ]
        if not missing:
            return
        self._kb_prewarm_inflight = True
        assets = self._assets_dir
        cache = self._kb_focus_frame_cache
        mode_ref = mode
        lower_ref = supports_lower
        target_ref = kb_target

        def _work() -> None:
            try:
                if self._state.keyboard is None:
                    return
                live = self._state.keyboard
                if getattr(live, "mode", None) != mode_ref:
                    return
                if bool(getattr(live, "supports_lowercase", True)) != lower_ref:
                    return
                if str(getattr(live, "target", "") or "") != target_ref:
                    return
                snap = copy.deepcopy(live)
                from pigeon.widgets.settings_keyboard import render_keyboard_bgra

                for idx in missing:
                    if self._state.keyboard is None:
                        return
                    if getattr(self._state.keyboard, "mode", None) != mode_ref:
                        return
                    snap.focus_index = idx
                    key = self._keyboard_overlay_sig_for(
                        mode_ref,
                        idx,
                        supports_lowercase=lower_ref,
                        target=target_ref,
                    )
                    if key in cache:
                        continue
                    try:
                        frame = render_keyboard_bgra(snap, assets_dir=assets)
                    except Exception:
                        return
                    cache.setdefault(key, frame)
            finally:
                self._kb_prewarm_inflight = False

        threading.Thread(
            target=_work, name="pigeon-settings-prewarm-kb", daemon=True
        ).start()

    def _prewarm_neighbor_keyboard(self, *, forward: bool) -> None:
        """Rasterize the next keyboard key overlay off-thread."""
        import copy
        import threading

        kb = self._state.keyboard
        if kb is None or not kb.focus_ring:
            return
        mode = getattr(kb, "mode", None)
        supports_lower = bool(getattr(kb, "supports_lowercase", True))
        kb_target = str(getattr(kb, "target", "") or "")
        if self._kb_cache_mode not in (None, mode):
            return
        n = len(kb.focus_ring)
        nxt = (int(kb.focus_index) + (1 if forward else -1)) % n
        key = self._keyboard_overlay_sig_for(
            mode,
            nxt,
            supports_lowercase=supports_lower,
            target=kb_target,
        )
        if key in self._kb_focus_frame_cache:
            # Also warm one more step ahead when possible.
            nxt2 = (nxt + (1 if forward else -1)) % n
            key2 = self._keyboard_overlay_sig_for(
                mode,
                nxt2,
                supports_lowercase=supports_lower,
                target=kb_target,
            )
            if key2 in self._kb_focus_frame_cache:
                return
            nxt = nxt2
            key = key2
        snap = copy.deepcopy(kb)
        snap.focus_index = nxt
        assets = self._assets_dir
        cache = self._kb_focus_frame_cache
        mode_ref = mode
        lower_ref = supports_lower
        target_ref = kb_target

        def _work() -> None:
            try:
                from pigeon.widgets.settings_keyboard import render_keyboard_bgra

                frame = render_keyboard_bgra(snap, assets_dir=assets)
            except Exception:
                return
            if self._state.keyboard is None:
                return
            live = self._state.keyboard
            if getattr(live, "mode", None) != mode_ref:
                return
            if bool(getattr(live, "supports_lowercase", True)) != lower_ref:
                return
            if str(getattr(live, "target", "") or "") != target_ref:
                return
            cache.setdefault(key, frame)

        threading.Thread(
            target=_work, name="pigeon-settings-prewarm-kb-n", daemon=True
        ).start()

    def navigate_vertical(self, *, up: bool) -> None:
        """Vertical navigation disabled — use left/right only."""
        return

    def _any_box_scanning(self) -> bool:
        if self._state.keyboard_open:
            return False
        return bool(self._state.box2_devices.scanning or self._state.box3_devices.scanning)

    def tick(self) -> None:
        """Advance WiFi/box/location-switch animations; complete when platform scan returns."""
        st = self._state
        now = time.monotonic()
        dt = max(0.0, now - self._last_tick_mono)
        self._last_tick_mono = now
        invalidated = False
        if st.wifi_scanning or st.wifi_connecting:
            st.wifi_scan_angle_deg = (st.wifi_scan_angle_deg + _WIFI_SCAN_ROTATION_DPS * dt) % 360.0
        if st.location_switching:
            # Keep advancing once the delayed spinner is eligible so frames stay smooth.
            if st.location_switch_spinner_visible():
                prev = st.location_switch_angle_deg
                st.location_switch_angle_deg = (
                    st.location_switch_angle_deg + _LOCATION_SWITCH_ROTATION_DPS * dt
                ) % 360.0
                if int(round(prev / 10.0)) != int(round(st.location_switch_angle_deg / 10.0)):
                    invalidated = True
            elif (now - float(st.location_switch_started_mono)) >= float(
                _LOCATION_SWITCH_SPINNER_DELAY_S
            ):
                # Crossed the delay threshold — force a paint so the spinner appears.
                invalidated = True
        if st.wifi_scanning:
            elapsed = now - st.wifi_scan_started_mono
            result_ready = self._wifi_scan_result is not None
            timed_out = elapsed >= _WIFI_SCAN_MAX_DURATION_S
            if result_ready or timed_out:
                networks = self._wifi_scan_result if self._wifi_scan_result is not None else ()
                st.complete_wifi_scan(networks)
                if networks:
                    self._wifi_scan_cache = (networks, now)
                self._wifi_scan_result = None
                self._wifi_search_glyph_cache = None
                self._wifi_search_rotated_cache = None
                invalidated = True
        for box_num in (2, 3):
            panel = st._box_panel(box_num)
            pending = box_num in self._box_scan_pending
            if panel.scanning:
                panel.scan_angle_deg = (panel.scan_angle_deg + _BOX_SCAN_ROTATION_DPS * dt) % 360.0
            if not panel.scanning and not pending:
                continue
            elapsed = now - panel.scan_started_mono
            result = self._box_scan_result.get(box_num)
            result_ready = result is not None
            timed_out = elapsed >= _BOX_SCAN_MAX_DURATION_S
            if result_ready or timed_out:
                devices = result if result is not None else ()
                self._box_scan_result.pop(box_num, None)
                self._box_scan_pending.discard(box_num)
                self._clear_box_search_spinner_cache(box_num)
                if panel.scanning:
                    st.complete_box_device_scan(box_num, devices)
                    if devices:
                        self._box_scan_cache[box_num] = (*devices, now)
                    self._pre_scan_main_bgra.pop(box_num, None)
                elif devices:
                    # UI was aborted mid-scan — keep results for the next open.
                    self._box_scan_cache[box_num] = (*devices, now)
                invalidated = True
                self.invalidate()
        if invalidated and not (
            st.wifi_scanning
            or self._any_box_scanning()
            or st.location_switch_spinner_visible()
        ):
            self.invalidate()
        elif invalidated:
            # Spinner still animating — drop composed frames so the next blit rotates.
            self._cached_bgra = None
            self._cached_sig = None

    def _ensure_location_switch_spinner_frames(self) -> tuple[np.ndarray, ...] | None:
        if self._location_switch_spinner_frames is not None:
            return self._location_switch_spinner_frames
        if self._location_switch_spinner_tried:
            return None
        self._location_switch_spinner_tried = True
        try:
            from pigeon.widgets.search_spinner import build_search_spinner_frames

            frames = build_search_spinner_frames(self._assets_dir)
        except Exception:
            frames = None
        if frames:
            self._location_switch_spinner_frames = frames
        return self._location_switch_spinner_frames

    def _draw_location_switch_spinner(self, frame: np.ndarray) -> None:
        frames = self._ensure_location_switch_spinner_frames()
        if not frames:
            return
        from pigeon.widgets.search_spinner import blit_spinner_patch, rotated_patch_for_angle

        patch = rotated_patch_for_angle(frames, self._state.location_switch_angle_deg)
        blit_spinner_patch(
            frame,
            patch,
            cx=int(DESIGN_W) // 2,
            cy=int(DESIGN_H) // 2,
        )

    def _apply_location_switch_overlay(self, frame: np.ndarray) -> None:
        if not self._state.location_switch_spinner_visible():
            return
        self._draw_location_switch_spinner(frame)

    def _clear_box_search_spinner_cache(self, box_num: int) -> None:
        self._box_search_glyph_cache.pop(box_num, None)
        self._box_search_rotated_cache.pop(box_num, None)

    def _search_glyph_center_px(self) -> tuple[int, int]:
        return _svg_to_px(_WIFI_SEARCH_CENTER_SVG[0], _WIFI_SEARCH_CENTER_SVG[1])

    def _build_wifi_search_glyph_patch(self) -> np.ndarray | None:
        """Render glyph-only patch (transparent bg) for rotation during scan."""
        if self._svg_path is not None:
            path = Path(self._svg_path)
        else:
            path = default_main_settings_svg_path(self._assets_dir)
        if not path.is_file():
            return None
        root = _svg_tree_from_path(path)
        arc_specs = _discover_onboarding_search_arc_specs(root)
        triangle_specs = _discover_onboarding_search_triangle_specs(root)
        if not arc_specs and not triangle_specs:
            return None
        cx_px, cy_px = self._search_glyph_center_px()
        r = _WIFI_SEARCH_PATCH_RADIUS_PX
        x0 = max(0, cx_px - r)
        y0 = max(0, cy_px - r)
        patch = np.zeros((2 * r, 2 * r, 4), dtype=np.uint8)
        _draw_onboarding_search_arc_overlays_on_patch(
            patch, arc_specs, origin_x0=x0, origin_y0=y0, color_bgr=_WIFI_SEARCH_GLYPH_BGR
        )
        _draw_triangle_specs_on_patch(
            patch,
            triangle_specs,
            origin_x0=x0,
            origin_y0=y0,
            color_bgr=_WIFI_SEARCH_GLYPH_BGR,
        )
        return _mask_wifi_search_glyph_patch(patch)

    def _ensure_wifi_search_glyph_cache(self) -> None:
        if self._wifi_search_rotated_cache is not None:
            return
        patch = self._build_wifi_search_glyph_patch()
        if patch is not None:
            self._wifi_search_glyph_cache = patch
            self._wifi_search_rotated_cache = _precompute_rotated_patches(patch)

    def _cached_wifi_scan(self) -> tuple[str, ...] | None:
        cached = self._wifi_scan_cache
        if cached is None:
            return None
        networks, ts = cached
        if time.monotonic() - ts > _WIFI_SCAN_CACHE_TTL_S:
            self._wifi_scan_cache = None
            return None
        return networks

    def _cached_box_scan(self, box_num: int) -> tuple[tuple[tuple[str, str], ...], tuple[dict[str, str], ...]] | None:
        cached = self._box_scan_cache.get(box_num)
        if cached is None:
            return None
        display, rows, ts = cached
        if time.monotonic() - ts > _BOX_SCAN_CACHE_TTL_S:
            self._box_scan_cache.pop(box_num, None)
            return None
        return display, rows

    def _wifi_prefetch_needed(self) -> bool:
        return self._cached_wifi_scan() is None and not getattr(self, "_wifi_prefetch_inflight", False)

    def _box_prefetch_needed(self, box_num: int) -> bool:
        return (
            self._cached_box_scan(box_num) is None
            and box_num not in getattr(self, "_box_prefetch_inflight", set())
        )

    def prefetch_scans_for_settings(self) -> None:
        """Silent background scans so Space on WiFi/box buttons can finish from cache."""
        st = self._state
        if not st.wifi_configured:
            self._prefetch_wifi_into_cache()
        else:
            self._prefetch_box_into_cache(2)
            self._prefetch_box_into_cache(3)
        # Full-ring prewarm after the first live paint — avoid contending with cold SVG.
        self._want_prewarm_after_paint = True
        self._prewarm_neighbor_focus(forward=True)

    def prefetch_scans_for_focus(self, focused: str) -> None:
        """Prefetch when the user lands on a search-capable control."""
        if focused in ("main_dual_network_button", "main_box2_add_search_icon"):
            if not self._state.wifi_configured:
                self._prefetch_wifi_into_cache()
            return
        if focused == "main_box2_button":
            self._prefetch_box_into_cache(2)
        elif focused == "main_box3_button":
            self._prefetch_box_into_cache(3)

    def _prefetch_wifi_into_cache(self) -> None:
        if not self._wifi_prefetch_needed():
            return
        import threading

        self._wifi_prefetch_inflight = True

        def worker() -> None:
            try:
                from pigeon.wifi_scan import scan_wifi_networks

                result = scan_wifi_networks()
            except Exception:
                result = ()
            finally:
                self._wifi_prefetch_inflight = False
            if result:
                self._wifi_scan_cache = (result, time.monotonic())

        threading.Thread(target=worker, name="pigeon-wifi-prefetch", daemon=True).start()

    def _prefetch_box_into_cache(self, box_num: int) -> None:
        if box_num not in (2, 3) or not self._box_prefetch_needed(box_num):
            return
        import threading

        inflight = getattr(self, "_box_prefetch_inflight", None)
        if inflight is None:
            inflight = set()
            self._box_prefetch_inflight = inflight
        inflight.add(box_num)

        def worker() -> None:
            try:
                result = scan_lan_devices(box_num)
            except Exception:
                result = (), ()
            finally:
                self._box_prefetch_inflight.discard(box_num)
            display, rows = result
            if display or rows:
                self._box_scan_cache[box_num] = (*result, time.monotonic())

        threading.Thread(
            target=worker, name=f"pigeon-box{box_num}-prefetch", daemon=True
        ).start()

    def _start_wifi_scan_async(self) -> None:
        import threading

        def worker() -> None:
            try:
                from pigeon.wifi_scan import scan_wifi_networks

                result = scan_wifi_networks()
            except Exception:
                result = ()
            self._wifi_scan_result = result
            if result:
                self._wifi_scan_cache = (result, time.monotonic())

        self._wifi_scan_result = None
        threading.Thread(target=worker, daemon=True).start()

    def _start_box_device_scan_async(self, box_num: int) -> None:
        import threading

        def worker() -> None:
            try:
                result = scan_lan_devices(box_num)
            except Exception:
                result = (), ()
            self._box_scan_result[box_num] = result
            display, rows = result
            if display or rows:
                self._box_scan_cache[box_num] = (*result, time.monotonic())

        self._box_scan_result[box_num] = None
        threading.Thread(target=worker, daemon=True).start()

    def _begin_wifi_scan(self) -> None:
        st = self._state
        cached = self._cached_wifi_scan()
        if cached is not None:
            # Prefetch hit — open the picker immediately (no spinner).
            st.complete_wifi_scan(cached)
            self.invalidate()
            return
        st.show_network_picker = False
        st.wifi_networks = ()
        st.spinner_glyph_capture = True
        try:
            patch = self._build_wifi_search_glyph_patch()
            self._wifi_search_glyph_cache = patch
            self._wifi_search_rotated_cache = (
                _precompute_rotated_patches(patch) if patch is not None else None
            )
        finally:
            st.spinner_glyph_capture = False
        st.start_wifi_scan()
        self._start_wifi_scan_async()
        self.invalidate()

    def _start_box_device_scan(self, box_num: int) -> None:
        st = self._state
        cached = self._cached_box_scan(box_num)
        if cached is not None:
            # Prefetch hit — open results immediately (no spinner).
            if box_num == 2:
                st.show_box2_panel = True
            elif box_num == 3:
                st.show_box3_panel = True
            panel = st._box_panel(box_num)
            panel.active = True
            st.complete_box_device_scan(box_num, cached)
            self.invalidate()
            return
        # Snapshot the idle UI so abort can restore it without a mid-spin freeze frame.
        if self._cached_main_bgra is not None:
            self._pre_scan_main_bgra[box_num] = self._cached_main_bgra.copy()
        panel = st._box_panel(box_num)
        panel.active = True
        panel.phase = "scanning"
        if box_num == 2:
            st.show_box2_panel = True
        elif box_num == 3:
            st.show_box3_panel = True
        st.spinner_glyph_capture = True
        try:
            patch = self._build_box_search_glyph_patch(box_num)
            self._box_search_glyph_cache[box_num] = patch
            if patch is not None:
                self._box_search_rotated_cache[box_num] = _precompute_rotated_patches(patch)
            else:
                self._box_search_rotated_cache.pop(box_num, None)
        finally:
            st.spinner_glyph_capture = False
        st.start_box_device_scan(box_num)
        self._box_scan_pending.add(box_num)
        self._start_box_device_scan_async(box_num)

    def _abort_box_device_scan_ui(self, box_num: int) -> bool:
        """Dismiss the spinner immediately; restore prior device/idle state."""
        st = self._state
        self._clear_box_search_spinner_cache(box_num)
        if not st.abort_box_device_scan_ui(box_num):
            return False
        self._pre_scan_main_bgra.pop(box_num, None)
        # Force a full re-render from restored state (not a stale mid-scan frame).
        self._cached_kb_bgra = None
        self._cached_kb_sig = None
        self._cached_bgra = None
        self._cached_sig = None
        self._cached_main_bgra = None
        self._cached_main_sig = None
        self.invalidate()
        return True

    def _box_search_center_px(self, box_num: int) -> tuple[int, int]:
        cx, cy = box_search_center_svg(box_num)
        return _svg_to_px(cx, cy)

    def _build_box_search_glyph_patch(self, box_num: int) -> np.ndarray | None:
        if self._svg_path is not None:
            path = Path(self._svg_path)
        else:
            path = default_main_settings_svg_path(self._assets_dir)
        if not path.is_file():
            return None
        root = _svg_tree_from_path(path)
        arc_specs = _discover_box_search_arc_specs(root, box_num)
        triangle_specs = _discover_box_search_triangle_specs(root, box_num)
        if not arc_specs and not triangle_specs:
            return None
        cx_px, cy_px = self._box_search_center_px(box_num)
        r = _WIFI_SEARCH_PATCH_RADIUS_PX
        x0 = max(0, cx_px - r)
        y0 = max(0, cy_px - r)
        patch = np.zeros((2 * r, 2 * r, 4), dtype=np.uint8)
        color = _hex_to_bgr("#202020")
        _draw_onboarding_search_arc_overlays_on_patch(
            patch, arc_specs, origin_x0=x0, origin_y0=y0
        )
        _draw_triangle_specs_on_patch(
            patch, triangle_specs, origin_x0=x0, origin_y0=y0, color_bgr=color
        )
        return _mask_wifi_search_glyph_patch(patch)

    def _ensure_box_search_glyph_cache(self, box_num: int) -> None:
        if box_num in self._box_search_rotated_cache:
            return
        st = self._state
        st.spinner_glyph_capture = True
        try:
            patch = self._build_box_search_glyph_patch(box_num)
        finally:
            st.spinner_glyph_capture = False
        if patch is not None:
            self._box_search_glyph_cache[box_num] = patch
            self._box_search_rotated_cache[box_num] = _precompute_rotated_patches(patch)

    def _draw_box_search_spinner(self, frame: np.ndarray, box_num: int) -> None:
        rotated_frames = self._box_search_rotated_cache.get(box_num)
        if not rotated_frames:
            return
        panel = self._state._box_panel(box_num)
        cx, cy = self._box_search_center_px(box_num)
        patch = _rotated_patch_for_angle(rotated_frames, panel.scan_angle_deg)
        _blit_spinner_patch(frame, patch, cx=cx, cy=cy)

    def _draw_wifi_search_spinner(self, frame: np.ndarray) -> None:
        rotated_frames = self._wifi_search_rotated_cache
        if not rotated_frames:
            return
        cx, cy = self._search_glyph_center_px()
        patch = _rotated_patch_for_angle(rotated_frames, self._state.wifi_scan_angle_deg)
        _blit_spinner_patch(frame, patch, cx=cx, cy=cy)

    def _apply_box_search_overlays(self, frame: np.ndarray) -> None:
        if self._state.manual_device_entry is not None:
            return
        for box_num in (2, 3):
            panel = self._state._box_panel(box_num)
            if not panel.scanning:
                continue
            self._ensure_box_search_glyph_cache(box_num)
            self._draw_box_search_spinner(frame, box_num)

    def _apply_wifi_search_overlay(self, frame: np.ndarray) -> None:
        st = self._state
        if st.wifi_configured:
            return
        if st.wifi_scanning or st.wifi_connecting:
            self._ensure_wifi_search_glyph_cache()
            self._draw_wifi_search_spinner(frame)

    def activate(self) -> str:
        """Return an action string for the focused control."""
        st = self._state
        if st.keyboard is not None:
            from pigeon.widgets.settings_keyboard import activate_key

            result = activate_key(st.keyboard, assets_dir=self._assets_dir)
            if result == "typing":
                kb = st.keyboard
                if (
                    kb is not None
                    and str(getattr(kb, "target", "") or "") == "network"
                    and st.network_password_error
                ):
                    st.network_password_error = False
                    # Error chrome changed — rebuild chrome under the keyboard.
                    self._cached_kb_chrome_bgra = None
                    self._cached_kb_chrome_sig = None
                chrome_sig = self._keyboard_chrome_sig()
                if (
                    self._cached_kb_chrome_bgra is not None
                    and self._cached_kb_chrome_sig == chrome_sig
                ):
                    frame = self._main_with_text_from_chrome()
                    self._cached_main_bgra = frame
                    self._cached_main_sig = self._main_state_sig()
                    self._cached_bgra = None
                    self._kb_composed_cache.clear()
                    self._paste_fully_opaque = None
                else:
                    self._cached_main_bgra = None
                    self._cached_main_sig = None
                    self._cached_bgra = None
                    self._kb_composed_cache.clear()
            elif result.startswith("mode:"):
                # Keep main chrome; only rebuild keyboard overlays for the new layout.
                self._clear_keyboard_focus_caches()
                self._cached_bgra = None
                self._paste_fully_opaque = None
                self._want_prewarm_after_paint = True
            else:
                self.invalidate()
            if result == "cancel":
                st.close_keyboard(commit=False)
                if st.manual_device_entry is not None:
                    box_num = int(st.manual_device_entry.box_num)
                    st.manual_device_entry = None
                    st.reset_box_device_panel(box_num)
                    self._clear_box_search_spinner_cache(box_num)
                    st.ensure_focus_ring()
                if st.box_pairing is not None:
                    try:
                        from pigeon.apple_tv_now_playing import abandon_pairing_session

                        sk = st.box_pairing.session_key
                        if sk:
                            abandon_pairing_session(sk)
                    except Exception:
                        pass
                    st.clear_box_pairing()
                if st.renaming_location_id or st.show_location_picker:
                    st.renaming_location_id = ""
                    st.renaming_location_slot = 0
                    try:
                        from pigeon.app_state import read_current_location_name

                        st.location_name = read_current_location_name()
                    except Exception:
                        pass
                    if st.show_location_picker:
                        st.ensure_focus_ring()
                self.invalidate()
                return "keyboard_cancel"
            if result == "yes":
                st.close_keyboard(commit=False)
                self.invalidate()
                return "wifi_logout:yes"
            if result == "no":
                st.close_keyboard(commit=False)
                st.ensure_focus_ring()
                btn = "main_dual_network_button"
                if btn in st.focus_ring:
                    st.focus_index = st.focus_ring.index(btn)
                self.invalidate()
                return "wifi_logout:no"
            if result == "go":
                kb = st.keyboard
                pin_buf = ""
                kb_target = str(getattr(kb, "target", "") or "") if kb is not None else ""
                if kb is not None and kb_target == "pin":
                    pin_buf = "".join(c for c in str(getattr(kb, "buffer", "") or "") if c.isdigit())
                    if len(pin_buf) != 4:
                        return "keyboard_pin_incomplete"
                if kb is not None and kb_target == "network":
                    st.pending_network_password = str(getattr(kb, "buffer", "") or "")
                    st.close_keyboard(commit=False)
                    self.invalidate()
                    return "keyboard_go:network"
                if kb is not None and kb_target == "device_ip":
                    entry = st.manual_device_entry
                    if entry is None:
                        st.close_keyboard(commit=False)
                        self.invalidate()
                        return "keyboard_cancel"
                    entry.ip = str(getattr(kb, "buffer", "") or "").strip()
                    entry.step = "name"
                    st.close_keyboard(commit=False)
                    st.ensure_focus_ring()
                    self.invalidate()
                    return "manual_device_ip_done"
                if kb is not None and kb_target == "device_name":
                    entry = st.manual_device_entry
                    if entry is None:
                        st.close_keyboard(commit=False)
                        self.invalidate()
                        return "keyboard_cancel"
                    name = str(getattr(kb, "buffer", "") or "").strip()
                    st.close_keyboard(commit=False)
                    st.finish_manual_device_entry(
                        name=name,
                        ip=str(entry.ip or "").strip(),
                        ip_valid=True,
                    )
                    self.invalidate()
                    return "keyboard_go:device_name"
                target, _buf = st.close_keyboard(commit=True)
                self.invalidate()
                if target == "pin" and pin_buf:
                    return f"keyboard_pin:{pin_buf}"
                return f"keyboard_go:{target or ''}"
            return f"keyboard:{result}"

        if st.show_pigeon_settings:
            if st.show_metadata_debug:
                # EXIT is the only control — land back on the pigeon settings grid.
                st.close_metadata_debug()
                self.invalidate()
                return "metadata_debug_exit"
            if st.show_ui_color:
                action = st.activate_ui_color()
                self._cached_bgra = None
                self._cached_sig = None
                self._cached_main_bgra = None
                self._cached_main_sig = None
                self._paste_fully_opaque = None
                self._want_prewarm_after_paint = True
                self.invalidate()
                return action
            if st.show_preferences:
                action = st.activate_preferences()
                # Soft invalidate: keep neighbor focus bitmaps; drop current paste only.
                self._cached_bgra = None
                self._cached_sig = None
                self._cached_main_bgra = None
                self._cached_main_sig = None
                self._paste_fully_opaque = None
                self._want_prewarm_after_paint = True
                return action
            if st.show_update_popup:
                if st.update_applying or st.update_checking:
                    return "update_popup:busy"
                choice = st.update_popup_focused_choice
                if choice == "later":
                    st.close_update_popup()
                    self.invalidate()
                    return "update_popup:later"
                if st.update_available and not st.update_error:
                    return "update_popup:now"
                # Up-to-date (or check error): NOW acknowledges and closes.
                st.close_update_popup()
                self.invalidate()
                return "update_popup:dismiss"
            focused = st.pigeon_focused_id
            if focused == "pigeon_back":
                st.exit_pigeon_settings()
                self.invalidate()
                return "pigeon_settings_back"
            if focused == "update_button":
                st.open_update_popup()
                self.invalidate()
                return "update_popup:open"
            if focused == "color_button":
                st.close_preferences()
                st.open_ui_color()
                self.invalidate()
                return "ui_color_open"
            if focused == "reset_button":
                self.invalidate()
                return "pigeon_factory_reset"
            if focused == "info_button":
                st.open_preferences()
                self.invalidate()
                return "preferences_open"
            if focused == "general_button":
                # WIDGETS — opens the now-playing zone widget editor.
                st.open_preferences()
                self.invalidate()
                return "preferences_open"
            if focused in (
                "wifi_button",
                "metadata_button",
                "hdmi_button",
                "audio_button",
            ):
                kind = focused.removesuffix("_button")
                from pigeon.source_toggles import (
                    apply_toggles_to_settings_state,
                    toggle_source,
                )

                on = toggle_source(kind)
                apply_toggles_to_settings_state(st)
                if kind == "hdmi" and not on:
                    try:
                        from pigeon.hdmi_ocr import release_capture

                        release_capture()
                    except Exception:
                        pass
                self.invalidate()
                return f"source_toggle:{kind}:{int(on)}"
            return f"pigeon_activate:{focused}"

        focused = st.focused_id
        action = _ACTIVATE_ACTIONS.get(focused, f"activate:{focused}")
        if action == "exit" and st.show_location_picker:
            st.exit_location_picker()
            try:
                from pigeon.app_state import read_current_location_name

                st.location_name = read_current_location_name()
            except Exception:
                pass
            self.invalidate()
            return "location_picker_exit"
        if action == "focus_location":
            entry = st.manual_device_entry
            if entry is not None and entry.step == "name" and str(entry.ip or "").strip():
                st.open_keyboard("device_name", assets_dir=self._assets_dir)
                self.invalidate()
                return "keyboard_open:device_name"
            if st.show_location_picker:
                st.exit_location_picker()
                try:
                    from pigeon.app_state import read_current_location_name

                    st.location_name = read_current_location_name()
                except Exception:
                    pass
                self.invalidate()
                return "location_picker_exit"
            st.enter_location_picker()
            self.invalidate()
            return "location_picker"
        if action == "focus_network":
            if st.show_location_picker:
                if st.begin_rename_current_location():
                    st.open_keyboard(
                        "location",
                        assets_dir=self._assets_dir,
                        trigger_button="main_dual_location_button",
                    )
                    self.invalidate()
                    return "keyboard_open:location"
                return action
            if not st.wifi_configured:
                self._begin_wifi_scan()
                return "wifi_scan_start"
            st.open_keyboard("wifi_logout", assets_dir=self._assets_dir)
            self.invalidate()
            return "keyboard_open:wifi_logout"
        if action == "wifi_search":
            self._begin_wifi_scan()
            return "wifi_scan_start"
        if action == "focus_network_picker":
            rows = st.wifi_networks
            idx = st.network_picker_absolute_row
            ssid = rows[idx] if 0 <= idx < len(rows) else ""
            if ssid:
                st.select_wifi_network(ssid)
                self._wifi_search_glyph_cache = None
                self._wifi_search_rotated_cache = None
                st.open_keyboard(
                    "network",
                    assets_dir=self._assets_dir,
                    trigger_button="main_dual_network_button",
                )
                self.invalidate()
                return f"wifi_selected:{ssid}"
            return action
        if action == "focus_box1":
            if st.show_location_picker:
                if st.location_switching:
                    return "location_switch:busy"
                if st.select_location_slot(1):
                    self.invalidate()
                    return "location_switch"
                return action
            st.enter_pigeon_settings()
            self.invalidate()
            return "pigeon_settings"
        if action in ("pick_box2_device", "focus_box2"):
            if st.show_location_picker and action == "focus_box2":
                if st.location_switching:
                    return "location_switch:busy"
                if st.select_location_slot(2):
                    self.invalidate()
                    return "location_switch"
                return action
            if st.box2_devices.results_locked or action == "pick_box2_device":
                row = st.pick_box_device(2)
                if row is None:
                    self._clear_box_search_spinner_cache(2)
                if st.manual_device_entry is not None:
                    st.open_keyboard("device_ip", assets_dir=self._assets_dir)
                    self.invalidate()
                    return "keyboard_open:device_ip"
                if row:
                    st.start_box_pairing(2, row)
                    self.invalidate()
                    return "box2_pair_start"
                self.invalidate()
                return action
            if st.box2_devices.scanning:
                self._abort_box_device_scan_ui(2)
                return "box2_scan_abort"
            if st.box2_devices.phase != "scanning":
                self._start_box_device_scan(2)
                self.invalidate()
                return "box2_scan_start"
            return action
        if action in ("pick_box3_device", "focus_box3"):
            if st.show_location_picker and action == "focus_box3":
                if st.location_switching:
                    return "location_switch:busy"
                if st.select_location_slot(3):
                    self.invalidate()
                    return "location_switch"
                return action
            if st.box3_devices.results_locked or action == "pick_box3_device":
                row = st.pick_box_device(3)
                if row is None:
                    self._clear_box_search_spinner_cache(3)
                if st.manual_device_entry is not None:
                    st.open_keyboard("device_ip", assets_dir=self._assets_dir)
                    self.invalidate()
                    return "keyboard_open:device_ip"
                if row:
                    st.start_box_pairing(3, row)
                    self.invalidate()
                    return "box3_pair_start"
                self.invalidate()
                return action
            if st.box3_devices.scanning:
                self._abort_box_device_scan_ui(3)
                return "box3_scan_abort"
            if st.box3_devices.phase != "scanning":
                self._start_box_device_scan(3)
                self.invalidate()
                return "box3_scan_start"
            return action
        return action

    def bgra_frame(self) -> np.ndarray | None:
        try:
            st = self._state
            if st.show_pigeon_settings:
                main_sig = self._main_state_sig()
                structure = self._structure_sig()
                focus_key = self._focus_cache_key()
                if structure != self._focus_cache_structure:
                    self._focus_frame_cache.clear()
                    self._focus_cache_structure = structure
                if st.show_metadata_debug:
                    # Live debug page — render every frame signature change; the
                    # sig carries a 1s token so values stay fresh.
                    from pigeon.widgets.metadata_debug import (
                        render_metadata_debug_bgra,
                    )

                    if (
                        self._cached_main_bgra is not None
                        and self._cached_main_sig == main_sig
                    ):
                        frame = self._cached_main_bgra
                    else:
                        frame = render_metadata_debug_bgra(
                            st,
                            assets_dir=self._assets_dir,
                        )
                        self._cached_main_bgra = frame
                        self._cached_main_sig = main_sig
                elif st.show_ui_color:
                    from pigeon.widgets.ui_color_settings import (
                        render_ui_color_settings_bgra,
                    )

                    frame = render_ui_color_settings_bgra(
                        st,
                        assets_dir=self._assets_dir,
                    )
                    self._cached_main_bgra = frame
                    self._cached_main_sig = main_sig
                    self._store_focus_frame(frame)
                elif st.show_preferences:
                    # Prefs keeps a structure cache + live overlays; always go through
                    # render so HH:MM / NP progress stay fresh without per-second SVG.
                    from pigeon.widgets.preferences_settings import (
                        render_preferences_settings_bgra,
                    )

                    frame = render_preferences_settings_bgra(
                        st,
                        assets_dir=self._assets_dir,
                    )
                    self._cached_main_bgra = frame
                    self._cached_main_sig = main_sig
                    self._store_focus_frame(frame)
                elif focus_key in self._focus_frame_cache:
                    frame = self._focus_frame_cache[focus_key]
                    self._cached_main_bgra = frame
                    self._cached_main_sig = main_sig
                elif self._cached_main_bgra is not None and self._cached_main_sig == main_sig:
                    frame = self._cached_main_bgra
                else:
                    from pigeon.widgets.pigeon_settings import render_pigeon_settings_bgra

                    frame = render_pigeon_settings_bgra(
                        st,
                        assets_dir=self._assets_dir,
                    )
                    if st.show_update_popup:
                        from pigeon.widgets.update_popup import (
                            composite_update_popup_over_bgra,
                        )

                        frame = composite_update_popup_over_bgra(
                            frame,
                            st,
                            assets_dir=self._assets_dir,
                        )
                    self._cached_main_bgra = frame
                    self._cached_main_sig = main_sig
                    self._store_focus_frame(frame)
                self._cached_bgra = frame
                if self._want_prewarm_after_paint:
                    self._want_prewarm_after_paint = False
                    self.prewarm_focus_ring()
                return frame

            main_sig = self._main_state_sig()
            structure = self._structure_sig()
            focus_key = self._focus_cache_key()
            if structure != self._focus_cache_structure:
                self._focus_frame_cache.clear()
                self._focus_cache_structure = structure
            if focus_key in self._focus_frame_cache:
                frame = self._focus_frame_cache[focus_key]
                self._cached_main_bgra = frame
                self._cached_main_sig = main_sig
            elif self._cached_main_bgra is not None and self._cached_main_sig == main_sig:
                frame = self._cached_main_bgra
            elif st.keyboard is not None:
                chrome_sig = self._keyboard_chrome_sig()
                if (
                    self._cached_kb_chrome_bgra is not None
                    and self._cached_kb_chrome_sig == chrome_sig
                ):
                    frame = self._main_with_text_from_chrome()
                else:
                    chrome = render_main_settings_bgra(
                        self._state,
                        svg_path=self._svg_path,
                        assets_dir=self._assets_dir,
                        skip_text_entry=True,
                    )
                    self._cached_kb_chrome_bgra = chrome
                    self._cached_kb_chrome_sig = chrome_sig
                    frame = self._main_with_text_from_chrome()
                self._cached_main_bgra = frame
                self._cached_main_sig = main_sig
            else:
                frame = render_main_settings_bgra(
                    self._state,
                    svg_path=self._svg_path,
                    assets_dir=self._assets_dir,
                )
                self._cached_main_bgra = frame
                self._cached_main_sig = main_sig
                self._store_focus_frame(frame)

            if self._state.wifi_scanning or self._state.wifi_connecting:
                frame = frame.copy()
                self._apply_wifi_search_overlay(frame)

            if self._any_box_scanning():
                if not self._state.wifi_scanning and not self._state.wifi_connecting:
                    frame = frame.copy()
                self._apply_box_search_overlays(frame)

            if self._state.location_switch_spinner_visible():
                if not (
                    self._state.wifi_scanning
                    or self._state.wifi_connecting
                    or self._any_box_scanning()
                ):
                    frame = frame.copy()
                self._apply_location_switch_overlay(frame)

            if self._state.keyboard is not None:
                from pigeon.widgets.settings_keyboard import render_keyboard_bgra

                kb = self._state.keyboard
                mode = getattr(kb, "mode", None)
                if self._kb_cache_mode is None:
                    self._kb_cache_mode = mode
                elif self._kb_cache_mode != mode:
                    self._clear_keyboard_focus_caches()
                    self._kb_cache_mode = mode
                kb_sig = self._keyboard_overlay_sig()
                compose_key = (main_sig, kb_sig)
                if compose_key in self._kb_composed_cache:
                    frame = self._kb_composed_cache[compose_key]
                    self._cached_kb_bgra = self._kb_focus_frame_cache.get(kb_sig)  # type: ignore[arg-type]
                    self._cached_kb_sig = kb_sig
                else:
                    if (
                        kb_sig is not None
                        and self._cached_kb_bgra is not None
                        and self._cached_kb_sig == kb_sig
                    ):
                        kb_frame = self._cached_kb_bgra
                    elif kb_sig is not None and kb_sig in self._kb_focus_frame_cache:
                        kb_frame = self._kb_focus_frame_cache[kb_sig]
                        self._cached_kb_bgra = kb_frame
                        self._cached_kb_sig = kb_sig
                    else:
                        kb_frame = render_keyboard_bgra(
                            self._state.keyboard,
                            assets_dir=self._assets_dir,
                        )
                        self._cached_kb_bgra = kb_frame
                        self._cached_kb_sig = kb_sig
                        if kb_sig is not None:
                            self._store_kb_frame(kb_sig, kb_frame)
                    frame = self._compose_keyboard_over_main(frame, kb_frame, kb_sig)
                    if kb_sig is not None:
                        self._store_kb_composed(compose_key, frame)
        except Exception:
            self.invalidate()
            raise
        self._cached_bgra = frame
        if self._want_prewarm_after_paint and not st.show_pigeon_settings:
            self._want_prewarm_after_paint = False
            if st.keyboard is not None:
                self.prewarm_keyboard_focus()
            else:
                self.prewarm_focus_ring()
        elif st.keyboard is not None and not self._kb_prewarm_inflight:
            # Keep a lookahead window warm while browsing keys.
            if len(self._kb_focus_frame_cache) < 3:
                self.prewarm_keyboard_focus()
        return frame

    def render(self, canvas_bgr: np.ndarray) -> None:
        """Paste main settings onto the design canvas (800×480, uniform scale only)."""
        self.tick()
        frame = self.bgra_frame()
        if frame is None or canvas_bgr is None or canvas_bgr.size == 0:
            return
        if self._state.keyboard is not None:
            frame = frame.copy()
            _draw_text_entry_cursor(frame, self._state)
            self._paste_fully_opaque = None
        ch, cw = int(canvas_bgr.shape[0]), int(canvas_bgr.shape[1])
        fh, fw = int(frame.shape[0]), int(frame.shape[1])
        if fh == ch and fw == cw:
            alpha_u8 = frame[:, :, 3]
            # Settings is usually fully opaque over black — avoid full-frame float blend.
            opaque = self._paste_fully_opaque
            if opaque is None:
                opaque = int(alpha_u8.min()) == 255
                self._paste_fully_opaque = opaque
            if opaque:
                canvas_bgr[:] = frame[:, :, :3]
            else:
                canvas_bgr[:] = alpha_blend_bgra_over_bgr(canvas_bgr, frame)
            return
        scale = min(cw / float(fw), ch / float(fh))
        tw = max(1, int(round(fw * scale)))
        th = max(1, int(round(fh * scale)))
        resized = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
        x0 = max(0, (cw - tw) // 2)
        y0 = max(0, (ch - th) // 2)
        roi = canvas_bgr[y0 : y0 + th, x0 : x0 + tw]
        alpha_u8 = resized[:, :, 3]
        if int(alpha_u8.min()) == 255:
            roi[:] = resized[:, :, :3]
        else:
            roi[:] = alpha_blend_bgra_over_bgr(roi, resized)

    # Alias used by ``pigeon_0_9`` composite paths.
    render_on_bgr = render


__all__ = [
    "COLOR_ACCENT_DEFAULT",
    "COLOR_DESELECTED",
    "COLOR_INACTIVE",
    "COLOR_SELECTED",
    "COLOR_UI_DEFAULT",
    "DESIGN_H",
    "DESIGN_W",
    "KEYBOARD_NUMERIC_IP_SVG",
    "KEYBOARD_SVG_NAMES",
    "MainSettingsFocus",
    "MainSettingsState",
    "MainSettingsWidget",
    "SettingsTheme",
    "apply_main_settings_svg_state",
    "decode_svg_id",
    "default_main_settings_svg_path",
    "encode_svg_id",
    "keyboard_svg_path",
    "render_main_settings_bgra",
]
