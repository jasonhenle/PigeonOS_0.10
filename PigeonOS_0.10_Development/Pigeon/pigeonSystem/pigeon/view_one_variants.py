"""View 1 fallback-variant resolver, text rendering, and asset helpers.

Nine variants describe the View-1 composition depending on which content
assets and metadata are live. As of v0.6.14 V01 and V02 were **swapped**: V01
is now the minimal "TT-only on black" layout (was V02) and V02 is the full
backdrop + TT + app-logo + chrome composition (was V01). V01 remains the
default state (returned when the [1] toggle is in its initial position).

    .01 default (all present)  — TT-only simple layout
    .02 alternate (all present) — full composition (BD + TT + appLogo + chrome)
    .03 default (pigeonTMDB_BD missing)  .04 alternate (pigeonTMDB_BD missing)
    .05 default (pigeonTMDB_TT missing)  .06 alternate (pigeonTMDB_TT missing)
    .07 default (BD + TT missing)        -- no alternate
    .08 default (BD + TT + appLogo gone) -- no alternate
    .09 default (everything missing)     -- no alternate

This module is self-contained and importable from ``pigeon_0_9.py``. It does
not touch Tk or the compositor; it only resolves which variant should render
and provides the text/image patches the compositor needs for fallbacks.
"""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path
from typing import Optional

import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont

    _PIL_OK = True
except ImportError:
    _PIL_OK = False

from pigeon.font_paths import resolve_ui_font_bold, resolve_ui_font_book


class ViewOneVariant(IntEnum):
    V01 = 1
    V02 = 2
    V03 = 3
    V04 = 4
    V05 = 5
    V06 = 6
    V07 = 7
    V08 = 8
    V09 = 9


_FULL_PATH_VARIANTS = frozenset({ViewOneVariant.V02, ViewOneVariant.V03, ViewOneVariant.V05})
_NO_ALT_VARIANTS = frozenset({ViewOneVariant.V07, ViewOneVariant.V08, ViewOneVariant.V09})


def variant_uses_full_path(v: ViewOneVariant) -> bool:
    """True when this variant renders through the pigeon-full composition path
    (backdrop cover + small top-aligned title treatment + chrome).

    After the v0.6.14 V01 ↔ V02 swap this set is ``{V02, V03, V05}`` — V01
    now renders through the simple TT-only path alongside V04/V06–V09."""
    return v in _FULL_PATH_VARIANTS


def variant_has_alternate(v: ViewOneVariant) -> bool:
    """True when pressing [1] again should be allowed to flip default <-> alternate.
    Variants .07 / .08 / .09 only have a single layout, so the toggle is ignored."""
    return v not in _NO_ALT_VARIANTS


def resolve_view_one_variant(
    *,
    layout_is_simple: bool,
    has_title_meta: bool,
    has_app_meta: bool,
    has_tmdb_bd: bool,
    has_tmdb_tt: bool,
    has_app_logo: bool,
) -> ViewOneVariant:
    """Map live asset/metadata presence + layout preference to a variant.

    ``layout_is_simple`` is the user's current [1] / [1,1] toggle state. It is
    only honored when the resolved scenario has both a default and alternate
    variant (scenarios 1, 2, 3). Scenarios 4 / 5 / 6 always return a single
    variant regardless of toggle (the caller should ignore the toggle then).
    """
    if has_title_meta and has_app_meta:
        if has_tmdb_bd and has_tmdb_tt:
            # V01 is the default TT-only layout (simple path); V02 is the alternate
            # full composition (backdrop + TT + appLogo + chrome). See module docstring.
            return ViewOneVariant.V02 if layout_is_simple else ViewOneVariant.V01
        if (not has_tmdb_bd) and has_tmdb_tt:
            return ViewOneVariant.V04 if layout_is_simple else ViewOneVariant.V03
        if has_tmdb_bd and (not has_tmdb_tt):
            return ViewOneVariant.V06 if layout_is_simple else ViewOneVariant.V05
        # Neither BD nor TT available but title+app metadata present: collapse to
        # the simple text fallback (same visual as .06 — black + generated title).
        return ViewOneVariant.V06

    # Title metadata missing: scenarios 4 / 5 / 6 -- no alternate.
    if has_app_meta and has_app_logo:
        return ViewOneVariant.V07
    if has_app_meta:
        return ViewOneVariant.V08
    return ViewOneVariant.V09


def _pil_image_to_bgra(img: "Image.Image") -> np.ndarray:
    arr = np.array(img.convert("RGBA"))
    return arr[..., [2, 1, 0, 3]].copy()


def _load_bgra_from_path(path_str: str, mtime: float) -> Optional[np.ndarray]:
    if not _PIL_OK:
        return None
    p = Path(path_str)
    if not p.is_file():
        return None
    try:
        with Image.open(p) as img:
            return _pil_image_to_bgra(img)
    except Exception:
        return None


# Key on (path, mtime) so editing the PNG on disk picks up automatically.
_LOAD_BGRA_CACHE: "dict[tuple[str, float], Optional[np.ndarray]]" = {}


def _load_bgra_cached(path: Path) -> Optional[np.ndarray]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = (str(path), mtime)
    hit = _LOAD_BGRA_CACHE.get(key)
    if hit is not None:
        return hit
    pixels = _load_bgra_from_path(key[0], mtime)
    if pixels is None:
        return None
    _LOAD_BGRA_CACHE.clear()
    _LOAD_BGRA_CACHE[key] = pixels
    return pixels


def load_pigeon_temp_logo_bgra(assets_root: Path) -> Optional[np.ndarray]:
    """Return BGRA pixels of the canonical Pigeon logo (``App logos/AppLogo_Pigeon.png``).

    The legacy ``pigeonTempLogo.png`` is no longer consulted — Pigeon's brand mark lives
    at exactly one path now, resolved via :func:`pigeon.layout_paths.pick_pigeon_logo_png`
    so the same file that drives the poster art also drives View One V09.
    Returns ``None`` if the file is missing or cannot be decoded.
    """
    # Late import to avoid a circular dep at module import time.
    from pigeon.layout_paths import pick_pigeon_logo_png

    p = pick_pigeon_logo_png(assets_root)
    if p is None:
        return None
    return _load_bgra_cached(p)


# ---- Text rendering -------------------------------------------------------

# Ceiling for text height as a fraction of the box, so descenders never crop
# and there's room for a small visual margin inside the rect.
_TEXT_HEIGHT_FRACTION = 0.82
# Horizontal breathing room inside the box.
_TEXT_WIDTH_FRACTION = 0.96
# Per-render cache to avoid re-rasterizing the same string each frame.
_TEXT_CACHE: "dict[tuple[str, int, int], Optional[np.ndarray]]" = {}
_TEXT_CACHE_MAX = 32
_FONT_OBJECT_CACHE: "dict[tuple[Optional[str], int], ImageFont.ImageFont]" = {}
_FONT_OBJECT_CACHE_MAX = 64


def _measure(font: "ImageFont.ImageFont", text: str) -> "tuple[int, int, tuple]":
    try:
        bbox = font.getbbox(text)
    except AttributeError:
        w, h = font.getsize(text)
        bbox = (0, 0, w, h)
    tw = max(1, int(bbox[2] - bbox[0]))
    th = max(1, int(bbox[3] - bbox[1]))
    return tw, th, bbox


def _fit_font(font_path: Optional[str], text: str, box_w: int, box_h: int) -> "Optional[ImageFont.ImageFont]":
    if not _PIL_OK:
        return None
    max_w = max(1, int(round(box_w * _TEXT_WIDTH_FRACTION)))
    max_h = max(1, int(round(box_h * _TEXT_HEIGHT_FRACTION)))

    def _make(size: int):
        k = (font_path or None, int(size))
        hit = _FONT_OBJECT_CACHE.get(k)
        if hit is not None:
            return hit
        if font_path:
            try:
                fnt = ImageFont.truetype(font_path, size)
            except Exception:
                fnt = ImageFont.load_default()
        else:
            fnt = ImageFont.load_default()
        _FONT_OBJECT_CACHE[k] = fnt
        if len(_FONT_OBJECT_CACHE) > _FONT_OBJECT_CACHE_MAX:
            _FONT_OBJECT_CACHE.pop(next(iter(_FONT_OBJECT_CACHE)))
        return fnt

    size = max(8, max_h)
    for _ in range(40):
        font = _make(size)
        tw, th, _ = _measure(font, text)
        if tw <= max_w and th <= max_h:
            return font
        shrink = min(max_w / float(tw), max_h / float(th))
        new_size = max(8, int(size * max(0.5, shrink) * 0.97))
        if new_size >= size:
            new_size = size - 1
        if new_size < 8:
            return _make(8)
        size = new_size
    return _make(max(8, size))


def render_ui_text_patch_bgra(text: str, box_w: int, box_h: int) -> Optional[np.ndarray]:
    """Render ``text`` centered inside a (``box_w``, ``box_h``) BGRA patch.

    Uses Pigeon's configured UI bold font (``resolve_ui_font_bold``) with a
    PIL default fallback. Returns ``None`` when PIL is unavailable or text is
    empty; otherwise returns a BGRA ``np.uint8`` array of shape
    (``box_h``, ``box_w``, 4) with transparent background and white glyphs.
    """
    text = (text or "").strip()
    if not text or box_w < 2 or box_h < 2 or not _PIL_OK:
        return None
    key = (text, int(box_w), int(box_h))
    cached = _TEXT_CACHE.get(key)
    if cached is not None:
        return cached

    font_path = resolve_ui_font_bold()
    font = _fit_font(font_path, text, box_w, box_h)
    if font is None:
        return None

    img = Image.new("RGBA", (int(box_w), int(box_h)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    tw, th, bbox = _measure(font, text)
    ox = (int(box_w) - tw) // 2 - int(bbox[0])
    oy = (int(box_h) - th) // 2 - int(bbox[1])
    draw.text((ox, oy), text, font=font, fill=(255, 255, 255, 255))

    patch = _pil_image_to_bgra(img)
    _TEXT_CACHE[key] = patch
    if len(_TEXT_CACHE) > _TEXT_CACHE_MAX:
        _TEXT_CACHE.pop(next(iter(_TEXT_CACHE)))
    return patch


_VC_B_TITLE_CACHE: "dict[tuple[str, int, int], Optional[np.ndarray]]" = {}
_VC_B_TITLE_CACHE_MAX = 24


def render_view_one_video_content_b_title_patch_bgra(text: str) -> Optional[np.ndarray]:
    """viewOne.videoContent_b: centered title at grid row **6.25** (Sharp Sans ExtraBold).

    Typography is **not** fitted to the full ``DESIGN_W`` strip: the font is chosen to fit
    the **row 6, columns 5–16** band (12×1 cells), then rasterized on a full-width one-cell
    strip with horizontal centering and ellipsis to that band width — smaller than the old
    audio-badge–referenced sizing.
    """
    text = (text or "").strip()
    if not text or not _PIL_OK:
        return None
    from pigeon.design import DESIGN_H, DESIGN_W, get_grid_geometry, rect_for_span_at_cell
    from pigeon.widgets.playback_overlay import _fit_font_to_box

    g = get_grid_geometry(width=DESIGN_W, height=DESIGN_H)
    cell_i = max(1, int(round(float(g.cell))))
    _bx, _by, band_ww, band_hh = rect_for_span_at_cell(
        12,
        1,
        row_1based=6,
        col_1based=5,
        width=DESIGN_W,
        height=DESIGN_H,
    )
    band_ww_i = max(1, int(band_ww))
    band_hh_i = max(1, int(band_hh))
    key = (text, band_ww_i, band_hh_i, cell_i)
    hit = _VC_B_TITLE_CACHE.get(key)
    if hit is not None:
        return hit

    pad_b = max(2, int(round(min(band_ww_i, band_hh_i) * 0.06)))
    mw_fit = max(4, band_ww_i - 2 * pad_b)
    mh_fit = max(4, band_hh_i - 2 * pad_b)
    title_font = _fit_font_to_box(text, mw_fit, mh_fit)

    wh = max(1, int(round(float(g.cell))))
    ww = int(DESIGN_W)
    line = _ellipsize_to_width(text, title_font, mw_fit)

    img = Image.new("RGBA", (ww, wh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    tw, th, bbox = _measure(title_font, line)
    ox = (ww - tw) // 2 - int(bbox[0])
    oy = (wh - th) // 2 - int(bbox[1])
    draw.text((ox, oy), line, font=title_font, fill=(255, 255, 255, 255))

    patch = _pil_image_to_bgra(img)
    _VC_B_TITLE_CACHE[key] = patch
    if len(_VC_B_TITLE_CACHE) > _VC_B_TITLE_CACHE_MAX:
        _VC_B_TITLE_CACHE.pop(next(iter(_VC_B_TITLE_CACHE)))
    return patch


# ---- Music three-line text patch -----------------------------------------
#
# Separate cache from the single-line renderer: key shape differs (includes
# three strings + a "music" sentinel), and we don't want one tier evicting
# the other.
_MUSIC_TEXT_CACHE: "dict[tuple, Optional[np.ndarray]]" = {}
_MUSIC_TEXT_CACHE_MAX = 16

# Vertical layout tuning: fraction of total height the title takes when all
# three lines are present. Artist + album share the remainder.
_MUSIC_TITLE_FRAC_THREE = 0.60
_MUSIC_TITLE_FRAC_TWO = 0.76


def _ellipsize_to_width(
    text: str, font: "ImageFont.ImageFont", max_w: int
) -> str:
    tw, _, _ = _measure(font, text)
    if tw <= max_w:
        return text
    # Prefer ASCII dots: some UI fonts (esp. Digital-7) map U+2026 to a bogus O/D glyph.
    ell = "..."
    ell_w, _, _ = _measure(font, ell)
    if ell_w > max_w:
        return ""
    lo, hi = 0, len(text)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = text[:mid].rstrip() + ell
        cw, _, _ = _measure(font, cand)
        if cw <= max_w:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best or ell


def render_ui_music_text_patch_bgra(
    title: str,
    artist: str,
    album: str,
    box_w: int,
    box_h: int,
    *,
    subtitle_top_frac: Optional[float] = None,
) -> Optional[np.ndarray]:
    """Render a Music-styled text patch inside ``(box_w, box_h)``.

    Line 1 is the track ``title`` set large in Pigeon's UI bold font; lines 2
    and 3 are ``artist`` and ``album`` set in Sharp Sans Book (regular weight)
    at the same smaller size beneath it. All lines center-align horizontally,
    white on transparent, and fit within the box. Long small-line text
    ellipsizes at the shared font size so line 2 and line 3 stay visually
    matched.

    ``subtitle_top_frac`` (0.0 .. 1.0), when provided, overrides the
    auto-layout and anchors the TOP of the first small line at that fraction
    of ``box_h``. The title then renders in the region above that y-coordinate.
    Use this when you need line 2 to land on a specific grid row (e.g. ``3.5``
    inside the 4-row TT rect on viewOne.audioContent => ``1.5/4 = 0.375``).

    Falls back to :func:`render_ui_text_patch_bgra` when only ``title`` is
    present. Returns ``None`` when PIL is unavailable or all inputs are empty.
    """
    title = (title or "").strip()
    artist = (artist or "").strip()
    album = (album or "").strip()
    if not title and not artist and not album:
        return None
    if box_w < 2 or box_h < 2 or not _PIL_OK:
        return None
    if title and not artist and not album:
        return render_ui_text_patch_bgra(title, box_w, box_h)

    # The font pair differs from the single-line renderer, so tag the cache
    # key accordingly to avoid cross-contamination.
    key = (
        "music",
        title,
        artist,
        album,
        int(box_w),
        int(box_h),
        float(subtitle_top_frac) if subtitle_top_frac is not None else -1.0,
    )
    cached = _MUSIC_TEXT_CACHE.get(key)
    if cached is not None:
        return cached

    title_font_path = resolve_ui_font_bold()
    small_font_path = resolve_ui_font_book() or title_font_path
    bw, bh = int(box_w), int(box_h)
    inner_w = max(1, int(round(bw * _TEXT_WIDTH_FRACTION)))

    small_lines: list[str] = []
    if artist:
        small_lines.append(artist)
    if album:
        small_lines.append(album)
    n_small = len(small_lines)

    gap_small = max(1, int(round(bh * 0.015)))

    if subtitle_top_frac is not None and n_small > 0:
        # Explicit vertical anchor: title region ends at the subtitle top; the
        # subtitle region spans from there to the bottom of the box.
        sub_top = max(0, min(bh - 1, int(round(bh * float(subtitle_top_frac)))))
        title_h = max(12, sub_top)
        remaining = bh - sub_top
        gap_between = 0
    else:
        if n_small == 2:
            title_frac = _MUSIC_TITLE_FRAC_THREE
        elif n_small == 1:
            title_frac = _MUSIC_TITLE_FRAC_TWO
        else:
            title_frac = 0.92
        gap_between = max(2, int(round(bh * 0.03)))
        title_h = max(12, int(round(bh * title_frac)))
        remaining = bh - title_h - (gap_between if n_small > 0 else 0)

    if n_small == 2:
        per_small_h = max(8, (remaining - gap_small) // 2)
    elif n_small == 1:
        per_small_h = max(8, remaining)
    else:
        per_small_h = 0

    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Title (line 1) — set in the bold UI font.
    if title:
        t_font = _fit_font(title_font_path, title, bw, title_h)
        if t_font is not None:
            tw, th, bbox = _measure(t_font, title)
            ox = (bw - tw) // 2 - int(bbox[0])
            oy = (title_h - th) // 2 - int(bbox[1])
            draw.text((ox, oy), title, font=t_font, fill=(255, 255, 255, 255))

    # Small lines (artist / album) — set in Sharp Sans Book, sharing one font
    # size for visual parity between the two lines.
    if n_small > 0 and per_small_h > 0:
        candidate_sizes: list[int] = []
        for s in small_lines:
            f = _fit_font(small_font_path, s, bw, per_small_h)
            if f is None:
                continue
            sz = getattr(f, "size", None)
            if isinstance(sz, (int, float)):
                candidate_sizes.append(int(sz))
        if candidate_sizes:
            shared_size = max(8, min(candidate_sizes))
            try:
                shared_font = (
                    ImageFont.truetype(small_font_path, shared_size)
                    if small_font_path
                    else ImageFont.load_default()
                )
            except Exception:
                shared_font = ImageFont.load_default()

            if subtitle_top_frac is not None:
                # TOP of first small line sits exactly at sub_top; subsequent
                # lines stack with gap_small between them.
                y_cursor = title_h
            else:
                y_cursor = title_h + gap_between
            for i, s in enumerate(small_lines):
                disp = _ellipsize_to_width(s, shared_font, inner_w)
                tw, th, bbox = _measure(shared_font, disp)
                ox = (bw - tw) // 2 - int(bbox[0])
                if subtitle_top_frac is not None:
                    # Anchor text TOP at y_cursor (not centered).
                    oy = y_cursor - int(bbox[1])
                    y_cursor += th + gap_small
                else:
                    oy = y_cursor + (per_small_h - th) // 2 - int(bbox[1])
                    y_cursor += per_small_h
                    if i < n_small - 1:
                        y_cursor += gap_small
                draw.text(
                    (ox, oy), disp, font=shared_font, fill=(255, 255, 255, 255)
                )

    patch = _pil_image_to_bgra(img)
    _MUSIC_TEXT_CACHE[key] = patch
    if len(_MUSIC_TEXT_CACHE) > _MUSIC_TEXT_CACHE_MAX:
        _MUSIC_TEXT_CACHE.pop(next(iter(_MUSIC_TEXT_CACHE)))
    return patch
