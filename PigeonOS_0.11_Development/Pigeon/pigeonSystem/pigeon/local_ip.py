"""Best-effort local IPv4 lookup for this Pigeon host."""

from __future__ import annotations

import platform
import re
import socket
import subprocess
from functools import lru_cache

_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def clear_local_ipv4_cache() -> None:
    """Drop cached LAN address (call after WiFi join or interface change)."""
    local_ipv4_address.cache_clear()


@lru_cache(maxsize=1)
def local_ipv4_address() -> str:
    """Return a non-loopback LAN IPv4 for this machine, or ``""`` if unknown."""
    for ip in _candidate_ipv4_addresses():
        if ip and not ip.startswith("127."):
            return ip
    return ""


def _candidate_ipv4_addresses() -> tuple[str, ...]:
    ips: list[str] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            ips.append(str(sock.getsockname()[0]))
        finally:
            sock.close()
    except OSError:
        pass

    # Fast path: UDP connect usually yields the primary LAN address immediately.
    if any(ip and not ip.startswith("127.") for ip in ips):
        seen: set[str] = set()
        out: list[str] = []
        for ip in ips:
            if ip in seen:
                continue
            seen.add(ip)
            out.append(ip)
        return tuple(out)

    if platform.system() == "Darwin":
        for iface in ("en0", "en1", "en2", "en3", "bridge100", "bridge101"):
            try:
                proc = subprocess.run(
                    ["ipconfig", "getifaddr", iface],
                    capture_output=True,
                    text=True,
                    timeout=0.4,
                    check=False,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
            value = (proc.stdout or "").strip()
            if _IPV4_RE.match(value):
                ips.append(value)
                break
    else:
        try:
            proc = subprocess.run(
                ["hostname", "-I"],
                capture_output=True,
                text=True,
                timeout=0.5,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            proc = None
        if proc is not None and proc.stdout:
            for part in proc.stdout.split():
                if _IPV4_RE.match(part):
                    ips.append(part)

    if not any(ip and not ip.startswith("127.") for ip in ips):
        try:
            host = socket.gethostname()
            for res in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM):
                ips.append(str(res[4][0]))
        except OSError:
            pass

    seen: set[str] = set()
    out: list[str] = []
    for ip in ips:
        if ip in seen:
            continue
        seen.add(ip)
        out.append(ip)
    return tuple(out)
