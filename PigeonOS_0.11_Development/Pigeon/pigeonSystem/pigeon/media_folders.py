"""App-level TMDB media paths and purge helpers."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from pigeon.layout_paths import pigeon_python_dir

# Cap TMDB display-media file count (oldest by mtime removed first).
TMDB_MEDIA_MAX_FILES = 20


def pigeon_app_dir() -> Path:
    """Directory containing Pigeon_python (e.g. P0.5_App)."""
    return pigeon_python_dir().parent


def pigeon_tmdb_root_dir() -> Path:
    return pigeon_python_dir() / "pigeonTMDB"


def pigeon_tmdb_original_dir() -> Path:
    return pigeon_tmdb_root_dir() / "pigeonTMDB_ORIGINAL"


def pigeon_tmdb_bd_dir() -> Path:
    return pigeon_tmdb_root_dir() / "pigeonTMDB_BD"


def pigeon_tmdb_tt_dir() -> Path:
    return pigeon_tmdb_root_dir() / "pigeonTMDB_TT"


def pigeon_tmdb_poster_dir() -> Path:
    return pigeon_tmdb_root_dir() / "pigeonTMDB_Poster"


def pigeon_pulled_media_dir() -> Path:
    # Canonical "original fetched media" directory.
    return pigeon_tmdb_original_dir()


def pigeon_reformatted_media_dir() -> Path:
    # Kept for compatibility with older call sites. New code should use BD/TT helpers directly.
    return pigeon_tmdb_root_dir()


def ensure_reformatted_media_dir() -> Path:
    return ensure_tmdb_media_dirs()


def ensure_tmdb_media_dirs() -> Path:
    """Create pigeonTMDB cache folders (BD, TT, Poster, staging)."""
    root = pigeon_tmdb_root_dir()
    for sub in (
        "pigeonTMDB_ORIGINAL",
        "pigeonTMDB_BD",
        "pigeonTMDB_TT",
        "pigeonTMDB_Poster",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def legacy_pigeondata_pulled_dir() -> Path:
    """Older layout: P0.5_App/pigeonData/pigeonPulledMedia (superseded by app root)."""
    return pigeon_app_dir() / "pigeonData" / "pigeonPulledMedia"


def legacy_pigeondata_reformatted_dir() -> Path:
    return pigeon_app_dir() / "pigeonData" / "pigeonReFormattedMedia"


def _sha256_file(path: Path, max_bytes: int = 50 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    n = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            n += len(chunk)
            if n > max_bytes:
                raise ValueError("file too large for hash compare")
            h.update(chunk)
    return h.hexdigest()


def _same_file_content(a: Path, b: Path) -> bool:
    if not a.is_file() or not b.is_file():
        return False
    if a.stat().st_size != b.stat().st_size:
        return False
    try:
        return _sha256_file(a) == _sha256_file(b)
    except (OSError, ValueError):
        return False


def _merge_one_file(src: Path, dest_dir: Path) -> str:
    """Move ``src`` into ``dest_dir``. Returns a short log line."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if not dest.exists():
        shutil.move(str(src), str(dest))
        return f"moved {src.name} → {dest_dir.name}/"
    if _same_file_content(src, dest):
        src.unlink()
        return f"removed duplicate {src.name} (identical to {dest_dir.name}/)"
    stem, suf = dest.stem, dest.suffix
    alt = dest_dir / f"{stem}_from_pigeonData{suf}"
    i = 2
    while alt.exists():
        alt = dest_dir / f"{stem}_from_pigeonData_{i}{suf}"
        i += 1
    shutil.move(str(src), str(alt))
    return f"moved collision {src.name} → {alt.name}"


def consolidate_legacy_pigeondata_media_folders() -> list[str]:
    """
    One-time migration: merge ``pigeonData/pigeonPulledMedia`` and
    ``pigeonData/pigeonReFormattedMedia`` into the canonical app-root folders, then
    remove the empty legacy directories.

    Safe to call repeatedly (no-op if legacy folders are missing or empty).
    """
    logs: list[str] = []
    pairs = (
        (legacy_pigeondata_pulled_dir(), pigeon_pulled_media_dir()),
        (legacy_pigeondata_reformatted_dir(), pigeon_reformatted_media_dir()),
    )
    for legacy, canonical in pairs:
        if not legacy.is_dir():
            continue
        try:
            names = sorted(legacy.iterdir(), key=lambda p: p.name.lower())
        except OSError as e:
            logs.append(f"skip {legacy.name}: {e}")
            continue
        for child in names:
            if child.is_file():
                try:
                    logs.append(_merge_one_file(child, canonical))
                except OSError as e:
                    logs.append(f"error {child.name}: {e}")
            elif child.is_dir():
                logs.append(f"skip subfolder in {legacy}: {child.name}/ (move manually if needed)")
        try:
            if legacy.is_dir() and not any(legacy.iterdir()):
                legacy.rmdir()
                logs.append(f"removed empty {legacy.relative_to(pigeon_app_dir())}")
        except OSError:
            pass

    pigeondata = pigeon_app_dir() / "pigeonData"
    try:
        if pigeondata.is_dir() and not any(pigeondata.iterdir()):
            pigeondata.rmdir()
            logs.append("removed empty pigeonData/")
    except OSError:
        pass
    return logs


def trim_dir_to_max_files(path: Path, *, max_files: int = TMDB_MEDIA_MAX_FILES) -> None:
    """Keep at most ``max_files`` regular files in ``path``; delete oldest (mtime) first."""
    if max_files < 1:
        return
    d = path
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    try:
        files = [p for p in d.iterdir() if p.is_file()]
    except OSError:
        return
    if len(files) <= max_files:
        return
    files.sort(key=lambda p: p.stat().st_mtime)
    for p in files[: len(files) - max_files]:
        try:
            p.unlink()
        except OSError:
            pass


def trim_pulled_media_dir(*, max_files: int = TMDB_MEDIA_MAX_FILES) -> None:
    """Compatibility wrapper for older call sites."""
    trim_dir_to_max_files(pigeon_pulled_media_dir(), max_files=max_files)


def purge_directory_contents(path: Path) -> tuple[bool, str]:
    """Remove all files and subdirectories inside ``path`` (not ``path`` itself)."""
    if not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, str(e)
        return True, "Folder did not exist; created empty folder."
    if not path.is_dir():
        return False, f"Not a directory: {path}"
    errors: list[str] = []
    try:
        for child in path.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError as e:
                errors.append(f"{child.name}: {e}")
    except OSError as e:
        return False, str(e)
    if errors:
        return False, "; ".join(errors)
    return True, f"Emptied {path.name}."


if __name__ == "__main__":
    # Run from Pigeon_python: python -m pigeon.media_folders
    for _line in consolidate_legacy_pigeondata_media_folders():
        print(_line)
