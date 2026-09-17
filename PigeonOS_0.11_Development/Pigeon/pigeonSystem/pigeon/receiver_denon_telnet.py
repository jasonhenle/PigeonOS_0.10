"""Denon / Marantz AVR control-protocol client over TCP port 23 (Telnet).

Most Denon/Marantz receivers expose their richest live state through a
line-oriented ASCII protocol on TCP/23. Each command is a short token
terminated by ``\\r`` (carriage return, no line feed); each response is
one-or-more CR-terminated tokens. The unit publishes unsolicited status on
connect, and it allows exactly one client at a time — if another controller
(another phone app, another instance of Pigeon, the vendor's own remote app)
is already holding the socket, new connects either stall or drop.

This module implements a defensive best-effort poll: connect, drain the
on-connect broadcast, send a small battery of ``?`` queries, read until the
stream idles, parse the responses, then close. Everything is bounded by a
short total timeout so a flaky receiver can never wedge the rest of the
polling loop.

Parsed / exposed keys:

* ``PW``      — ``ON`` / ``STANDBY``
* ``MV``      — master volume (raw Denon steps, e.g. ``"475"`` = -24.5 dB)
* ``MV_DB``   — human dB string, e.g. ``"-24.5dB"``
* ``MVMAX``   — max master volume step
* ``MU``      — ``ON`` / ``OFF``
* ``ZM``      — zone-main power
* ``SI``      — source input, e.g. ``"MPLAY"``, ``"CBL/SAT"``, ``"TV"``
* ``MS``      — surround / mode status, e.g. ``"DOLBY DIGITAL+"``, ``"DTS HD MSTR"``
* ``SV``      — video select
* ``DC``      — digital-in decode mode, e.g. ``"AUTO"`` / ``"PCM"`` / ``"DTS"``
* ``PS_<KEY>`` — parameter-set values, e.g. ``PS_MULTEQ`` = ``"AUDYSSEY"``
* ``_raw``    — ``"\\n"``-joined dump of every line the unit emitted, for debug

The caller gets ``{}`` when the host isn't reachable or isn't a Denon-style
unit; callers MUST treat the data as advisory and keep their HTTP / XML path
as the authoritative source of truth.
"""

from __future__ import annotations

import re
import select
import socket
import threading
import time
from collections.abc import Callable
from typing import Iterable

# One client at a time on port 23. Poll and volume sends must not overlap.
_TELNET_LOCK = threading.Lock()

_DEFAULT_PORT = 23
# Commands sent on connect. Order matters: power first so we can bail early
# if the zone is off, then source / surround / decode, then parameter set.
_POLL_COMMANDS: tuple[str, ...] = (
    "PW?",       # power
    "ZM?",       # zone main power
    "MV?",       # master volume (sends MV## and MVMAX ##)
    "MU?",       # mute
    "SI?",       # source input
    "MS?",       # surround mode
    "SV?",       # video select
    "DC?",       # digital decode mode
    "SSINFAISFOR ?",    # input channel layout / source format
    "SYSDA ?",          # input audio format (codec)
    "PSMULTEQ: ?",      # Audyssey MultEQ mode
    "PSDYNEQ ?",        # dynamic EQ
    "PSDYNVOL ?",       # dynamic volume
    "PSREFLEV ?",       # reference level offset
    "PSLFE ?",          # LFE trim
    "PSTONE CTRL ?",    # tone control on/off
    "PSBAS ?",          # bass trim
    "PSTRE ?",          # treble trim
)

# The on-connect banner can take up to ~400 ms on slower units; allow a bit more.
_CONNECT_DRAIN_S = 0.45
# Per-command spacing. The protocol is happy to accept back-to-back writes,
# but a tiny gap lets the unit interleave its responses so parsing is cleaner.
_COMMAND_GAP_S = 0.06
# After the last command, keep reading until we've seen this much idle silence.
_IDLE_TAIL_S = 0.35

# Denon control-protocol responses are always ``XX<rest>`` where ``XX`` is a
# 2-letter ASCII uppercase token (``PW``, ``MV``, ``MU``, ``SI``, ``MS``, ``ZM``,
# ``SV``, ``DC``, ``PS``…) and ``<rest>`` is the value (which may itself contain
# spaces/colons, e.g. ``"MULTEQ:AUDYSSEY"`` or ``"TONE CTRL ON"``). Filter on the
# 2-letter prefix only — anything longer is just data we slice with ``line[2:]``.
_RESP_PREFIX_RE = re.compile(r"^[A-Z]{2}(?:$|[A-Z0-9 :./+\-])")


def _normalize_host(host: str) -> str:
    h = (host or "").strip()
    h = re.sub(r"^tcp://", "", h, flags=re.I).strip().rstrip("/")
    # Strip any ``:port`` suffix — control protocol is always TCP/23.
    m = re.match(r"^(.+):(\d+)$", h)
    if m:
        return m.group(1)
    return h


def _recv_until_idle(sock: socket.socket, *, idle_s: float, total_deadline: float) -> bytes:
    """Read from ``sock`` until ``idle_s`` seconds have elapsed without new bytes,
    or until ``total_deadline`` passes. Returns everything read so far."""
    buf = bytearray()
    last_rx = time.monotonic()
    while True:
        now = time.monotonic()
        if now >= total_deadline:
            break
        # Cap the select wait at whichever is smaller: remaining deadline or idle window.
        remaining = total_deadline - now
        wait = min(remaining, idle_s)
        r, _, _ = select.select([sock], [], [], max(wait, 0.02))
        if not r:
            if (time.monotonic() - last_rx) >= idle_s:
                break
            continue
        try:
            chunk = sock.recv(4096)
        except (BlockingIOError, InterruptedError):
            continue
        except OSError:
            break
        if not chunk:
            # Remote closed — no more data is coming.
            break
        buf.extend(chunk)
        last_rx = time.monotonic()
    return bytes(buf)


def _split_cr_lines(blob: bytes) -> list[str]:
    # Denon uses bare CR as the line terminator; some firmwares slip in LFs
    # (especially on the newer Heos-bridged models), so split on both.
    txt = blob.decode("ascii", errors="replace")
    lines: list[str] = []
    for raw in re.split(r"[\r\n]+", txt):
        s = raw.strip()
        if s:
            lines.append(s)
    return lines


def _parse_denon_response_lines(lines: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        if not _RESP_PREFIX_RE.match(line):
            continue
        prefix = line[:2]
        body = line[2:].strip()

        if prefix == "MV":
            # Two shapes: ``MV57`` (current level) and ``MVMAX 98``.
            if body.upper().startswith("MAX"):
                rest = body[3:].strip()
                if rest:
                    out["MVMAX"] = rest
                continue
            # The whole remainder after "MV" is digits in ``line[2:]``.
            raw_after_prefix = line[2:].strip()
            digits = raw_after_prefix
            if digits.isdigit() and 1 <= len(digits) <= 3:
                out["MV"] = digits
                out["MV_DB"] = _denon_mv_to_db(digits)
            continue

        if prefix == "PW":
            val = (line[2:].strip() or body).upper()
            if val in ("ON", "STANDBY"):
                out["PW"] = val
            continue
        if prefix == "MU":
            val = (line[2:].strip() or body).upper()
            if val in ("ON", "OFF"):
                out["MU"] = val
            continue
        if prefix == "ZM":
            val = (line[2:].strip() or body).upper()
            if val in ("ON", "OFF"):
                out["ZM"] = val
            continue
        if prefix == "SI":
            val = line[2:].strip()
            if val and "SI" not in out:
                out["SI"] = val
            continue
        if prefix == "MS":
            val = line[2:].strip()
            if val and "MS" not in out:
                out["MS"] = val
            continue
        if prefix == "SV":
            val = line[2:].strip()
            if val and "SV" not in out:
                out["SV"] = val
            continue
        if prefix == "DC":
            val = line[2:].strip().upper()
            if val and "DC" not in out:
                out["DC"] = val
            continue
        if prefix == "SS":
            rest = line[2:].strip()
            up = rest.upper()
            if up.startswith("INFAISFOR"):
                val = rest[len("INFAISFOR") :].strip()
                if val:
                    out["SSINFAISFOR"] = val
            elif up.startswith("INFAISSIG"):
                val = rest[len("INFAISSIG") :].strip()
                if val:
                    out["SSINFAISSIG"] = val
            elif rest and "SS" not in out:
                out["SS"] = rest
            continue
        if prefix == "SY":
            rest = line[2:].strip()
            if rest.upper().startswith("SDA"):
                val = rest[3:].strip()
                if val:
                    out["SYSDA"] = val
            elif rest and "SY" not in out:
                out["SY"] = rest
            continue
        if prefix == "PS":
            # "PSMULTEQ:AUDYSSEY" / "PSDYNEQ OFF" / "PSBAS 50" / "PSTONE CTRL ON"
            rest = line[2:].strip()
            key, val = _split_ps_response(rest)
            if key:
                out[f"PS_{key}"] = val
            continue
        # Unrecognized 2-letter prefixes get bucketed raw so the debug view
        # can surface anything new a firmware adds.
        raw_after = line[2:].strip()
        if raw_after and prefix not in out:
            out[prefix] = raw_after
    return out


def _split_ps_response(rest: str) -> tuple[str, str]:
    """``MULTEQ:AUDYSSEY`` / ``DYNEQ OFF`` / ``TONE CTRL ON`` → (KEY, value)."""
    if not rest:
        return "", ""
    if ":" in rest:
        left, right = rest.split(":", 1)
        return left.strip().upper().replace(" ", "_"), right.strip()
    # space-separated forms: the key is every leading word that's all caps/letters;
    # the value is whatever remains.
    parts = rest.split()
    if not parts:
        return "", ""
    key_parts: list[str] = []
    for p in parts:
        if p.isalpha() and p.upper() == p:
            key_parts.append(p)
        else:
            break
    if not key_parts:
        key_parts = [parts[0]]
    key = "_".join(key_parts).upper()
    value_tail = rest[len(" ".join(key_parts)):].strip()
    return key, value_tail


def _denon_mv_to_db(digits: str) -> str:
    """Convert Denon MV step-count to a dB string. ``"57"`` → ``"-23.5dB"``.

    Two encodings coexist in the wild:
      * 2-digit form ``NN`` (integer dB offset from -80, so MV80 == 0 dB).
      * 3-digit form ``NNN`` where the third digit is a 0.5 dB flag
        (e.g. ``"575"`` = 57.5 → -22.5 dB, since 80 = 0 dB reference).
    """
    try:
        if len(digits) == 2:
            n = int(digits)
        elif len(digits) == 3:
            n = int(digits[:2]) + (0.5 if digits[2] == "5" else 0.0)
        else:
            return ""
    except ValueError:
        return ""
    db = n - 80.0
    # Always one decimal + spaced lowercase-d suffix: ``-22.5 dB``.
    return f"{db:.1f} dB"


def _telnet_ack_matches(command: str, line: str) -> bool:
    """True when ``line`` is a control-protocol reply to ``command``."""
    cmd = (command or "").strip().upper()
    u = (line or "").strip().upper()
    if not cmd or not u:
        return False
    if cmd.startswith("MV"):
        return u.startswith("MV") and not u.startswith("MVMAX")
    if cmd.startswith("PW"):
        return u.startswith("PW")
    if cmd.startswith("ZM"):
        return u.startswith("ZM")
    if cmd.startswith("MU"):
        return u.startswith("MU")
    return u.startswith(cmd[:2])


def _recv_until_ack(
    sock: socket.socket,
    *,
    command: str,
    deadline: float,
    ignore_mv: str = "",
) -> tuple[bool, str]:
    """Read CR lines until one matches ``command``, or the deadline hits."""
    buf = bytearray()
    skip = "".join(c for c in str(ignore_mv or "") if c.isdigit())
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        r, _, _ = select.select([sock], [], [], max(0.02, min(remaining, 0.12)))
        if not r:
            continue
        try:
            chunk = sock.recv(4096)
        except (BlockingIOError, InterruptedError, socket.timeout):
            continue
        except OSError:
            break
        if not chunk:
            break
        buf.extend(chunk)
        for line in _split_cr_lines(bytes(buf)):
            if not _telnet_ack_matches(command, line):
                continue
            if skip and command.upper() in ("MVUP", "MVDOWN"):
                digits = "".join(c for c in line[2:] if c.isdigit())
                if digits == skip:
                    continue
            return True, line
    return False, ""


# After power-on, wait on the same socket before volume commands.
_CMD_PAUSE_S = {
    "PWON": 0.7,
    "ZMON": 0.15,
}


def send_denon_telnet_commands(
    host: str,
    commands: list[str],
    *,
    port: int = _DEFAULT_PORT,
    timeout: float = 2.5,
) -> tuple[bool, str]:
    """Send one or more commands on a single telnet session and wait for acks.

    Closing immediately after ``send`` is why MVUP appeared to succeed while
    the AVR never moved — Denon-class units apply the command when they
    echo ``MV``.
    """
    h = _normalize_host(host)
    cmds = [str(c or "").strip().upper() for c in commands if str(c or "").strip()]
    if not h:
        return False, "No receiver host."
    if not cmds:
        return False, "No receiver command."
    per = max(0.35, min(1.2, float(timeout) / max(1, len(cmds))))
    connect_t = min(1.0, max(0.4, float(timeout) * 0.35))
    _VOLUME_HUB.suspend()
    try:
        return _send_denon_telnet_commands_locked(
            h, cmds, port=port, timeout=timeout, per=per, connect_t=connect_t
        )
    finally:
        _VOLUME_HUB.resume()


def _send_denon_telnet_commands_locked(
    h: str,
    cmds: list[str],
    *,
    port: int,
    timeout: float,
    per: float,
    connect_t: float,
) -> tuple[bool, str]:
    with _TELNET_LOCK:
        sock: socket.socket | None = None
        try:
            sock = socket.create_connection((h, port), timeout=connect_t)
            sock.setblocking(False)
            drain_deadline = min(
                time.monotonic() + 0.25,
                time.monotonic() + float(timeout),
            )
            drain = _recv_until_idle(sock, idle_s=0.08, total_deadline=drain_deadline)
            pre_mv = ""
            for raw in reversed(_split_cr_lines(drain)):
                u = raw.strip().upper()
                if u.startswith("MV") and not u.startswith("MVMAX"):
                    pre_mv = "".join(c for c in u[2:] if c.isdigit())
                    break
            acks: list[str] = []
            for cmd in cmds:
                try:
                    sock.sendall((cmd + "\r").encode("ascii", errors="ignore"))
                except OSError as exc:
                    return False, str(exc)
                ok, line = _recv_until_ack(
                    sock,
                    command=cmd,
                    deadline=time.monotonic() + per,
                    ignore_mv=pre_mv if cmd in ("MVUP", "MVDOWN") else "",
                )
                if ok and cmd in ("MVUP", "MVDOWN"):
                    nxt = "".join(c for c in line[2:] if c.isdigit())
                    if nxt:
                        pre_mv = nxt
                if not ok:
                    return False, f"Denon: {cmd} sent, no reply"
                acks.append(line)
                pause = _CMD_PAUSE_S.get(cmd, 0.0)
                if pause > 0:
                    time.sleep(pause)
            return True, " / ".join(f"Denon: {c} → {a}" for c, a in zip(cmds, acks))
        except (OSError, socket.timeout) as exc:
            return False, str(exc)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


def send_denon_telnet_command(
    host: str,
    command: str,
    *,
    port: int = _DEFAULT_PORT,
    timeout: float = 1.5,
) -> tuple[bool, str]:
    """Send one Denon/Marantz control command, e.g. ``MVUP`` or ``MUON``."""
    return send_denon_telnet_commands(host, [command], port=port, timeout=timeout)


def query_denon_volume_telnet(
    host: str,
    *,
    port: int = _DEFAULT_PORT,
    timeout: float = 0.9,
    blocking: bool = False,
) -> dict[str, str]:
    """Short ``MV?`` / ``MU?`` snapshot. Skips if the telnet lock is held."""
    h = _normalize_host(host)
    if not h:
        return {}
    # Hub owns TCP/23 for this host — never open a second client.
    if _VOLUME_HUB.is_managing(h):
        return _VOLUME_HUB.volume_snapshot(h)
    wait = 0.35 if blocking else 0.0
    got = _TELNET_LOCK.acquire(timeout=wait) if wait else _TELNET_LOCK.acquire(False)
    if not got:
        return {}
    deadline = time.monotonic() + max(0.35, float(timeout))
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((h, port), timeout=min(0.45, float(timeout)))
        sock.setblocking(False)
        _recv_until_idle(
            sock,
            idle_s=0.06,
            total_deadline=min(deadline, time.monotonic() + 0.12),
        )
        for cmd in ("PW?", "MV?", "MU?"):
            if time.monotonic() >= deadline:
                break
            sock.sendall((cmd + "\r").encode("ascii", errors="ignore"))
            time.sleep(0.04)
        blob = _recv_until_idle(
            sock, idle_s=0.08, total_deadline=deadline
        )
    except (OSError, socket.timeout):
        return {}
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        _TELNET_LOCK.release()
    parsed = _parse_denon_response_lines(_split_cr_lines(blob))
    return parsed if parsed.get("MV") or parsed.get("MV_DB") or parsed.get("MU") else {}


def send_denon_volume_action(host: str, action: str, *, timeout: float = 1.5) -> tuple[bool, str]:
    """Send volume_up / volume_down / mute_toggle to a Denon-class receiver."""
    act = (action or "").strip().lower()
    if act == "volume_up":
        return send_denon_telnet_command(host, "MVUP", timeout=timeout)
    if act == "volume_down":
        return send_denon_telnet_command(host, "MVDOWN", timeout=timeout)
    if act == "mute_toggle":
        state = poll_denon_telnet(host, timeout=max(1.0, timeout))
        muted = str(state.get("MU") or "").strip().upper() == "ON"
        return send_denon_telnet_command(host, "MUOFF" if muted else "MUON", timeout=timeout)
    return False, f"Unknown receiver volume action: {action}"


def poll_denon_telnet(
    host: str,
    *,
    port: int = _DEFAULT_PORT,
    timeout: float = 2.5,
) -> dict[str, str]:
    """Return a parsed snapshot of Denon AVR state, or ``{}`` on any failure.

    ``timeout`` bounds the *total* wall-clock time spent in this function
    (connect + send + drain). Callers that run this on the existing receiver
    polling thread should pick ``timeout <= remaining_poll_budget``.
    """
    h = _normalize_host(host)
    if not h:
        return {}
    if _VOLUME_HUB.is_managing(h):
        return _VOLUME_HUB.full_snapshot(h)

    deadline = time.monotonic() + max(0.5, float(timeout))
    connect_timeout = min(1.2, max(0.4, timeout * 0.45))
    _VOLUME_HUB.suspend()
    try:
        return _poll_denon_telnet_locked(
            h, port=port, deadline=deadline, connect_timeout=connect_timeout
        )
    finally:
        _VOLUME_HUB.resume()


def _poll_denon_telnet_locked(
    h: str,
    *,
    port: int,
    deadline: float,
    connect_timeout: float,
) -> dict[str, str]:
    sock: socket.socket | None = None
    initial_blob = b""
    tail_blob = b""
    with _TELNET_LOCK:
        try:
            sock = socket.create_connection((h, port), timeout=connect_timeout)
        except (OSError, socket.timeout):
            return {}

        try:
            sock.setblocking(False)
            # Drain the on-connect broadcast. Denon pushes current state as soon as
            # the socket opens; capturing it gives us data even if our explicit
            # queries race a busy unit.
            initial_deadline = min(deadline, time.monotonic() + _CONNECT_DRAIN_S)
            initial_blob = _recv_until_idle(
                sock, idle_s=0.12, total_deadline=initial_deadline
            )

            # Send our command battery. Each command is CR-terminated.
            for cmd in _POLL_COMMANDS:
                if time.monotonic() >= deadline:
                    break
                payload = (cmd + "\r").encode("ascii", errors="ignore")
                try:
                    sock.sendall(payload)
                except OSError:
                    break
                # Short gap so responses come back interleaved but ordered.
                time.sleep(_COMMAND_GAP_S)

            # Drain responses until the stream idles.
            tail_blob = _recv_until_idle(
                sock,
                idle_s=_IDLE_TAIL_S,
                total_deadline=deadline,
            )
        finally:
            try:
                if sock is not None:
                    sock.close()
            except OSError:
                pass

    combined_lines = _split_cr_lines(initial_blob) + _split_cr_lines(tail_blob)
    if not combined_lines:
        return {}

    parsed = _parse_denon_response_lines(combined_lines)
    if not parsed:
        # If we got some bytes but nothing parsed, at least hand back the raw
        # dump so the debug view isn't completely silent.
        return {"_raw": "\n".join(combined_lines)}
    parsed["_raw"] = "\n".join(combined_lines)
    return parsed


class _DenonTelnetHub:
    """One long-lived TCP/23 session. Denon allows a single client.

    Unsolicited ``MV`` / ``MU`` lines (IR, front panel, HEOS) update the
    snapshot. Periodic ``MV?`` covers firmware that stays quiet. One-shot
    send/poll calls ``suspend`` so they can take the socket, then resume.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._suspend = threading.Event()
        self._host = ""
        self._snap: dict[str, str] = {}
        self._snap_lock = threading.Lock()
        self._listener: Callable[[dict[str, str]], None] | None = None
        self._last_vol = ""

    def set_listener(self, cb: Callable[[dict[str, str]], None] | None) -> None:
        self._listener = cb

    def ensure(self, host: str) -> None:
        h = _normalize_host(host)
        if not h:
            self.stop()
            return
        alive = bool(self._thread and self._thread.is_alive())
        if alive and self._host == h:
            return
        self.stop()
        self._stop = threading.Event()
        self._suspend = threading.Event()
        self._host = h
        with self._snap_lock:
            self._snap = {}
        self._last_vol = ""
        self._thread = threading.Thread(
            target=self._run, name="denon-telnet-hub", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._suspend.clear()
        t = self._thread
        self._thread = None
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.2)
        self._host = ""

    def suspend(self) -> None:
        self._suspend.set()

    def resume(self) -> None:
        self._suspend.clear()

    def is_managing(self, host: str) -> bool:
        return bool(
            _normalize_host(host) == self._host
            and self._thread is not None
            and self._thread.is_alive()
        )

    def volume_snapshot(self, host: str) -> dict[str, str]:
        if not self.is_managing(host):
            return {}
        with self._snap_lock:
            snap = dict(self._snap)
        if snap.get("MV") or snap.get("MV_DB") or snap.get("MU"):
            return snap
        return {}

    def full_snapshot(self, host: str) -> dict[str, str]:
        if not self.is_managing(host):
            return {}
        with self._snap_lock:
            return dict(self._snap)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._suspend.is_set():
                time.sleep(0.05)
                continue
            if not _TELNET_LOCK.acquire(timeout=0.25):
                continue
            try:
                if self._stop.is_set() or self._suspend.is_set():
                    continue
                self._session()
            except Exception:
                pass
            finally:
                _TELNET_LOCK.release()
            if not self._stop.is_set():
                time.sleep(0.8)

    def _session(self) -> None:
        h = self._host
        if not h:
            return
        sock: socket.socket | None = None
        try:
            sock = socket.create_connection((h, _DEFAULT_PORT), timeout=1.0)
            sock.setblocking(False)
            last_ping = 0.0
            leftover = b""
            banner = _recv_until_idle(
                sock, idle_s=0.08, total_deadline=time.monotonic() + 0.4
            )
            self._ingest(banner)
            try:
                sock.sendall(b"PW?\rMV?\rMU?\r")
            except OSError:
                return
            while not self._stop.is_set() and not self._suspend.is_set():
                now = time.monotonic()
                if now - last_ping >= 1.0:
                    try:
                        sock.sendall(b"MV?\r")
                    except OSError:
                        return
                    last_ping = now
                r, _, _ = select.select([sock], [], [], 0.2)
                if not r:
                    continue
                try:
                    chunk = sock.recv(4096)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    return
                if not chunk:
                    return
                leftover += chunk
                if leftover.endswith((b"\r", b"\n")):
                    blob, leftover = leftover, b""
                else:
                    idx = max(leftover.rfind(b"\r"), leftover.rfind(b"\n"))
                    if idx < 0:
                        continue
                    blob, leftover = leftover[: idx + 1], leftover[idx + 1 :]
                self._ingest(blob)
        except (OSError, socket.timeout):
            return
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def _ingest(self, blob: bytes) -> None:
        if not blob:
            return
        parsed = _parse_denon_response_lines(_split_cr_lines(blob))
        if not parsed:
            return
        with self._snap_lock:
            self._snap.update(parsed)
            snap = dict(self._snap)
        vol = str(snap.get("MV_DB") or "").strip()
        if not vol and snap.get("MV"):
            vol = _denon_mv_to_db(str(snap.get("MV") or ""))
        if str(snap.get("MU") or "").upper() == "ON":
            vol = "mute"
        if vol and vol != self._last_vol:
            self._last_vol = vol
            cb = self._listener
            if cb is not None:
                try:
                    cb(snap)
                except Exception:
                    pass


_VOLUME_HUB = _DenonTelnetHub()


def start_denon_telnet_hub(
    host: str,
    *,
    on_change: Callable[[dict[str, str]], None] | None = None,
) -> None:
    """Keep one telnet session on ``host`` and notify when master volume moves."""
    if on_change is not None:
        _VOLUME_HUB.set_listener(on_change)
    _VOLUME_HUB.ensure(host)


def stop_denon_telnet_hub() -> None:
    _VOLUME_HUB.stop()
    _VOLUME_HUB.set_listener(None)


def prime_denon_telnet_hub_snapshot_for_tests(
    host: str, fields: dict[str, str]
) -> None:
    """Test helper: expose a snapshot without opening a socket."""
    h = _normalize_host(host)
    _VOLUME_HUB._host = h
    _VOLUME_HUB._thread = threading.current_thread()
    with _VOLUME_HUB._snap_lock:
        _VOLUME_HUB._snap = dict(fields)
    _VOLUME_HUB._last_vol = str(fields.get("MV_DB") or "")


def telnet_hub_ingest_for_tests(blob: bytes) -> str:
    """Test helper: parse a telnet chunk and return the last volume string."""
    _VOLUME_HUB._ingest(blob)
    return _VOLUME_HUB._last_vol
