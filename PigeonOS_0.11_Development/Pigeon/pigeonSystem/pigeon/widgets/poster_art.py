"""Poster art widget: 4×6 grid, layered border + masked poster."""

from __future__ import annotations

import os
import re
from pathlib import Path

import cv2
import numpy as np

from pigeon.compositing import alpha_blend_bgra_over_bgr
from pigeon.design import rect_for_span_at_cell
from pigeon.app_state import auto_delete_pulled_media
from pigeon.image_ui_protocol import (
    medium_poster_bgr_for_active_widget,
    poster_master_bgra_from_source,
    pulled_path_is_under_pulled_dir,
    save_png_bgra,
)
from pigeon.layout_paths import (
    POSTER_ART_STEMS,
    dir_has_poster_border,
    find_poster_widget_dir,
    pick_pigeon_logo_png,
    pigeon_logo_asset_dir_candidates,
    poster_stem_for_dir,
    poster_widget_parent_candidates,
)
from pigeon.media_folders import ensure_reformatted_media_dir

_SPAN = (4, 6)

_POSTER_SUBDIR = "P0.5_WIDGET_POSTER_4X6_MEDIUM"

# Poster prep for …_poster.png: see pigeon.image_ui_protocol (1800×2700 master, 59% for 4×6 medium).

_GRAVE_CLERIC_JPEG = "Human | Grave Cleric - Lazarus Friendly.JPG"
# Canonical Pigeon logo: ``pigeonAssets/App logos/AppLogo_Pigeon.png`` (resolved via
# :func:`pigeon.layout_paths.pick_pigeon_logo_png`). The ``PIGEON_LOGO_PNG`` env var still
# works as a power-user override.
_PIGEON_LOGO_PNG = "AppLogo_Pigeon.png"


def _terminator_source_filenames(stem: str) -> tuple[str, ...]:
    """On-disk terminator art (new …_terminator.png, then legacy …_poster_terminator.png)."""
    return (f"{stem}_terminator.png", f"{stem}_poster_terminator.png")

_HENLIVISION_TEST_FILENAMES = (
    "henlivision_events2.png",
    "henlivision_evens.png",
    "henlivision_evens2.png",
)

# Exported copies of the current …_poster.png (overlay field text → [title]).
_REFORMATTED_POSTER_SUBDIR = "P_0.5_WIDGET_reformateedPosterArt"
_REFORMATTED_NAME_PREFIX = "P0.5_reFormattedPosterArt_"


def _resolve_poster_asset_dir() -> Path | None:
    env = os.environ.get("PIGEON_POSTER_ART_DIR")
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p
    for d in pigeon_logo_asset_dir_candidates():
        if dir_has_poster_border(d):
            return d
    for parent in poster_widget_parent_candidates():
        d = find_poster_widget_dir(parent)
        if d is not None:
            return d
        legacy = parent / _POSTER_SUBDIR
        if legacy.is_dir():
            return legacy
    return None


def _poster_png_path(dest_dir: Path) -> Path:
    stem = poster_stem_for_dir(dest_dir) or POSTER_ART_STEMS[0]
    return dest_dir / f"{stem}_poster.png"


def sync_stage_background_from_active_poster() -> None:
    """Set global stage tint from active …_poster.png (dark-dominant color), or black if missing."""
    from pigeon.stage_background import dominant_dark_bgr_from_poster_file, set_stage_bgr

    dest_dir = _resolve_poster_asset_dir()
    if dest_dir is None:
        set_stage_bgr(0, 0, 0)
        return
    p = _poster_png_path(dest_dir)
    if not p.is_file():
        set_stage_bgr(0, 0, 0)
        return
    try:
        b, g, r = dominant_dark_bgr_from_poster_file(p)
        set_stage_bgr(b, g, r)
    except Exception:
        set_stage_bgr(0, 0, 0)


def _reformatted_subdir_for_poster_dir(poster_dir: Path) -> Path:
    """Pick reformatted-export folder (renamed dirs OK if they still hold P0.5_reFormattedPosterArt_*.png)."""
    alt_names = (
        _REFORMATTED_POSTER_SUBDIR,
        "P_0.5_Widget_reformateedPosterArt",
        "ReformattedPosterArt",
        "reFormattedPosterArt",
    )
    for name in alt_names:
        p = poster_dir / name
        if p.is_dir():
            return p
    try:
        for child in sorted(poster_dir.iterdir()):
            if child.is_dir() and any(child.glob(f"{_REFORMATTED_NAME_PREFIX}*.png")):
                return child
    except OSError:
        pass
    return poster_dir / _REFORMATTED_POSTER_SUBDIR


def _reformatted_output_dir() -> Path | None:
    """Directory for P0.5_reFormattedPosterArt_*.png — prefers app pigeonReFormattedMedia, then legacy widget subfolder."""
    env_out = os.environ.get("PIGEON_REFORMATTED_POSTER_DIR")
    if env_out:
        p = Path(env_out).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return p
    try:
        return ensure_reformatted_media_dir()
    except OSError:
        pass
    dest_dir = _resolve_poster_asset_dir()
    if dest_dir is None:
        return None
    return _reformatted_subdir_for_poster_dir(dest_dir)


def apply_poster_from_source_path(
    src: Path,
    *,
    reformatted_title: str | None = None,
) -> tuple[bool, str]:
    """
    Run the standard medium poster pipeline from any image file (e.g. TMDb download in pigeonPulledMedia)
    into the active …_poster.png.

    If ``reformatted_title`` is set, the optional P0.5_reFormattedPosterArt_*.png export uses that title
    instead of ``src.stem`` (e.g. when ``src`` is ``tmdb_m_123``).
    """
    dest_dir = _resolve_poster_asset_dir()
    if dest_dir is None:
        return False, "Poster widget folder not found."
    return _write_medium_poster_from_source_file(dest_dir, src, reformatted_title=reformatted_title)


def copy_png_to_active_poster(src: Path) -> tuple[bool, str]:
    """Copy a ready-sized poster PNG onto …_poster.png (BGR, 3-channel write)."""
    dest_dir = _resolve_poster_asset_dir()
    if dest_dir is None:
        return False, "Poster widget folder not found."
    if not src.is_file():
        return False, f"Missing file: {src}"
    raw = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
    if raw is None or raw.size == 0:
        return False, f"Could not read: {src.name}"
    img = _bgr_from_imread_rgba_or_gray(raw)
    if img is None or img.size == 0:
        return False, f"Could not decode: {src.name}"
    poster = _poster_png_path(dest_dir)
    if not cv2.imwrite(str(poster), img):
        return False, f"Could not write {poster.name}"
    return True, f"Active poster ← {src.name}"


def prepare_default_poster_at_startup() -> tuple[bool, str, bool]:
    """
    On app launch: use …/P_0.5_WIDGET_reformateedPosterArt/P0.5_reFormattedPosterArt_pigeon.png
    if present; otherwise normalize_medium_poster_in_place (logo / henlivision / Grave Cleric).
    Returns same 3-tuple shape as normalize_medium_poster_in_place for drop-in use.
    """
    rd = _reformatted_output_dir()
    if rd is not None:
        pigeon_png = rd / f"{_REFORMATTED_NAME_PREFIX}pigeon.png"
        if pigeon_png.is_file():
            ok, msg = copy_png_to_active_poster(pigeon_png)
            if ok:
                return True, msg, False
    return normalize_medium_poster_in_place()


def apply_poster_command_terminator() -> tuple[bool, str]:
    """
    Command 'terminator': prefer reformatted PNG if present, else run medium pipeline from
    …_poster_terminator.png in the widget folder.
    """
    dest_dir = _resolve_poster_asset_dir()
    if dest_dir is None:
        return False, "Poster widget folder not found."

    rd = _reformatted_output_dir()
    if rd is not None and rd.is_dir():
        for name in (
            "P0.5_reFormat67edPosterArt_terminator.png",
            f"{_REFORMATTED_NAME_PREFIX}terminator.png",
        ):
            p = rd / name
            if p.is_file():
                return copy_png_to_active_poster(p)

    src = _resolve_terminator_poster_path(dest_dir)
    if src is not None:
        return _write_medium_poster_from_source_file(dest_dir, src)
    return (
        False,
        "Terminator art not found (reformatted …terminator.png or "
        "…_terminator.png / …_poster_terminator.png next to border assets).",
    )


def apply_poster_command_pigeon() -> tuple[bool, str]:
    """Command 'pigeon': restore from P0.5_reFormattedPosterArt_pigeon.png or Pigeon_Logo pipeline."""
    rd = _reformatted_output_dir()
    if rd is not None and rd.is_dir():
        pigeon_png = rd / f"{_REFORMATTED_NAME_PREFIX}pigeon.png"
        if pigeon_png.is_file():
            return copy_png_to_active_poster(pigeon_png)
    return apply_medium_poster_pigeon_logo()


def _bgr_from_imread_rgba_or_gray(img: np.ndarray) -> np.ndarray:
    """Normalize cv2.imread result to BGR uint8 for the poster pipeline."""
    if img is None or img.size == 0:
        return img
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 3:
        return img
    if img.shape[2] == 4:
        bgr = img[:, :, :3].astype(np.float32)
        a = img[:, :, 3:4].astype(np.float32) / 255.0
        bg = np.zeros_like(bgr)
        return (bgr * a + bg * (1.0 - a)).astype(np.uint8)
    return img[:, :, :3]


def _resolve_henlivision_test_path(dest_dir: Path) -> Path | None:
    env = os.environ.get("PIGEON_HENLIVISION_POSTER_PNG")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
    for name in _HENLIVISION_TEST_FILENAMES:
        p = dest_dir / name
        if p.is_file():
            return p
    return None


def _resolve_grave_cleric_jpeg_path(dest_dir: Path) -> Path | None:
    env = os.environ.get("PIGEON_GRAVE_CLERIC_JPEG")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
    p = dest_dir / _GRAVE_CLERIC_JPEG
    return p if p.is_file() else None


def _resolve_pigeon_logo_path(dest_dir: Path) -> Path | None:
    """Only ``AppLogo_Pigeon.png`` (under ``App logos/``), or the ``PIGEON_LOGO_PNG`` env override."""
    env = os.environ.get("PIGEON_LOGO_PNG")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
    return pick_pigeon_logo_png(dest_dir)


def _resolve_terminator_poster_path(dest_dir: Path) -> Path | None:
    env = os.environ.get("PIGEON_TERMINATOR_POSTER_PNG")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
    stem = poster_stem_for_dir(dest_dir)
    stems = (stem,) if stem else POSTER_ART_STEMS
    for s in stems:
        for name in _terminator_source_filenames(s):
            p = dest_dir / name
            if p.is_file():
                return p
    return None


def _write_medium_poster_from_source_file(
    dest_dir: Path,
    src: Path,
    *,
    reformatted_title: str | None = None,
) -> tuple[bool, str]:
    """Read any supported source file, run image_ui_protocol medium pipeline, write …_poster.png + optional master PNG."""
    raw = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
    if raw is None or raw.size == 0:
        return False, f"Could not read image: {src}"
    if raw.ndim == 3 and raw.shape[2] == 4:
        src_for_pipeline = raw
    else:
        img = _bgr_from_imread_rgba_or_gray(raw)
        if img is None or img.size == 0:
            return False, f"Could not decode image: {src}"
        src_for_pipeline = img
    out = medium_poster_bgr_for_active_widget(src_for_pipeline)
    poster = _poster_png_path(dest_dir)
    if not cv2.imwrite(str(poster), out):
        return False, f"Could not write {poster}"
    rd = _reformatted_output_dir()
    if rd is not None:
        try:
            rd.mkdir(parents=True, exist_ok=True)
        except OSError:
            rd = None
    if rd is not None:
        master = poster_master_bgra_from_source(src_for_pipeline)
        title_src = reformatted_title if reformatted_title is not None else src.stem
        stem = sanitize_reformatted_poster_title(title_src) or "poster"
        save_png_bgra(rd / f"{_REFORMATTED_NAME_PREFIX}{stem}.png", master)
    if pulled_path_is_under_pulled_dir(src) and auto_delete_pulled_media():
        try:
            src.unlink()
        except OSError:
            pass
    return True, str(poster)


def normalize_medium_poster_in_place() -> tuple[bool, str, bool]:
    """
    App start: Pigeon_Logo.png if present, else henlivision, else Grave Cleric → pipeline → …_poster.png.
    Returns (ok, message, poster_is_grave_cleric).
    """
    dest_dir = _resolve_poster_asset_dir()
    if dest_dir is None:
        return False, "Poster widget folder not found (under P0.5_Assets/P0.5_Widgets or P0.5_App/…).", False
    logo = _resolve_pigeon_logo_path(dest_dir)
    gc = _resolve_grave_cleric_jpeg_path(dest_dir)
    hl = _resolve_henlivision_test_path(dest_dir)
    if logo is not None:
        src = logo
        is_gc = False
    elif hl is not None:
        src = hl
        is_gc = False
    elif gc is not None:
        src = gc
        is_gc = True
    else:
        return (
            False,
            "No poster source (Pigeon_Logo.png, henlivision_events2.png, or Grave Cleric JPEG in widget folder).",
            False,
        )
    ok, msg = _write_medium_poster_from_source_file(dest_dir, src)
    if not ok:
        return ok, msg, False
    return ok, msg, is_gc


def restore_medium_poster_to_henlivision() -> tuple[bool, str]:
    """Rebuild …_poster.png from the henlivision source file only."""
    dest_dir = _resolve_poster_asset_dir()
    if dest_dir is None:
        return False, "Poster widget folder not found (under P0.5_Assets/P0.5_Widgets or P0.5_App/…)."
    src = _resolve_henlivision_test_path(dest_dir)
    if src is None:
        return (
            False,
            "henlivision image not found (henlivision_events2.png in widget folder or PIGEON_HENLIVISION_POSTER_PNG).",
        )
    return _write_medium_poster_from_source_file(dest_dir, src)


def apply_medium_poster_grave_cleric() -> tuple[bool, str]:
    """
    Overlay caption 'text': read Grave Cleric JPEG (unchanged on disk) → pipeline → write …_poster.png.
    """
    dest_dir = _resolve_poster_asset_dir()
    if dest_dir is None:
        return False, "Poster widget folder not found (under P0.5_Assets/P0.5_Widgets or P0.5_App/…)."
    src = _resolve_grave_cleric_jpeg_path(dest_dir)
    if src is None:
        return (
            False,
            f"Grave Cleric JPEG not found ({_GRAVE_CLERIC_JPEG} in widget folder or PIGEON_GRAVE_CLERIC_JPEG).",
        )
    return _write_medium_poster_from_source_file(dest_dir, src)


def apply_medium_poster_pigeon_logo() -> tuple[bool, str]:
    """Overlay 'pigeon': ``App logos/AppLogo_Pigeon.png`` → pipeline → …_poster.png."""
    dest_dir = _resolve_poster_asset_dir()
    if dest_dir is None:
        return False, "Poster widget folder not found (under P0.5_Assets/P0.5_Widgets or P0.5_App/…)."
    src = _resolve_pigeon_logo_path(dest_dir)
    if src is None:
        return (
            False,
            "Pigeon logo not found. Place it at "
            "'pigeonAssets/App logos/AppLogo_Pigeon.png' (or set PIGEON_LOGO_PNG).",
        )
    return _write_medium_poster_from_source_file(dest_dir, src)


def apply_medium_poster_terminator() -> tuple[bool, str]:
    """Overlay 'terminator': …_poster_terminator.png → pipeline → …_poster.png."""
    dest_dir = _resolve_poster_asset_dir()
    if dest_dir is None:
        return False, "Poster widget folder not found (under P0.5_Assets/P0.5_Widgets or P0.5_App/…)."
    src = _resolve_terminator_poster_path(dest_dir)
    if src is None:
        return (
            False,
            "Terminator poster not found (…_terminator.png / …_poster_terminator.png or PIGEON_TERMINATOR_POSTER_PNG).",
        )
    return _write_medium_poster_from_source_file(dest_dir, src)


def sanitize_reformatted_poster_title(title: str) -> str:
    """Make overlay caption safe as a single path segment (preserves case)."""
    t = title.strip()
    if not t:
        return ""
    out: list[str] = []
    for c in t:
        if ord(c) < 32 or c in '\\/:*?"<>|':
            out.append("_")
        else:
            out.append(c)
    s = "".join(out)
    s = re.sub(r"_+", "_", s).strip("._ ")
    if not s or s in (".", ".."):
        return ""
    return s[:120]


def export_current_medium_poster_as_reformatted(title: str) -> tuple[bool, str]:
    """
    Copy current …_poster.png pixels into pigeonReFormattedMedia (or env / legacy folder) as
    P0.5_reFormattedPosterArt_[title].png.
    """
    safe = sanitize_reformatted_poster_title(title)
    if not safe:
        return False, "Empty or invalid title for export filename."

    out_dir = _reformatted_output_dir()
    if out_dir is None:
        return False, "Could not resolve reformatted media folder."
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, str(e)

    dest_dir = _resolve_poster_asset_dir()
    if dest_dir is None:
        return False, "Poster widget folder not found (cannot read source poster)."
    src = _poster_png_path(dest_dir)
    if not src.is_file():
        return False, f"Source poster missing: {src.name}"

    img = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
    if img is None or img.size == 0:
        return False, f"Could not read {src.name}"
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = _bgr_from_imread_rgba_or_gray(img)

    out_path = out_dir / f"{_REFORMATTED_NAME_PREFIX}{safe}.png"
    if not cv2.imwrite(str(out_path), img):
        return False, f"Could not write {out_path.name}"
    return True, str(out_path)


def _load_bgr(path: Path) -> np.ndarray | None:
    if not path.is_file():
        return None
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    return img


def _load_bgra_resized(path: Path, tw: int, th: int) -> np.ndarray | None:
    """BGRA uint8 (th, tw, 4); alpha 255 if source has no alpha."""
    if not path.is_file():
        return None
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None or img.size == 0:
        return None
    if img.ndim == 2:
        bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        a = np.full((bgr.shape[0], bgr.shape[1], 1), 255, dtype=np.uint8)
        bgra = np.concatenate([bgr, a], axis=2)
    elif img.shape[2] == 3:
        bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        bgra[:, :, 3] = 255
    else:
        bgra = img
    return cv2.resize(bgra, (tw, th), interpolation=cv2.INTER_AREA)


def _poster_bgr_and_alpha(path: Path, tw: int, th: int) -> tuple[np.ndarray | None, np.ndarray]:
    """Resized poster BGR and per-pixel alpha in [0,1] (th, tw); alpha all-ones if no alpha channel."""
    if not path.is_file():
        return None, np.ones((th, tw), dtype=np.float32)
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None or img.size == 0:
        return None, np.ones((th, tw), dtype=np.float32)
    if img.ndim == 2:
        bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        bgr = cv2.resize(bgr, (tw, th), interpolation=cv2.INTER_AREA)
        return bgr, np.ones((th, tw), dtype=np.float32)
    if img.shape[2] == 3:
        bgr = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
        return bgr, np.ones((th, tw), dtype=np.float32)
    bgr = cv2.resize(img[:, :, :3], (tw, th), interpolation=cv2.INTER_AREA)
    pa = cv2.resize(img[:, :, 3], (tw, th), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    return bgr, np.clip(pa, 0.0, 1.0)


def _load_mask_poster_visibility(path: Path, tw: int, th: int) -> np.ndarray | None:
    """
    Mask for **layer 2 (poster) only**: where to show poster vs let layer 1 (border) show through.

    - **White + opaque** in the mask → include that poster pixel.
    - **Transparent** (alpha = 0) → exclude poster there (border visible).

    BGRA masks use **luminance × alpha** so a white rounded rectangle on a transparent field
    includes the interior and drops RGB fringe outside the alpha silhouette.

    Returns float32 (th, tw) in [0, 1].
    """
    if not path.is_file():
        return None
    m = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if m is None:
        return None
    m = cv2.resize(m, (tw, th), interpolation=cv2.INTER_AREA)
    if m.ndim == 2:
        lum = m.astype(np.float32) / 255.0
        return np.clip(lum, 0.0, 1.0)
    if m.shape[2] == 4:
        bgr = m[:, :, :3].astype(np.float32) / 255.0
        lum = 0.299 * bgr[:, :, 2] + 0.587 * bgr[:, :, 1] + 0.114 * bgr[:, :, 0]
        a = m[:, :, 3].astype(np.float32) / 255.0
        vis = lum * a
        return np.clip(vis, 0.0, 1.0)
    gray = cv2.cvtColor(m, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    return np.clip(gray, 0.0, 1.0)


class PosterArtWidget:
    """
    Layer stack (bottom → top), 4×6 cell:

      1) ``P_0.5_posterArt_4x6_MEDIUM_border`` (or legacy ``…_WIDGET_POSTER_…_border``) — frame.
      2) Active poster image — ``…_poster.png`` (e.g. reformatted export or pipeline).
      3) ``…_mask`` — applied **only** to layer 2: **white + opaque** keeps the poster visible;
         **transparent** cuts the poster away so the border shows.
    """

    def __init__(self, *, anchor_row: int = 1, anchor_col: int = 1) -> None:
        self._anchor_row = anchor_row
        self._anchor_col = anchor_col
        d = _resolve_poster_asset_dir()
        stem = poster_stem_for_dir(d) if d else None
        if d is not None and stem is not None:
            self._border_path = d / f"{stem}_border.png"
            self._poster_path = d / f"{stem}_poster.png"
            self._mask_path = d / f"{stem}_mask.png"
        else:
            self._border_path = None
            self._poster_path = None
            self._mask_path = None
        self._comp_bgra_cache: np.ndarray | None = None
        self._comp_bgra_wh: tuple[int, int] | None = None

    @property
    def grid_span(self) -> tuple[int, int]:
        return _SPAN

    @property
    def grid_anchor(self) -> tuple[int, int]:
        return (self._anchor_row, self._anchor_col)

    def _build_bgra_tile(self, tw: int, th: int) -> np.ndarray:
        """Widget-sized BGRA; alpha from border/poster/mask so video can show through."""
        if self._border_path is None or self._poster_path is None or self._mask_path is None:
            tile = np.zeros((th, tw, 4), dtype=np.uint8)
            tile[:, :, :3] = (40, 40, 40)
            tile[:, :, 3] = 230
            cv2.putText(
                tile,
                "posterArt assets?",
                (8, th // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (180, 180, 180),
                1,
                lineType=cv2.LINE_AA,
            )
            return tile

        border_bgra = _load_bgra_resized(self._border_path, tw, th)
        poster_bgr, pa = _poster_bgr_and_alpha(self._poster_path, tw, th)
        if border_bgra is None or poster_bgr is None:
            tile = np.zeros((th, tw, 4), dtype=np.uint8)
            tile[:, :, :3] = (50, 0, 0)
            tile[:, :, 3] = 240
            return tile

        border_bgr = border_bgra[:, :, :3]
        ba = border_bgra[:, :, 3].astype(np.float32) / 255.0

        vis = _load_mask_poster_visibility(self._mask_path, tw, th)
        if vis is None:
            vis = np.ones((th, tw), dtype=np.float32)
        else:
            if os.environ.get("PIGEON_POSTER_MASK_INVERT", "").strip() in ("1", "true", "yes"):
                vis = 1.0 - vis

        # Layer 3 on layer 2 only: vis=1 → poster; vis=0 → border (mask transparent → exclude poster).
        v4 = vis[..., np.newaxis]
        rgb = poster_bgr.astype(np.float32) * v4 + border_bgr.astype(np.float32) * (1.0 - v4)
        # Same weights for alpha. Border PNGs often use alpha=0 in the poster window; keep
        # out_a consistent with RGB so the composite stays correct when blended onto video.
        out_a = vis * pa + (1.0 - vis) * ba
        out_a = np.clip(out_a, 0.0, 1.0)

        out = np.zeros((th, tw, 4), dtype=np.uint8)
        out[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        out[:, :, 3] = (out_a * 255.0).astype(np.uint8)
        return out

    def clear_bgra_cache(self) -> None:
        """After replacing …_poster.png on disk, call before next draw."""
        self._comp_bgra_cache = None
        self._comp_bgra_wh = None

    def bgra_patch(self) -> np.ndarray:
        """Design-scale BGRA tile for this widget (cached)."""
        wx, wy, w, h = rect_for_span_at_cell(
            _SPAN[0],
            _SPAN[1],
            row_1based=self._anchor_row,
            col_1based=self._anchor_col,
        )
        tw, th = w, h
        if self._comp_bgra_wh == (tw, th) and self._comp_bgra_cache is not None:
            return self._comp_bgra_cache
        self._comp_bgra_cache = self._build_bgra_tile(tw, th).copy()
        self._comp_bgra_wh = (tw, th)
        return self._comp_bgra_cache

    def render(self, canvas_bgr: np.ndarray) -> None:
        wx, wy, w, h = rect_for_span_at_cell(
            _SPAN[0],
            _SPAN[1],
            row_1based=self._anchor_row,
            col_1based=self._anchor_col,
        )
        roi = canvas_bgr[wy : wy + h, wx : wx + w]
        roi[:] = alpha_blend_bgra_over_bgr(roi, self.bgra_patch())
