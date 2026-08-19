"""Resolve Pigeon0.5 asset folders after renames (Desktop / iCloud, flexible folder names).

File names inside asset folders are still expected to match a known stem prefix
(``P_0.5_posterArt_4x6_MEDIUM_*`` or legacy ``P_0.5_WIDGET_POSTER_4x6_MEDIUM_*``).
"""

from __future__ import annotations

from pathlib import Path

_ICLOUD = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"

# New canonical stem first; legacy widget stem second (border finds the active folder).
POSTER_ART_STEMS = (
    "P_0.5_posterArt_4x6_MEDIUM",
    "P_0.5_WIDGET_POSTER_4x6_MEDIUM",
)
# Back-compat aliases (single legacy stem).
POSTER_STEM = POSTER_ART_STEMS[1]
POSTER_BORDER_NAME = f"{POSTER_STEM}_border.png"


def dir_has_poster_border(d: Path) -> bool:
    """True if ``d`` contains any recognized …_border.png for poster compositing."""
    if not d.is_dir():
        return False
    for stem in POSTER_ART_STEMS:
        if (d / f"{stem}_border.png").is_file():
            return True
    return False


def poster_stem_for_dir(d: Path) -> str | None:
    """Return the stem that matches an on-disk border in ``d`` (prefers POSTER_ART_STEMS order)."""
    if not d.is_dir():
        return None
    for stem in POSTER_ART_STEMS:
        if (d / f"{stem}_border.png").is_file():
            return stem
    return None

# App bundle folder (sibling of Pigeon_python).
_APP_DIR_NAMES = ("P0.5_App", "P0.5_Code", "App", "0.5_App", "P05_App")

# Middle “assets” container (optional in some layouts).
_ASSET_ROOT_NAMES = ("P0.5_Assets", "Assets", "0.5_Assets", "P05_Assets", "Media")

# Folder that contains poster widget subfolders (or is scanned for a poster leaf).
_WIDGETS_DIR_NAMES = ("P0.5_Widgets", "Widgets", "0.5_Widgets", "P05_Widgets", "UI_Widgets")

# Video scenes container names.
_SCENE_DIR_NAMES = ("P0.5_Scenes", "Scenes", "Scene", "Video", "Videos", "0.5_Scenes", "P05_Scenes")

# Shared logos / art (e.g. P_0.5_pigeonLogo_4x6_MEDIUM_pigeonLogo.png).
_PIGEON_LOGO_ASSET_DIR_NAMES = (
    "pigeonAssets",
    "PigeonAssets",
    "pigeon_assets",
    "Pigeon_Assets",
    "P0.5_pigeonAssets",
)

# Known poster widget folder names (legacy + common renames).
_POSTER_LEAF_DIR_NAMES = (
    "pigeonAssets",
    "PigeonAssets",
    "P0.5_WIDGET_POSTER_4X6_MEDIUM",
    "P0.5_Widget_Poster_4X6_Medium",
    "Widget_Poster_4x6_Medium",
    "Poster_4x6_Medium",
    "poster_4x6_medium",
    "Poster4x6Medium",
)


def _pigeon_package_dir() -> Path:
    return Path(__file__).resolve().parent


def pigeon_python_dir() -> Path:
    """Project root (contains pigeonAssets / pigeonTMDB for the current build layout)."""
    code_home = _pigeon_package_dir().parent
    if (code_home / "pigeonAssets").is_dir():
        return code_home
    parent = code_home.parent
    if (parent / "pigeonAssets").is_dir():
        return parent
    return code_home


def pigeon05_bundle_root() -> Path | None:
    """
    Pigeon0.5 root when the app lives at …/Pigeon0.5/<app>/Pigeon_python/pigeon/…
    """
    pp = pigeon_python_dir()
    app = pp.parent
    if app.name not in _APP_DIR_NAMES:
        return None
    return app.parent


def _dedupe(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _all_pigeon05_roots() -> list[Path]:
    roots: list[Path] = []
    br = pigeon05_bundle_root()
    if br is not None:
        roots.append(br)
    h = Path.home()
    roots.extend(
        [
            h / "Desktop" / "Pigeon0.5",
            _ICLOUD / "Desktop" / "Pigeon0.5",
            _ICLOUD / "Pigeon0.5",
        ]
    )
    return _dedupe(roots)


def _collect_scene_dirs_under_bundle(bundle: Path, out: list[Path]) -> None:
    if not bundle.is_dir():
        return

    def add_scene_path(p: Path) -> None:
        if p.is_dir():
            out.append(p)

    for app in _APP_DIR_NAMES:
        ap = bundle / app
        if not ap.is_dir():
            continue
        for ar in _ASSET_ROOT_NAMES:
            aroot = ap / ar
            if not aroot.is_dir():
                continue
            for sn in _SCENE_DIR_NAMES:
                add_scene_path(aroot / sn)
        for sn in _SCENE_DIR_NAMES:
            add_scene_path(ap / sn)

    for ar in _ASSET_ROOT_NAMES:
        aroot = bundle / ar
        if not aroot.is_dir():
            continue
        for sn in _SCENE_DIR_NAMES:
            add_scene_path(aroot / sn)

    for sn in _SCENE_DIR_NAMES:
        add_scene_path(bundle / sn)


def scene_dir_candidates() -> list[Path]:
    out: list[Path] = []
    for bundle in _all_pigeon05_roots():
        _collect_scene_dirs_under_bundle(bundle, out)
    return _dedupe(out)


# Skip when scanning the bundle for a default scene (code, venv, old prototypes).
_SKIP_DIR_NAMES = frozenset(
    {
        ".venv",
        "__pycache__",
        ".git",
        "node_modules",
        ".cursor",
        "pigeonOld",
        ".Trash",
        "P0.5_Docs",
    }
)

# Filename / path substring hints (original clip was SCEENE_001_60SECS_scarytrain_718…).
_SCENE_NAME_HINTS = (
    "scarytrain",
    "sceene",
    "scene",
    "718",
    "300",
    "60sec",
    "60_secs",
    "scary",
    "train",
    "p0.5",
    "pigeon",
    "background",
    "loop",
    "sceene_001",
)


def _is_video_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in (".mp4", ".mov", ".m4v")


def _videos_under(root: Path, *, max_depth: int) -> list[Path]:
    """List video files under root up to max_depth (0 = files in root only)."""
    out: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth or not d.is_dir():
            return
        if d.name in _SKIP_DIR_NAMES:
            return
        try:
            for p in d.iterdir():
                if _is_video_file(p):
                    out.append(p)
                elif p.is_dir():
                    walk(p, depth + 1)
        except OSError:
            pass

    if root.is_dir():
        walk(root, 0)
    return out


def discover_scene_video_files() -> list[Path]:
    """
    Find .mp4 / .mov under known scene directories, then elsewhere under Pigeon0.5
    (excluding venv, pigeonOld, etc.) if none found there.
    """
    found: list[Path] = []
    for sd in scene_dir_candidates():
        found.extend(_videos_under(sd, max_depth=4))
    found = _dedupe(found)
    if found:
        return found
    for bundle in _all_pigeon05_roots():
        if not bundle.is_dir():
            continue
        found.extend(_videos_under(bundle, max_depth=12))
    return _dedupe(found)


def _scene_path_score(p: Path) -> int:
    blob = f"{p.parent.name}/{p.name}".lower()
    return sum(1 for t in _SCENE_NAME_HINTS if t in blob)


def _video_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def pick_default_scene_video() -> Path | None:
    """
    Choose the best default scene file among discovered videos (hint scoring + newest tie-break).
    """
    cands = discover_scene_video_files()
    if not cands:
        return None
    cands.sort(key=lambda p: (-_scene_path_score(p), -_video_mtime(p), str(p).lower()))
    return cands[0]


# ``pigeonAssets/App logos/`` — kept in sync with ``streaming_service_badges.APP_LOGOS_REL_DIR``
# without creating an import cycle (this module is imported very early).
_APP_LOGOS_REL_DIR = "App logos"

# Pigeon's own logo is canonical: one filename, one folder, no fallbacks.
_PIGEON_LOGO_FILENAME = "AppLogo_Pigeon.png"


def _collect_pigeon_assets_dirs_under_bundle(bundle: Path, out: list[Path]) -> None:
    if not bundle.is_dir():
        return

    def add(p: Path) -> None:
        if p.is_dir():
            out.append(p)

    for app in _APP_DIR_NAMES:
        ap = bundle / app
        if not ap.is_dir():
            continue
        # e.g. P0.5_App/Pigeon_python/pigeonAssets (code-adjacent, not under P0.5_Assets)
        code_home = ap / "Pigeon_python"
        if code_home.is_dir():
            for pn in _PIGEON_LOGO_ASSET_DIR_NAMES:
                add(code_home / pn)
        for ar in _ASSET_ROOT_NAMES:
            aroot = ap / ar
            if not aroot.is_dir():
                continue
            for pn in _PIGEON_LOGO_ASSET_DIR_NAMES:
                add(aroot / pn)
        for pn in _PIGEON_LOGO_ASSET_DIR_NAMES:
            add(ap / pn)

    for ar in _ASSET_ROOT_NAMES:
        aroot = bundle / ar
        if not aroot.is_dir():
            continue
        for pn in _PIGEON_LOGO_ASSET_DIR_NAMES:
            add(aroot / pn)

    for pn in _PIGEON_LOGO_ASSET_DIR_NAMES:
        add(bundle / pn)


def pigeon_logo_asset_dir_candidates() -> list[Path]:
    """pigeonAssets next to the running Pigeon_python tree, then under bundle layout."""
    out: list[Path] = []
    pp = pigeon_python_dir()
    for pn in _PIGEON_LOGO_ASSET_DIR_NAMES:
        p = pp / pn
        if p.is_dir():
            out.append(p)
    for bundle in _all_pigeon05_roots():
        _collect_pigeon_assets_dirs_under_bundle(bundle, out)
    return _dedupe(out)


def pick_pigeon_logo_png(poster_widget_dir: Path | None = None) -> Path | None:
    """
    Return the single canonical Pigeon logo at ``App logos/AppLogo_Pigeon.png``, or ``None``.

    Search order (first hit wins):

      1. ``<poster_widget_dir>/App logos/AppLogo_Pigeon.png`` when a widget folder is supplied.
      2. ``<assets_root>/App logos/AppLogo_Pigeon.png`` for every discovered ``pigeonAssets`` root.

    No legacy filenames, no shipped package fallback, no heuristic walks — Pigeon's own
    logo is one file at one well-known path.
    """
    if poster_widget_dir is not None and poster_widget_dir.is_dir():
        p = poster_widget_dir / _APP_LOGOS_REL_DIR / _PIGEON_LOGO_FILENAME
        if p.is_file():
            return p

    for assets_root in pigeon_logo_asset_dir_candidates():
        p = assets_root / _APP_LOGOS_REL_DIR / _PIGEON_LOGO_FILENAME
        if p.is_file():
            return p
    return None


def _collect_widgets_parents_under_bundle(bundle: Path, out: list[Path]) -> None:
    if not bundle.is_dir():
        return

    def add(p: Path) -> None:
        if p.is_dir():
            out.append(p)

    for app in _APP_DIR_NAMES:
        ap = bundle / app
        if not ap.is_dir():
            continue
        for ar in _ASSET_ROOT_NAMES:
            aroot = ap / ar
            if not aroot.is_dir():
                continue
            for wn in _WIDGETS_DIR_NAMES:
                add(aroot / wn)
        for wn in _WIDGETS_DIR_NAMES:
            add(ap / wn)

    for ar in _ASSET_ROOT_NAMES:
        aroot = bundle / ar
        if not aroot.is_dir():
            continue
        for wn in _WIDGETS_DIR_NAMES:
            add(aroot / wn)

    for wn in _WIDGETS_DIR_NAMES:
        add(bundle / wn)


def poster_widget_parent_candidates() -> list[Path]:
    """Directories that may contain the poster widget leaf folder."""
    out: list[Path] = []
    for bundle in _all_pigeon05_roots():
        _collect_widgets_parents_under_bundle(bundle, out)
    return _dedupe(out)


def find_poster_widget_dir(widgets_parent: Path) -> Path | None:
    """
    Resolve the poster asset folder under a widgets parent.

    Tries known leaf names, then any subfolder that contains the expected border PNG.
    """
    if not widgets_parent.is_dir():
        return None
    for name in _POSTER_LEAF_DIR_NAMES:
        p = widgets_parent / name
        if p.is_dir() and dir_has_poster_border(p):
            return p
    try:
        for p in sorted(widgets_parent.iterdir()):
            if p.is_dir() and dir_has_poster_border(p):
                return p
    except OSError:
        pass
    return None


def poster_asset_dir_candidates() -> list[Path]:
    """Resolved poster widget directories (for diagnostics / search order)."""
    found: list[Path] = []
    for parent in poster_widget_parent_candidates():
        leaf = find_poster_widget_dir(parent)
        if leaf is not None:
            found.append(leaf)
    return _dedupe(found)
