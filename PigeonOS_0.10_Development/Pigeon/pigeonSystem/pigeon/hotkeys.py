"""Human-readable hotkey list for Developer settings (keep in sync with pigeon_0_5 binds)."""

from __future__ import annotations

# (key combo, description)
PIGEON_HOTKEY_ROWS: list[tuple[str, str]] = [
    ("Tab", "Toggle settings_main ↔ off (does not enter Grid)"),
    ("Shift+Tab", "Toggle settings_main ↔ off (same as Tab)"),
    ("Ctrl+Tab", "Same as Shift+Tab"),
    ("Ctrl+Shift+Tab", "Open advanced capability matrix (extension build)"),
    ("F9", "Same as Shift+Tab"),
    (
        "Return / Enter",
        "Grid overlay (key 5) or developer grid: open command bar. Normal view / settings_main: Select on the player (or activate in settings).",
    ),
    ("Return / Enter (in command bar)", "Submit command and close bar"),
    (
        "Command bar (TMDb)",
        "Title only: searches movies and TV; auto mode prefers TV on popularity ties. Prefix tv <title> or movie <title> to force. tmdb … same.",
    ),
    ("Esc", "Close command bar if open; otherwise quit"),
    (
        "Space",
        "Play/pause on the current player when possible; else if a TMDb backdrop is saved: show backdrop + title logo; "
        "else landing brightness pulse (when scene is on).",
    ),
    (
        "1, 4, 5",
        "Views: 1 five-zone now-playing (circles); "
        "4 plain background + rawTitle debug text only; "
        "5 design grid overlay (19×8) on the composite. "
        "Clock saver still appears automatically on idle (no dedicated view shortcut).",
    ),
    (
        "Arrow keys (Pigeon_ext)",
        "Plain arrows: TV navigation. Shift+arrows: volume / skip. Cmd+arrows: TV back, home, power (device-dependent).",
    ),
    (
        "Rotary encoder (USB)",
        "CW=Right (forward), CCW=Left (backward), PUSH=Space (activate). "
        "HID boards emit keys directly; Arduino UNO Q uses Monitor+Bridge "
        "(TCP :7500 via adb forward, or USB CDC) through pigeon.rotary_serial.",
    ),
    ("S", "Toggle scene on/off (only while grid overlay is visible: key 5 or developer grid)"),
    (
        "F10",
        "While grid overlay is visible: cycle scene landing → black → TMDb backdrop → landing. "
        "Otherwise: toggle scene on/off.",
    ),
    ("Double-click video", "Toggle scene"),
    ("Ctrl+Shift+S", "Toggle scene"),
    ("Right-click video", "Same as Shift+Tab (settings_main ↔ off)"),
    (
        "Apple TV (settings)",
        "Apple TV → TMDb: settings_main / pigeon settings discover and pair players; playback poll drives TMDb art (needs pyatv + pairing; see pigeon/apple_tv_now_playing.py).",
    ),
]


def format_hotkey_help_text() -> str:
    lines = [f"{keys}\t{desc}" for keys, desc in PIGEON_HOTKEY_ROWS]
    return "\n".join(lines)
