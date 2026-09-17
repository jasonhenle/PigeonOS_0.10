"""1280×800 settings zone geometry and shared asset paths.

Illustrator boards are 2365.03×2422.04. The live canvas is the 1280×800
rect at ``SETTINGS_CANVAS_ORIGIN``. Widget-local viewBoxes stay as exported.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pigeon.design import DESIGN_H, DESIGN_W

# Full-board origin of the 1280×800 artboard inside settings SVG exports.
SETTINGS_CANVAS_ORIGIN = (581.39, 673.72)
SETTINGS_CANVAS_W = float(DESIGN_W)
SETTINGS_CANVAS_H = float(DESIGN_H)

# Rounded menu plate that clips the hue slants (board space → canvas).
# Path starts at the top straight edge (x + r), so the AABB includes both radii.
_MENU_CLIP_TOP_STRAIGHT = (648.66, 882.51)
_MENU_CLIP_STRAIGHT_W = 1143.85
_MENU_CLIP_INNER_H = 441.41
MENU_PLATE_RADIUS = 31.64
# Nudge the settings plate/slants up without moving widgets.
SETTINGS_BACKGROUND_SHIFT_Y = -20
# Grow the plate down so the NP status-bar track sits inside it. Service text
# stays in the black field below. Columns/keyboard still use the original clip.
SETTINGS_PLATE_STATUS_BAR_PAD = 30.0
MENU_PLATE_XYWH = (
    _MENU_CLIP_TOP_STRAIGHT[0] - MENU_PLATE_RADIUS - SETTINGS_CANVAS_ORIGIN[0],
    _MENU_CLIP_TOP_STRAIGHT[1] - SETTINGS_CANVAS_ORIGIN[1] + SETTINGS_BACKGROUND_SHIFT_Y,
    _MENU_CLIP_STRAIGHT_W + 2.0 * MENU_PLATE_RADIUS,
    _MENU_CLIP_INNER_H + 2.0 * MENU_PLATE_RADIUS + SETTINGS_PLATE_STATUS_BAR_PAD,
)


def menu_plate_chrome_bottom() -> float:
    """Bottom of the original menu clip — columns and keyboard sit above this."""
    return float(
        MENU_PLATE_XYWH[1] + MENU_PLATE_XYWH[3] - SETTINGS_PLATE_STATUS_BAR_PAD
    )


# Settings-main zones (own numbering, not now-playing).
# zone0: PDF 125×45 at 55,94 — exit SVG artboard is 125×90.
# zone1: dual bar. zone2–4: pigeon / player / audio columns.
_DUAL_W, _DUAL_H = 1165.0, 105.0
# Dual bottom is 305; chrome bottom is ~693. Keep list arrows inside that clip.
_COL_W, _COL_H = 375.0, 360.0
_COL_Y = 316.0
_COL_XS = (67.0, 452.0, 838.0)


@dataclass(frozen=True)
class SettingsZone:
    index: int
    x: float
    y: float
    w: float
    h: float

    @property
    def xywh(self) -> tuple[int, int, int, int]:
        return (
            int(round(self.x)),
            int(round(self.y)),
            int(round(self.w)),
            int(round(self.h)),
        )


SETTINGS_MAIN_ZONES: dict[int, SettingsZone] = {
    0: SettingsZone(0, 55.0, 94.0, 125.0, 90.0),
    1: SettingsZone(1, (DESIGN_W - _DUAL_W) * 0.5, 200.0, _DUAL_W, _DUAL_H),
    2: SettingsZone(2, _COL_XS[0], _COL_Y, _COL_W, _COL_H),
    3: SettingsZone(3, _COL_XS[1], _COL_Y, _COL_W, _COL_H),
    4: SettingsZone(4, _COL_XS[2], _COL_Y, _COL_W, _COL_H),
}

# Dual inner pills (widget-local, widget_sm_01_dual.svg).
DUAL_SLOT_A = (20.16, 20.6, 553.61, 63.81)
DUAL_SLOT_B = (594.33, 20.6, 552.3, 63.81)

BACKGROUND_BRIGHTNESS: dict[str, float] = {
    "_85_color": 0.85,
    "_75_Color": 0.75,
    "_75_color": 0.75,
    "_65_color": 0.65,
    "_55_color": 0.55,
    "_45_color": 0.45,
    "_35_color": 0.35,
}

WIDGET_FILES: dict[str, tuple[str, ...]] = {
    "background": ("general", "widget_general_settings_background.svg"),
    "exit": ("general", "widget_general_0_exit.svg"),
    "location_icon": ("general", "widget_general_location_icon.svg"),
    "search": ("general", "widget_general_searching_icon.svg"),
    "dual": ("main", "widget_sm_01_dual.svg"),
    "location": ("main", "widget_sm_01-a_location.svg"),
    "network": ("main", "widget_sm_01-b_network_name.svg"),
    "password": ("main", "widget_sm_01-b_network_password.svg"),
    "pigeon": ("main", "widget_sm_02_pigeon.svg"),
    "pigeon_logo": ("main", "widget_sm_02_pigeon.png"),
    "list": ("main", "widget_sm_02-03-04_list_a-e.svg"),
    "list_rows": ("main", "widget_sm_02-03-04_a-e_ip_and_device.svg"),
    "column_container": ("main", "widget_sm_02-03-04_container.svg"),
    "device_info": ("main", "widget_sm_03-04_device_info.svg"),
    "add_player": ("main", "widget_sm_03_add_player.svg"),
    "add_audio": ("main", "widget_sm_04_add_audio.svg"),
    "pigeon_page": ("pigeon", "settings_pigeon.svg"),
    "ui_color_bar": ("pigeon", "widget_sp_ui_color_zone0.svg"),
    "options_bar": ("pigeon", "widget_sp_options.svg"),
    "widgets_page": ("pigeon", "settings_pigeon_widgets.svg"),
}


def settings_assets_root(assets_dir: Path | str | None = None) -> Path:
    if assets_dir is not None:
        root = Path(assets_dir)
        direct = root / "settings"
        if direct.is_dir():
            return direct
        return root
    return Path(__file__).resolve().parents[2] / "pigeonAssets" / "settings"


def settings_widget_path(key: str, *, assets_dir: Path | str | None = None) -> Path:
    folder, name = WIDGET_FILES[key]
    return settings_assets_root(assets_dir) / folder / name


def zone_center_rect(
    zone: SettingsZone, view_w: float, view_h: float
) -> tuple[int, int, int, int]:
    """Contain-fit ``view_w``×``view_h`` inside ``zone``, centered."""
    if view_w <= 0 or view_h <= 0:
        return zone.xywh
    scale = min(zone.w / view_w, zone.h / view_h)
    w = max(1, int(round(view_w * scale)))
    h = max(1, int(round(view_h * scale)))
    x = int(round(zone.x + (zone.w - w) * 0.5))
    y = int(round(zone.y + (zone.h - h) * 0.5))
    return x, y, w, h


def dual_slot_design(slot: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """Map a dual-local pill onto the design canvas."""
    zone = SETTINGS_MAIN_ZONES[1]
    sx = zone.w / _DUAL_W
    sy = zone.h / _DUAL_H
    lx, ly, lw, lh = slot
    return (
        int(round(zone.x + lx * sx)),
        int(round(zone.y + ly * sy)),
        max(1, int(round(lw * sx))),
        max(1, int(round(lh * sy))),
    )
