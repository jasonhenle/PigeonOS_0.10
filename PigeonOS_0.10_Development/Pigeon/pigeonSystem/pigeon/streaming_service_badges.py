"""Map Apple TV / Roku foreground app to logos under ``pigeonAssets/App logos``.

Files use ``AppLogo_<ServiceName>.<ext>`` (e.g. ``AppLogo_DisneyPlus.png``).
Extensions tried: ``.png``, ``.jpg``, ``.jpeg``, ``.webp``, ``.svg``.

Legacy filenames in ``pigeonAssets`` root are still used if no App-logo file exists.
"""

from __future__ import annotations

from pathlib import Path

# Subfolder of pigeonAssets (matches shipped layout; space is intentional).
APP_LOGOS_REL_DIR = "App logos"

_LOGO_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".svg")
_LOGO_EXTENSIONS_LOWER = frozenset(e.lower() for e in _LOGO_EXTENSIONS)

# Order: first matching rule wins.
# ``bundle_contains`` / ``name_contains`` are matched case-insensitively (substring).
# ``logo_stems``: ``AppLogo_<stem>.<ext>`` under APP_LOGOS_REL_DIR; first stem with any ext wins.
# ``legacy_basenames``: optional fallback files in ``assets_dir`` root (old layout).
_RULES: tuple[
    tuple[str | None, str | None, tuple[str, ...], str, tuple[str, ...]],
    ...,
] = (
    ("disney.disneyplus", None, ("DisneyPlus",), "Disney+", ("DisneyPluslogo.png", "Disney Plus logo white text.png", "disneyPlusLogo.png")),
    (None, "disney+", ("DisneyPlus",), "Disney+", ("DisneyPluslogo.png", "Disney Plus logo white text.png", "disneyPlusLogo.png")),
    (None, "disney plus", ("DisneyPlus",), "Disney+", ("DisneyPluslogo.png", "Disney Plus logo white text.png", "disneyPlusLogo.png")),
    ("netflix", None, ("Netflix",), "Netflix", ("Netflix_Logo_RGB.png",)),
    (None, "netflix", ("Netflix",), "Netflix", ("Netflix_Logo_RGB.png",)),
    ("hbomax", None, ("HBOMax",), "HBO Max", ()),
    ("com.wbd", None, ("HBOMax",), "Max", ()),
    (None, "hbo max", ("HBOMax",), "HBO Max", ()),
    (None, "max", ("HBOMax", "Max"), "Max", ()),
    ("paramount", None, ("ParamountPlus",), "Paramount+", ()),
    (None, "paramount+", ("ParamountPlus",), "Paramount+", ()),
    (None, "paramount plus", ("ParamountPlus",), "Paramount+", ()),
    ("peacock", None, ("Peacock",), "Peacock", ("peacockLogo.jpg", "peacockLogo.png")),
    (None, "peacock", ("Peacock",), "Peacock", ("peacockLogo.jpg", "peacockLogo.png")),
    ("hulu", None, ("Hulu",), "Hulu", ()),
    (None, "hulu", ("Hulu",), "Hulu", ()),
    ("spotify", None, ("Spotify",), "Spotify", ()),
    (None, "spotify", ("Spotify",), "Spotify", ()),
    ("amazon.aiv", None, ("PrimeVideo",), "Prime Video", ()),
    (None, "prime video", ("PrimeVideo",), "Prime Video", ()),
    (None, "amazon video", ("PrimeVideo",), "Prime Video", ()),
    ("youtube", None, ("YouTube",), "YouTube", ()),
    (None, "youtube", ("YouTube",), "YouTube", ()),
    ("google.ios.youtube", None, ("YouTube",), "YouTube", ()),
    (None, "apple music", ("AppleMusic",), "Apple Music", ()),
    ("com.apple.music", None, ("AppleMusic",), "Apple Music", ()),
    ("shudder", None, ("Shudder",), "Shudder", ()),
    (None, "shudder", ("Shudder",), "Shudder", ()),
    ("discovery", None, ("DiscoveryPlus",), "Discovery+", ()),
    (None, "discovery+", ("DiscoveryPlus",), "Discovery+", ()),
    (None, "discovery plus", ("DiscoveryPlus",), "Discovery+", ()),
    (None, "pbs kids", ("PBSKids",), "PBS Kids", ()),
    (None, "pbskids", ("PBSKids",), "PBS Kids", ()),
    ("pbs", None, ("PBSKids",), "PBS Kids", ()),
    ("airplay", None, ("AirPlay",), "AirPlay", ()),
    (None, "airplay", ("AirPlay",), "AirPlay", ()),
    ("tvwatch", None, ("AppleTV",), "Apple TV", ("Apple_TV_Plus_logo.png",)),
    (None, "apple tv", ("AppleTV",), "Apple TV", ("Apple_TV_Plus_logo.png",)),
    ("com.apple.tv", None, ("AppleTV",), "Apple TV", ("Apple_TV_Plus_logo.png",)),
)


def _first_app_logo_relpath(assets_root: Path, stems: tuple[str, ...]) -> str | None:
    logos_dir = assets_root / APP_LOGOS_REL_DIR
    if not logos_dir.is_dir():
        return None
    for stem_suffix in stems:
        base = f"AppLogo_{stem_suffix}"
        for ext in _LOGO_EXTENSIONS:
            name = f"{base}{ext}"
            p = logos_dir / name
            if p.is_file():
                return f"{APP_LOGOS_REL_DIR}/{name}"
        # Linux/Pi: shipped assets often use ``.PNG`` / ``.WEBP`` (macOS is case-insensitive).
        base_lower = base.lower()
        try:
            for p in sorted(logos_dir.iterdir()):
                if not p.is_file():
                    continue
                if p.stem.lower() == base_lower and p.suffix.lower() in _LOGO_EXTENSIONS_LOWER:
                    return f"{APP_LOGOS_REL_DIR}/{p.name}"
        except OSError:
            pass
    return None


def _first_legacy_root_asset(assets_root: Path, basenames: tuple[str, ...]) -> str | None:
    for name in basenames:
        p = assets_root / name
        if p.is_file():
            return name
    return None


def _resolve_badge_file(
    assets_root: Path,
    stems: tuple[str, ...],
    legacy: tuple[str, ...],
) -> str | None:
    rel = _first_app_logo_relpath(assets_root, stems)
    if rel is not None:
        return rel
    return _first_legacy_root_asset(assets_root, legacy)


def resolve_streaming_badge_media(
    assets_dir: str | Path,
    *,
    app_name: str,
    app_id: str,
) -> tuple[str | None, str]:
    """
    Return ``(relative_path_or_none, display_name)`` for the streaming service.

    ``relative_path`` is under ``assets_dir`` (e.g. ``App logos/AppLogo_Netflix.png``).
    When a rule matches but no file exists, returns ``(None, display)`` for text fallback.
    """
    root = Path(assets_dir)
    bid = (app_id or "").strip().lower()
    an = (app_name or "").strip().lower()

    for bfrag, nfrag, stems, display, legacy in _RULES:
        bundle_hit = bool(bfrag and bfrag in bid)
        name_hit = bool(nfrag and nfrag in an)
        if not bundle_hit and not name_hit:
            continue
        fn = _resolve_badge_file(root, stems, legacy)
        return (fn, display)

    raw_name = (app_name or "").strip()
    if raw_name:
        return None, raw_name
    if bid:
        leaf = bid.rsplit(".", 1)[-1].replace("_", " ")
        if leaf:
            return None, leaf[:40]
    return None, "Playing"


def resolve_streaming_badge_filename(
    assets_dir: str | Path,
    *,
    app_name: str,
    app_id: str,
) -> str | None:
    """Backward-compatible: first matching logo basename / relative path, or ``None``."""
    fn, _ = resolve_streaming_badge_media(
        assets_dir, app_name=app_name, app_id=app_id
    )
    return fn
