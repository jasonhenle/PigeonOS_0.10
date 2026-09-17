"""Join a WiFi network and verify the connection (settings onboarding)."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time

from pigeon.wifi_scan import _current_connected_ssid, _darwin_wifi_interface_name

_SWIFT_COREWLAN_JOIN = """
import CoreWLAN
import Foundation

guard CommandLine.arguments.count >= 3 else { exit(2) }
let want = CommandLine.arguments[1].trimmingCharacters(in: .whitespacesAndNewlines)
let password = CommandLine.arguments[2]

let client = CWWiFiClient.shared()
guard let iface = client.interface() else { exit(1) }

func ssidMatch(_ got: String?, _ target: String) -> Bool {
    guard let got = got?.trimmingCharacters(in: .whitespacesAndNewlines), !got.isEmpty else {
        return false
    }
    return got.caseInsensitiveCompare(target) == .orderedSame
}

if ssidMatch(iface.ssid(), want) {
    print("ok")
    exit(0)
}

func match(_ net: CWNetwork, _ ssid: String) -> Bool {
    ssidMatch(net.ssid, ssid)
}

do {
    var target: CWNetwork?
    let named = try iface.scanForNetworks(withName: want)
    target = named.first(where: { match($0, want) })
    if target == nil {
        let all = try iface.scanForNetworks(withName: nil)
        target = all.first(where: { match($0, want) })
    }
    guard let network = target else { exit(3) }
    try iface.associate(to: network, password: password)
    print("ok")
    exit(0)
} catch {
    if ssidMatch(iface.ssid(), want) {
        print("ok")
        exit(0)
    }
    let msg = error.localizedDescription.lowercased()
    if msg.contains("password") || msg.contains("passphrase")
        || msg.contains("incorrect") || msg.contains("wrong") {
        exit(4)
    }
    if msg.contains("denied") || msg.contains("auth fail") || msg.contains("authentication") {
        exit(4)
    }
    exit(5)
}
"""


def try_join_wifi_network(
    ssid: str,
    password: str,
    *,
    timeout_s: float = 25.0,
) -> tuple[bool, str]:
    """
    Attempt to join ``ssid`` with ``password``.

    Returns ``(ok, message)``. On failure the message is user-facing
    (typically *incorrect password*).
    """
    name = str(ssid or "").strip()
    if not name:
        return False, "No network selected."
    if sys.platform == "darwin":
        return _join_darwin(name, str(password or ""), timeout_s=timeout_s)
    if sys.platform.startswith("linux"):
        return _join_linux(name, str(password or ""), timeout_s=timeout_s)
    if str(password or ""):
        return True, f'Connected to "{name}".'
    return False, "incorrect password"


def _normalize_ssid(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _ssid_matches(want: str, got: str) -> bool:
    if not got:
        return False
    return _normalize_ssid(want).casefold() == _normalize_ssid(got).casefold()


def _wait_for_connected_ssid(ssid: str, *, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + max(0.5, float(timeout_s))
    while time.monotonic() < deadline:
        if _ssid_matches(_current_connected_ssid(), ssid):
            return True
        time.sleep(0.2)
    return _ssid_matches(_current_connected_ssid(), ssid)


def _looks_like_password_error(text: str) -> bool:
    low = str(text or "").lower()
    if not low:
        return False
    markers = (
        "password",
        "passphrase",
        "incorrect",
        "wrong",
        "authentication failed",
        "auth fail",
        "failed to authenticate",
        "pre-shared key",
        "psk",
    )
    if any(m in low for m in markers):
        return True
    return "denied" in low and "access" not in low


def _join_darwin(ssid: str, password: str, *, timeout_s: float) -> tuple[bool, str]:
    if _ssid_matches(_current_connected_ssid(), ssid):
        return True, f'Connected to "{ssid}".'
    if shutil.which("swift"):
        ok, msg = _join_darwin_corewlan(ssid, password)
        if ok:
            return True, msg
        if msg == "incorrect password":
            return False, msg
    ok, msg = _join_darwin_networksetup(ssid, password, timeout_s=timeout_s)
    if ok:
        return True, msg
    if _wait_for_connected_ssid(ssid, timeout_s=min(timeout_s, 10.0)):
        return True, f'Connected to "{ssid}".'
    return False, msg or "incorrect password"


def _join_darwin_corewlan(ssid: str, password: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["swift", "-e", _SWIFT_COREWLAN_JOIN, ssid, password],
            capture_output=True,
            text=True,
            timeout=40.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    out = (proc.stdout or "").strip()
    if proc.returncode == 0 and "ok" in out:
        return True, f'Connected to "{ssid}".'
    if _ssid_matches(_current_connected_ssid(), ssid):
        return True, f'Connected to "{ssid}".'
    if proc.returncode == 4:
        return False, "incorrect password"
    err = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    if _looks_like_password_error(err):
        return False, "incorrect password"
    return False, ""


def _join_darwin_networksetup(ssid: str, password: str, *, timeout_s: float) -> tuple[bool, str]:
    iface = _darwin_wifi_interface_name() or _darwin_wifi_interface_legacy()
    if not iface:
        return False, "WiFi interface not found."
    try:
        subprocess.run(
            ["networksetup", "-setairportpower", iface, "on"],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
        subprocess.run(
            [
                "networksetup",
                "-addpreferredwirelessnetworkatindex",
                iface,
                ssid,
                "WPA2",
                password,
                "0",
            ],
            capture_output=True,
            text=True,
            timeout=12.0,
            check=False,
        )
        proc = subprocess.run(
            ["networksetup", "-setairportnetwork", iface, ssid, password],
            capture_output=True,
            text=True,
            timeout=min(timeout_s, 30.0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "incorrect password"
    err = f"{proc.stderr or ''}\n{proc.stdout or ''}".strip()
    if proc.returncode == 0:
        if _wait_for_connected_ssid(ssid, timeout_s=min(timeout_s, 6.0)):
            return True, f'Connected to "{ssid}".'
        # networksetup reported success — trust it even if SSID polling lags.
        return True, f'Connected to "{ssid}".'
    if _ssid_matches(_current_connected_ssid(), ssid):
        return True, f'Connected to "{ssid}".'
    if _looks_like_password_error(err):
        return False, "incorrect password"
    return False, "incorrect password"


def _darwin_wifi_interface_legacy() -> str:
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


def _join_linux(ssid: str, password: str, *, timeout_s: float) -> tuple[bool, str]:
    if _ssid_matches(_current_connected_ssid(), ssid):
        return True, f'Connected to "{ssid}".'
    if not shutil.which("nmcli"):
        return False, "Network manager (nmcli) not available."
    cmd = ["nmcli", "-w", str(int(timeout_s)), "dev", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s + 5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "incorrect password"
    out = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    if proc.returncode == 0:
        if _wait_for_connected_ssid(ssid, timeout_s=min(timeout_s, 8.0)):
            return True, f'Connected to "{ssid}".'
        return True, f'Connected to "{ssid}".'
    if _ssid_matches(_current_connected_ssid(), ssid):
        return True, f'Connected to "{ssid}".'
    if _looks_like_password_error(out):
        return False, "incorrect password"
    return False, "incorrect password"


__all__ = ["try_join_wifi_network"]
