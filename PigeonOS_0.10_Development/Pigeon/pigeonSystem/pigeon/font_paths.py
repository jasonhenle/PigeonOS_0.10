"""Resolve Sharp Sans Bold / Medium (or overrides) for widget text."""

from __future__ import annotations

import os
from pathlib import Path

_BOLD_FALLBACK_PATHS: tuple[Path, ...] = (
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/Library/Fonts/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
)

_REGULAR_FALLBACK_PATHS: tuple[Path, ...] = (
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
)

_BUNDLED_EXTRABOLD_NAMES: tuple[str, ...] = (
    "SharpSansExtrabold.otf",
    "Sharp Sans Extrabold.otf",
    "SharpSans-Extrabold.otf",
)

_BUNDLED_MEDIUM_NAMES: tuple[str, ...] = (
    "SharpSansMedium.otf",
    "Sharp Sans Medium.otf",
    "SharpSans-Medium.otf",
)

_BUNDLED_BOLD_NAMES: tuple[str, ...] = (
    "Sharp Sans Bold.otf",
    "SharpSansBold.otf",
)


def _pigeon_assets_fonts_dir() -> Path | None:
    """``pigeonAssets/fonts`` next to ``pigeonSystem`` (shipped with the app)."""
    d = Path(__file__).resolve().parents[2] / "pigeonAssets" / "fonts"
    return d if d.is_dir() else None


def _first_bundled_font(names: tuple[str, ...]) -> str | None:
    fonts_dir = _pigeon_assets_fonts_dir()
    if fonts_dir is None:
        return None
    for name in names:
        p = fonts_dir / name
        if p.is_file():
            return str(p)
    return None


def _font_search_roots() -> list[Path]:
    """macOS user/system dirs plus common Linux font trees (Raspberry Pi, etc.)."""
    candidates = (
        Path.home() / "Library" / "Fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
        Path("/System/Library/Fonts/Supplemental"),
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype/liberation"),
        Path("/usr/share/fonts/truetype/freefont"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".local" / "share" / "fonts",
        Path.home() / ".fonts",
    )
    return [p for p in candidates if p.is_dir()]


def _first_existing_path(*paths: Path | str) -> str | None:
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            return str(p)
    return None


def resolve_ui_font_book() -> str | None:
    """
    Return path to Sharp Sans (regular/Book weight), or ``None`` when nothing matches.

    Set ``PIGEON_FONT_BOOK`` to a ``.ttf``/``.otf`` path to override. The Sharp Sans
    family ships a plain ``Sharp Sans.otf`` file for its regular weight; that is
    what Pigeon treats as "Book". Italic / Light / Medium / Semibold / Bold weights
    are intentionally excluded by name so we don't accidentally return a heavier
    weight when the regular file is missing.
    """
    env = os.environ.get("PIGEON_FONT_BOOK")
    if env and Path(env).is_file():
        return env

    roots = _font_search_roots()
    # Prefer the exact "Sharp Sans.otf" / "Sharp Sans.ttf" filename (no weight suffix).
    exact_names = ("Sharp Sans.otf", "Sharp Sans.ttf", "SharpSans.otf", "SharpSans.ttf")
    for root in roots:
        for name in exact_names:
            p = root / name
            if p.is_file():
                return str(p)

    # As a last resort, glob for any Sharp Sans file whose weight tag indicates regular.
    banned_tags = (
        "bold", "light", "medium", "semibold", "extrabold", "thin", "italic",
    )
    for root in roots:
        for p in sorted(root.glob("*Sharp*Sans*.otf")) + sorted(root.glob("*Sharp*Sans*.ttf")):
            low = p.name.lower()
            if any(tag in low for tag in banned_tags):
                continue
            if p.is_file():
                return str(p)
    return _first_existing_path(*_REGULAR_FALLBACK_PATHS)


def resolve_ui_font_medium() -> str | None:
    """
    Return path to Sharp Sans Medium, or None to fall back elsewhere.

    Set PIGEON_FONT_MEDIUM to a .ttf/.otf/.ttc path to override.
    """
    env = os.environ.get("PIGEON_FONT_MEDIUM")
    if env and Path(env).is_file():
        return env

    bundled = _first_bundled_font(_BUNDLED_MEDIUM_NAMES)
    if bundled:
        return bundled

    roots = _font_search_roots()
    globs = (
        "*Sharp*Sans*Medium*.ttf",
        "*Sharp*Sans*Medium*.otf",
        "*SharpSans*Medium*.ttf",
        "*SharpSans*Medium*.otf",
    )
    for root in roots:
        for pattern in globs:
            for p in sorted(root.glob(pattern)):
                if p.is_file():
                    return str(p)

    # Often only one "Sharp Sans" file is installed; avoid Bold if possible via weight in name
    for root in roots:
        for p in sorted(root.glob("*Sharp*Sans*.otf")) + sorted(root.glob("*Sharp*Sans*.ttf")):
            if p.is_file() and "bold" not in p.name.lower():
                return str(p)
    return resolve_ui_font_book()


def resolve_ui_font_extrabold() -> str | None:
    """
    Return path to Sharp Sans ExtraBold, or None to fall back to Bold / Medium.

    Set PIGEON_FONT_EXTRABOLD to a .ttf/.otf path to override.
    """
    env = os.environ.get("PIGEON_FONT_EXTRABOLD")
    if env and Path(env).is_file():
        return env

    bundled = _first_bundled_font(_BUNDLED_EXTRABOLD_NAMES)
    if bundled:
        return bundled

    roots = _font_search_roots()
    globs = (
        "*Sharp*Sans*Extra*Bold*.otf",
        "*Sharp*Sans*Extra*Bold*.ttf",
        "*SharpSans*Extra*Bold*.otf",
        "*SharpSans*Extra*Bold*.ttf",
        "*Sharp*Sans*ExtraBold*.otf",
        "*Sharp*Sans*ExtraBold*.ttf",
    )
    for root in roots:
        for pattern in globs:
            for p in sorted(root.glob(pattern)):
                if p.is_file() and "italic" not in p.name.lower():
                    return str(p)
    return None


def resolve_ui_font_light_italic() -> str | None:
    """
    Sharp Sans Light Italic for very light UI labels (e.g. zone0 date).

    Set ``PIGEON_FONT_LIGHT_ITALIC`` to override. Falls back to Medium Italic,
    then Book Italic / Medium.
    """
    env = os.environ.get("PIGEON_FONT_LIGHT_ITALIC")
    if env and Path(env).is_file():
        return env

    bundled_names = (
        "Sharp Sans Light Italic.otf",
        "SharpSansLightItalic.otf",
        "SharpSans-LightItalic.otf",
    )
    bundled = _first_bundled_font(bundled_names)
    if bundled:
        return bundled

    roots = _font_search_roots()
    globs = (
        "*Sharp*Sans*Light*Italic*.otf",
        "*Sharp*Sans*Light*Italic*.ttf",
        "*SharpSans*Light*Italic*.otf",
        "*SharpSans*Light*Italic*.ttf",
    )
    for root in roots:
        for pattern in globs:
            for p in sorted(root.glob(pattern)):
                if p.is_file():
                    return str(p)

    return resolve_ui_font_medium_italic()


def resolve_ui_font_medium_italic() -> str | None:
    """
    Sharp Sans Medium Italic for lighter UI labels (e.g. zone0 date).

    Set ``PIGEON_FONT_MEDIUM_ITALIC`` to override. Falls back to Medium (roman),
    then Book Italic / Book when the italic cut is missing.
    """
    env = os.environ.get("PIGEON_FONT_MEDIUM_ITALIC")
    if env and Path(env).is_file():
        return env

    bundled_names = (
        "Sharp Sans Medium Italic.otf",
        "SharpSansMediumItalic.otf",
        "SharpSans-MediumItalic.otf",
    )
    bundled = _first_bundled_font(bundled_names)
    if bundled:
        return bundled

    roots = _font_search_roots()
    globs = (
        "*Sharp*Sans*Medium*Italic*.otf",
        "*Sharp*Sans*Medium*Italic*.ttf",
        "*SharpSans*Medium*Italic*.otf",
        "*SharpSans*Medium*Italic*.ttf",
    )
    for root in roots:
        for pattern in globs:
            for p in sorted(root.glob(pattern)):
                if p.is_file():
                    return str(p)

    # Book / regular italic as a lighter alternative.
    for root in roots:
        for pattern in (
            "*Sharp*Sans*Italic*.otf",
            "*SharpSans*Italic*.otf",
            "Sharp Sans Italic.otf",
        ):
            for p in sorted(root.glob(pattern)):
                name = p.name.lower()
                if not p.is_file():
                    continue
                if any(w in name for w in ("bold", "black", "heavy", "light", "thin", "semi")):
                    continue
                if "italic" in name or "oblique" in name:
                    return str(p)

    return resolve_ui_font_medium() or resolve_ui_font_book()


def resolve_ui_font_extrabold_italic() -> str | None:
    """
    Sharp Sans ExtraBold Italic for update-popup ``PigeonOS`` wordmark.

    Set ``PIGEON_FONT_EXTRABOLD_ITALIC`` to override. Falls back to ExtraBold
    (roman) when the italic cut is not installed on the device.
    """
    env = os.environ.get("PIGEON_FONT_EXTRABOLD_ITALIC")
    if env and Path(env).is_file():
        return env

    bundled_names = (
        "Sharp Sans ExtraBold Italic.otf",
        "SharpSansExtraBoldItalic.otf",
        "SharpSans-ExtraboldItalic.otf",
        "Sharp Sans Extrabold Italic.otf",
    )
    bundled = _first_bundled_font(bundled_names)
    if bundled:
        return bundled

    roots = _font_search_roots()
    globs = (
        "*Sharp*Sans*Extra*Bold*Italic*.otf",
        "*Sharp*Sans*Extra*Bold*Italic*.ttf",
        "*SharpSans*Extra*Bold*Italic*.otf",
        "*SharpSans*ExtraBold*Italic*.otf",
    )
    for root in roots:
        for pattern in globs:
            for p in sorted(root.glob(pattern)):
                if p.is_file():
                    return str(p)
    return resolve_ui_font_extrabold()


def resolve_ui_font_semibold() -> str | None:
    """
    Return path to Sharp Sans Semibold for settings / keyboard SVG labels.

    Set ``PIGEON_FONT_SEMIBOLD`` to a ``.ttf``/``.otf`` path to override.
    Falls back to Medium when Semibold is not installed.
    """
    env = os.environ.get("PIGEON_FONT_SEMIBOLD")
    if env and Path(env).is_file():
        return env

    bundled_names = (
        "Sharp Sans Semibold.otf",
        "SharpSansSemibold.otf",
        "SharpSans-Semibold.otf",
    )
    bundled = _first_bundled_font(bundled_names)
    if bundled:
        return bundled

    roots = _font_search_roots()
    globs = (
        "*Sharp*Sans*Semibold*.otf",
        "*Sharp*Sans*Semibold*.ttf",
        "*SharpSans*Semibold*.otf",
        "*SharpSans*Semibold*.ttf",
    )
    for root in roots:
        for pattern in globs:
            for p in sorted(root.glob(pattern)):
                if p.is_file() and "italic" not in p.name.lower():
                    return str(p)
    return resolve_ui_font_medium()


def resolve_ui_font_bold() -> str | None:
    """
    Return path to Sharp Sans Bold, or None to use Pillow fallback.

    Set PIGEON_FONT to a .ttf/.otf/.ttc path to override.
    """
    env = os.environ.get("PIGEON_FONT")
    if env and Path(env).is_file():
        return env

    bundled = _first_bundled_font(_BUNDLED_BOLD_NAMES)
    if bundled:
        return bundled

    roots = _font_search_roots()
    # Typical retail / Adobe naming patterns
    globs = (
        "*Sharp*Sans*Bold*.ttf",
        "*Sharp*Sans*Bold*.otf",
        "*SharpSans*Bold*.ttf",
        "*SharpSans*Bold*.otf",
        "*Sharp Sans*.ttf",
    )
    for root in roots:
        for pattern in globs:
            for p in sorted(root.glob(pattern)):
                if p.is_file():
                    return str(p)

    return _first_existing_path(*_BOLD_FALLBACK_PATHS)


def resolve_ui_font_regular() -> str | None:
    """Regular / book weight for UI labels when Medium is unavailable."""
    return resolve_ui_font_book() or resolve_ui_font_medium() or _first_existing_path(
        *_REGULAR_FALLBACK_PATHS
    )


def resolve_digital7_font() -> str | None:
    """
    LCD-style font for the clock saver time (e.g. Digital-7).

    Set ``PIGEON_FONT_CLOCK_SAVER`` to a ``.ttf`` path to override. Otherwise searches
    ``pigeonAssets/fonts`` and ``pigeonAssets/`` next to the package, then common font
    directories for names like ``Digital-7.ttf`` / ``digital-7.ttf`` / ``*Digital*7*.ttf``.
    """
    env = os.environ.get("PIGEON_FONT_CLOCK_SAVER")
    if env and Path(env).is_file():
        return env

    pkg_root = Path(__file__).resolve().parents[2]  # app folder (sibling of pigeonSystem)
    assets = pkg_root / "pigeonAssets"
    name_candidates = (
        "Digital-7.ttf",
        "digital-7.ttf",
        "Digital-7 (mono).ttf",
        "digital-7 (mono).ttf",
    )
    search_dirs: list[Path] = []
    if assets.is_dir():
        fonts_subdir = assets / "fonts"
        if fonts_subdir.is_dir():
            search_dirs.append(fonts_subdir)
        search_dirs.append(assets)
    for folder in search_dirs:
        for name in name_candidates:
            p = folder / name
            if p.is_file():
                return str(p)
        for p in sorted(folder.glob("*Digital*7*.ttf")) + sorted(folder.glob("*digital*7*.ttf")):
            if p.is_file() and "italic" not in p.name.lower():
                return str(p)

    roots = _font_search_roots()
    exact = ("Digital-7.ttf", "digital-7.ttf")
    for root in roots:
        for name in exact:
            p = root / name
            if p.is_file():
                return str(p)
    globs = (
        "*Digital*7*.ttf",
        "*digital*7*.ttf",
        "*Digital*7*.otf",
    )
    for root in roots:
        for pattern in globs:
            for p in sorted(root.glob(pattern)):
                if p.is_file() and "italic" not in p.name.lower() and "mono" not in p.name.lower():
                    return str(p)
    return None
