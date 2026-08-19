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
import time
from typing import Iterable

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

    deadline = time.monotonic() + max(0.5, float(timeout))
    connect_timeout = min(1.2, max(0.4, timeout * 0.45))

    sock: socket.socket | None = None
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
        initial_blob = _recv_until_idle(sock, idle_s=0.12, total_deadline=initial_deadline)

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
