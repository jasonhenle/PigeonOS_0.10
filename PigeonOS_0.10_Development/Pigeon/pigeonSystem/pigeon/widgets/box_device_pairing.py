"""Box2 (player) / box3 (receiver) pick → pair helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BoxPairingSession:
    """In-flight pyatv pairing after a box results pick."""

    box_num: int
    row: dict[str, str]
    step: str = ""  # remote_pin | airplay_pin
    session_key: str = ""
    device_name: str = ""
    pin_buffer: str = ""

    @property
    def expects_pin(self) -> bool:
        return self.step in ("remote_pin", "airplay_pin")


def scan_devices_for_box(box_num: int, *, scan_timeout_s: int = 12) -> tuple[tuple[tuple[str, str], ...], tuple[dict[str, str], ...]]:
    """
    LAN scan filtered for box role.

    Box2 → Apple TV / tvOS players (Companion/MRP).
    Box3 → AirPlay AV receivers (non-tvOS).
    """
    try:
        from pigeon.apple_tv_now_playing import scan_apple_tv_devices
        from pigeon.app_state import (
            filter_discovery_for_receiver,
            filter_discovery_for_streaming,
        )
    except ImportError:
        return (), ()

    _ok, _msg, rows = scan_apple_tv_devices(scan_timeout_s=scan_timeout_s)
    raw = [dict(r) for r in (rows or [])]
    if box_num == 2:
        filtered = filter_discovery_for_streaming(raw)
    elif box_num == 3:
        filtered = filter_discovery_for_receiver(raw)
    else:
        filtered = raw

    display: list[tuple[str, str]] = []
    out_rows: list[dict[str, str]] = []
    for row in filtered:
        name = str(row.get("name") or row.get("label") or "device").strip()
        ip = str(row.get("address") or "").strip()
        if not name and not ip:
            continue
        display.append((name or ip, ip or name))
        out_rows.append(row)
    return tuple(display), tuple(out_rows)


def device_row_from_pick(
    panel_devices: tuple[tuple[str, str], ...],
    device_rows: tuple[dict[str, str], ...],
    *,
    scroll: int,
    row: int,
) -> dict[str, str] | None:
    idx = int(scroll) + int(row)
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
    "BoxPairingSession",
    "device_row_from_pick",
    "scan_devices_for_box",
]
