"""
Cached TMDB assets:

- Backdrops in ``pigeonTMDB/pigeonTMDB_BD``
- Title treatments (logos) in ``pigeonTMDB/pigeonTMDB_TT``
- Posters in ``pigeonTMDB/pigeonTMDB_Poster``

Title is sanitized (same rules as poster export filenames).

``LogoEn`` holds the English title logo at TMDb ``w1280`` (or legacy smaller pulls); the UI scales it
into each layout’s logo rectangle.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pigeon.media_folders import (
    TMDB_MEDIA_MAX_FILES,
    ensure_reformatted_media_dir,
    pigeon_tmdb_poster_dir,
    pigeon_reformatted_media_dir,
    pigeon_tmdb_bd_dir,
    pigeon_tmdb_tt_dir,
    trim_dir_to_max_files,
)
from pigeon.widgets.poster_art import sanitize_reformatted_poster_title

ASSET_POSTER_ART = "PosterArt"
ASSET_LOGO = "Logo"
ASSET_LOGO_EN = "LogoEn"
ASSET_BACKDROP = "Backdrop"

_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def title_key(display_title: str) -> str:
    return sanitize_reformatted_poster_title(display_title) or "unknown"


def _asset_dir_for_type(asset_type: str) -> Path:
    if asset_type == ASSET_BACKDROP:
        return pigeon_tmdb_bd_dir()
    if asset_type == ASSET_POSTER_ART:
        return pigeon_tmdb_poster_dir()
    if asset_type in (ASSET_LOGO, ASSET_LOGO_EN):
        return pigeon_tmdb_tt_dir()
    return pigeon_reformatted_media_dir()


def find_cached_reformatted_asset(title_key_str: str, asset_type: str) -> Path | None:
    """Return first existing file ``{title_key}_{asset_type}{ext}`` or None."""
    d = _asset_dir_for_type(asset_type)
    for ext in _EXTS:
        p = d / f"{title_key_str}_{asset_type}{ext}"
        if p.is_file():
            return p
    return None


def copy_pulled_to_reformatted(src: Path, title_key_str: str, asset_type: str) -> Path:
    """Copy ``src`` to reformatted folder with ``{title}_{asset}{ext}``."""
    ensure_reformatted_media_dir()
    dest_dir = _asset_dir_for_type(asset_type)
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    if ext not in _EXTS:
        ext = ".jpg"
    dest = dest_dir / f"{title_key_str}_{asset_type}{ext}"
    shutil.copy2(src, dest)
    if asset_type in (ASSET_BACKDROP, ASSET_LOGO, ASSET_LOGO_EN, ASSET_POSTER_ART):
        trim_dir_to_max_files(dest_dir, max_files=TMDB_MEDIA_MAX_FILES)
    return dest
