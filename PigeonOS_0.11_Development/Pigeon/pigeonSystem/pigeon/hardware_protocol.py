"""Pigeon hardware serial protocol: SOURCE,TYPE,ID,DATA.

See ``Pigeon/hardware/PROTOCOL.md``. The Pi owns all application meaning;
microcontrollers only report events (MEGA) or observations (Q), or execute
Pi-authored output commands (PI → MEGA lights, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass

# Canonical sources
SOURCE_MEGA = "MEGA"
SOURCE_Q = "Q"
SOURCE_PI = "PI"

# Common types
TYPE_ENCODER = "ENCODER"
TYPE_BUTTON = "BUTTON"
TYPE_POT = "POT"
TYPE_LIGHT = "LIGHT"
TYPE_SYS = "SYS"
TYPE_TITLE = "TITLE"
TYPE_STATE = "STATE"
TYPE_PERSON = "PERSON"

# Nav encoder / shaft switch (current bring-up hardware)
ID_NAV = "NAV"


@dataclass(frozen=True)
class HardwareMessage:
    """One newline-delimited hardware bus message."""

    source: str
    msg_type: str
    msg_id: str
    data: str
    raw: str = ""

    @property
    def canonical(self) -> str:
        return format_message(self.source, self.msg_type, self.msg_id, self.data)


def format_message(source: str, msg_type: str, msg_id: str, data: str) -> str:
    """Build a wire line (no trailing newline)."""
    return f"{source},{msg_type},{msg_id},{data}"


def _norm_token(value: str) -> str:
    return (value or "").strip().upper()


# Legacy single-token lines from early rotary firmware → canonical messages.
_LEGACY_LINES: dict[str, tuple[str, str, str, str]] = {
    "RIGHT": (SOURCE_MEGA, TYPE_ENCODER, ID_NAV, "RIGHT"),
    "CW": (SOURCE_MEGA, TYPE_ENCODER, ID_NAV, "RIGHT"),
    "FORWARD": (SOURCE_MEGA, TYPE_ENCODER, ID_NAV, "RIGHT"),
    "FWD": (SOURCE_MEGA, TYPE_ENCODER, ID_NAV, "RIGHT"),
    "LEFT": (SOURCE_MEGA, TYPE_ENCODER, ID_NAV, "LEFT"),
    "CCW": (SOURCE_MEGA, TYPE_ENCODER, ID_NAV, "LEFT"),
    "BACKWARD": (SOURCE_MEGA, TYPE_ENCODER, ID_NAV, "LEFT"),
    "BACK": (SOURCE_MEGA, TYPE_ENCODER, ID_NAV, "LEFT"),
    "PREV": (SOURCE_MEGA, TYPE_ENCODER, ID_NAV, "LEFT"),
    "PUSH": (SOURCE_MEGA, TYPE_BUTTON, ID_NAV, "PRESSED"),
    "PRESS": (SOURCE_MEGA, TYPE_BUTTON, ID_NAV, "PRESSED"),
    "CLICK": (SOURCE_MEGA, TYPE_BUTTON, ID_NAV, "PRESSED"),
    "SELECT": (SOURCE_MEGA, TYPE_BUTTON, ID_NAV, "PRESSED"),
    "SPACE": (SOURCE_MEGA, TYPE_BUTTON, ID_NAV, "PRESSED"),
    "ACTIVATE": (SOURCE_MEGA, TYPE_BUTTON, ID_NAV, "PRESSED"),
    "ENTER": (SOURCE_MEGA, TYPE_BUTTON, ID_NAV, "PRESSED"),
    "PIGEON_CONTROLLER_READY": (SOURCE_MEGA, TYPE_SYS, "READY", "1"),
    "READY": (SOURCE_MEGA, TYPE_SYS, "READY", "1"),
}


def parse_line(raw: str) -> HardwareMessage | None:
    """Parse one serial line into a :class:`HardwareMessage`, or None if empty.

    Accepts canonical ``SOURCE,TYPE,ID,DATA`` and legacy single-token lines.
    Unknown shapes that look like four-field CSV are still returned so the Pi
    can ignore them gracefully by TYPE.
    """
    line = (raw or "").strip()
    if not line:
        return None
    if "=" in line and "," not in line:
        line = line.rsplit("=", 1)[-1].strip()

    if "," in line:
        parts = line.split(",")
        if len(parts) < 4:
            return None
        source, msg_type, msg_id = parts[0], parts[1], parts[2]
        data = ",".join(parts[3:])
        return HardwareMessage(
            source=_norm_token(source),
            msg_type=_norm_token(msg_type),
            msg_id=_norm_token(msg_id),
            data=(data or "").strip(),
            raw=line,
        )

    legacy = _LEGACY_LINES.get(_norm_token(line))
    if legacy is None:
        return None
    source, msg_type, msg_id, data = legacy
    return HardwareMessage(
        source=source,
        msg_type=msg_type,
        msg_id=msg_id,
        data=data,
        raw=line,
    )


def is_ready_message(msg: HardwareMessage | None) -> bool:
    if msg is None:
        return False
    return msg.msg_type == TYPE_SYS and msg.msg_id == "READY"


def navigation_action(msg: HardwareMessage, *, invert: bool = False) -> str | None:
    """Map Mega nav encoder / shaft-switch messages to app actions.

    Returns ``forward`` | ``backward`` | ``activate``, or None if unrelated.
    The Pi decides what those actions mean for the current screen.
    """
    if msg.source != SOURCE_MEGA:
        return None

    data_u = _norm_token(msg.data)
    action: str | None = None

    if msg.msg_type == TYPE_ENCODER and msg.msg_id == ID_NAV:
        if data_u in ("RIGHT", "CW", "FORWARD", "FWD"):
            action = "forward"
        elif data_u in ("LEFT", "CCW", "BACKWARD", "BACK", "PREV"):
            action = "backward"
        elif data_u in ("PUSH", "PRESS", "CLICK", "SELECT"):
            # Allow encoder DATA=PUSH if a sketch doesn't split the shaft switch.
            action = "activate"
    elif msg.msg_type == TYPE_BUTTON and msg.msg_id == ID_NAV:
        if data_u in ("PRESSED", "PRESS", "PUSH", "CLICK", "DOWN"):
            action = "activate"
        # RELEASED ignored — Pi may use it later for hold detection.

    if action is None:
        return None
    if invert and action in ("forward", "backward"):
        return "backward" if action == "forward" else "forward"
    return action


def encode_nav_encoder(direction: str) -> str:
    """Firmware helper mirror: RIGHT/LEFT → wire line."""
    return format_message(SOURCE_MEGA, TYPE_ENCODER, ID_NAV, _norm_token(direction))


def encode_nav_button(state: str = "PRESSED") -> str:
    return format_message(SOURCE_MEGA, TYPE_BUTTON, ID_NAV, _norm_token(state))


def encode_ready(source: str = SOURCE_MEGA) -> str:
    return format_message(source, TYPE_SYS, "READY", "1")


def encode_light(light_id: str, state: str) -> str:
    """Pi → Mega lighting command."""
    return format_message(SOURCE_PI, TYPE_LIGHT, _norm_token(light_id), _norm_token(state))
