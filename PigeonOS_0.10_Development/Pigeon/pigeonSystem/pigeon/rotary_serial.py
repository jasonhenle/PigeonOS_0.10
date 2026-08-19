"""USB-serial / UNO Q Monitor bridge for Pigeon rotary controllers.

HID-capable boards (Leonardo / Pro Micro / …) already emit Left / Right / Space
and need nothing here. Serial-mode firmware (`hardware/rotary_hid/rotary_hid.ino`)
prints one line per action:

  RIGHT / CW / FORWARD     → forward
  LEFT  / CCW / BACKWARD   → backward
  PRESS / PUSH / SELECT    → activate

Transports (both tried):
  1) USB CDC serial (``/dev/ttyACM*``, ``PIGEON_ROTARY_PORT=…``)
  2) Arduino UNO Q Monitor TCP — MCU ``Monitor.println`` is forwarded by the
     board's Linux router to ``localhost:7500``. The Pi opens that via
     ``adb -s SERIAL forward tcp:7500 tcp:7500`` (or ``PIGEON_ROTARY_TCP=host:port``).

Optional: ``PIGEON_ROTARY_INVERT=1`` swaps forward/backward.
Optional: ``PIGEON_ROTARY_SERIAL=0`` disables the whole bridge.
Optional: ``PIGEON_ROTARY_TCP=host:port`` enables the UNO Q TCP path
(off by default; ``1`` / ``on`` uses ``127.0.0.1:7500``).
Optional: ``PIGEON_ADB_SERIAL=<serial>`` selects the ADB device when several are present.
Optional: ``PIGEON_ADB=/path/to/adb`` custom adb binary.
"""

from __future__ import annotations

import glob
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Callable

_READY = "PIGEON_CONTROLLER_READY"
_UNO_Q_MONITOR_PORT = 7500
# Cross-transport only: suppress USB+TCP copies of one physical edge.
_CROSS_TRANSPORT_DEDUP_S = 0.04
_BACKOFF_S = (2.0, 5.0, 10.0, 30.0)
# Map every reasonable token the board (or a hand-rolled sketch) might send.
_LINE_TO_ACTION: dict[str, str] = {
    "RIGHT": "forward",
    "CW": "forward",
    "FORWARD": "forward",
    "FWD": "forward",
    "LEFT": "backward",
    "CCW": "backward",
    "BACKWARD": "backward",
    "BACK": "backward",
    "PREV": "backward",
    "PRESS": "activate",
    "PUSH": "activate",
    "CLICK": "activate",
    "SELECT": "activate",
    "SPACE": "activate",
    "ACTIVATE": "activate",
    "ENTER": "activate",
}
_ACTION_TO_KEYSYM = {
    "forward": "Right",
    "backward": "Left",
    "activate": "space",
}
_BAUD = 115200
_PROBE_SECONDS = 1.25
_RECONNECT_S = 2.0


def _stderr(msg: str) -> None:
    try:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    try:
        from pigeon.pi_diagnostics import append_pigeon_log

        append_pigeon_log(msg)
    except Exception:
        pass


def _env_port() -> str | None:
    raw = (os.environ.get("PIGEON_ROTARY_PORT") or "").strip()
    return raw or None


def _env_invert() -> bool:
    flag = (os.environ.get("PIGEON_ROTARY_INVERT") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def _env_adb_serial() -> str | None:
    raw = (os.environ.get("PIGEON_ADB_SERIAL") or "").strip()
    return raw or None


def _normalize_line(raw: str) -> str:
    line = (raw or "").strip()
    if not line:
        return ""
    # Allow "PUSH\r", "push", "PUSH ", "action=PUSH", etc.
    if "=" in line:
        line = line.rsplit("=", 1)[-1].strip()
    return line.upper()


def _is_ready_line(line: str) -> bool:
    if not line or line == _READY:
        return True
    try:
        from pigeon.hardware_protocol import is_ready_message, parse_line

        return is_ready_message(parse_line(line))
    except Exception:
        return _normalize_line(line) in ("MEGA,SYS,READY,1", "READY")


def _action_for_line(line: str, *, invert: bool = False) -> str | None:
    """Map a serial line to forward/backward/activate.

    Prefers canonical ``MEGA,TYPE,ID,DATA`` (and legacy single tokens) via
    ``hardware_protocol``; falls back to the local token table.
    """
    try:
        from pigeon.hardware_protocol import navigation_action, parse_line

        msg = parse_line(line)
        if msg is not None:
            action = navigation_action(msg, invert=invert)
            if action is not None:
                return action
            # Known protocol shape but not a nav action (e.g. READY) → not unknown.
            if "," in (line or ""):
                return None
    except Exception:
        pass
    action = _LINE_TO_ACTION.get(_normalize_line(line))
    if action is None:
        return None
    if invert and action in ("forward", "backward"):
        return "backward" if action == "forward" else "forward"
    return action


def _port_blob(port_info) -> str:
    return " ".join(
        str(x or "")
        for x in (
            getattr(port_info, "description", ""),
            getattr(port_info, "manufacturer", ""),
            getattr(port_info, "product", ""),
            getattr(port_info, "hwid", ""),
            getattr(port_info, "device", ""),
        )
    ).lower()


def _is_strong_arduino_match(blob: str) -> bool:
    return any(
        k in blob
        for k in (
            "arduino",
            "mega",
            "uno q",
            "uno-q",
            "zephyr",
        )
    )


def _candidate_ports() -> tuple[list[str], set[str]]:
    """Return (ordered device paths, set of strong Arduino matches safe to open without probe)."""
    env = _env_port()
    if env:
        return [env], {env}
    found: list[str] = []
    strong: set[str] = set()
    try:
        import serial.tools.list_ports  # type: ignore[import-untyped]

        ports = list(serial.tools.list_ports.comports())
        preferred: list[str] = []
        other: list[str] = []
        for p in ports:
            dev = getattr(p, "device", "") or ""
            if not dev:
                continue
            blob = _port_blob(p)
            if "bluetooth" in blob or "debug-console" in blob:
                continue
            if _is_strong_arduino_match(blob):
                preferred.append(dev)
                strong.add(dev)
            elif any(
                k in blob
                for k in (
                    "stm32",
                    "cdc",
                    "usbmodem",
                    "ttyacm",
                    "ttyusb",
                )
            ):
                preferred.append(dev)
            elif "usb" in blob or "acm" in blob:
                other.append(dev)
        found = preferred + [d for d in other if d not in preferred]
    except Exception:
        pass
    if not found:
        for pattern in (
            "/dev/ttyACM*",
            "/dev/ttyUSB*",
            "/dev/cu.usbmodem*",
            "/dev/cu.usbserial*",
        ):
            found.extend(sorted(glob.glob(pattern)))
    out: list[str] = []
    seen: set[str] = set()
    for d in found:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out, strong


def _open_pyserial(port: str):
    import serial  # type: ignore[import-untyped]

    ser = serial.Serial(port=port, baudrate=_BAUD, timeout=0.2)
    return ser


def _open_posix(port: str):
    """Minimal serial reader when pyserial is unavailable (explicit port only)."""
    import termios

    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[4] = attrs[5] = termios.B115200  # ispeed / ospeed
    # 8N1, raw-ish
    attrs[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)
    attrs[2] |= termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] &= ~(termios.ICANON | termios.ECHO | termios.ECHOE | termios.ISIG)
    termios.tcsetattr(fd, termios.TCSANOW, attrs)

    class _PosixSerial:
        def __init__(self, file_fd: int) -> None:
            self._fd = file_fd
            self._buf = b""

        def readline(self) -> bytes:
            import select

            deadline = time.monotonic() + 0.2
            while b"\n" not in self._buf and time.monotonic() < deadline:
                r, _, _ = select.select([self._fd], [], [], 0.05)
                if not r:
                    continue
                try:
                    chunk = os.read(self._fd, 256)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                self._buf += chunk
            if b"\n" not in self._buf:
                return b""
            line, self._buf = self._buf.split(b"\n", 1)
            return line + b"\n"

        def close(self) -> None:
            try:
                os.close(self._fd)
            except OSError:
                pass

    return _PosixSerial(fd)


def _open_port(port: str):
    try:
        return _open_pyserial(port)
    except ImportError:
        if sys.platform == "win32":
            raise
        return _open_posix(port)


def _looks_like_controller(ser) -> bool:
    """Read briefly; accept READY banner or any known action line."""
    deadline = time.monotonic() + _PROBE_SECONDS
    while time.monotonic() < deadline:
        try:
            raw = ser.readline()
        except Exception:
            return False
        if not raw:
            continue
        try:
            line = raw.decode("utf-8", errors="ignore").strip()
        except Exception:
            continue
        if not line:
            continue
        if _is_ready_line(line) or _action_for_line(line) is not None:
            return True
    return False


def inject_keysym(root, keysym: str) -> None:
    """Synthesize the same Tk events HID boards emit."""
    try:
        if keysym == "space":
            root.event_generate("<KeyPress-space>", when="tail")
            try:
                root.event_generate("<space>", when="tail")
            except Exception:
                pass
        else:
            root.event_generate(f"<KeyPress-{keysym}>", when="tail")
    except Exception as exc:
        _stderr(f"pigeon: rotary_serial: event_generate({keysym}) failed: {exc}")


class _ActionGate:
    """Suppress only cross-transport copies of the same physical action.

    Same-source bursts (USB,USB or TCP,TCP) are never discarded — firmware can
    emit legitimate turns faster than a same-action time gate would allow.
    """

    def __init__(self, *, window_s: float = _CROSS_TRANSPORT_DEDUP_S) -> None:
        self._lock = threading.Lock()
        self._window_s = float(window_s)
        # (action, source, monotonic timestamp at receive)
        self._last: tuple[str, str, float] | None = None

    def accept(self, action: str, source: str, *, when: float | None = None) -> bool:
        now = time.monotonic() if when is None else float(when)
        with self._lock:
            if self._last is not None:
                prev_action, prev_source, t0 = self._last
                if (
                    prev_action == action
                    and prev_source != source
                    and (now - t0) < self._window_s
                ):
                    return False
            self._last = (action, source, now)
            return True


class _RetryBackoff:
    """Bounded reconnect delay: 2s → 5s → 10s → 30s (sticky max)."""

    def __init__(self, steps: tuple[float, ...] = _BACKOFF_S) -> None:
        self._steps = steps
        self._idx = 0

    def delay(self) -> float:
        return float(self._steps[min(self._idx, len(self._steps) - 1)])

    def bump(self) -> float:
        delay = self.delay()
        if self._idx < len(self._steps) - 1:
            self._idx += 1
        return delay

    def reset(self) -> None:
        self._idx = 0


class _StateLog:
    """Log state transitions once; suppress identical repeat keys."""

    def __init__(self) -> None:
        self._last_key: str | None = None

    def emit(self, key: str, message: str) -> None:
        if key == self._last_key:
            return
        self._last_key = key
        _stderr(message)

    def clear(self) -> None:
        self._last_key = None


def _dispatch_action(
    root,
    action: str,
    on_action: Callable[[str], None] | None,
    gate: _ActionGate | None = None,
    *,
    source: str = "usb",
    received_at: float | None = None,
) -> None:
    if gate is not None and not gate.accept(action, source, when=received_at):
        return
    if on_action is not None:
        try:
            on_action(action)
            return
        except Exception as exc:
            _stderr(f"pigeon: rotary_serial: on_action({action}) failed: {exc}")
    keysym = _ACTION_TO_KEYSYM.get(action)
    if keysym:
        inject_keysym(root, keysym)


def _handle_line(
    root,
    line: str,
    *,
    on_action: Callable[[str], None] | None,
    invert: bool,
    gate: _ActionGate,
    logged_ok: list[int],
    ignored: list[int],
    source: str,
) -> None:
    if not line or _is_ready_line(line):
        return
    action = _action_for_line(line, invert=invert)
    if action is None:
        # Canonical MEGA/Q CSV that is not nav — quiet skip (not an "unknown").
        if "," in line and line.upper().startswith(("MEGA,", "Q,", "PI,")):
            return
        if ignored[0] < 12:
            _stderr(f"pigeon: rotary_serial: ignore unknown line {line!r} ({source})")
            ignored[0] += 1
        return
    if logged_ok[0] < 8:
        _stderr(f"pigeon: rotary_serial: {line!r} → {action} ({source})")
        logged_ok[0] += 1
    # Stamp at receive time so Tk queue latency does not widen the dedupe window.
    received_at = time.monotonic()
    try:
        root.after(
            0,
            lambda act=action, src=source, ts=received_at: _dispatch_action(
                root,
                act,
                on_action,
                gate,
                source=src,
                received_at=ts,
            ),
        )
    except Exception:
        pass


def _read_loop(
    root,
    ser,
    stop: threading.Event,
    *,
    on_action: Callable[[str], None] | None,
    invert: bool,
    gate: _ActionGate,
) -> None:
    ignored = [0]
    logged_ok = [0]
    while not stop.is_set():
        try:
            raw = ser.readline()
        except Exception as exc:
            _stderr(f"pigeon: rotary_serial: read error: {exc}")
            break
        if not raw:
            continue
        line = raw.decode("utf-8", errors="ignore").strip()
        _handle_line(
            root,
            line,
            on_action=on_action,
            invert=invert,
            gate=gate,
            logged_ok=logged_ok,
            ignored=ignored,
            source="usb",
        )


def _env_tcp_endpoint() -> tuple[str, int] | None:
    """Return (host, port) for UNO Q Monitor TCP, or None if disabled.

    Off unless ``PIGEON_ROTARY_TCP`` is set. The UNO Q is not in the
    current hardware, so startup must not probe ADB or log that it is missing.
    """
    raw = (os.environ.get("PIGEON_ROTARY_TCP") or "").strip()
    if not raw or raw.lower() in ("0", "false", "off", "no"):
        return None
    if raw.lower() in ("1", "true", "on", "yes"):
        return ("127.0.0.1", _UNO_Q_MONITOR_PORT)
    if ":" in raw:
        host, _, port_s = raw.rpartition(":")
        try:
            return host.strip() or "127.0.0.1", int(port_s)
        except ValueError:
            return None
    return None


def _adb_bin() -> str | None:
    bundled = os.environ.get("PIGEON_ADB", "").strip()
    if bundled and os.path.isfile(bundled):
        return bundled
    return shutil.which("adb")


def _parse_adb_devices(stdout: str) -> list[str]:
    """Return serials in authorized ``device`` state only."""
    devices: list[str] = []
    for ln in (stdout or "").splitlines():
        line = ln.strip()
        if not line or line.startswith("List"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        if state == "device":
            devices.append(serial)
    return devices


def _select_adb_serial(authorized: list[str]) -> str | None:
    """Pick one ADB serial, or None when missing / ambiguous."""
    override = _env_adb_serial()
    if override:
        if override in authorized:
            return override
        _stderr(
            f"pigeon: rotary_serial: PIGEON_ADB_SERIAL={override!r} not among "
            f"authorized devices {authorized or '(none)'}"
        )
        return None
    if len(authorized) == 1:
        return authorized[0]
    if not authorized:
        return None
    _stderr(
        "pigeon: rotary_serial: Multiple ADB devices detected; "
        "set PIGEON_ADB_SERIAL=<serial> "
        f"(seen: {', '.join(authorized)})"
    )
    return None


def _ensure_adb_forward(port: int) -> bool:
    """Forward host TCP ``port`` to the UNO Q Monitor socket when possible."""
    adb = _adb_bin()
    if not adb:
        return False
    try:
        proc = subprocess.run(
            [adb, "devices"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _stderr(f"pigeon: rotary_serial: adb devices failed: {exc}")
        return False
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if len(detail) > 240:
            detail = detail[:240] + "…"
        _stderr(
            f"pigeon: rotary_serial: adb devices failed "
            f"(rc={proc.returncode})"
            + (f": {detail}" if detail else "")
        )
        return False
    devices = _parse_adb_devices(proc.stdout or "")
    if not devices:
        return False
    serial = _select_adb_serial(devices)
    if serial is None:
        return False
    try:
        fwd = subprocess.run(
            [adb, "-s", serial, "forward", f"tcp:{port}", f"tcp:{port}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _stderr(f"pigeon: rotary_serial: adb forward failed: {exc}")
        return False
    if fwd.returncode != 0:
        detail = (fwd.stderr or fwd.stdout or "").strip()
        if len(detail) > 240:
            detail = detail[:240] + "…"
        _stderr(
            f"pigeon: rotary_serial: adb forward failed "
            f"(rc={fwd.returncode}, serial={serial})"
            + (f": {detail}" if detail else "")
        )
        return False
    _stderr(f"pigeon: rotary_serial: adb forward tcp:{port} → device {serial}")
    return True


def _interruptible_wait(stop: threading.Event, seconds: float) -> None:
    """Sleep up to ``seconds`` unless ``stop`` is set (checks ~0.25s)."""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while not stop.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        stop.wait(min(0.25, remaining))


def _tcp_worker(
    root,
    stop: threading.Event,
    *,
    on_action: Callable[[str], None] | None,
    invert: bool,
    gate: _ActionGate,
) -> None:
    endpoint = _env_tcp_endpoint()
    if endpoint is None:
        return
    host, port = endpoint
    ignored = [0]
    logged_ok = [0]
    backoff = _RetryBackoff()
    state_log = _StateLog()
    while not stop.is_set():
        if host in ("127.0.0.1", "localhost") and port == _UNO_Q_MONITOR_PORT:
            if not _ensure_adb_forward(port):
                delay = backoff.bump()
                state_log.emit(
                    f"waiting:{delay:.0f}",
                    f"pigeon: rotary_serial: UNO Q not detected; retrying in {delay:.0f} seconds",
                )
                _interruptible_wait(stop, delay)
                continue
        sock: socket.socket | None = None
        connected = False
        try:
            sock = socket.create_connection((host, port), timeout=2.0)
            sock.settimeout(0.5)
            connected = True
            backoff.reset()
            state_log.emit(
                "connected",
                f"pigeon: rotary_serial: UNO Q Monitor connected at {host}:{port}",
            )
            buf = b""
            while not stop.is_set():
                try:
                    chunk = sock.recv(256)
                except socket.timeout:
                    continue
                except OSError as exc:
                    _stderr(f"pigeon: rotary_serial: tcp read error: {exc}")
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("utf-8", errors="ignore").strip()
                    _handle_line(
                        root,
                        line,
                        on_action=on_action,
                        invert=invert,
                        gate=gate,
                        logged_ok=logged_ok,
                        ignored=ignored,
                        source="tcp",
                    )
        except OSError:
            pass
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        if stop.is_set():
            return
        if connected:
            state_log.emit(
                "disconnected",
                "pigeon: rotary_serial: UNO Q Monitor disconnected",
            )
        delay = backoff.bump()
        state_log.emit(
            f"waiting:{delay:.0f}",
            f"pigeon: rotary_serial: UNO Q not detected; retrying in {delay:.0f} seconds",
        )
        _interruptible_wait(stop, delay)


def start_rotary_serial_listener(
    root,
    *,
    enabled: bool | None = None,
    on_action: Callable[[str], None] | None = None,
) -> Callable[[], None] | None:
    """Start daemons that map CW/CCW/PUSH → app actions (USB serial + UNO Q TCP).

    ``on_action`` receives ``\"forward\"``, ``\"backward\"``, or ``\"activate\"`` on
    the Tk thread. Returns a stop callable, or None if disabled.
    """
    if enabled is None:
        flag = (os.environ.get("PIGEON_ROTARY_SERIAL") or "1").strip().lower()
        enabled = flag not in ("0", "false", "off", "no")
    if not enabled:
        return None

    stop = threading.Event()
    invert = _env_invert()
    gate = _ActionGate()
    logged_ports = [False]
    usb_backoff = _RetryBackoff()
    usb_state = _StateLog()

    def usb_worker() -> None:
        while not stop.is_set():
            ports, strong = _candidate_ports()
            if not ports:
                delay = usb_backoff.bump()
                usb_state.emit(
                    "no-ports",
                    "pigeon: rotary_serial: no USB serial ports yet "
                    f"(retrying in {delay:.0f}s)",
                )
                _interruptible_wait(stop, delay)
                continue
            if not logged_ports[0]:
                _stderr(
                    "pigeon: rotary_serial: USB candidates "
                    + ", ".join(ports[:8])
                    + (" …" if len(ports) > 8 else "")
                )
                logged_ports[0] = True
            opened = False
            for port in ports:
                if stop.is_set():
                    break
                ser = None
                try:
                    ser = _open_port(port)
                except Exception as exc:
                    _stderr(f"pigeon: rotary_serial: open {port} failed: {exc}")
                    continue
                trust = port in strong or _env_port() is not None
                try:
                    ok = True if trust else _looks_like_controller(ser)
                except Exception:
                    ok = trust
                if not ok:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    continue
                usb_backoff.reset()
                usb_state.emit(
                    "connected",
                    f"pigeon: rotary_serial: connected {port} @ {_BAUD}"
                    + (" (invert)" if invert else ""),
                )
                opened = True
                try:
                    _read_loop(
                        root,
                        ser,
                        stop,
                        on_action=on_action,
                        invert=invert,
                        gate=gate,
                    )
                finally:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    _stderr(f"pigeon: rotary_serial: disconnected {port}")
                break
            if not opened:
                delay = usb_backoff.bump()
                _interruptible_wait(stop, delay)
            elif not stop.is_set():
                delay = usb_backoff.bump()
                _interruptible_wait(stop, delay)

    threading.Thread(target=usb_worker, name="pigeon-rotary-usb", daemon=True).start()
    if _env_tcp_endpoint() is not None:
        threading.Thread(
            target=_tcp_worker,
            name="pigeon-rotary-tcp",
            args=(root, stop),
            kwargs={"on_action": on_action, "invert": invert, "gate": gate},
            daemon=True,
        ).start()
    return stop.set
