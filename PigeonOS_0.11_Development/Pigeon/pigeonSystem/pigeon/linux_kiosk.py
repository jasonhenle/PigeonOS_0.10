"""Keep Pigeon over the whole Linux display with no PiOS chrome.

labwc / wf-panel-pi reserve a top strip (~62 px). Tk ``-fullscreen`` then
maximizes into the leftover 1280×738 work area, so PiOS chrome stays visible
and the bottom of the 800 px UI is clipped. Stopping the panel and forcing
the window to ``0,0`` × screen size covers the display.

While Pigeon is running this module also:
- SIGSTOPs the desktop, on-screen keyboard, and notification daemons
- closes Chromium / Firefox / PiOS dialogs that steal the screen
- hides the pointer (Tk + X11 + a blank cursor theme)
"""

from __future__ import annotations

import os
import signal
import struct
import subprocess
import sys
from pathlib import Path

# Pi OS Trixie default panel / desktop. Do not match ``lwrespawn`` with
# ``pgrep -f`` — that pattern is too broad and can hit unrelated shells.
_STOP_EXACT = (
    "wf-panel-pi",
    "wf-panel",
    "lxpanel",
    "waybar",
    "pcmanfm",
    "squeekboard",
    "notification-daemon",
    "xfce4-notifyd",
    "mako",
    "dunst",
    "mate-notification-daemon",
    "lxsession-default-apps",
)

_STOP_CMDLINE = (
    "/usr/lib/notification-daemon/notification-daemon",
    "/usr/bin/sbtest",
    "/usr/bin/pcmanfm --desktop",
)

# Browsers and PiOS dialogs — terminate, do not resume later.
_KILL_EXACT = (
    "chromium",
    "chromium-browser",
    "chrome",
    "google-chrome",
    "firefox",
    "firefox-esr",
    "epiphany",
    "piwiz",
    "zenity",
    "yad",
    "gnome-software",
    "update-manager",
    "nm-connection-editor",
    "blueman-manager",
    "blueman-applet",
)

_KILL_CMDLINE = (
    "/usr/lib/chromium/chromium",
    "/usr/lib/firefox/firefox",
    "chrome --type=",
)

_CURSOR_NAMES = (
    "left_ptr",
    "arrow",
    "top_left_arrow",
    "watch",
    "xterm",
    "ibeam",
    "hand1",
    "hand2",
    "pointer",
    "sb_h_double_arrow",
    "sb_v_double_arrow",
    "bottom_side",
    "top_side",
    "left_side",
    "right_side",
    "fleur",
    "sizing",
    "plus",
    "cross",
    "crosshair",
    "pirate",
    "X_cursor",
    "not-allowed",
    "circle",
)

_BLANK_THEME = "PigeonBlank"
_DEFAULT_INDEX_BACKUP = "index.theme.pigeon-bak"
_session_installed = False
_default_index_replaced = False


def linux_kiosk_enabled() -> bool:
    if sys.platform != "linux":
        return False
    raw = os.environ.get("PIGEON_PI_FULLSCREEN", "1").strip().lower()
    return raw in ("", "1", "true", "yes", "on")


def kiosk_guard_ms() -> int:
    raw = os.environ.get("PIGEON_KIOSK_GUARD_MS", "2000").strip()
    try:
        return max(250, int(raw))
    except ValueError:
        return 2000


def _pgrep(pattern: str, *, exact: bool) -> list[int]:
    args = ["pgrep", "-x", pattern] if exact else ["pgrep", "-f", pattern]
    try:
        out = subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _self_pid() -> int:
    try:
        return int(os.getpid())
    except Exception:
        return -1


def _safe_pids(pattern: str, *, exact: bool) -> list[int]:
    me = _self_pid()
    return [pid for pid in _pgrep(pattern, exact=exact) if pid != me]


def hide_desktop_chrome() -> list[int]:
    """SIGSTOP PiOS panel / desktop / notification processes."""
    pids: list[int] = []
    seen: set[int] = set()
    for pat in _STOP_EXACT:
        for pid in _safe_pids(pat, exact=True):
            if pid not in seen:
                pids.append(pid)
                seen.add(pid)
    for pat in _STOP_CMDLINE:
        for pid in _safe_pids(pat, exact=False):
            if pid not in seen:
                pids.append(pid)
                seen.add(pid)
    stopped: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGSTOP)
            stopped.append(pid)
        except OSError:
            continue
    return stopped


def show_desktop_chrome(pids: list[int]) -> None:
    """SIGCONT processes previously stopped by :func:`hide_desktop_chrome`."""
    for pid in pids:
        try:
            os.kill(int(pid), signal.SIGCONT)
        except (OSError, ValueError, TypeError):
            continue


def suppress_os_overlays() -> list[int]:
    """Terminate browsers and PiOS dialogs that can cover Pigeon."""
    pids: list[int] = []
    seen: set[int] = set()
    for pat in _KILL_EXACT:
        for pid in _safe_pids(pat, exact=True):
            if pid not in seen:
                pids.append(pid)
                seen.add(pid)
    for pat in _KILL_CMDLINE:
        for pid in _safe_pids(pat, exact=False):
            if pid not in seen:
                pids.append(pid)
                seen.add(pid)
    killed: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except OSError:
            continue
    return killed


def _icons_root() -> Path:
    return Path.home() / ".icons"


def _blank_theme_dir() -> Path:
    return _icons_root() / _BLANK_THEME


def _default_theme_dir() -> Path:
    return _icons_root() / "default"


def write_blank_xcursor(path: Path, *, size: int = 24) -> None:
    """Write a fully transparent Xcursor image (type 0xfffd0001)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pixel_count = max(1, int(size)) * max(1, int(size))
    pixels = b"\x00\x00\x00\x00" * pixel_count
    header_size = 16
    version = 0x00010000
    ntoc = 1
    toc_off = header_size
    img_off = toc_off + 12
    img_chunk_header = 36
    buf = bytearray()
    buf += b"Xcur"
    buf += struct.pack("<III", header_size, version, ntoc)
    buf += struct.pack("<III", 0xFFFD0001, size, img_off)
    buf += struct.pack(
        "<IIIIIIII",
        img_chunk_header,
        0xFFFD0001,
        size,
        1,
        size,
        size,
        0,
        0,
    )
    buf += struct.pack("<I", 0)  # delay
    buf += pixels
    path.write_bytes(bytes(buf))


def install_blank_cursor_theme() -> Path:
    theme = _blank_theme_dir()
    cursors = theme / "cursors"
    cursors.mkdir(parents=True, exist_ok=True)
    target = cursors / "left_ptr"
    if not target.is_file() or target.stat().st_size < 32:
        write_blank_xcursor(target)
    for name in _CURSOR_NAMES:
        link = cursors / name
        if link == target:
            continue
        try:
            if link.is_symlink() or link.is_file():
                if link.resolve() == target.resolve():
                    continue
                link.unlink()
            link.symlink_to("left_ptr")
        except OSError:
            try:
                write_blank_xcursor(link)
            except OSError:
                continue
    index = theme / "index.theme"
    if not index.is_file():
        index.write_text(
            "[Icon Theme]\n"
            f"Name={_BLANK_THEME}\n"
            "Comment=Invisible cursor used while Pigeon is running\n"
            "Inherits=\n",
            encoding="utf-8",
        )
    return theme


def _set_default_cursor_theme(theme_name: str) -> None:
    global _default_index_replaced
    default = _default_theme_dir()
    default.mkdir(parents=True, exist_ok=True)
    index = default / "index.theme"
    backup = default / _DEFAULT_INDEX_BACKUP
    body = (
        "[Icon Theme]\n"
        f"Inherits={theme_name}\n"
    )
    if index.is_file() and not backup.is_file() and not _default_index_replaced:
        try:
            index.replace(backup)
        except OSError:
            try:
                backup.write_text(index.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass
    try:
        index.write_text(body, encoding="utf-8")
        _default_index_replaced = True
    except OSError:
        return


def _restore_default_cursor_theme() -> None:
    global _default_index_replaced
    default = _default_theme_dir()
    index = default / "index.theme"
    backup = default / _DEFAULT_INDEX_BACKUP
    try:
        if backup.is_file():
            backup.replace(index)
        elif _default_index_replaced and index.is_file():
            index.unlink()
    except OSError:
        pass
    _default_index_replaced = False


def _hide_xdg_autostart() -> None:
    dest = Path.home() / ".config" / "autostart"
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    for name in (
        "pprompt.desktop",
        "squeekboard.desktop",
        "chromium.desktop",
        "chromium-browser.desktop",
        "firefox.desktop",
        "firefox-esr.desktop",
    ):
        path = dest / name
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "Hidden=true" in text:
                continue
        try:
            path.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                f"Name={name.removesuffix('.desktop')}\n"
                "Hidden=true\n"
                "X-GNOME-Autostart-enabled=false\n",
                encoding="utf-8",
            )
        except OSError:
            continue


def install_user_kiosk_session() -> None:
    """User-writable kiosk files (cursor theme + hide PiOS autostart)."""
    global _session_installed
    if _session_installed:
        return
    try:
        install_blank_cursor_theme()
        _set_default_cursor_theme(_BLANK_THEME)
        _hide_xdg_autostart()
        os.environ["XCURSOR_THEME"] = _BLANK_THEME
        os.environ.setdefault("XCURSOR_SIZE", "24")
        _session_installed = True
    except Exception:
        return


def restore_kiosk_session() -> None:
    """Undo cursor-theme override so the desktop pointer returns after Pigeon."""
    global _session_installed
    _restore_default_cursor_theme()
    if os.environ.get("XCURSOR_THEME") == _BLANK_THEME:
        os.environ.pop("XCURSOR_THEME", None)
    _session_installed = False


def _xsetroot_blank() -> None:
    xbm = Path.home() / ".cache" / "pigeon" / "blank.xbm"
    try:
        xbm.parent.mkdir(parents=True, exist_ok=True)
        xbm.write_text(
            "#define blank_width 1\n"
            "#define blank_height 1\n"
            "static unsigned char blank_bits[] = { 0x00 };\n",
            encoding="utf-8",
        )
    except OSError:
        return
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    try:
        subprocess.run(
            ["xsetroot", "-cursor", str(xbm), str(xbm)],
            check=False,
            timeout=2,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return


def hide_pointer(root: object | None = None) -> None:
    """Hide the pointer over Pigeon and park it at the origin."""
    if root is not None:
        try:
            root.configure(cursor="none")  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            root.option_add("*cursor", "none")  # type: ignore[union-attr]
        except Exception:
            pass
    _xsetroot_blank()
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    for cmd in (
        ["xdotool", "mousemove", "0", "0"],
        ["xte", "mousemove 0 0"],
    ):
        try:
            subprocess.run(
                cmd,
                check=False,
                timeout=1,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            break
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue


def apply_kiosk_fullscreen(root, *, borderless: bool = True) -> None:
    """Cover the physical display: origin 0,0, full screen size.

    ``borderless`` is deferred until the window has mapped. Setting
    ``overrideredirect`` on an unmapped Tk window can make labwc/Xwayland
    drop it immediately.
    """
    try:
        sw = int(root.winfo_screenwidth())
        sh = int(root.winfo_screenheight())
    except Exception:
        return
    sw = max(1, sw)
    sh = max(1, sh)
    if borderless:
        try:
            root.overrideredirect(True)
        except Exception:
            pass
    for attr, value in (("-fullscreen", True), ("-topmost", True)):
        try:
            root.attributes(attr, value)
        except Exception:
            continue
    try:
        root.geometry(f"{sw}x{sh}+0+0")
        root.minsize(sw, sh)
        root.maxsize(sw, sh)
    except Exception:
        pass
    try:
        root.lift()
        root.focus_force()
    except Exception:
        pass


def window_covers_display(root, *, slop: int = 4) -> bool:
    """True when the window origin and size match the screen."""
    try:
        sw = int(root.winfo_screenwidth())
        sh = int(root.winfo_screenheight())
        x = int(root.winfo_rootx())
        y = int(root.winfo_rooty())
        w = int(root.winfo_width())
        h = int(root.winfo_height())
    except Exception:
        return False
    if abs(x) > slop or abs(y) > slop:
        return False
    if w + slop < sw or h + slop < sh:
        return False
    return True


def merge_stopped_pids(existing: list[int], extra: list[int]) -> list[int]:
    seen = set()
    out: list[int] = []
    for pid in list(existing) + list(extra):
        try:
            n = int(pid)
        except (TypeError, ValueError):
            continue
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def enforce_kiosk(
    root,
    stopped_pids: list[int] | None = None,
    *,
    borderless: bool = True,
) -> list[int]:
    """Hide PiOS chrome, kill overlays, hide the pointer, cover the display."""
    install_user_kiosk_session()
    stopped = merge_stopped_pids(list(stopped_pids or []), hide_desktop_chrome())
    suppress_os_overlays()
    hide_pointer(root)
    try:
        covers = bool(window_covers_display(root))
    except Exception:
        covers = False
    if not covers:
        apply_kiosk_fullscreen(root, borderless=borderless)
    else:
        try:
            root.attributes("-topmost", True)
            root.lift()
        except Exception:
            pass
    return stopped


def release_kiosk(stopped_pids: list[int] | None = None) -> None:
    show_desktop_chrome(list(stopped_pids or []))
    restore_kiosk_session()


def schedule_kiosk_guard(root, stopped_holder: list[int]) -> None:
    """Re-apply kiosk every few seconds so late PiOS popups cannot stick."""
    if not linux_kiosk_enabled():
        return

    def _tick() -> None:
        if not linux_kiosk_enabled():
            return
        try:
            updated = enforce_kiosk(root, stopped_holder, borderless=True)
        except Exception:
            updated = stopped_holder
        stopped_holder[:] = merge_stopped_pids(stopped_holder, updated)
        try:
            root.after(kiosk_guard_ms(), _tick)
        except Exception:
            return

    try:
        root.after(kiosk_guard_ms(), _tick)
    except Exception:
        return
