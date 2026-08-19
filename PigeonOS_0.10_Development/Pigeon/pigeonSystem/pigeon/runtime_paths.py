"""Canonical user-local paths for Pigeon 0.6+ (state, credentials, TMDb logs).

Override with env ``PIGEON_STATE_DIR`` (absolute or ``~``-expandable path).

On first use, if ``~/.pigeon_0_6`` is missing and a legacy ``~/.pigeon_0_5``
directory exists, it is **renamed** to ``~/.pigeon_0_6`` so existing installs
keep their data without duplicating.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_CACHED: Path | None = None
_LEGACY_MIGRATION_ATTEMPTED = False

# Display string for help text / UI (no trailing slash).
PIGEON_STATE_DIR_TILDE = "~/.pigeon_0_6"


def pigeon_state_dir() -> Path:
    """Directory for state.json, pyatv credentials, TMDb tokens, JSONL logs, etc."""
    global _CACHED, _LEGACY_MIGRATION_ATTEMPTED
    if _CACHED is not None:
        return _CACHED

    raw = os.environ.get("PIGEON_STATE_DIR", "").strip()
    if raw:
        _CACHED = Path(raw).expanduser().resolve()
        return _CACHED

    home = Path.home()
    new_dir = home / ".pigeon_0_6"
    legacy = home / ".pigeon_0_5"

    if not _LEGACY_MIGRATION_ATTEMPTED:
        _LEGACY_MIGRATION_ATTEMPTED = True
        if not new_dir.exists() and legacy.is_dir():
            try:
                shutil.move(str(legacy), str(new_dir))
            except OSError:
                # Permission / concurrent use — continue with fresh or existing tree
                pass

    _CACHED = new_dir
    return _CACHED
