"""Poll Denon/Marantz-class receivers over HTTP (Main Zone status XML)."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import platform
import re
import select
import socket
import ssl
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlparse

from pigeon.receiver_denon_telnet import (
    _denon_mv_to_db,
    poll_denon_telnet,
    query_denon_volume_telnet,
)

# Same endpoints the Denon 2016+ web UI and denonavr use for main zone snapshot.
_STATUS_PATHS = (
    "/goform/formMainZone_MainZoneXml.xml",
    "/goform/formMainZone_MainZoneXmlStatus.xml",
)


@dataclass(frozen=True)
class ReceiverPollResult:
    ok: bool
    volume: str
    incoming: str
    config: str
    # Snapshot of Telnet-only richer fields (SI/MS/DC/PS_*/_raw) when available.
    telnet_debug: dict[str, str] = field(default_factory=dict)
    # True when the receiver answered but reports OFF/STANDBY power — callers
    # should treat this the same as an absent receiver (no metadata shown).
    standby: bool = False


def _normalize_host(host: str) -> str:
    h = (host or "").strip()
    h = re.sub(r"^https?://", "", h, flags=re.I).strip().rstrip("/")
    return h


_SSL_UNVERIFIED = ssl.create_default_context()
_SSL_UNVERIFIED.check_hostname = False
_SSL_UNVERIFIED.verify_mode = ssl.CERT_NONE


def _fetch(host: str, path: str, timeout: float, *, scheme: str = "http") -> str | None:
    sch = scheme if scheme in ("http", "https") else "http"
    url = f"{sch}://{host}{path}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Pigeon/0.5; +Denon-AVR-status)",
                "Accept": "*/*",
            },
        )
        ctx = _SSL_UNVERIFIED if sch == "https" else None
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


# Newer Denon/Marantz units often return 403 or no document on port 80 for ``formMainZone*`` GETs,
# but still answer ``AppCommand.xml`` POST on port 8080 (same as Home Assistant / openHAB).
_APPCOMMAND_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<tx>
  <cmd id="1">GetAllZonePowerStatus</cmd>
  <cmd id="1">GetVolumeLevel</cmd>
  <cmd id="1">GetMuteStatus</cmd>
</tx>
"""


def _post_fetch(host: str, path: str, body: bytes, timeout: float, *, scheme: str = "http") -> str | None:
    sch = scheme if scheme in ("http", "https") else "http"
    url = f"{sch}://{host}{path}"
    try:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Pigeon/0.5; +Denon-AVR-AppCommand)",
                "Accept": "*/*",
                "Content-Type": "text/xml; charset=utf-8",
            },
        )
        ctx = _SSL_UNVERIFIED if sch == "https" else None
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _parse_appcommand_rx(xml_text: str) -> dict[str, str]:
    """Map ``<rx>`` / ``<cmd>`` children from AppCommand.xml POST into MainZone-style keys."""
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for el in root.iter():
        raw = (el.tag or "").split("}")[-1].lower()
        txt = (el.text or "").strip()
        if not txt:
            continue
        if raw == "volume":
            out.setdefault("MasterVolume", txt)
        elif raw == "mute":
            out.setdefault("Mute", txt)
        elif raw == "zone1":
            u = txt.upper()
            if u in ("ON", "OFF", "STANDBY"):
                out.setdefault("Power", u)
        elif raw == "power" and len(txt) <= 12:
            out.setdefault("Power", txt.upper())
    return out


def send_denon_http_command(
    host: str,
    command: str,
    *,
    timeout: float = 1.5,
) -> tuple[bool, str]:
    """Fire a Denon ``formiPhoneAppDirect`` command (does not need the telnet socket).

    Many units answer ``200`` with an empty body on ``:8080`` and ``403`` on ``:80``.
    """
    h = _normalize_host(host)
    cmd = str(command or "").strip()
    if not h or not cmd:
        return False, "No receiver command."
    from urllib.parse import quote

    base = h
    m = re.match(r"^(.+):(\d+)$", h)
    if m:
        base = m.group(1)
    targets: list[str] = []
    if m and int(m.group(2)) in (80, 8080):
        targets.append(h)
    targets.extend((f"{base}:8080", base))
    seen: set[str] = set()
    ordered: list[str] = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    last_err = "no endpoint"
    for target in ordered:
        url = (
            f"http://{target}/goform/formiPhoneAppDirect.xml?{quote(cmd, safe='')}"
        )
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; Pigeon/0.10; +Denon-AppDirect)",
                    "Accept": "*/*",
                },
            )
            with urllib.request.urlopen(req, timeout=max(0.3, float(timeout))) as resp:
                if 200 <= int(getattr(resp, "status", 200) or 200) < 300:
                    return True, f"Denon HTTP: {cmd}"
                last_err = f"HTTP {getattr(resp, 'status', '?')}"
        except Exception as exc:
            last_err = str(exc)
            continue
    return False, last_err


def send_denon_control_command(
    host: str,
    command: str,
    *,
    timeout: float = 1.5,
) -> tuple[bool, str]:
    """Telnet with ack. HTTP AppDirect is fire-and-forget only (empty 200)."""
    from pigeon.receiver_denon_telnet import send_denon_telnet_command

    ok_tn, msg_tn = send_denon_telnet_command(host, command, timeout=timeout)
    if ok_tn:
        try:
            send_denon_http_command(host, command, timeout=min(0.8, timeout))
        except Exception:
            pass
        return True, msg_tn
    return send_denon_http_command(host, command, timeout=timeout)


def read_denon_appcommand_status(
    host: str,
    *,
    timeout: float = 1.2,
) -> dict[str, str]:
    """Volume / mute / zone power from ``AppCommand.xml`` (no telnet lock)."""
    h = _normalize_host(host)
    if not h:
        return {}
    m = re.match(r"^(.+):(\d+)$", h)
    ip = m.group(1) if m else h
    ac = _merge_appcommand_status(f"{ip}:8080", timeout, scheme="http")
    if not ac:
        ac = _merge_appcommand_status(ip, timeout, scheme="http")
    return ac or {}


def coalesce_receiver_volume_actions(actions: list[str]) -> tuple[int, int]:
    """Net ``MV`` steps and mute toggles from a knob burst."""
    steps = 0
    mutes = 0
    for raw in actions:
        act = str(raw or "").strip().lower()
        if act == "volume_up":
            steps += 1
        elif act == "volume_down":
            steps -= 1
        elif act == "mute_toggle":
            mutes += 1
    return steps, mutes


# ip → (pid, monotonic). Bound only to the player whose HEOS ``ip`` matches
# this host — never list order or model name (one HEOS account can list an
# S670H and a theater X3800H together).
_HEOS_PID: dict[str, tuple[int, float]] = {}
_HEOS_PID_TTL_S = 300.0
_HEOS_PORT = 1255


def _heos_ip(host: str) -> str:
    h = _normalize_host(host)
    m = re.match(r"^(.+):(\d+)$", h)
    return m.group(1) if m else h


def _heos_player_ip(row: dict[str, object]) -> str:
    return str(row.get("ip") or row.get("ipaddr") or "").strip()


def pick_heos_player_for_host(
    host: str, payload: object
) -> dict[str, object] | None:
    """Return the HEOS player at ``host``, or None.

    ``player/get_players`` is account-wide. Taking ``payload[0]`` would send
    volume to whichever AVR HEOS listed first, not the paired receiver.
    """
    want = _heos_ip(host)
    if not want or not isinstance(payload, list):
        return None
    matched: list[dict[str, object]] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        if _heos_player_ip(raw) == want:
            matched.append(raw)
    if not matched:
        return None
    for row in matched:
        try:
            if int(row.get("lineout") or 0) == 0:
                return row
        except (TypeError, ValueError):
            continue
    return matched[0]


def forget_heos_player(host: str) -> None:
    ip = _heos_ip(host)
    if ip:
        _HEOS_PID.pop(ip, None)


def _heos_exchange(host: str, command: str, *, timeout: float = 1.5) -> dict[str, object]:
    """One HEOS CLI line on port 1255 → first JSON object."""
    ip = _heos_ip(host)
    cmd = str(command or "").strip()
    if not ip or not cmd:
        return {}
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((ip, _HEOS_PORT), timeout=min(1.0, timeout))
        sock.settimeout(max(0.4, float(timeout)))
        sock.sendall((cmd + "\r\n").encode("ascii", errors="ignore"))
        buf = b""
        deadline = time.monotonic() + max(0.4, float(timeout))
        while time.monotonic() < deadline:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"{" in buf and (b"\r\n" in buf or b"\n" in buf):
                break
    except (OSError, socket.timeout):
        return {}
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    text = buf.decode("utf-8", errors="replace")
    start = text.find("{")
    if start < 0:
        return {}
    end = text.find("\n", start)
    blob = text[start:] if end < 0 else text[start:end]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def heos_player_id(host: str, *, timeout: float = 1.5) -> int | None:
    """HEOS pid for the player whose LAN IP is ``host``. Model is ignored."""
    ip = _heos_ip(host)
    if not ip:
        return None
    cached = _HEOS_PID.get(ip)
    if cached:
        pid, ts = cached
        if pid and (time.monotonic() - ts) < _HEOS_PID_TTL_S:
            return pid
        _HEOS_PID.pop(ip, None)
    data = _heos_exchange(host, "heos://player/get_players", timeout=timeout)
    row = pick_heos_player_for_host(host, data.get("payload"))
    if not row:
        return None
    try:
        pid = int(row.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if pid:
        _HEOS_PID[ip] = (pid, time.monotonic())
        return pid
    return None


def _heos_message_fields(message: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in str(message or "").split("&"):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        if key:
            out[key] = val
    return out


def heos_level_to_db(level: int) -> str:
    """Map a HEOS *player* level (0–98) to a dB string (80 = 0 dB).

    This is the HEOS music-player volume, not AVR master / HDMI volume.
    When HEOS is stopped the player often reports ``level=0`` (``-80.0 dB``)
    while the front-panel master level is something else — do not show this
    on the now-playing volume widget.
    """
    try:
        n = int(level)
    except (TypeError, ValueError):
        return ""
    return f"{n - 80:.1f} dB"


def read_heos_volume(host: str, *, timeout: float = 1.2) -> dict[str, object]:
    """HEOS *player* volume at this host's IP. Empty when HEOS is missing or unbound.

    Not AVR master volume. HDMI / Apple TV listening leaves this at 0 / muted.
    """
    pid = heos_player_id(host, timeout=timeout)
    if not pid:
        return {}
    vol = _heos_exchange(host, f"heos://player/get_volume?pid={pid}", timeout=timeout)
    mute = _heos_exchange(host, f"heos://player/get_mute?pid={pid}", timeout=timeout)
    fields = _heos_message_fields(
        str((vol.get("heos") or {}).get("message") or "")
        if isinstance(vol.get("heos"), dict)
        else ""
    )
    mute_fields = _heos_message_fields(
        str((mute.get("heos") or {}).get("message") or "")
        if isinstance(mute.get("heos"), dict)
        else ""
    )
    level: int | None
    try:
        level = int(fields["level"]) if "level" in fields else None
    except (TypeError, ValueError):
        level = None
    muted = str(mute_fields.get("state") or "").lower() in ("on", "true", "1")
    return {"pid": pid, "level": level, "muted": muted}


def send_heos_volume_control(
    host: str,
    *,
    steps: int = 0,
    mute_toggles: int = 0,
    timeout: float = 1.5,
) -> tuple[bool, str]:
    """HEOS CLI volume for the player at this host's IP."""
    pid = heos_player_id(host, timeout=timeout)
    if not pid:
        return False, "No HEOS player."
    cmds: list[str] = []
    heos_now = read_heos_volume(host, timeout=min(1.0, timeout))
    if int(steps) != 0 and bool(heos_now.get("muted")):
        cmds.append(f"heos://player/set_mute?pid={pid}&state=off")
    if int(mute_toggles) % 2 == 1:
        nxt = "off" if bool(heos_now.get("muted")) else "on"
        cmds.append(f"heos://player/set_mute?pid={pid}&state={nxt}")
    n = max(-12, min(12, int(steps)))
    level = heos_now.get("level")
    if n != 0 and isinstance(level, int):
        nxt = max(0, min(98, int(level) + n))
        if nxt != int(level):
            cmds.append(f"heos://player/set_volume?pid={pid}&level={nxt}")
    elif n != 0:
        verb = "volume_up" if n > 0 else "volume_down"
        cmds.extend([f"heos://player/{verb}?pid={pid}"] * abs(n))
    if not cmds:
        return True, "No HEOS volume change."
    last = {}
    for cmd in cmds:
        last = _heos_exchange(host, cmd, timeout=timeout)
        heos = last.get("heos") if isinstance(last.get("heos"), dict) else {}
        if str(heos.get("result") or "").lower() != "success":
            forget_heos_player(host)
            return False, f"HEOS failed: {cmd}"
    return True, f"HEOS: {cmds[-1]}"


def _volume_fields_line(fields: dict[str, str]) -> str:
    return _denon_volume_line(fields) if fields else ""


def _volume_readout_same(a: str, b: str) -> bool:
    sa = str(a or "").strip()
    sb = str(b or "").strip()
    if not sa or not sb:
        return False
    if sa.lower() == sb.lower():
        return True
    va = _volume_db_value(sa)
    vb = _volume_db_value(sb)
    if va is None or vb is None:
        return False
    return abs(va - vb) < 0.05


def coalesce_receiver_volume_read(
    *,
    telnet_line: str = "",
    http_line: str = "",
    last_http: str = "",
    last_telnet: str = "",
    held: str = "",
    last_http_mono: float = 0.0,
    last_telnet_mono: float = 0.0,
) -> tuple[str, str]:
    """Pick a display level from telnet ``MV`` and AppCommand ``GetVolumeLevel``.

    A *moving* source always wins. That covers both S670H failure modes: HTTP
    frozen at play-start while telnet ``MV`` is live, and a persistent telnet
    hub stuck on its connect-time ``MV`` while HTTP is live. When the two
    disagree and neither just moved, prefer the more recently changed source;
    a tie prefers HTTP (front-panel / HDMI master).
    """
    tn = str(telnet_line or "").strip()
    http = str(http_line or "").strip()
    prev_http = str(last_http or "").strip()
    prev_tn = str(last_telnet or "").strip()
    keep = str(held or "").strip()
    tn_moved = bool(tn) and (not prev_tn or not _volume_readout_same(tn, prev_tn))
    http_moved = bool(http) and (
        not prev_http or not _volume_readout_same(http, prev_http)
    )
    if tn and http and not _volume_readout_same(tn, http):
        if tn_moved and not http_moved:
            return tn, "telnet"
        if http_moved and not tn_moved:
            return http, "appcommand"
        http_t = float(last_http_mono or 0.0)
        tn_t = float(last_telnet_mono or 0.0)
        if http_t > tn_t:
            return http, "appcommand"
        if tn_t > http_t:
            return tn, "telnet"
        return http, "appcommand"
    if tn_moved:
        return tn, "telnet"
    if http_moved:
        return http, "appcommand"
    if keep:
        return keep, "hold"
    if tn:
        return tn, "telnet"
    return (http, "appcommand") if http else ("", "")


def _volume_db_value(line: str) -> float | None:
    s = str(line or "").strip().lower()
    if not s or s in ("mute", "muted"):
        return None
    m = re.search(r"([+-]?\d+(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _volume_lines_moved(prev: str, vol: str, *, steps: int = 0) -> bool:
    """True when master volume moved in the intended direction."""
    if not prev or not vol:
        return False
    if prev.strip().lower() != vol.strip().lower() and (
        "mute" in prev.lower() or "mute" in vol.lower()
    ):
        return True
    a = _volume_db_value(prev)
    b = _volume_db_value(vol)
    if a is None or b is None:
        return prev.strip() != vol.strip()
    delta = b - a
    if int(steps) > 0:
        return delta >= 0.4
    if int(steps) < 0:
        return delta <= -0.4
    return abs(delta) >= 0.4


def observe_receiver_volume(
    host: str,
    *,
    timeout: float = 1.2,
    telnet_blocking: bool = False,
    allow_appcommand: bool = False,
) -> tuple[str, str]:
    """Return ``(telnet_line, appcommand_line)`` for the caller to coalesce."""
    from pigeon.receiver_denon_telnet import query_denon_volume_telnet

    h = _normalize_host(host)
    if not h:
        return "", ""
    try:
        tn = query_denon_volume_telnet(
            h, timeout=min(0.9, timeout), blocking=telnet_blocking
        )
    except Exception:
        tn = {}
    tn_line = _volume_fields_line(tn)
    ac_line = ""
    if allow_appcommand:
        ac = read_denon_appcommand_status(h, timeout=min(1.0, timeout))
        ac_line = _volume_fields_line(ac)
    return tn_line, ac_line


def read_live_receiver_volume(
    host: str,
    *,
    timeout: float = 1.2,
    telnet_blocking: bool = False,
    allow_appcommand: bool = False,
    last_http: str = "",
    last_telnet: str = "",
    held: str = "",
    last_http_mono: float = 0.0,
    last_telnet_mono: float = 0.0,
) -> tuple[str, str]:
    """Live AVR *master* volume. Telnet ``MV`` and AppCommand, then coalesce.

    AppCommand ``GetVolumeLevel`` is the HDMI / front-panel level. HEOS player
    volume is a different control and is never used here.
    """
    tn_line, ac_line = observe_receiver_volume(
        host,
        timeout=timeout,
        telnet_blocking=telnet_blocking,
        allow_appcommand=allow_appcommand,
    )
    if allow_appcommand:
        return coalesce_receiver_volume_read(
            telnet_line=tn_line,
            http_line=ac_line,
            last_http=last_http,
            last_telnet=last_telnet,
            held=held,
            last_http_mono=last_http_mono,
            last_telnet_mono=last_telnet_mono,
        )
    if tn_line:
        return tn_line, "telnet"
    return "", ""


def apply_denon_master_volume(
    host: str,
    *,
    steps: int = 0,
    mute_toggles: int = 0,
    timeout: float = 3.5,
) -> tuple[bool, str, str]:
    """Apply knob steps to master volume on this host.

    Telnet ``MV*`` first; if the reported level does not move, HEOS for the
    player at this IP. A telnet echo of the *current* ``MV`` is not success.
    AppCommand ``zone1`` Power is not trusted.
    """
    from pigeon.receiver_denon_telnet import (
        query_denon_volume_telnet,
        send_denon_telnet_commands,
    )

    h = _normalize_host(host)
    if not h:
        return False, "No receiver host.", ""
    before = read_denon_appcommand_status(h, timeout=min(1.2, timeout))
    try:
        before_tn = query_denon_volume_telnet(h, timeout=0.7, blocking=False)
    except Exception:
        before_tn = {}
    prev = _volume_fields_line(before_tn) or _volume_fields_line(before)
    n = max(-24, min(24, int(steps)))
    if before and _denon_power_is_standby(before) and not before_tn:
        try:
            send_denon_http_command(h, "PWON", timeout=min(0.8, timeout))
        except Exception:
            pass
    cmds: list[str] = []
    fields = before_tn or before or {}
    muted = str(fields.get("Mute") or fields.get("MU") or "").strip().lower() in (
        "on",
        "1",
        "true",
        "yes",
    )
    if int(mute_toggles) % 2 == 1:
        cmds.append("MUOFF" if muted else "MUON")
    if n > 0:
        cmds.extend(["MVUP"] * n)
    elif n < 0:
        cmds.extend(["MVDOWN"] * (-n))
    if not cmds:
        return True, "No receiver volume change.", prev
    ok, msg = send_denon_telnet_commands(h, cmds, timeout=max(2.5, float(timeout)))
    try:
        after_tn = query_denon_volume_telnet(h, timeout=0.8, blocking=True)
    except Exception:
        after_tn = {}
    after = read_denon_appcommand_status(h, timeout=min(1.2, timeout))
    vol = _volume_fields_line(after_tn) or _volume_fields_line(after)
    moved = _volume_lines_moved(prev, vol, steps=n)
    if ok and moved:
        return True, msg, vol
    ok_h, msg_h = send_heos_volume_control(
        h, steps=n, mute_toggles=mute_toggles, timeout=min(1.8, timeout)
    )
    if ok_h:
        try:
            after_tn = query_denon_volume_telnet(h, timeout=0.8, blocking=True)
        except Exception:
            after_tn = {}
        after_h = read_denon_appcommand_status(h, timeout=min(1.2, timeout))
        master = _volume_fields_line(after_tn) or _volume_fields_line(after_h) or vol
        return True, msg_h, master
    msg = f"{msg} / {msg_h}"
    return False, msg, vol or prev


def send_denon_volume_control(
    host: str,
    action: str,
    *,
    timeout: float = 1.5,
) -> tuple[bool, str]:
    """``volume_up`` / ``volume_down`` / ``mute_toggle`` with a telnet ack."""
    act = str(action or "").strip().lower()
    steps, mutes = coalesce_receiver_volume_actions([act])
    if act not in ("volume_up", "volume_down", "mute_toggle"):
        return False, f"Unknown receiver volume action: {action}"
    ok, msg, _vol = apply_denon_master_volume(
        host, steps=steps, mute_toggles=mutes, timeout=max(2.5, float(timeout))
    )
    return ok, msg


def _merge_appcommand_status(host: str, timeout: float, *, scheme: str) -> dict[str, str] | None:
    body = _post_fetch(host, "/goform/AppCommand.xml", _APPCOMMAND_XML, timeout, scheme=scheme)
    if not body or len(body.strip()) < 20:
        return None
    low = body.lower()
    if "<!doctype html" in low or "<html" in low[:400]:
        return None
    parsed = _parse_appcommand_rx(body)
    if not parsed:
        return None
    if "MasterVolume" not in parsed and "Mute" not in parsed and "Power" not in parsed:
        return None
    return parsed


def _parse_item_xml(xml_text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    item = root if root.tag.lower() == "item" else None
    if item is None:
        for el in root.iter():
            if el.tag.lower() == "item":
                item = el
                break
    if item is None:
        return out
    skip_containers = {
        "videoselectlists",
        "ecomodelists",
        "inputfunclist",
        "renamesource",
        "sourcedelete",
    }
    for child in list(item):
        if child.tag.lower() in skip_containers:
            continue
        val_el = child.find("value")
        if val_el is not None and val_el.text is not None:
            t = val_el.text.strip()
            if t:
                out[child.tag] = t
        elif child.text and str(child.text).strip():
            out[child.tag] = str(child.text).strip()
    return out


# <TagName>...<value>text</value>...</TagName> (works when <item> wrapper is missing).
_DENON_VALUE_PAIR_RE = re.compile(
    r"<([A-Za-z][\w:.-]*)\b[^>]*>\s*(?:<value>\s*([^<]*?)\s*</value>|([^<]+?))\s*</\1>",
    re.I | re.DOTALL,
)


def _parse_denon_regex_value_pairs(xml_text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _DENON_VALUE_PAIR_RE.finditer(xml_text):
        tag = (m.group(1) or "").strip()
        inner = (m.group(2) or m.group(3) or "").strip()
        if not tag or not inner or tag in out:
            continue
        out[tag] = inner
    return out


def _parse_zone_xml_walk_values(xml_text: str) -> dict[str, str]:
    """Collect <Tag><value>text</value></Tag> anywhere in the tree (newer Denon layouts)."""
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    skip_tags = frozenset(
        x.lower()
        for x in (
            "videoselectlists",
            "ecomodelists",
            "inputfunclist",
            "renamesource",
            "sourcedelete",
        )
    )
    for el in root.iter():
        raw_tag = el.tag.split("}")[-1] if el.tag else ""
        if not raw_tag or raw_tag.lower() in skip_tags:
            continue
        val_el = el.find("value")
        if val_el is not None and val_el.text is not None:
            t = val_el.text.strip()
            if t and raw_tag not in out:
                out[raw_tag] = t
    return out


def _merge_parsed_denon_status_fields(xml_text: str) -> dict[str, str]:
    merged: dict[str, str] = {}
    merged.update(_parse_item_xml(xml_text))
    merged.update(_parse_zone_xml_walk_values(xml_text))
    merged.update(_parse_denon_regex_value_pairs(xml_text))
    return merged


def _body_looks_like_denon_zone_xml(body: str) -> bool:
    """True when the HTTP body is probably MainZone status XML (not the HTML setup UI)."""
    s = (body or "").strip()
    if len(s) < 40:
        return False
    low = s.lower()
    if "<!doctype html" in low or "<html" in low[:300]:
        return False
    if "mainzone" in low or "formmainzone" in low:
        return True
    if "<power" in low or "<zonepower" in low or "<mastervolume" in low:
        return True
    if "<item" in low and ("<value>" in low or "</value>" in low):
        return True
    return s.startswith("<?xml")


def _schemes_for_host(host: str) -> tuple[str, ...]:
    """Pick URL schemes for a probe host (may include ``host:port``)."""
    m = re.match(r"^(.+):(\d+)$", host)
    if not m:
        return ("http", "https")
    port = int(m.group(2))
    if port == 443:
        return ("https",)
    if port in (80, 8080):
        return ("http",)
    return ("https", "http")


# Last working (host_variant, scheme) per normalized input host — probed cold only
# on first poll or after the cached endpoint stops answering.
_LAST_GOOD_ENDPOINT: dict[str, tuple[str, str]] = {}

# Cap any single HTTP request so cold probes can't each consume the whole poll budget.
_PER_REQUEST_TIMEOUT_CAP_S = 2.5
_MIN_REQUEST_TIMEOUT_S = 0.25


def _budget_timeout(timeout: float, deadline: float | None) -> float:
    """Per-request timeout bounded by the remaining poll deadline (<=0 means skip)."""
    t = min(float(timeout), _PER_REQUEST_TIMEOUT_CAP_S)
    if deadline is not None:
        t = min(t, deadline - time.monotonic())
    return t


def _merge_zone_status(
    host: str,
    timeout: float,
    *,
    scheme_order: tuple[str, ...] | None = None,
    deadline: float | None = None,
    cache_key: str | None = None,
) -> dict[str, str] | None:
    schemes = scheme_order if scheme_order is not None else _schemes_for_host(host)
    for scheme in schemes:
        merged: dict[str, str] = {}
        ok_any = False
        last_body: str | None = None
        for path in _STATUS_PATHS:
            t = _budget_timeout(timeout, deadline)
            if t < _MIN_REQUEST_TIMEOUT_S:
                return None
            body = _fetch(host, path, t, scheme=scheme)
            if not body:
                continue
            ok_any = True
            last_body = body
            merged.update(_merge_parsed_denon_status_fields(body))
        if ok_any and not merged and last_body and _body_looks_like_denon_zone_xml(last_body):
            merged = {"Power": "ON"}
        # Many models block MainZone GET on :80 but still answer AppCommand POST on :8080 — merge both.
        t = _budget_timeout(timeout, deadline)
        if t >= _MIN_REQUEST_TIMEOUT_S:
            ac = _merge_appcommand_status(host, t, scheme=scheme)
            if ac:
                combo = dict(merged)
                combo.update(ac)
                merged = combo
        if merged:
            if cache_key:
                _LAST_GOOD_ENDPOINT[cache_key] = (host, scheme)
            return merged
    return None


def _receiver_probe_host_variants(host: str) -> list[str]:
    """Try bare IP/hostname, then common Denon API ports (web UI may differ from status XML)."""
    hn = _normalize_host(host)
    if not hn:
        return []
    if re.search(r":\d+\s*$", hn):
        return [hn]
    variants = [hn]
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", hn):
        variants.extend((f"{hn}:8080", f"{hn}:10443"))
    return variants


def _merge_zone_status_with_fallback(
    host: str,
    timeout: float,
    *,
    deadline: float | None = None,
) -> dict[str, str] | None:
    hn = _normalize_host(host)
    variants = _receiver_probe_host_variants(host)
    cached = _LAST_GOOD_ENDPOINT.get(hn)
    if cached is not None:
        ch, cs = cached
        # Try the endpoint that answered last time first, with its known scheme.
        d = _merge_zone_status(
            ch, timeout, scheme_order=(cs,), deadline=deadline, cache_key=hn
        )
        if d:
            return d
        variants = [v for v in variants if v != ch]
    for h in variants:
        if deadline is not None and time.monotonic() >= deadline:
            break
        d = _merge_zone_status(h, timeout, deadline=deadline, cache_key=hn)
        if d:
            return d
    # Nothing answered: forget the cached endpoint so the next poll re-probes fully.
    _LAST_GOOD_ENDPOINT.pop(hn, None)
    return None


def _denon_power_is_standby(d: dict[str, str]) -> bool:
    """True when the AVR is off / in standby.

    Telnet ``PW`` is authoritative when present: HTTP ``Power`` on many
    HEOS-era units stays ``ON`` in network-standby (or ``STANDBY`` while
    ``PW`` is ``ON``). ``ZM`` (zone-main) is ignored — zone-off is not the
    same as the AVR having no master volume.
    """
    on_tokens = {"ON"}
    off_tokens = {"OFF", "STANDBY"}
    pw = _denon_field_ci(d, "PW").upper()
    if pw:
        token = pw.replace("/", " ").split()[0]
        if token in off_tokens:
            return True
        if token in on_tokens:
            return False
    tokens: list[str] = []
    for key in ("Power", "ZonePower"):
        raw = _denon_field_ci(d, key).upper()
        if not raw:
            continue
        token = raw.replace("/", " ").split()[0]
        tokens.append(token)
    if any(t in on_tokens for t in tokens):
        return False
    return bool(tokens) and all(t in off_tokens for t in tokens)


def _denon_volume_line(d: dict[str, str]) -> str:
    """Master-volume readout (``mute`` / ``-22.5 dB``), including while in standby."""
    mute = _denon_field_ci(d, "Mute", "MU").strip().lower()
    muted = mute in ("on", "1", "true", "yes")
    mv = _denon_field_ci(
        d,
        "MV_DB",
        "MasterVolume",
        "MasterVolumeDisplay",
        "VolumeDisplay",
        "DispVolume",
        "MainZoneVolume",
    )
    if mv and re.fullmatch(r"\d{2,3}", mv.strip()):
        # Bare 2-3 digit values are Denon volume steps, not dB (e.g. "575" = -22.5dB).
        mv = _denon_mv_to_db(mv.strip()) or mv
    if not mv:
        mv_step = _denon_field_ci(d, "MV")
        if mv_step and re.fullmatch(r"\d{2,3}", mv_step.strip()):
            mv = _denon_mv_to_db(mv_step.strip())
    if muted:
        return "mute"
    if not mv:
        return ""
    low_mv = mv.lower()
    return mv if "db" in low_mv or mv.strip().endswith("%") else f"{mv} dB"


def _denon_field_ci(d: dict[str, str], *names: str) -> str:
    """
    Read the first non-empty field matching one of ``names``, case-insensitive on keys.
    Firmware / parsers vary in XML tag casing (``MasterVolume`` vs ``mastervolume``).
    """
    if not d or not names:
        return ""
    for n in names:
        v = (d.get(n) or "").strip()
        if v:
            return v
    by_lower: dict[str, str] = {}
    for k, v in d.items():
        t = str(v or "").strip()
        if not t:
            continue
        kl = str(k or "").lower()
        if kl not in by_lower:
            by_lower[kl] = t
    for n in names:
        t = by_lower.get(str(n or "").lower())
        if t:
            return t
    return ""


# Incoming signal labels that are HDMI/source selectors, not audio formats.
_AUDIO_FORMAT_HINTS = (
    "dolby",
    "dts",
    "atmos",
    "stereo",
    "multi",
    "pcm",
    "auro",
    "truehd",
    "digital",
    "thd",
    "surround",
    "dd+",
    "eac3",
    "dtsx",
    "mal",
    "bitstream",
)
_GENERIC_DECODE_MODES = frozenset({"auto", "pcm", "off", "on", "unknown", "no", "no signal"})

# HTTP/XML keys that report incoming codec/signal (never ``SI`` — that is the HDMI input).
_INCOMING_FORMAT_KEYS = (
    "signalDisplay",
    "HDMIAudio",
    "HDsignalMode",
    "HDMISig",
    "SYSDA",
    "InputSignal",
    "AudioInputSignal",
    "DigitalInputSignal",
    "AudioCodec",
    "CodecDisp",
    "SSINFAISFOR",
    "DC",
)


def _looks_like_speaker_layout_not_format(value: str) -> bool:
    """True for Denon layout tokens (``3/2/.1``, ``7.1.4``) — not a codec label."""
    t = str(value or "").strip()
    if not t:
        return False
    compact = t.replace(" ", "")
    if re.fullmatch(r"[\d./+]+", compact):
        return True
    return bool(re.fullmatch(r"\d+\.\d+(?:\.\d+)?", t))


def looks_like_hdmi_input_selector(value: str) -> bool:
    """True when ``value`` names an AVR input (SAT/CBL, HDMI3, …), not an audio format."""
    s = str(value or "").strip()
    if not s:
        return False
    low = re.sub(r"\s+", " ", s.lower())
    compact = re.sub(r"\s+", "", low)
    if any(h in low for h in _AUDIO_FORMAT_HINTS):
        return False
    if re.fullmatch(r"hdmi\s*\d*", low):
        return True
    if re.fullmatch(r"aux\s*\d*", low):
        return True
    if re.fullmatch(r"(?:sat/?cbl|cbl/?sat)", compact):
        return True
    if low in {
        "tv",
        "dvd",
        "bd",
        "bluray",
        "game",
        "game2",
        "mplay",
        "cd",
        "tuner",
        "phono",
        "net",
        "heos",
        "network",
        "usb",
        "vcr",
        "dvr",
        "tvaudio",
        "dock",
        "ipod",
        "source",
        "unbal",
        "sat",
        "cbl",
    }:
        return True
    return False


def _pick_incoming_audio_format(d: dict[str, str]) -> str:
    for key in _INCOMING_FORMAT_KEYS:
        v = _denon_field_ci(d, key)
        if not v:
            continue
        v = v.strip()
        if key == "DC" and v.lower() in _GENERIC_DECODE_MODES:
            continue
        if key == "SYSDA" and v.lower() in {"pcm", "auto"}:
            continue
        if looks_like_hdmi_input_selector(v):
            continue
        if _looks_like_speaker_layout_not_format(v):
            continue
        return v
    for k, v in d.items():
        if not v or len(v) < 2:
            continue
        kl = k.lower()
        if ("signal" in kl or "codec" in kl) and "power" not in kl and "mute" not in kl:
            if looks_like_hdmi_input_selector(v):
                continue
            if _looks_like_speaker_layout_not_format(v):
                continue
            return v.strip()
    return ""


def poll_denon_like_receiver(
    host: str,
    timeout: float = 4.0,
    *,
    include_telnet: bool = True,
) -> ReceiverPollResult:
    """
    Return overlay strings. On transport/parse failure or no signal: ok=False and empty
    incoming/config/volume lines.
    """
    h = _normalize_host(host)
    if not h:
        return ReceiverPollResult(False, "", "", "")

    # One shared deadline bounds the whole HTTP probe phase; without it, a dead
    # receiver walks 3 host variants x 2 schemes x 3 endpoints at full timeout each.
    deadline = time.monotonic() + max(1.0, float(timeout))
    d = _merge_zone_status_with_fallback(h, timeout, deadline=deadline)
    if not d:
        return ReceiverPollResult(False, "", "", "")
    # Telnet often carries richer live audio metadata (SI/MS/DC/PS*) than
    # XML. Merge when the host answers; HTTP/XML remains authoritative.
    telnet_state: dict[str, str] = {}
    if include_telnet:
        try:
            telnet_state = poll_denon_telnet(h, timeout=max(0.6, min(1.8, timeout * 0.4)))
        except Exception:
            telnet_state = {}
    else:
        # Full metadata telnet is occasional (one-client socket). Always take a
        # short PW/MV/MU snapshot so IR / front-panel volume stays live and so
        # S670H AppCommand ``Power=OFF`` during HDMI does not look like standby.
        try:
            telnet_state = query_denon_volume_telnet(
                h, timeout=min(0.9, max(0.5, timeout * 0.25)), blocking=True
            )
        except Exception:
            telnet_state = {}
    if telnet_state:
        for k, v in telnet_state.items():
            if not v:
                continue
            d[k] = v

    vol_s = _denon_volume_line(d)
    if _denon_power_is_standby(d):
        # Last-MV is still a real readout when GetVolumeLevel answers in
        # network-standby. Hide source/format so idle chrome does not look live.
        return ReceiverPollResult(True, vol_s, "", "", telnet_state, standby=True)

    # Incoming = source audio format (codec/signal). Playback = surround/output mode (``MS``).
    # Never use ``SI`` (HDMI input selector such as SAT/CBL) for the widget line.
    incoming = _pick_incoming_audio_format(d)

    cfg = _denon_field_ci(d, "MS", "selectSurround", "SurrMode", "surroundmode").strip()
    if cfg and looks_like_hdmi_input_selector(cfg):
        cfg = ""
    cfg = " ".join(cfg.split())

    if incoming:
        incoming = incoming.lower()

    if cfg:
        cfg = cfg.lower()

    return ReceiverPollResult(True, vol_s, incoming, cfg, telnet_state)


def _darwin_extra_lan_ipv4() -> list[str]:
    """macOS often reports one address via ``getaddrinfo``; query common interfaces."""
    if platform.system() != "Darwin":
        return []
    ips: list[str] = []
    for iface in ("en0", "en1", "en2", "en3", "bridge100", "bridge101"):
        try:
            out = subprocess.run(
                ["ipconfig", "getifaddr", iface],
                capture_output=True,
                text=True,
                timeout=0.4,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        s = (out.stdout or "").strip()
        if s and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", s) and not s.startswith("127."):
            ips.append(s)
    return ips


def _local_class_c_bases() -> list[str]:
    """Unique ``a.b.c`` /24 prefixes for this machine's non-loopback IPv4 addresses."""
    seen: set[str] = set()
    bases: list[str] = []
    candidate_ips: list[str] = []
    try:
        hn = socket.gethostname()
        for res in socket.getaddrinfo(hn, None, socket.AF_INET, socket.SOCK_STREAM):
            candidate_ips.append(res[4][0])
    except Exception:
        pass
    candidate_ips.extend(_darwin_extra_lan_ipv4())
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        candidate_ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    for ip in candidate_ips:
        if ip.startswith("127."):
            continue
        parts = ip.split(".")
        if len(parts) == 4:
            b = f"{parts[0]}.{parts[1]}.{parts[2]}"
            if b not in seen:
                seen.add(b)
                bases.append(b)
    if not bases:
        bases.append("192.168.1")
    return bases


_SSDP_BRAND_MARKERS = ("denon", "marantz", "sound united", "heos")


def _ssdp_collect_probe_hints(total_wait: float = 3.5) -> list[tuple[str, tuple[str, ...] | None]]:
    """
    Broadcast SSDP (``upnp:rootdevice`` + MediaRenderer) and turn matching replies
    into HTTP probe targets. Denon/Marantz AVRs advertise over UPnP even when a blind
    /24 port-80 sweep would miss them (different subnet inference, HTTPS-only UI, etc.).
    """
    hints: list[tuple[str, tuple[str, ...] | None]] = []
    seen: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        except OSError:
            pass
        sock.bind(("", 0))
        sock.setblocking(False)
        searches = (
            (
                "M-SEARCH * HTTP/1.1\r\n"
                "HOST: 239.255.255.250:1900\r\n"
                'MAN: "ssdp:discover"\r\n'
                "ST: upnp:rootdevice\r\n"
                "MX: 2\r\n"
                "\r\n"
            ),
            (
                "M-SEARCH * HTTP/1.1\r\n"
                "HOST: 239.255.255.250:1900\r\n"
                'MAN: "ssdp:discover"\r\n'
                "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
                "MX: 2\r\n"
                "\r\n"
            ),
        )
        for pkt in searches:
            try:
                sock.sendto(pkt.encode("ascii"), ("239.255.255.250", 1900))
            except OSError:
                pass
        deadline = time.monotonic() + total_wait
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            r, _, _ = select.select([sock], [], [], min(remaining, 0.5))
            if not r:
                continue
            try:
                data, _addr = sock.recvfrom(16384)
            except OSError:
                continue
            text = data.decode("utf-8", errors="replace")
            low = text.lower()
            if not any(m in low for m in _SSDP_BRAND_MARKERS):
                continue
            loc_raw: str | None = None
            for line in text.split("\r\n"):
                if line.lower().startswith("location:"):
                    loc_raw = line.split(":", 1)[1].strip()
                    break
            if not loc_raw:
                continue
            try:
                u = urlparse(loc_raw)
            except Exception:
                continue
            hostname = (u.hostname or "").strip()
            if not hostname:
                continue
            scheme_l = (u.scheme or "http").lower()
            port = u.port
            scheme_order: tuple[str, ...] | None
            if scheme_l == "https":
                host_str = f"{hostname}:{port}" if port and port != 443 else hostname
                scheme_order = ("https", "http")
            elif scheme_l == "http":
                host_str = f"{hostname}:{port}" if port and port != 80 else hostname
                scheme_order = None
            else:
                continue
            if not host_str or host_str in seen:
                continue
            seen.add(host_str)
            hints.append((host_str, scheme_order))
    finally:
        sock.close()
    return hints


def _looks_like_denon_zone_status(d: dict[str, str]) -> bool:
    if not d:
        return False
    if d.get("FriendlyName"):
        return True
    if (d.get("Power") or d.get("ZonePower")) and (
        "MasterVolume" in d or "InputFuncSelect" in d or "SurrMode" in d or "selectSurround" in d
    ):
        return True
    return False


def _receiver_row_from_status(host: str, d: dict[str, str]) -> dict[str, str]:
    name = (d.get("FriendlyName") or "").strip()
    if not name or name.upper() in ("MARANTZ_MODEL", "DENON_MODEL"):
        name = "Receiver"
    return {
        "host": host,
        "name": name,
        "label": f"{name} — {host}",
        "id": "",
    }


def _canonical_receiver_key(host: str) -> str:
    """Stable dedupe key per physical host (``split(':')[0]`` breaks ``foo.local:8080``)."""
    h = (host or "").strip()
    if not h:
        return ""
    m = re.match(r"^(\d{1,3}(?:\.\d{1,3}){3}):(\d+)$", h)
    if m:
        return m.group(1)
    m2 = re.match(r"^(.+):(\d+)$", h)
    if m2 and m2.group(2).isdigit():
        return m2.group(1).lower()
    return h.lower()


def _dedupe_host_list(hosts: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in hosts or []:
        t = str(raw or "").strip()
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


_AVR_MODEL_RE = re.compile(r"avr-?([a-z0-9]+)", re.I)
_MAC_HEX_RE = re.compile(r"[^0-9a-f]")


def receiver_model_key(name: str) -> str:
    """``AVR-S670H`` / ``Denon AVR-S670H`` → ``s670h``. Brand-only names stay empty."""
    m = _AVR_MODEL_RE.search(str(name or ""))
    if not m:
        return ""
    return (m.group(1) or "").strip().lower()


def _norm_mac12(raw: str) -> str:
    hexed = _MAC_HEX_RE.sub("", str(raw or "").lower())
    return hexed if len(hexed) == 12 else ""


def paired_receiver_mac(row: dict[str, object] | None) -> str:
    if not isinstance(row, dict):
        return ""
    for key in ("identifier", "id", "mac"):
        mac = _norm_mac12(str(row.get(key) or ""))
        if mac:
            return mac
    return ""


def receiver_identities_match(
    paired: dict[str, object] | None,
    *,
    name: str = "",
    model: str = "",
    host: str = "",
    device_id: str = "",
) -> bool:
    """True when a discovered AVR is the paired unit (MAC or model), not just 'Denon'."""
    _ = host
    if not isinstance(paired, dict):
        return False
    want_mac = paired_receiver_mac(paired)
    got_mac = _norm_mac12(device_id)
    if want_mac and got_mac and want_mac == got_mac:
        return True
    want_model = receiver_model_key(
        str(paired.get("name") or paired.get("label") or "")
    )
    if not want_model:
        return False
    return want_model in (
        receiver_model_key(name),
        receiver_model_key(model),
    )


def _prefix_from_netmask(mask: str) -> int | None:
    s = str(mask or "").strip()
    if not s:
        return None
    try:
        if s.lower().startswith("0x"):
            return bin(int(s, 16)).count("1")
        if "." in s:
            return ipaddress.IPv4Network(f"0.0.0.0/{s}").prefixlen
        n = int(s)
        if 0 <= n <= 32:
            return n
    except (TypeError, ValueError):
        return None
    return None


def _local_ipv4_networks() -> list[ipaddress.IPv4Network]:
    """LAN prefixes from the OS (``10.0.4.44/22`` stays /22, not a lone /24)."""
    found: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()

    def _add(ip: str, prefix: int) -> None:
        if ip.startswith("127.") or ip.startswith("169.254."):
            return
        use = int(prefix)
        if use < 22:
            use = 24
        try:
            net = ipaddress.IPv4Network(f"{ip}/{use}", strict=False)
        except ValueError:
            return
        key = str(net)
        if key in seen:
            return
        seen.add(key)
        found.append(net)

    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=0.8,
            check=False,
        )
        for line in (out.stdout or "").splitlines():
            m = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
            if m:
                _add(m.group(1), int(m.group(2)))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    if platform.system() == "Darwin":
        try:
            out = subprocess.run(
                ["ifconfig"],
                capture_output=True,
                text=True,
                timeout=0.8,
                check=False,
            )
            cur_ip = ""
            for line in (out.stdout or "").splitlines():
                m_ip = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)", line)
                if m_ip:
                    cur_ip = m_ip.group(1)
                m_mask = re.search(r"\bnetmask\s+(\S+)", line)
                if cur_ip and m_mask:
                    pref = _prefix_from_netmask(m_mask.group(1))
                    if pref is not None:
                        _add(cur_ip, pref)
                    cur_ip = ""
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    if not found:
        for base in _local_class_c_bases():
            _add(f"{base}.1", 24)
    return found


def ipv4_hosts_for_networks(
    networks: list[ipaddress.IPv4Network] | None = None,
    *,
    max_hosts: int = 2048,
) -> list[str]:
    """Host IPv4s to probe (excludes network/broadcast). Caps very wide prefixes."""
    nets = list(networks or _local_ipv4_networks())
    out: list[str] = []
    seen: set[str] = set()
    for net in nets:
        for host in net.hosts():
            s = str(host)
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= int(max_hosts):
                return out
    return out


def heos_players_from_hosts(
    hosts: list[str] | None,
    *,
    timeout: float = 1.2,
) -> list[dict[str, object]]:
    """Union of ``player/get_players`` from each host that speaks HEOS."""
    out: list[dict[str, object]] = []
    seen_pid: set[int] = set()
    for raw in _dedupe_host_list(hosts):
        data = _heos_exchange(raw, "heos://player/get_players", timeout=timeout)
        payload = data.get("payload")
        if not isinstance(payload, list):
            continue
        for row in payload:
            if not isinstance(row, dict):
                continue
            try:
                pid = int(row.get("pid") or 0)
            except (TypeError, ValueError):
                pid = 0
            if pid and pid in seen_pid:
                continue
            if pid:
                seen_pid.add(pid)
            out.append(row)
    return out


def pick_heos_player_for_paired_row(
    paired: dict[str, object] | None,
    payload: object,
) -> dict[str, object] | None:
    """Account-wide HEOS row for the *paired* AVR (model / MAC), not list order."""
    if not isinstance(paired, dict) or not isinstance(payload, list):
        return None
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        if receiver_identities_match(
            paired,
            name=str(raw.get("name") or ""),
            model=str(raw.get("model") or ""),
            host=str(raw.get("ip") or raw.get("ipaddr") or ""),
        ):
            return raw
    return None


def _host_has_explicit_trailing_port(h: str) -> bool:
    """True for ``192.168.1.5:8080`` or ``avr.local:8080``; false for IPv6 like ``::1``."""
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}:\d+$", h):
        return True
    if re.search(r"\]:\d+$", h):
        return True
    if h.count(":") == 1:
        left, right = h.rsplit(":", 1)
        if right.isdigit() and not re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", left):
            return True
        if right.isdigit() and "." in left:
            return True
    return False


def _probe_host_for_receiver(host: str, timeout: float) -> dict[str, str] | None:
    """
    Probe a saved or mDNS address (IPv4, hostname, optional ``:port``) for Denon/Marantz XML.
    IPv4 gets a second try on ``:8080``; explicit ports are probed once.
    """
    h = _normalize_host(host)
    if not h:
        return None
    half = max(0.12, timeout * 0.5)
    if _host_has_explicit_trailing_port(h):
        d = _merge_zone_status(h, half)
        if d is not None and _looks_like_denon_zone_status(d):
            return _receiver_row_from_status(h, d)
        return None
    candidates = [h]
    if "." in h and h.count(":") == 0:
        candidates.append(f"{h}:8080")
    for cand in candidates:
        d = _merge_zone_status(cand, half)
        if d is not None and _looks_like_denon_zone_status(d):
            return _receiver_row_from_status(cand, d)
    return None


def _probe_ip_for_receiver(ip: str, timeout: float) -> dict[str, str] | None:
    """Subnet sweep: same as :func:`_probe_host_for_receiver` for a bare IPv4."""
    return _probe_host_for_receiver(ip, timeout)


def _probe_host_hint(
    host: str,
    timeout: float,
    scheme_order: tuple[str, ...] | None,
) -> dict[str, str] | None:
    d = _merge_zone_status(host, timeout, scheme_order=scheme_order)
    if d is not None and _looks_like_denon_zone_status(d):
        return _receiver_row_from_status(host, d)
    return None


def scan_denon_like_receivers_on_lan(
    *,
    timeout_per_host: float = 0.4,
    max_workers: int = 64,
    ssdp_wait: float = 3.5,
    extra_hosts: list[str] | None = None,
    subnet_sweep: bool = True,
) -> tuple[bool, str, list[dict[str, str]]]:
    """
    Discover receivers via SSDP (UPnP), optional ``extra_hosts`` (e.g. IPs from pyatv /
    AirPlay discovery), plus a sweep of the real interface prefix (``/22`` on this
    LAN, not only the Pi's ``/24``) for Denon/Marantz ``MainZone`` HTTP(S) XML.

    Returns ``(ok, message, rows)`` where each row has ``host``, ``name``, ``label``, ``id``.
    """
    hints = _ssdp_collect_probe_hints(ssdp_wait)
    ips = ipv4_hosts_for_networks() if subnet_sweep else []
    airplay_or_saved = _dedupe_host_list(extra_hosts)
    by_canonical: dict[str, dict[str, str]] = {}

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures: list[concurrent.futures.Future[dict[str, str] | None]] = []
            for host, scheme_order in hints:
                futures.append(
                    ex.submit(_probe_host_hint, host, timeout_per_host, scheme_order)
                )
            for h in airplay_or_saved:
                futures.append(
                    ex.submit(_probe_host_for_receiver, h, timeout_per_host)
                )
            futures.extend(
                ex.submit(_probe_ip_for_receiver, ip, timeout_per_host) for ip in ips
            )
            for fut in concurrent.futures.as_completed(futures, timeout=240):
                try:
                    row = fut.result()
                except Exception:
                    row = None
                if not row:
                    continue
                canon = _canonical_receiver_key(str(row.get("host") or ""))
                if not canon:
                    continue
                if canon not in by_canonical:
                    by_canonical[canon] = row
    except concurrent.futures.TimeoutError:
        pass

    rows = sorted(by_canonical.values(), key=lambda r: (r.get("host") or ""))
    if not rows:
        return (
            True,
            "No Denon/Marantz-style receivers answered via UPnP (SSDP), HTTP(S) on addresses "
            "from Apple TV / AirPlay discovery (when available), or the subnet sweep. "
            "Use “Add receiver…” with the IP from your AVR’s network menu, or enable the "
            "receiver’s network / IP control / web UI option if your model hides the API.",
            [],
        )
    return True, f"Found {len(rows)} receiver(s).", rows


def resolve_paired_receiver_host(
    paired: dict[str, object] | None,
    *,
    extra_hosts: list[str] | None = None,
    subnet_sweep: bool = False,
    timeout: float = 0.7,
) -> str:
    """Return a reachable control IP for the paired AVR, or ``""``.

    Box 3 often saves the AirPlay advertisement IP. That address can go stale
    (DHCP) or never speak telnet/AppCommand. HEOS ``get_players`` on any other
    Denon on the account, SSDP, and an optional subnet sweep are used to find
    the same model / MAC at a live host.
    """
    if not isinstance(paired, dict):
        return ""
    saved = _canonical_receiver_key(
        str(paired.get("address") or paired.get("host") or "")
    )
    seeds = _dedupe_host_list(list(extra_hosts or []) + ([saved] if saved else []))
    probed = _probe_host_for_receiver(saved, timeout) if saved else None
    if probed:
        return _canonical_receiver_key(str(probed.get("host") or saved))

    hints = _ssdp_collect_probe_hints(min(2.2, max(0.8, timeout * 3)))
    hint_hosts = [_canonical_receiver_key(h) for h, _s in hints]
    directory_hosts = _dedupe_host_list(seeds + hint_hosts)
    for raw in heos_players_from_hosts(directory_hosts, timeout=min(1.2, timeout + 0.4)):
        if not receiver_identities_match(
            paired,
            name=str(raw.get("name") or ""),
            model=str(raw.get("model") or ""),
            host=str(raw.get("ip") or raw.get("ipaddr") or ""),
        ):
            continue
        ip = _canonical_receiver_key(str(raw.get("ip") or raw.get("ipaddr") or ""))
        if not ip:
            continue
        hit = _probe_host_for_receiver(ip, timeout)
        if hit:
            return _canonical_receiver_key(str(hit.get("host") or ip))

    _ok, _msg, rows = scan_denon_like_receivers_on_lan(
        timeout_per_host=max(0.25, timeout * 0.6),
        ssdp_wait=0.0,
        extra_hosts=directory_hosts,
        subnet_sweep=bool(subnet_sweep),
    )
    for row in rows:
        if receiver_identities_match(
            paired,
            name=str(row.get("name") or ""),
            model=str(row.get("name") or ""),
            host=str(row.get("host") or ""),
            device_id=str(row.get("id") or ""),
        ):
            return _canonical_receiver_key(str(row.get("host") or ""))
    return ""
