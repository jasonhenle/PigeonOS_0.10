"""Platform WiFi SSID discovery for settings onboarding."""

from __future__ import annotations

import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import time

_SSID_CACHE: tuple[float, str] | None = None
_SSID_CACHE_TTL_S = 2.0

_SWIFT_COREWLAN_SCAN = """
import CoreWLAN
let client = CWWiFiClient.shared()
guard let iface = client.interface() else { exit(0) }
do {
    let networks = try iface.scanForNetworks(withName: nil)
    for case let ssid as String in networks.compactMap({ $0.ssid }) {
        print(ssid)
    }
} catch {
    exit(1)
}
"""

_SWIFT_COREWLAN_CURRENT_SSID = """
import CoreWLAN
if let ssid = CWWiFiClient.shared().interface()?.ssid() {
    print(ssid)
}
"""


def scan_wifi_networks(*, timeout_s: float = 55.0) -> tuple[str, ...]:
    """Return visible WiFi SSIDs sorted by name (deduplicated, blanks omitted)."""
    if sys.platform == "darwin":
        names = _scan_darwin(timeout_s=timeout_s)
    elif sys.platform.startswith("linux"):
        names = _scan_linux(timeout_s=timeout_s)
    else:
        names = ()
    return filter_scan_results_for_picker(names)


def filter_scan_results_for_picker(
    names: tuple[str, ...] | list[str],
    *,
    exclude_connected: bool = True,
) -> tuple[str, ...]:
    """Drop blanks/dupes and omit the currently connected SSID from picker rows.

    If excluding the connected SSID would leave the list empty (common on a Pi
    that only sees its own AP), keep the connected name so the picker is not blank.
    """
    cleaned = _dedupe_preserve_order(names)
    if not exclude_connected or not cleaned:
        return cleaned
    connected = _current_connected_ssid()
    if not connected:
        return cleaned
    connected_cf = connected.casefold()
    filtered = tuple(n for n in cleaned if n.casefold() != connected_cf)
    return filtered if filtered else cleaned


def current_connected_ssid(*, max_age_s: float | None = None) -> str:
    """SSID the radio is associated with, cached for settings paint."""
    global _SSID_CACHE
    ttl = _SSID_CACHE_TTL_S if max_age_s is None else max(0.0, float(max_age_s))
    now = time.monotonic()
    if ttl > 0 and _SSID_CACHE is not None and (now - _SSID_CACHE[0]) < ttl:
        return _SSID_CACHE[1]
    ssid = _probe_connected_ssid(allow_slow=False)
    _SSID_CACHE = (now, ssid)
    return ssid


def clear_connected_ssid_cache() -> None:
    global _SSID_CACHE
    _SSID_CACHE = None


def _current_connected_ssid() -> str:
    return _probe_connected_ssid(allow_slow=True)


def _probe_connected_ssid(*, allow_slow: bool) -> str:
    if sys.platform == "darwin":
        return _current_connected_ssid_darwin(allow_slow=allow_slow)
    if sys.platform.startswith("linux"):
        return _current_connected_ssid_linux()
    return ""


def _current_connected_ssid_darwin(*, allow_slow: bool = True) -> str:
    # networksetup is faster and more reliable than CoreWLAN ssid() (often nil).
    fast = _current_connected_ssid_darwin_fast()
    if fast:
        return fast
    corewlan = _current_connected_ssid_darwin_corewlan()
    if corewlan:
        return corewlan
    if not allow_slow:
        return ""
    try:
        proc = subprocess.run(
            ["system_profiler", "SPAirPortDataType"],
            capture_output=True,
            text=True,
            timeout=12.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0 and not proc.stdout:
        return ""
    in_current = False
    for line in proc.stdout.splitlines():
        if "Current Network Information:" in line:
            in_current = True
            continue
        if not in_current:
            continue
        if line.startswith("            ") and not line.startswith("              "):
            stripped = line.strip()
            if stripped.endswith(":"):
                return stripped[:-1].strip()
    return ""


def _current_connected_ssid_darwin_corewlan() -> str:
    if not shutil.which("swift"):
        return ""
    try:
        proc = subprocess.run(
            ["swift", "-e", _SWIFT_COREWLAN_CURRENT_SSID],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    ssid = (proc.stdout or "").strip()
    if proc.returncode != 0 or not ssid:
        return ""
    return ssid


def _current_connected_ssid_darwin_fast() -> str:
    """Fast SSID lookup via ``networksetup`` (avoids slow ``system_profiler`` polling)."""
    if not shutil.which("networksetup"):
        return ""
    iface = _darwin_wifi_interface_name()
    if not iface:
        return ""
    try:
        proc = subprocess.run(
            ["networksetup", "-getairportnetwork", iface],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        return ""
    low = out.lower()
    if "not associated" in low or "you are not" in low:
        return ""
    prefix = "Current Wi-Fi Network:"
    if prefix in out:
        return out.split(prefix, 1)[1].strip()
    if ":" in out:
        return out.split(":", 1)[1].strip()
    return out


def _darwin_wifi_interface_name() -> str:
    if not shutil.which("networksetup"):
        return ""
    try:
        proc = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    blocks = re.split(r"\n\s*\n", proc.stdout or "")
    for block in blocks:
        if "Wi-Fi" not in block and "AirPort" not in block:
            continue
        m = re.search(r"Device:\s*(\S+)", block)
        if m:
            return m.group(1).strip()
    return "en0"


def _linux_ssid_token(raw: str) -> str:
    ssid = str(raw or "").strip()
    if not ssid or ssid == "--":
        return ""
    return ssid


def _current_connected_ssid_linux() -> str:
    ssid = _linux_nmcli_dev_wifi_active()
    if ssid:
        return ssid
    ssid = _linux_nmcli_active_wifi_connection()
    if ssid:
        return ssid
    return _linux_iwgetid()


def _linux_nmcli_dev_wifi_active() -> str:
    if not shutil.which("nmcli"):
        return ""
    try:
        proc = subprocess.run(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for line in proc.stdout.splitlines():
        parts = line.split(":", 1)
        if len(parts) == 2 and parts[0].strip() == "yes":
            ssid = _linux_ssid_token(parts[1])
            if ssid:
                return ssid
    return ""


def _linux_nmcli_active_wifi_connection() -> str:
    """Profile name for the active Wi-Fi connection (nmcli scan list can omit SSID)."""
    if not shutil.which("nmcli"):
        return ""
    try:
        proc = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "con", "show", "--active"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for line in proc.stdout.splitlines():
        parts = line.rsplit(":", 1)
        if len(parts) != 2:
            continue
        name, kind = parts[0].strip(), parts[1].strip().casefold()
        if kind in ("802-11-wireless", "wifi", "wireless") and name:
            return name
    return ""


def _linux_iwgetid() -> str:
    if not shutil.which("iwgetid"):
        return ""
    try:
        proc = subprocess.run(
            ["iwgetid", "-r"],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return _linux_ssid_token(proc.stdout)


def _dedupe_preserve_order(names: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return tuple(out)


def _scan_darwin(*, timeout_s: float) -> tuple[str, ...]:
    airport = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
    if os.path.isfile(airport):
        names = _scan_darwin_airport(airport, timeout_s=timeout_s)
        if names:
            return names

    names: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        fast_future = pool.submit(
            _scan_darwin_system_profiler,
            timeout_s=min(timeout_s, 15.0),
        )
        scan_future = pool.submit(_scan_darwin_corewlan, timeout_s=timeout_s)
        done, _pending = concurrent.futures.wait(
            (fast_future, scan_future),
            timeout=timeout_s,
        )
        for future in done:
            try:
                names.extend(future.result())
            except Exception:
                continue
    return _dedupe_preserve_order(names)


def _scan_darwin_corewlan(*, timeout_s: float) -> tuple[str, ...]:
    """Active WiFi scan via CoreWLAN (most complete results on modern macOS)."""
    if not shutil.which("swift"):
        return ()
    try:
        proc = subprocess.run(
            ["swift", "-e", _SWIFT_COREWLAN_SCAN],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if proc.returncode != 0 and not proc.stdout:
        return ()
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


def _scan_darwin_airport(airport: str, *, timeout_s: float) -> tuple[str, ...]:
    try:
        proc = subprocess.run(
            [airport, "-s"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if proc.returncode != 0 and not proc.stdout:
        return ()
    lines = proc.stdout.splitlines()
    if len(lines) <= 1:
        return ()
    names: list[str] = []
    for line in lines[1:]:
        line = line.rstrip()
        if not line:
            continue
        m = re.match(r"^(.*?\S)\s+-?\d", line)
        if m:
            names.append(m.group(1).strip())
            continue
        parts = line.split()
        if parts:
            names.append(parts[0].strip())
    return tuple(names)


def _scan_darwin_system_profiler(*, timeout_s: float) -> tuple[str, ...]:
    try:
        proc = subprocess.run(
            ["system_profiler", "SPAirPortDataType"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if proc.returncode != 0 and not proc.stdout:
        return ()
    return _parse_system_profiler_wifi(proc.stdout)


def _parse_system_profiler_wifi(text: str) -> tuple[str, ...]:
    names: list[str] = []
    in_current = False
    in_other = False
    for line in text.splitlines():
        if "Current Network Information:" in line:
            in_current = True
            in_other = False
            continue
        if "Other Local Wi-Fi Networks:" in line:
            in_other = True
            in_current = False
            continue
        if in_current:
            if line.startswith("            ") and not line.startswith("              "):
                stripped = line.strip()
                if stripped.endswith(":"):
                    name = stripped[:-1].strip()
                    if name:
                        names.append(name)
                    in_current = False
            continue
        if not in_other:
            continue
        if line.startswith("          ") and not line.startswith("            "):
            stripped = line.strip()
            if stripped.endswith(":") and "Other Local Wi-Fi Networks:" not in stripped:
                in_other = False
            continue
        if line.startswith("            ") and not line.startswith("              "):
            stripped = line.strip()
            if stripped.endswith(":"):
                name = stripped[:-1].strip()
                if name:
                    names.append(name)
    return tuple(names)


def _scan_linux(*, timeout_s: float) -> tuple[str, ...]:
    if not shutil.which("nmcli"):
        return ()
    try:
        proc = subprocess.run(
            ["nmcli", "-t", "-f", "SSID", "dev", "wifi", "list"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if proc.returncode != 0 and not proc.stdout:
        return ()
    names: list[str] = []
    for line in proc.stdout.splitlines():
        ssid = line.strip()
        if ssid and ssid != "--":
            names.append(ssid)
    return tuple(names)
