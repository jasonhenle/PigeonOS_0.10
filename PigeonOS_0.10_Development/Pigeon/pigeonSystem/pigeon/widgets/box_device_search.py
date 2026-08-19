"""Box2/box3 LAN device discovery UI state (search icon → results list)."""

from __future__ import annotations

from dataclasses import dataclass

_BOX_DEVICE_ROW_COUNT = 5
_BOX_SEARCH_CENTERS_SVG: dict[int, tuple[float, float]] = {
    2: (399.32, 309.323),
    3: (644.32, 309.323),
}
_BOX_SCAN_MAX_DURATION_S = 25.0
_BOX_SCAN_ROTATION_DPS = 540.0

BOX_DEVICE_ROW_CANCEL = "CANCEL"
BOX_DEVICE_ROW_ENTER_IP = "ENTER IP"


@dataclass
class ManualDeviceEntry:
    """Manual IP + name entry for box2/box3 (ENTER IP row)."""

    box_num: int
    step: str = "ip"  # ip | name
    ip: str = ""
    name: str = ""
    ip_valid: bool = True


@dataclass
class BoxDevicePanelState:
    """Per-column device search + results picker."""

    active: bool = False
    phase: str = "idle"  # idle | scanning | results
    scanning: bool = False
    scan_started_mono: float = 0.0
    scan_angle_deg: float = 0.0
    devices: tuple[tuple[str, str], ...] = ()  # (name, ip)
    device_rows: tuple[dict[str, str], ...] = ()
    scroll: int = 0
    row: int = 0
    arrow: str = ""  # "up" | "down" | ""
    picked: tuple[str, str] | None = None

    @property
    def results_locked(self) -> bool:
        return self.active and self.phase == "results"

    def max_scroll(self) -> int:
        return max(0, len(self.devices) - _BOX_DEVICE_ROW_COUNT)

    def visible_device_count(self) -> int:
        return len(self.devices)

    def can_scroll_up(self) -> bool:
        return int(self.scroll) > 0

    def can_scroll_down(self) -> bool:
        return int(self.scroll) < self.max_scroll()

    def absolute_row(self, visible_row: int | None = None) -> int:
        r = int(self.row) if visible_row is None else int(visible_row)
        return int(self.scroll) + r


def box_search_center_svg(box_num: int) -> tuple[float, float]:
    return _BOX_SEARCH_CENTERS_SVG.get(box_num, (400.0, 309.323))


def box_devices_with_special_rows(
    devices: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    """Append CANCEL and ENTER IP rows after discovered LAN devices."""
    extra = ((BOX_DEVICE_ROW_ENTER_IP, ""), (BOX_DEVICE_ROW_CANCEL, ""))
    if not devices:
        return extra
    return tuple(devices) + extra


def is_special_device_row(name: str) -> bool:
    return name in (BOX_DEVICE_ROW_CANCEL, BOX_DEVICE_ROW_ENTER_IP)


def device_row_label(name: str, ip: str) -> str:
    return str(name or "").strip().upper()


def scan_lan_devices(box_num: int = 2) -> tuple[tuple[tuple[str, str], ...], tuple[dict[str, str], ...]]:
    """Return display names + full pyatv rows for a box column (AV-filtered)."""
    from pigeon.widgets.box_device_pairing import scan_devices_for_box

    return scan_devices_for_box(box_num)


def device_row_from_pick(
    panel_devices: tuple[tuple[str, str], ...],
    device_rows: tuple[dict[str, str], ...],
    *,
    scroll: int,
    row: int,
) -> dict[str, str] | None:
    idx = int(scroll) + int(row)
    if 0 <= idx < len(panel_devices):
        name, ip = panel_devices[idx]
        if name == BOX_DEVICE_ROW_CANCEL:
            return {"special": "cancel", "name": name, "address": ""}
        if name == BOX_DEVICE_ROW_ENTER_IP:
            return {"special": "enter_ip", "name": name, "address": ""}
    if 0 <= idx < len(device_rows):
        return dict(device_rows[idx])
    if 0 <= idx < len(panel_devices):
        name, ip = panel_devices[idx]
        return {
            "identifier": ip,
            "address": ip,
            "name": name,
            "label": f"{name} — {ip}",
            "looks_like_apple_tv": "true",
        }
    return None


__all__ = [
    "BoxDevicePanelState",
    "ManualDeviceEntry",
    "BOX_DEVICE_ROW_CANCEL",
    "BOX_DEVICE_ROW_ENTER_IP",
    "_BOX_DEVICE_ROW_COUNT",
    "box_devices_with_special_rows",
    "box_search_center_svg",
    "device_row_from_pick",
    "is_special_device_row",
    "scan_lan_devices",
]
