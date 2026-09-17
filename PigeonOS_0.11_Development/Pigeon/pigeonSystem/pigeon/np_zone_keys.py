"""Temporary 1–8 now-playing zone cycling (persisted per video/music layout).

Repeat presses cycle that zone's catalog, wrapping through off. Turning a
zone on disables occupancy collisions (``NOW_PLAYING_ZONES`` restrictions)
so the most recently toggled zone wins. Zone 8 is the top header clock and
does not collide with zones 1–7 (it sits on top of the TT / album row).
"""

from __future__ import annotations

from pigeon.np_layout import (
    NOW_PLAYING_ZONES,
    TT_COUNTDOWN_16X9_WIDGET,
    apply_tt_countdown_16x9_override,
    tt_countdown_16x9_zone,
)

TT_WIDE = TT_COUNTDOWN_16X9_WIDGET
HEADER = "header_clock"

VIDEO_CYCLE: dict[int, tuple[str, ...]] = {
    1: (
        "",
        "tt_countdown",
        "clock",
        "poster",
        "volume",
        "cast_info",
        "now_playing",
        "audio_levels",
    ),
    2: (
        "",
        "tt_countdown",
        "clock",
        "poster",
        "volume",
        "cast_info",
        "now_playing",
        "audio_levels",
    ),
    3: (
        "",
        "volume",
        "clock",
        "poster",
        "tt_countdown",
        "cast_info",
        "now_playing",
        "audio_levels",
    ),
    4: ("", "cast_info", "clock_saver_volume"),
    5: ("", "status_bar", "cast_info"),
    6: ("", TT_WIDE),
    7: ("", TT_WIDE),
    8: ("", HEADER),
}

MUSIC_CYCLE: dict[int, tuple[str, ...]] = {
    1: (
        "",
        "tt_countdown",
        "clock",
        "poster",
        "volume",
        "cast_info",
        "now_playing",
        "audio_levels",
    ),
    2: (
        "",
        "tt_countdown",
        "clock",
        "poster",
        "volume",
        "cast_info",
        "now_playing",
        "audio_levels",
    ),
    3: ("", "volume", "clock_saver_volume", "clock", "poster", "tt_countdown"),
    4: ("", "cast_info", "clock_saver_volume"),
    5: ("", "status_bar"),
    6: ("", TT_WIDE),
    7: ("", TT_WIDE),
    8: ("", HEADER),
}


def cycle_catalog(zone: int, *, content_mode: str = "") -> tuple[str, ...]:
    table = MUSIC_CYCLE if str(content_mode or "").strip().lower() == "music" else VIDEO_CYCLE
    return table.get(int(zone), ("",))


def next_cycle_widget(cycle: tuple[str, ...], current: str) -> str:
    names = list(cycle) or [""]
    cur = str(current or "")
    try:
        i = names.index(cur)
    except ValueError:
        i = 0
    return names[(i + 1) % len(names)]


def zone_current_widget(
    assignments: tuple[str, ...] | list[str],
    zone: int,
    *,
    header_clock: bool = True,
) -> str:
    """Widget currently occupying ``zone``, or ``\"\"`` if off / covered."""
    z = int(zone)
    if z == 8:
        return HEADER if header_clock else ""
    a = list(assignments) + [""] * 5
    a = a[:5]
    wide = tt_countdown_16x9_zone(a)
    if z == 6:
        return TT_WIDE if wide == 6 else ""
    if z == 7:
        return TT_WIDE if wide == 7 else ""
    if not (1 <= z <= 5):
        return ""
    if wide == 6 and z in (1, 2):
        return ""
    if wide == 7 and z in (2, 3):
        return ""
    return str(a[z - 1] or "")


def _clear_zone(
    a: list[str], header_clock: bool, zone: int
) -> tuple[list[str], bool]:
    z = int(zone)
    if z == 8:
        return a, False
    if z == 6:
        if tt_countdown_16x9_zone(a) == 6:
            for i in range(3):
                if a[i] == TT_WIDE:
                    a[i] = ""
                    break
        return a, header_clock
    if z == 7:
        if tt_countdown_16x9_zone(a) == 7:
            a[2] = ""
        return a, header_clock
    if 1 <= z <= 5:
        a[z - 1] = ""
    return a, header_clock


def _disable_collisions(
    a: list[str], header_clock: bool, zone: int
) -> tuple[list[str], bool]:
    spec = NOW_PLAYING_ZONES.get(int(zone))
    if spec is None:
        return a, header_clock
    hc = header_clock
    for other in spec.restrictions:
        # Header clock (zone 8) is overlay chrome; it coexists with TT / album.
        if int(other) == 8:
            continue
        a, hc = _clear_zone(a, hc, int(other))
    return a, hc


def _set_zone(
    a: list[str], header_clock: bool, zone: int, widget: str
) -> tuple[list[str], bool]:
    z = int(zone)
    name = str(widget or "")
    hc = header_clock
    if z == 8:
        return a, name == HEADER
    if name:
        a, hc = _disable_collisions(a, hc, z)
    if z == 6:
        if name == TT_WIDE:
            a[0] = TT_WIDE
            a[1] = ""
            if a[2] == TT_WIDE:
                a[2] = ""
        else:
            a, hc = _clear_zone(a, hc, 6)
        return a, hc
    if z == 7:
        if name == TT_WIDE:
            a[2] = TT_WIDE
            a[1] = ""
            if a[0] == TT_WIDE:
                a[0] = ""
        else:
            a, hc = _clear_zone(a, hc, 7)
        return a, hc
    if 1 <= z <= 5:
        a[z - 1] = name
    return a, hc


def cycle_now_playing_zone(
    assignments: tuple[str, ...] | list[str],
    zone: int,
    *,
    content_mode: str = "",
    header_clock: bool = True,
) -> tuple[tuple[str, str, str, str, str], bool]:
    """Advance ``zone`` one step. Returns ``(assignments, header_clock)``."""
    z = int(zone)
    a = list(assignments) + [""] * 5
    a = a[:5]
    current = zone_current_widget(a, z, header_clock=header_clock)
    nxt = next_cycle_widget(cycle_catalog(z, content_mode=content_mode), current)
    a, hc = _set_zone(a, header_clock, z, nxt)
    norm = apply_tt_countdown_16x9_override((a[0], a[1], a[2], a[3], a[4]))
    return norm, bool(hc)
