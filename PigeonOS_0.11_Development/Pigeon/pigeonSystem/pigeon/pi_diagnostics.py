"""Linux / Raspberry Pi startup checks for metadata and TMDb."""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from pigeon.runtime_paths import pigeon_state_dir


def pigeon_log_path() -> Path:
    return pigeon_state_dir() / "pigeon.log"


def append_pigeon_log(line: str) -> None:
    """Append one timestamped line to ``~/.pigeon_0_6/pigeon.log`` (best-effort)."""
    text = (line or "").strip()
    if not text:
        return
    try:
        p = pigeon_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts}  {text}\n")
    except OSError:
        pass


def _emit(line: str) -> None:
    sys.stderr.write(line + "\n")
    sys.stderr.flush()
    append_pigeon_log(line)


def run_linux_startup_checks() -> None:
    """Log actionable warnings when metadata / TMDb prerequisites are missing on Pi."""
    if sys.platform != "linux":
        return

    state = pigeon_state_dir()
    state.mkdir(parents=True, exist_ok=True)

    try:
        from pigeon.tmdb_poster import tmdb_is_configured
    except Exception:

        def tmdb_is_configured() -> bool:  # type: ignore[misc]
            return False

    if not tmdb_is_configured():
        _emit(
            "pigeon [Pi]: TMDb is not configured — artwork will not load. "
            f"Create {state / 'tmdb_api_key'} (one line, from themoviedb.org) "
            "or copy ~/.pigeon_0_6 from your Mac."
        )
    else:
        _emit("pigeon [Pi]: TMDb API key found — artwork fetch enabled.")

    try:
        from pigeon.media_folders import ensure_tmdb_media_dirs

        ensure_tmdb_media_dirs()
    except Exception:
        pass

    try:
        import pyatv  # noqa: F401
    except ImportError:
        _emit(
            "pigeon [Pi]: pyatv is not installed — Apple TV metadata will not work. "
            "Re-run Install-Pigeon or: pip install pyatv"
        )

    cred = state / "pyatv_credentials"
    if not cred.is_file() or cred.stat().st_size < 32:
        _emit(
            "pigeon [Pi]: No Apple TV pairing on this device — "
            f"pair in Pigeon (Find device) or copy {cred} from a Mac that already works."
        )

    if shutil.which("avahi-daemon") is None:
        _emit(
            "pigeon [Pi]: avahi-daemon is not installed — LAN device discovery may fail. "
            "Run: sudo apt install avahi-daemon libnss-mdns"
        )
    else:
        try:
            import subprocess

            r = subprocess.run(
                ["systemctl", "is-active", "avahi-daemon"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if r.returncode != 0 and (r.stdout or "").strip().lower() != "active":
                _emit(
                    "pigeon [Pi]: avahi-daemon is not running — "
                    "sudo systemctl enable --now avahi-daemon"
                )
        except Exception:
            pass

    try:
        from pigeon.layout_paths import pigeon_python_dir

        assets = pigeon_python_dir() / "pigeonAssets"
        bar = assets / "pigeonNowPlaying_Bar.png"
        if not bar.is_file():
            _emit(
                f"pigeon [Pi]: status bar assets missing under {assets} — "
                "tap Updates (or run installer/pi_update_from_github.sh) to pull pigeonAssets from GitHub."
            )
    except Exception:
        pass
