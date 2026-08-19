"""Queue remote commands to the active Player (Apple TV via pyatv, Roku via ECP)."""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from typing import Any

from pigeon.app_state import row_is_playback_apple_tv


def roku_send_ecp_keypress(*, base_url: str, key: str, timeout: float = 3.0) -> tuple[bool, str]:
    """POST ``/keypress/{key}`` (e.g. ``Up``, ``VolumeUp``, ``Back``)."""
    from pigeon.roku_ecp import normalize_roku_ecp_base

    base = normalize_roku_ecp_base(base_url) if (base_url or "").strip() else ""
    if not base:
        return False, "No Roku ECP URL."
    k = (key or "").strip()
    if not k:
        return False, "Empty Roku key."
    url = f"{base.rstrip('/')}/keypress/{k}"
    req = urllib.request.Request(
        url,
        method="POST",
        data=b"",
        headers={"User-Agent": "Pigeon/0.5 (Roku ECP)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status not in (200, 204):
                return False, f"Roku keypress failed (HTTP {resp.status})."
        return True, f"Roku: {k}"
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        return False, str(e)


_ROKU_ACTION_KEYS: dict[str, str] = {
    "volume_up": "VolumeUp",
    "volume_down": "VolumeDown",
    "nav_up": "Up",
    "nav_down": "Down",
    "nav_left": "Left",
    "nav_right": "Right",
    "select": "Select",
    "skip_back": "Rev",
    "skip_fwd": "Fwd",
    "back": "Back",
    "home": "Home",
    "power_on": "PowerOn",
    "power_off": "PowerOff",
}


def queue_player_remote_action(
    stream_row: dict[str, str] | None,
    *,
    current_apple_tv: dict[str, Any],
    action: str,
    apple_tv_busy: dict[str, bool] | None = None,
) -> bool:
    """
    Fire-and-forget remote command for the current Player row.

    ``action`` is a logical name (``volume_up``, ``nav_left``, ``skip_back``, …).
    Returns True if a remote command was queued (Apple TV) or a Roku worker was started.
    """
    if not stream_row:
        return False
    if apple_tv_busy is not None and apple_tv_busy.get("active"):
        return False

    act = (action or "").strip().lower()
    if not act:
        return False

    if row_is_playback_apple_tv(stream_row):
        ident = str(current_apple_tv.get("identifier") or "").strip() or str(
            stream_row.get("identifier") or ""
        ).strip()
        addr = str(current_apple_tv.get("address") or "").strip() or str(
            stream_row.get("address") or ""
        ).strip()
        if not ident:
            return False
        if not addr:
            addr = ident
        pyatv_map: dict[str, str] = {
            "volume_up": "volume_up",
            "volume_down": "volume_down",
            "nav_up": "up",
            "nav_down": "down",
            "nav_left": "left",
            "nav_right": "right",
            "select": "select",
            "skip_back": "skip_backward",
            "skip_fwd": "skip_forward",
            "back": "menu",
            "home": "home",
            "power_on": "wakeup",
            "power_off": "suspend",
        }
        mname = pyatv_map.get(act)
        if not mname:
            return False
        try:
            from pigeon.apple_tv_now_playing import enqueue_apple_tv_remote_command

            return enqueue_apple_tv_remote_command(
                device_identifier=ident,
                device_address=addr,
                method_name=mname,
                scan_timeout_s=3,
            )
        except Exception:
            return False

    try:
        from pigeon.roku_ecp import resolve_roku_ecp_base_url_for_row
    except Exception:
        return False

    rbase = str(resolve_roku_ecp_base_url_for_row(stream_row) or "").strip()
    if not rbase:
        return False
    rkey = _ROKU_ACTION_KEYS.get(act)
    if not rkey:
        return False

    def _rok() -> None:
        try:
            roku_send_ecp_keypress(base_url=rbase, key=rkey, timeout=3.0)
        except Exception:
            pass

    threading.Thread(target=_rok, daemon=True).start()
    return True
