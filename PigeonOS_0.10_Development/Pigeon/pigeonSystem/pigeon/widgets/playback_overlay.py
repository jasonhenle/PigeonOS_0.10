"""Playback chrome: streaming badge, receiver-driven audio lines (grid-aligned)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pigeon.compositing import alpha_blend_bgra_over_bgr, cv_resize_interp
from pigeon.design import get_grid_geometry, rect_for_span_at_cell, rect_for_span_top_right_at_cell
from pigeon.font_paths import resolve_ui_font_bold, resolve_ui_font_extrabold
from pigeon.image_ui_protocol import load_image_bgra
from pigeon.layout_paths import pick_pigeon_logo_png
from pigeon.receiver_denon import looks_like_hdmi_input_selector
from pigeon.widgets.status_bar import DesignPatch

# Service badge + audio lines (audioConfig): 10% smaller, centered in grid cells.
AUDIO_CONFIG_SCALE = 0.9

# Volume line: fixed cap as a fraction of the cell height (no size animation).
VOLUME_TEXT_FIT_H = 0.52
# Volume glyph opacity vs other overlay text (0–255 alpha); ~20% for subtle readout.
_VOLUME_TEXT_RGBA = (255, 255, 255, 51)
_PLAYBACK_SETTING_TEXT_RGBA = (235, 238, 244, 210)

PATCH_LAYER_WORDMARK = "wordmark"  # legacy layer id; wordmark blit removed
PATCH_LAYER_STREAMING_BADGE = "streaming_badge"
PATCH_LAYER_RECEIVER_AUDIO = "receiver_audio"
PATCH_LAYER_PAUSED_ROW = "paused_row"

# Receiver-driven playback line + volume stack: nudge down in design pixels.
_RECEIVER_AUDIO_STACK_NUDGE_Y_PX = 5

# Row 8.0 (aligned with TRT pill row); 17 cells wide cols 2–18, centered text.
_PAUSED_ROW_TEXT = "paused"
_PAUSED_ROW_SPAN_W = 17
_PAUSED_ROW_SPAN_H = 1
_PAUSED_ROW_GRID_ROW = 8.0
_PAUSED_ROW_GRID_COL = 2
# Nudge in design pixels (negative = toward top of canvas).
_PAUSED_ROW_OFFSET_Y_PX = 0
_PAUSED_ROW_RGBA = (238, 240, 245, 242)


# Denon ``PS_*`` parameter-set fragments (``MULTEQ=AUDYSSEY``, etc.) — not input/config labels.
_PS_CFG_FRAGMENT_RE = re.compile(
    r"^(?:MULTEQ|DYNEQ|DYNVOL|REFLEV)=[^\s|]+$",
    re.I,
)
_PS_CFG_COMBO_RE = re.compile(
    r"^(?:[A-Z_]+=[^\s|]+\s*\|\s*)+[A-Z_]+=[^\s|]+$",
    re.I,
)

_IDLE_AUDIO_PLACEHOLDERS = frozenset(
    {
        "no audio",
        "no signal",
        "no sig",
        "unknown",
        "n/a",
        "na",
        "none",
        "--",
        "not available",
        "no input",
        "unsupported",
        "lock",
        "unlock",
        "hdmi no signal",
        "no data",
    }
)


def _looks_like_receiver_debug_blob(raw: object) -> bool:
    """True when ``raw`` stringifies to a telnet/debug dict (must never paint on UI)."""
    s = str(raw or "").strip()
    if not s.startswith("{"):
        return False
    if not s.endswith("}"):
        return False
    # Typical leak: ``{'PW': 'STANDBY', 'MV': '-26dB', ...}``
    return "'" in s and ":" in s


def _receiver_audio_display_line(raw: object) -> str:
    """Strip placeholder / empty receiver strings so the overlay can omit those rows."""
    if _looks_like_receiver_debug_blob(raw):
        return ""
    s = str(raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low in _IDLE_AUDIO_PLACEHOLDERS:
        return ""
    if looks_like_hdmi_input_selector(s):
        return ""
    if all(c in _VOLUME_PLACEHOLDER_CHARS or c.isspace() for c in s):
        return ""
    if _PS_CFG_FRAGMENT_RE.match(s) or _PS_CFG_COMBO_RE.match(s):
        return ""
    if re.fullmatch(r"PS_[A-Z0-9_]+(?:=[^\s|]+)?", s, re.I):
        return ""
    return s


def receiver_audio_config_display_line(incoming: str, config: str) -> str:
    """
    Source format and playback/surround mode for the audio-config row.

    When both differ (e.g. MULTI-IN → AURO3D), show ``SOURCE > PLAYBACK``.
    When they match after normalization, show a single label.
    HDMI input names (SAT/CBL, HDMI1, …) are never shown.
    """
    inc = _receiver_audio_display_line(incoming)
    cfg = _receiver_audio_display_line(config)
    if inc:
        inc = inc.upper()
    if cfg:
        cfg = cfg.upper()

    def _key(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", s.lower())

    if inc and cfg:
        if _key(inc) and _key(inc) == _key(cfg):
            return inc
        # Also collapse when a composed line somehow still has identical sides.
        line = f"{inc} > {cfg}"
        left, _, right = line.partition(" > ")
        if right and _key(left) == _key(right):
            return left.strip()
        return line
    return inc or cfg


# Volume unknown / idle placeholders from receiver poll — do not draw (glyphs read as a slab).
_VOLUME_PLACEHOLDER_CHARS = frozenset("—–-−")  # em dash, en dash, ASCII hyphen, minus sign


def _receiver_volume_display_line(raw: object) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.lower() == "no audio":
        return ""
    if all(c in _VOLUME_PLACEHOLDER_CHARS or c.isspace() for c in s):
        return ""
    if s.lower() in ("n/a", "na", "none", "--"):
        return ""
    # Canonical readout: ``-22.5 dB`` (space, lowercase d, uppercase B, one decimal).
    m_db = re.fullmatch(
        r"([+-]?\d+(?:\.\d+)?)\s*[dD][bB]\s*",
        s,
    )
    if m_db:
        try:
            db = float(m_db.group(1))
        except ValueError:
            return s
        return f"{db:.1f} dB"
    # Bare signed level from some receivers → same shape.
    m_bare = re.fullmatch(r"([+-]\d+(?:\.\d+)?)", s)
    if m_bare:
        try:
            db = float(m_bare.group(1))
        except ValueError:
            return s
        return f"{db:.1f} dB"
    # Already has a messy ``db`` / ``DB`` suffix mid-string.
    m_any = re.search(r"([+-]?\d+(?:\.\d+)?)\s*[dD][bB]\b", s)
    if m_any:
        try:
            db = float(m_any.group(1))
        except ValueError:
            return re.sub(r"\s*[dD][bB]\b", " dB", s)
        return f"{db:.1f} dB"
    return s


def volume_percent_to_widget_line(value: object) -> str:
    """Map a player-reported level (0–100) to a short overlay string, or ``\"\"`` if unknown."""
    if value is None:
        return ""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if f != f:  # NaN
        return ""
    p = int(max(0, min(100, round(f))))
    # Apple TV reports 0 when a physical AV receiver owns the volume line.
    if p == 0:
        return ""
    return str(p)


def _denon_volume_as_widget_line(effective: str) -> str:
    """Normalize Denon-style strings for the volume row (mute → 0, ``NN%`` → ``NN``)."""
    s = str(effective or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low in ("mute", "muted", "off"):
        return "0"
    m = re.search(r"(\d{1,3})\s*%", s)
    if m:
        n = int(m.group(1))
        if 0 <= n <= 100:
            return str(n)
    return s


def volume_fraction_from_display_line(
    raw: object,
    *,
    mvmax_step: float | None = None,
) -> float:
    """
    Map a volume display string to 0..1 for pie-ring crops.

    - ``mute`` / empty / unknown → ``0.0`` (caller should hide the accent)
    - ``NN%`` or bare ``0–100`` → ``n/100``
    - Denon-style dB (``-22.5``, ``-22.5dB``) → relative to ``-80 dB`` floor and
      ``0 dB`` (or ``MVMAX``-derived ceiling when provided)
    """
    s = str(raw or "").strip()
    if not s:
        return 0.0
    low = s.lower().replace(" ", "")
    if low in ("mute", "muted", "off", "0"):
        return 0.0
    m_pct = re.search(r"(\d{1,3})\s*%", s)
    if m_pct:
        n = int(m_pct.group(1))
        return max(0.0, min(1.0, n / 100.0))
    if re.fullmatch(r"\d{1,3}", s):
        n = int(s)
        if 0 <= n <= 100:
            return n / 100.0
    m_db = re.search(r"([+-]?\d+(?:\.\d+)?)\s*dB", s, flags=re.I)
    if not m_db:
        m_db = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)", s)
    if m_db:
        try:
            db = float(m_db.group(1))
        except ValueError:
            return 0.0
        db_min = -80.0
        if mvmax_step is not None and float(mvmax_step) > 0:
            db_max = float(mvmax_step) - 80.0
        else:
            db_max = 0.0
        span = db_max - db_min
        if span <= 1e-6:
            return 0.0
        return max(0.0, min(1.0, (db - db_min) / span))
    return 0.0


def compose_playback_volume_widget_line(
    *,
    stream_row: dict[str, object] | None,
    apple_tv_last_metadata: dict[str, object] | None,
    denon_vol_effective: str,
    roku_tv_volume_percent: str,
) -> str:
    """
    Prefer AV receiver readout (dB, %, level) when the poll returns a usable string; otherwise
    Apple TV ``volume_percent`` (0–100) or Roku TV device level.
    """
    from pigeon.app_state import row_is_playback_apple_tv

    denon_line = _denon_volume_as_widget_line(denon_vol_effective)
    if denon_line and _receiver_volume_display_line(denon_line):
        return denon_line

    is_apple = bool(stream_row) and row_is_playback_apple_tv(stream_row)
    if is_apple:
        md = apple_tv_last_metadata if isinstance(apple_tv_last_metadata, dict) else None
        vp = volume_percent_to_widget_line(md.get("volume_percent") if md else None)
        if vp:
            return vp
    else:
        tv = str(roku_tv_volume_percent or "").strip()
        if tv.isdigit() and 0 <= int(tv) <= 100:
            return tv

    return ""


# Large wordmark: top-left cell [2,3], bottom-right [5,15] → 13×4 cells.
_PIGEON_WORDMARK = "pigeon"
_PIGEON_WORDMARK_ROW = 2
_PIGEON_WORDMARK_COL = 3
_PIGEON_WORDMARK_SPAN_W = 13
_PIGEON_WORDMARK_SPAN_H = 4
# 90% black → ~10% white (RGB).
_PIGEON_WORDMARK_RGBA = (26, 26, 26, 255)


def _pick_extrabold_font_path() -> str | None:
    p = resolve_ui_font_extrabold()
    if p:
        return p
    return resolve_ui_font_bold()


def _fit_font_to_box(text: str, max_w: int, max_h: int) -> ImageFont.ImageFont:
    path = _pick_extrabold_font_path()
    lo, hi = 6, max(max_h * 4, max_w * 2, 400)
    best = ImageFont.load_default()
    if path:
        try:
            best = ImageFont.truetype(path, 6)
        except OSError:
            pass
    probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    while lo <= hi:
        mid = (lo + hi) // 2
        try:
            f = ImageFont.truetype(path, mid)
        except OSError:
            f = ImageFont.load_default()
        l, t, r, b = draw.textbbox((0, 0), text, font=f)
        tw, th = r - l, b - t
        if tw <= max_w and th <= max_h:
            best = f
            lo = mid + 1
        else:
            hi = mid - 1
    return best


# Cache the decoded ``AppLogo_Pigeon.png`` BGRA by (path, mtime) so edits-on-disk pick up
# automatically without a full app restart, but we don't re-read the PNG every render tick.
_PIGEON_LOGO_BGRA_CACHE: dict[tuple[str, float], np.ndarray] = {}


def _pigeon_logo_bgra() -> np.ndarray | None:
    """Return the cached BGRA pixels of ``AppLogo_Pigeon.png``, or ``None`` if it's missing."""
    p = pick_pigeon_logo_png()
    if p is None:
        return None
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    key = (str(p), mtime)
    hit = _PIGEON_LOGO_BGRA_CACHE.get(key)
    if hit is not None:
        return hit
    bgra = load_image_bgra(p)
    if bgra is None or bgra.size == 0:
        return None
    _PIGEON_LOGO_BGRA_CACHE.clear()  # only keep the latest (path, mtime) tuple
    _PIGEON_LOGO_BGRA_CACHE[key] = bgra
    return bgra


def _pigeon_wordmark_patch_bgra(w: int, h: int) -> np.ndarray:
    if w < 2 or h < 2:
        return np.zeros((h, w, 4), dtype=np.uint8)
    # Preferred: rasterize from ``pigeonAssets/App logos/AppLogo_Pigeon.png`` so the on-screen
    # wordmark matches the canonical brand art.
    logo_bgra = _pigeon_logo_bgra()
    if logo_bgra is not None:
        return _image_contain_center_bgra(logo_bgra, w, h)
    # Fallback (file missing): faint procedural text. Keeps the layout intact until the
    # user drops the PNG in place.
    pad = max(4, int(round(min(w, h) * 0.03)))
    mw, mh = max(4, w - 2 * pad), max(4, h - 2 * pad)
    font = _fit_font_to_box(_PIGEON_WORDMARK, mw, mh)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    l, t, r, b = draw.textbbox((0, 0), _PIGEON_WORDMARK, font=font)
    tw, th = r - l, b - t
    x = (w - tw) // 2 - l
    y = (h - th) // 2 - t
    draw.text((x, y), _PIGEON_WORDMARK, font=font, fill=_PIGEON_WORDMARK_RGBA)
    rgba = np.asarray(img)
    return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)


def pigeon_wordmark_design_patch() -> DesignPatch:
    """Faint ``pigeon`` wordmark; same placement as in ``AudioConfig`` when wordmark is shown."""
    wx, wy, ww, wh = rect_for_span_at_cell(
        _PIGEON_WORDMARK_SPAN_W,
        _PIGEON_WORDMARK_SPAN_H,
        row_1based=_PIGEON_WORDMARK_ROW,
        col_1based=_PIGEON_WORDMARK_COL,
    )
    return DesignPatch(
        x=wx,
        y=wy,
        w=ww,
        h=wh,
        bgra=_pigeon_wordmark_patch_bgra(ww, wh),
        layer=PATCH_LAYER_WORDMARK,
    )


def _text_patch_bgra(
    text: str,
    w: int,
    h: int,
    *,
    align: str = "left",
    fill_rgba: tuple[int, int, int, int] = (255, 255, 255, 255),
    fit_max_h: int | None = None,
    edge_pad_px: int | None = None,
    fit_box_scale: float = 1.0,
) -> np.ndarray:
    if w < 2 or h < 2 or not text:
        return np.zeros((h, w, 4), dtype=np.uint8)
    if edge_pad_px is not None:
        pad = max(0, int(edge_pad_px))
    else:
        pad = max(2, int(round(min(w, h) * 0.06)))
    mw, mh = max(4, w - 2 * pad), max(4, h - 2 * pad)
    fs = max(0.25, min(2.0, float(fit_box_scale)))
    if abs(fs - 1.0) > 1e-6:
        mw = max(4, int(round(float(mw) * fs)))
        mh = max(4, int(round(float(mh) * fs)))
    if fit_max_h is not None:
        mh = min(mh, max(4, fit_max_h))
    font = _fit_font_to_box(text, mw, mh)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Prefer anchor-based placement (Pillow 8+) so the left edge of ink matches ``pad`` for "left".
    try:
        if align == "right":
            draw.text(
                (w - pad, h // 2),
                text,
                font=font,
                fill=fill_rgba,
                anchor="rm",
            )
        elif align == "center":
            draw.text(
                (w // 2, h // 2),
                text,
                font=font,
                fill=fill_rgba,
                anchor="mm",
            )
        else:
            draw.text(
                (pad, h // 2),
                text,
                font=font,
                fill=fill_rgba,
                anchor="lm",
            )
    except (TypeError, ValueError):
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        tw, th = r - l, b - t
        if align == "right":
            x = w - pad - tw - l
        elif align == "center":
            x = (w - tw) // 2 - l
        else:
            x = pad - l
        y = (h // 2) - (th // 2) - t
        draw.text((x, y), text, font=font, fill=fill_rgba)
    rgba = np.asarray(img)
    return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)


def _text_patch_bgra_bottom_right(
    text: str,
    w: int,
    h: int,
    *,
    fill_rgba: tuple[int, int, int, int] = (255, 255, 255, 255),
    fit_max_h: int | None = None,
    edge_pad_px: int | None = None,
    baseline_offset_px: int = 0,
) -> np.ndarray:
    """Right-aligned text with glyph bottom pinned near patch bottom."""
    if w < 2 or h < 2 or not text:
        return np.zeros((h, w, 4), dtype=np.uint8)
    if edge_pad_px is not None:
        pad = max(0, int(edge_pad_px))
    else:
        pad = max(2, int(round(min(w, h) * 0.06)))
    mw, mh = max(4, w - 2 * pad), max(4, h - 2 * pad)
    if fit_max_h is not None:
        mh = min(mh, max(4, fit_max_h))
    font = _fit_font_to_box(text, mw, mh)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t
    x = w - pad - tw - l
    y = h - pad - th - t + int(baseline_offset_px)
    draw.text((x, y), text, font=font, fill=fill_rgba)
    rgba = np.asarray(img)
    return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)


def _text_patch_bgra_top_right(
    text: str,
    w: int,
    h: int,
    *,
    fill_rgba: tuple[int, int, int, int] = (255, 255, 255, 255),
    fit_max_h: int | None = None,
    edge_pad_px: int | None = None,
    top_offset_px: int = 0,
) -> np.ndarray:
    """Right-aligned text with glyph top pinned near patch top."""
    if w < 2 or h < 2 or not text:
        return np.zeros((h, w, 4), dtype=np.uint8)
    if edge_pad_px is not None:
        pad = max(0, int(edge_pad_px))
    else:
        pad = max(2, int(round(min(w, h) * 0.06)))
    mw, mh = max(4, w - 2 * pad), max(4, h - 2 * pad)
    if fit_max_h is not None:
        mh = min(mh, max(4, fit_max_h))
    font = _fit_font_to_box(text, mw, mh)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    tw = r - l
    x = w - pad - tw - l
    y = pad - t + int(top_offset_px)
    draw.text((x, y), text, font=font, fill=fill_rgba)
    rgba = np.asarray(img)
    return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)


def _text_patch_bgra_baseline_right(
    text: str,
    w: int,
    h: int,
    *,
    baseline_y_px: int,
    fill_rgba: tuple[int, int, int, int] = (255, 255, 255, 255),
    fit_max_h: int | None = None,
    edge_pad_px: int | None = None,
    baseline_offset_px: int = 0,
) -> np.ndarray:
    """Right-aligned text with explicit baseline position inside the patch."""
    if w < 2 or h < 2 or not text:
        return np.zeros((h, w, 4), dtype=np.uint8)
    if edge_pad_px is not None:
        pad = max(0, int(edge_pad_px))
    else:
        pad = max(2, int(round(min(w, h) * 0.06)))
    mw, mh = max(4, w - 2 * pad), max(4, h - 2 * pad)
    if fit_max_h is not None:
        mh = min(mh, max(4, fit_max_h))
    font = _fit_font_to_box(text, mw, mh)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    l, t, r, _b = draw.textbbox((0, 0), text, font=font)
    tw = r - l
    x = w - pad - tw - l
    try:
        ascent, _descent = font.getmetrics()  # type: ignore[attr-defined]
        asc = int(ascent)
    except Exception:
        # Fallback: keep baseline near lower text box edge.
        asc = max(1, int(round((r - l) * 0.5)))
    baseline_local = int(baseline_y_px) + int(baseline_offset_px)
    y = baseline_local - asc
    draw.text((x, y), text, font=font, fill=fill_rgba)
    rgba = np.asarray(img)
    return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)


def _image_contain_center_bgra(
    src_bgra: np.ndarray, w: int, h: int
) -> np.ndarray:
    if src_bgra is None or src_bgra.size == 0 or w < 1 or h < 1:
        return np.zeros((h, w, 4), dtype=np.uint8)
    sh, sw = src_bgra.shape[:2]
    if sh < 1 or sw < 1:
        return np.zeros((h, w, 4), dtype=np.uint8)
    scale = min(w / float(sw), h / float(sh))
    tw = max(1, int(round(sw * scale)))
    th = max(1, int(round(sh * scale)))
    resized = cv2.resize(
        src_bgra, (tw, th), interpolation=cv_resize_interp(sw, sh, tw, th)
    )
    out = np.zeros((h, w, 4), dtype=np.uint8)
    x0 = max(0, (w - tw) // 2)
    y0 = max(0, (h - th) // 2)
    out[y0 : y0 + th, x0 : x0 + tw] = resized
    return out


@dataclass
class AudioConfig:
    """
    Near top row: streaming-service badge with **top-right** on grid (default row 0.5, col 17); 2-wide sits in cols 16–17;
    up to three audio lines from ``receiver_state``
    (incoming, surround/config, volume); placeholder/unknown values are omitted.
    Volume is **top-right** anchored at ``(volume_anchor_row, volume_top_right_col_1based)``
    (same ``col_right`` convention as the clock); text is right-aligned in the span
    (``edge_pad_px=0``); fixed type size (no boost animation).
    Incoming and config stay in the right-aligned strip on ``audio_row``.
    """

    assets_dir: Path
    pigeon_logo_path: Path | None = None  # unused; PNG wordmark removed
    receiver_state: dict[str, str] = field(
        default_factory=lambda: {"incoming": "", "config": "", "volume": ""}
    )
    service_badge: dict[str, object] = field(
        default_factory=lambda: {"show": False, "filename": "", "label": ""}
    )
    overlay_flags: dict[str, bool] = field(
        default_factory=lambda: {
            "show_paused_row": False,
            "clock_saver_volume_only": False,
            "clock_saver_netflix_full_overlay": False,
            # viewOne.07: streaming logo fills pigeonTMDB_TT — draw "LIVE" here instead of duplicating the badge image.
            "badge_live_instead_of_logo": False,
        }
    )
    badge_row: int | float = 0.5
    # Right edge of the badge aligns with the right edge of grid column 17 (see ``rect_for_span_top_right_at_cell``).
    badge_top_right_col_1based: float = 18.0
    badge_span: tuple[int, int] = (2, 1)
    audio_row: int | float = 7.5
    audio_span_wide: int = 4
    # Volume: explicit baseline row in design-grid units (1-based).
    volume_baseline_row: int | float = 2.5
    volume_top_right_col_1based: float = 19.0
    volume_span_wide: int = 5
    volume_span_tall: int = 1
    # Playback format: explicit baseline row in design-grid units (1-based).
    playback_baseline_row: int | float = 1.75
    # Positive nudges playback baseline down inside its patch.
    playback_text_baseline_offset_px: int = 0

    _cached: list[DesignPatch] | None = field(default=None, repr=False)
    _receiver_sig: tuple[object, ...] | None = field(default=None, init=False, repr=False)
    _warned_badge_files: set[str] = field(default_factory=set, init=False, repr=False)

    def clear_cache(self) -> None:
        self._cached = None
        self._receiver_sig = None

    def _volume_patch(self) -> DesignPatch | None:
        line3 = _receiver_volume_display_line(self.receiver_state.get("volume"))
        if not line3:
            return None
        vx, vy, vw, vh = rect_for_span_top_right_at_cell(
            self.volume_span_wide,
            self.volume_span_tall,
            row_1based=1.0,
            col_right_1based=float(self.volume_top_right_col_1based),
        )
        g = get_grid_geometry()
        baseline_abs = int(round(g.y0 + (float(self.volume_baseline_row) - 1.0) * float(g.cell)))
        baseline_local = max(1, vh - 2)
        vy = int(round(baseline_abs - baseline_local)) + int(_RECEIVER_AUDIO_STACK_NUDGE_Y_PX)
        fit_h = max(4, int(round(vh * VOLUME_TEXT_FIT_H)))
        return DesignPatch(
            x=vx,
            y=vy,
            w=vw,
            h=vh,
            bgra=_text_patch_bgra_baseline_right(
                line3,
                vw,
                vh,
                baseline_y_px=baseline_local,
                fill_rgba=_VOLUME_TEXT_RGBA,
                fit_max_h=fit_h,
                edge_pad_px=0,
            ),
            layer=PATCH_LAYER_RECEIVER_AUDIO,
        )

    def _playback_setting_patch_above_volume(self) -> DesignPatch | None:
        """Optional playback-mode row (e.g. ``dolby atmos``) above the volume row.

        Uses the receiver ``config`` line only, so source labels like ``cat/cbl``
        are naturally excluded. The row is positioned so the text baseline sits
        on the top of row 2 while volume starts at row 1.5 beneath it.
        """
        line = _receiver_audio_display_line(self.receiver_state.get("config"))
        if not line:
            return None
        vx, vy, vw, vh = rect_for_span_top_right_at_cell(
            self.volume_span_wide,
            self.volume_span_tall,
            row_1based=1.0,
            col_right_1based=float(self.volume_top_right_col_1based),
        )
        g = get_grid_geometry()
        baseline_abs = int(round(g.y0 + (float(self.playback_baseline_row) - 1.0) * float(g.cell)))
        baseline_local = max(1, vh - 2)
        vy = int(round(baseline_abs - baseline_local)) + int(_RECEIVER_AUDIO_STACK_NUDGE_Y_PX)
        if vy + vh <= 0:
            return None
        fit_h = max(4, int(round(vh * 0.68)))
        return DesignPatch(
            x=vx,
            y=vy,
            w=vw,
            h=vh,
            bgra=_text_patch_bgra_baseline_right(
                line,
                vw,
                vh,
                baseline_y_px=baseline_local,
                fill_rgba=_PLAYBACK_SETTING_TEXT_RGBA,
                fit_max_h=fit_h,
                edge_pad_px=0,
                baseline_offset_px=int(self.playback_text_baseline_offset_px),
            ),
            layer=PATCH_LAYER_RECEIVER_AUDIO,
        )

    def _build(self) -> list[DesignPatch]:
        # Netflix + backdrop clock saver: draw full badge/audio/volume strip (not receiver-only volume row).
        if self.overlay_flags.get("clock_saver_volume_only") and not self.overlay_flags.get(
            "clock_saver_netflix_full_overlay"
        ):
            vp = self._volume_patch()
            return [vp] if vp is not None else []

        blits: list[DesignPatch] = []

        if self.overlay_flags.get("show_paused_row"):
            # Center ``paused`` inside the **visible** bar pill (opaque bounds), not the asset bbox.
            from pigeon.widgets.status_bar import (
                design_now_playing_bar_opaque_rect,
                design_now_playing_bar_rect,
            )

            br = design_now_playing_bar_opaque_rect(self.assets_dir)
            if br is None:
                br = design_now_playing_bar_rect(self.assets_dir)
            if br is not None:
                bx, by, bw, bh = br
                # Insets keep “paused” fully inside the **opaque** bar pill (italic + antialias).
                pad_x = max(3, int(round(bw * 0.07)))
                pad_y = max(3, int(round(bh * 0.18)))
                pw = max(8, bw - 2 * pad_x)
                ph = max(8, bh - 2 * pad_y)
                px = bx + max(0, (bw - pw) // 2)
                py = by + max(0, (bh - ph) // 2)
            else:
                px, py, pw, ph = rect_for_span_at_cell(
                    _PAUSED_ROW_SPAN_W,
                    _PAUSED_ROW_SPAN_H,
                    row_1based=_PAUSED_ROW_GRID_ROW,
                    col_1based=_PAUSED_ROW_GRID_COL,
                )
                py = max(0, py + _PAUSED_ROW_OFFSET_Y_PX)
            _paused_pad = max(4, int(round(min(pw, ph) * 0.09)))
            blits.append(
                DesignPatch(
                    x=px,
                    y=py,
                    w=pw,
                    h=ph,
                    bgra=_text_patch_bgra(
                        _PAUSED_ROW_TEXT,
                        pw,
                        ph,
                        align="center",
                        fill_rgba=_PAUSED_ROW_RGBA,
                        fit_max_h=max(6, int(round(0.94 * float(ph)))),
                        edge_pad_px=int(_paused_pad),
                    ),
                    layer=PATCH_LAYER_PAUSED_ROW,
                )
            )

        bw, bh = self.badge_span
        bx, by, bww, bhh = rect_for_span_top_right_at_cell(
            bw,
            bh,
            row_1based=self.badge_row,
            col_right_1based=float(self.badge_top_right_col_1based),
        )
        bww_i = max(1, int(round(bww * AUDIO_CONFIG_SCALE)))
        bhh_i = max(1, int(round(bhh * AUDIO_CONFIG_SCALE)))
        bx_i = bx + (bww - bww_i) // 2
        by_i = by + (bhh - bhh_i) // 2
        sb = self.service_badge
        show = bool(sb.get("show"))
        fname = str(sb.get("filename") or "").strip()
        svc_label = str(sb.get("label") or "").strip()
        replace_badge_with_live = bool(
            self.overlay_flags.get("badge_live_instead_of_logo")
        )
        if show and (fname or svc_label):
            badge_path = Path(self.assets_dir) / fname if fname else None
            has_file = bool(fname and badge_path and badge_path.is_file())
            if fname and not has_file and fname not in self._warned_badge_files:
                self._warned_badge_files.add(fname)
                import sys

                sys.stderr.write(f"pigeon: streaming badge not found — {badge_path}\n")
            img_bgra = load_image_bgra(badge_path) if has_file else None
            if has_file and img_bgra is None and fname not in self._warned_badge_files:
                self._warned_badge_files.add(fname)
                import sys

                sys.stderr.write(f"pigeon: streaming badge unreadable — {badge_path}\n")

            if replace_badge_with_live:
                lbl = _text_patch_bgra(
                    "LIVE",
                    bww_i,
                    bhh_i,
                    align="center",
                    fill_rgba=(240, 240, 245, 242),
                )
                blits.append(
                    DesignPatch(
                        x=bx_i,
                        y=by_i,
                        w=bww_i,
                        h=bhh_i,
                        bgra=lbl,
                        layer=PATCH_LAYER_STREAMING_BADGE,
                    )
                )
            # Prefer logo image alone when the asset loads; text is fallback when there is no file.
            elif img_bgra is not None:
                patch = _image_contain_center_bgra(img_bgra, bww_i, bhh_i)
                blits.append(
                    DesignPatch(
                        x=bx_i,
                        y=by_i,
                        w=bww_i,
                        h=bhh_i,
                        bgra=patch,
                        layer=PATCH_LAYER_STREAMING_BADGE,
                    )
                )
            elif svc_label:
                lbl = _text_patch_bgra(
                    svc_label,
                    bww_i,
                    bhh_i,
                    align="center",
                    fill_rgba=(240, 240, 240, 255),
                )
                blits.append(
                    DesignPatch(
                        x=bx_i,
                        y=by_i,
                        w=bww_i,
                        h=bhh_i,
                        bgra=lbl,
                        layer=PATCH_LAYER_STREAMING_BADGE,
                    )
                )

        playback_patch = self._playback_setting_patch_above_volume()
        if playback_patch is not None:
            blits.append(playback_patch)

        vp = self._volume_patch()
        if vp is not None:
            blits.append(vp)

        return blits

    def design_blits(self) -> list[DesignPatch]:
        sb = self.service_badge
        show_paused = bool(self.overlay_flags.get("show_paused_row"))
        sig = (
            str(self.receiver_state.get("incoming", "")),
            str(self.receiver_state.get("config", "")),
            str(self.receiver_state.get("volume", "")),
            bool(sb.get("show")),
            str(sb.get("filename") or ""),
            str(sb.get("label") or ""),
            show_paused,
            bool(self.overlay_flags.get("clock_saver_volume_only")),
            bool(self.overlay_flags.get("clock_saver_netflix_full_overlay")),
            bool(self.overlay_flags.get("badge_live_instead_of_logo")),
        )
        if self._cached is not None and self._receiver_sig == sig:
            return self._cached
        self._receiver_sig = sig
        self._cached = self._build()
        return self._cached

    def render(self, canvas_bgr: np.ndarray) -> None:
        ch, cw = canvas_bgr.shape[:2]
        for p in self.design_blits():
            x, y, w, h = p.x, p.y, p.w, p.h
            if w < 1 or h < 1:
                continue
            x0 = max(0, x)
            y0 = max(0, y)
            x1 = min(cw, x + w)
            y1 = min(ch, y + h)
            if x0 >= x1 or y0 >= y1:
                continue
            sx0 = x0 - x
            sy0 = y0 - y
            roi = canvas_bgr[y0:y1, x0:x1]
            patch = p.bgra[sy0 : sy0 + (y1 - y0), sx0 : sx0 + (x1 - x0)]
            roi[:] = alpha_blend_bgra_over_bgr(roi, patch)


# Backward-compatible name used by pigeon_0_5 and docs.
PlaybackOverlayWidget = AudioConfig
