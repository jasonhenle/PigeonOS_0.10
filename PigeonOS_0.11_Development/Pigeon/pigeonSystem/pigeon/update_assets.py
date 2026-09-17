"""Ship UI/media assets with GitHub app updates (status bar, logos, poster chrome)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from pigeon.update_check import (
    _ascii_only,
    _branch_candidates,
    _minimal_subprocess_env,
    github_auth_headers,
    github_http_get,
)

_UA = "Pigeon/0.8 (asset-sync)"

# Directories merged from the update source into the install root.
APP_ASSET_DIRS: tuple[str, ...] = ("pigeonAssets",)

# Minimum files needed for status bar, poster widget, and splash on Pi.
REQUIRED_ASSET_PATHS: tuple[str, ...] = (
    "pigeonAssets/view_circles.svg",
    "pigeonAssets/view_circles_music.svg",
    "pigeonAssets/settings_0.8/settings_main.svg",
    "pigeonAssets/settings_0.8/settings_main_box1_pigeon_logo.png",
    "pigeonAssets/settings_0.8/settings_pigeon.svg",
    "pigeonAssets/settings/general/widget_general_settings_background.svg",
    "pigeonAssets/settings/general/widget_general_0_exit.svg",
    "pigeonAssets/settings/main/widget_sm_01_dual.svg",
    "pigeonAssets/settings/main/widget_sm_02-03-04_container.svg",
    "pigeonAssets/settings/pigeon/settings_pigeon.svg",
    "pigeonAssets/settings_0.8/pigeon_settings_preferences.svg",
    "pigeonAssets/settings_0.8/settings_update_popup.svg",
    "pigeonAssets/settings/keyboard/keyboard_bottom_row.svg",
    "pigeonAssets/settings/keyboard/keyboard_lower.svg",
    "pigeonAssets/settings/keyboard/keyboard_upper.svg",
    "pigeonAssets/settings/keyboard/keyboard_numeric.svg",
    "pigeonAssets/settings/keyboard/keyboard_pin.svg",
    "pigeonAssets/settings/keyboard/keyboard_ip.svg",
    "pigeonAssets/settings/keyboard/keyboard_symbolic.svg",
    "pigeonAssets/pigeonNowPlaying_Bar.png",
    "pigeonAssets/pigeonNowPlaying_TC_container.png",
    "pigeonAssets/pigeonNowPlaying_TC_elaspsed.png",
    "pigeonAssets/pigeonNowPlaying_TC_remaining.png",
    "pigeonAssets/App logos/AppLogo_Pigeon.png",
)

_REPO_APP_PREFIXES: tuple[str, ...] = (
    "PigeonOS_0.11_Development/Pigeon",
    "PigeonOS_0.9_Development/Pigeon",
    "Pigeon",
)


def _repo_app_prefixes() -> list[str]:
    override = _ascii_only(os.environ.get("PIGEON_UPDATE_APP_PREFIX", "").strip().strip("/"))
    if override:
        return [override]
    return list(_REPO_APP_PREFIXES[:2])


def missing_required_assets(install_root: Path) -> list[str]:
    """Return repo-relative paths under ``install_root`` that are absent."""
    root = install_root.resolve()
    out: list[str] = []
    for rel in REQUIRED_ASSET_PATHS:
        if not (root / rel).is_file():
            out.append(rel)
    return out


def _rsync_tree(source: Path, dest: Path) -> tuple[bool, str]:
    if not source.is_dir():
        return True, f"skipped (no {source.name} in update)"
    dest.mkdir(parents=True, exist_ok=True)
    if shutil.which("rsync"):
        cmd = ["rsync", "-a", f"{source}/", f"{dest}/"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                env=_minimal_subprocess_env(),
            )
        except (OSError, UnicodeEncodeError) as e:
            return False, str(e)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            return False, err or f"rsync exited {proc.returncode}"
        return True, "synced"
    try:
        shutil.copytree(source, dest, dirs_exist_ok=True)
    except OSError as e:
        return False, str(e)
    return True, "copied"


def sync_app_assets(source_root: Path, install_root: Path) -> tuple[bool, str]:
    """
    Merge ``pigeonAssets/`` (and related dirs) from the GitHub zip into the install folder.

    Keeps existing ``pigeonTMDB/`` cache and other excluded trees untouched.
    """
    source_root = source_root.resolve()
    install_root = install_root.resolve()
    parts: list[str] = []
    for name in APP_ASSET_DIRS:
        src = source_root / name
        dst = install_root / name
        ok, msg = _rsync_tree(src, dst)
        if not ok:
            return False, f"Could not update {name}: {msg}"
        if src.is_dir():
            try:
                count = sum(1 for p in src.rglob("*") if p.is_file())
            except OSError:
                count = 0
            parts.append(f"{name} ({count} files)")
    if not parts:
        return True, "No asset folders in update package."
    return True, ", ".join(parts)


def fetch_missing_assets_from_github(
    install_root: Path,
    missing: list[str],
    *,
    branch: str | None = None,
    timeout_s: float = 45.0,
) -> tuple[list[str], list[str]]:
    """
    Download individual missing asset files from raw.githubusercontent.com.

    Returns ``(still_missing, fetched_paths)``.
    """
    if not missing:
        return [], []

    br = _ascii_only((branch or _branch_candidates()[0]).strip())
    user = _ascii_only(os.environ.get("PIGEON_UPDATE_GITHUB_USER", "jasonhenle").strip())
    repo = _ascii_only(os.environ.get("PIGEON_UPDATE_GITHUB_REPO", "PigeonOS_0.10").strip())
    headers = github_auth_headers(user_agent=_UA)
    install_root = install_root.resolve()

    still: list[str] = []
    fetched: list[str] = []
    for rel in missing:
        rel_clean = rel.lstrip("/")
        body: bytes | None = None
        for prefix in _repo_app_prefixes():
            url = f"https://raw.githubusercontent.com/{user}/{repo}/{br}/{prefix}/{rel_clean}"
            try:
                body = github_http_get(url, timeout_s=timeout_s, headers=headers)
            except OSError:
                body = None
            if body and len(body) >= 16:
                break
            body = None
        if not body:
            still.append(rel)
            continue
        dest = install_root / rel_clean
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            fetched.append(rel)
        except OSError:
            still.append(rel)
    return still, fetched


def ensure_required_assets(
    install_root: Path,
    *,
    source_root: Path | None = None,
    branch: str | None = None,
) -> tuple[bool, str]:
    """
    Sync assets from ``source_root`` when present, then fill any gaps from GitHub raw.

    Returns ``(ok, summary_message)``.
    """
    lines: list[str] = []
    if source_root is not None:
        ok, msg = sync_app_assets(source_root, install_root)
        if not ok:
            return False, msg
        lines.append(f"Assets updated: {msg}")

    missing = missing_required_assets(install_root)
    if missing and source_root is not None:
        # Zip may nest assets elsewhere; one more sync attempt is enough — fetch fills gaps.
        pass

    if missing:
        still, fetched = fetch_missing_assets_from_github(
            install_root, missing, branch=branch
        )
        if fetched:
            lines.append(f"Pulled {len(fetched)} missing asset(s) from GitHub.")
        missing = still

    if missing:
        sample = ", ".join(missing[:4])
        extra = f" (+{len(missing) - 4} more)" if len(missing) > 4 else ""
        lines.append(
            f"Warning: {len(missing)} required asset(s) still missing ({sample}{extra}). "
            "Reinstall from GitHub Releases or run installer/install_from_github.sh if the UI looks wrong."
        )
        return False, "\n".join(lines)

    if not lines:
        lines.append("All required UI assets are present.")
    return True, "\n".join(lines)
