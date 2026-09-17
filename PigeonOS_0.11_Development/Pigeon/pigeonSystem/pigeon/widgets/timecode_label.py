"""Static timecode-style label on the global 19×8 grid (configurable string, e.g. ``00:00:00``).

Each character is drawn in a fixed-width cell (tabular layout) so proportional fonts like
Sharp Sans do not shift when digits change. Occupies a horizontal band: anchor ``[row, col]``,
span ``(cols_wide, rows_tall)``.
"""

from __future__ import annotations

import inspect

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pigeon.compositing import alpha_blend_bgra_over_bgr
from pigeon.design import rect_for_span_at_cell
from pigeon.font_paths import resolve_ui_font_bold, resolve_ui_font_medium, resolve_ui_font_regular
from pigeon.ui_pill import pill_bgra_black

_FILL = (255, 255, 255, 255)
# Tight inset so glyphs nearly fill the grid squares (still avoids edge clipping).
_PAD_FRAC = 0.04
# Glyphs that can appear in H:MM:SS (and similar) timecodes; used to size every cell.
_TIMECODE_CHAR_SET = "0123456789:"


def _pick_font_path() -> str | None:
    return resolve_ui_font_medium() or resolve_ui_font_bold() or resolve_ui_font_regular()


def _load_font(path: str | None, size: int) -> ImageFont.ImageFont:
    if not path:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _char_bbox(
    draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, ch: str
) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), ch, font=font)


def _cell_metrics(
    draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, char_set: str
) -> tuple[int, int]:
    """Max glyph width and height in ``char_set`` for this font (for fixed cells)."""
    max_w = 1
    max_h = 1
    for ch in char_set:
        l, t, r, b = _char_bbox(draw, font, ch)
        max_w = max(max_w, r - l)
        max_h = max(max_h, b - t)
    return max_w, max_h


def _fit_font_fixed_cells(
    path: str,
    num_slots: int,
    char_set: str,
    max_w: int,
    max_h: int,
    min_sz: int = 6,
) -> ImageFont.ImageFont:
    """Largest font so ``num_slots`` × max(char widths) fits in ``max_w`` × ``max_h``."""
    if max_w < 4 or max_h < 4 or num_slots < 1:
        return _load_font(path, min_sz)
    lo, hi = min_sz, max(max_h * 4, max_w * 2, 400)
    best = _load_font(path, min_sz)
    probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    probe_draw = ImageDraw.Draw(probe)
    while lo <= hi:
        mid = (lo + hi) // 2
        f = _load_font(path, mid)
        cw, ch = _cell_metrics(probe_draw, f, char_set)
        total_w = num_slots * cw
        if total_w <= max_w and ch <= max_h:
            best = f
            lo = mid + 1
        else:
            hi = mid - 1
    return best


class TimecodeLabelWidget:
    """Timecode string in Sharp Sans (or fallback); each character in a fixed-width cell."""

    def __init__(
        self,
        *,
        anchor_row: int | float = 1,
        anchor_col: int = 7,
        text: str = "00:00:00",
        span_wide: int = 3,
        span_tall: int = 1,
        tabular_char_set: str = _TIMECODE_CHAR_SET,
    ) -> None:
        self._anchor_row = anchor_row
        self._anchor_col = anchor_col
        self._text = text
        self._span = (max(1, int(span_wide)), max(1, int(span_tall)))
        self._tabular_char_set = tabular_char_set or _TIMECODE_CHAR_SET

    @property
    def grid_span(self) -> tuple[int, int]:
        return self._span

    @property
    def grid_anchor(self) -> tuple[int | float, int]:
        return (self._anchor_row, self._anchor_col)

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> bool:
        new_text = str(text)
        if new_text == self._text:
            return False
        self._text = new_text
        return True

    def _rgba_image(self, w: int, h: int) -> Image.Image:
        """Tight capsule around the tabular timecode (grid cell footprint)."""
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        pad = max(2, int(round(min(w, h) * _PAD_FRAC)))
        mw = max(4, w - 2 * pad)
        mh = max(4, h - 2 * pad)
        font_path = _pick_font_path()
        s = self._text
        n = max(1, len(s))
        font = _fit_font_fixed_cells(
            font_path, n, self._tabular_char_set, mw, mh
        )
        cw, row_h = _cell_metrics(draw, font, self._tabular_char_set)
        block_w = n * cw
        block_x = pad + max(0, (mw - block_w) // 2)
        cy = pad + mh // 2

        px_pad = max(4, int(round(cw * 0.45)))
        py_pad = max(3, int(round(row_h * 0.22)))
        pill_x0 = max(0, block_x - px_pad)
        pill_x1 = min(w, block_x + block_w + px_pad)
        pill_y0 = max(0, cy - row_h // 2 - py_pad)
        pill_y1 = min(h, cy + row_h // 2 + py_pad + 2)
        pbw = max(1, pill_x1 - pill_x0)
        pbh = max(1, pill_y1 - pill_y0)
        pill_bgra = pill_bgra_black(pbw, pbh)
        pill_rgba = cv2.cvtColor(pill_bgra, cv2.COLOR_BGRA2RGBA)
        img.paste(
            Image.fromarray(pill_rgba, "RGBA"),
            (pill_x0, pill_y0),
            Image.fromarray(pill_rgba, "RGBA"),
        )

        use_anchor = "anchor" in inspect.signature(draw.text).parameters

        if use_anchor:
            for i, glyph in enumerate(s):
                cx = block_x + i * cw + cw // 2
                draw.text((cx, cy), glyph, font=font, anchor="mm", fill=_FILL)
        else:
            for i, glyph in enumerate(s):
                l, t, r, b = _char_bbox(draw, font, glyph)
                gw, gh = r - l, b - t
                x = block_x + i * cw + (cw - gw) // 2 - l
                y = cy - gh // 2 - t
                draw.text((x, y), glyph, font=font, fill=_FILL)
        return img

    def _rgba_image_extend_horizontal(
        self,
        canvas_w: int,
        canvas_h: int,
        *,
        content_width: int,
        align: str,
    ) -> Image.Image:
        """
        Tight vertical capsule around the glyphs (same height as :meth:`_rgba_image`),
        stretched horizontally across the full patch width (under the progress bar gap).
        """
        cvs_w = max(1, int(canvas_w))
        cvs_h = max(1, int(canvas_h))
        core_w = max(1, min(int(content_width), cvs_w))
        img = Image.new("RGBA", (cvs_w, cvs_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        pad = max(2, int(round(min(core_w, cvs_h) * _PAD_FRAC)))
        mw = max(4, core_w - 2 * pad)
        mh = max(4, cvs_h - 2 * pad)
        font_path = _pick_font_path()
        s = self._text
        n = max(1, len(s))
        font = _fit_font_fixed_cells(
            font_path, n, self._tabular_char_set, mw, mh
        )
        char_w, row_h = _cell_metrics(draw, font, self._tabular_char_set)
        block_w = n * char_w
        if align == "right":
            x_off = cvs_w - core_w
            block_x = x_off + pad + max(0, (mw - block_w) // 2)
        else:
            block_x = pad + max(0, (mw - block_w) // 2)
        cy = pad + mh // 2

        py_pad = max(3, int(round(row_h * 0.22)))
        pill_y0 = max(0, cy - row_h // 2 - py_pad)
        pill_y1 = min(cvs_h, cy + row_h // 2 + py_pad + 2)
        pbh = max(1, pill_y1 - pill_y0)

        pill_bgra = pill_bgra_black(cvs_w, pbh)
        pill_rgba = cv2.cvtColor(pill_bgra, cv2.COLOR_BGRA2RGBA)
        pill_pil = Image.fromarray(pill_rgba, "RGBA")
        img.paste(pill_pil, (0, pill_y0), pill_pil)

        use_anchor = "anchor" in inspect.signature(draw.text).parameters
        if use_anchor:
            for i, glyph in enumerate(s):
                cx = block_x + i * char_w + char_w // 2
                draw.text((cx, cy), glyph, font=font, anchor="mm", fill=_FILL)
        else:
            for i, glyph in enumerate(s):
                l, t, r, b = _char_bbox(draw, font, glyph)
                gw, gh = r - l, b - t
                x = block_x + i * char_w + (char_w - gw) // 2 - l
                y = cy - gh // 2 - t
                draw.text((x, y), glyph, font=font, fill=_FILL)
        return img

    def _pill_metrics_extend_horizontal(
        self,
        canvas_w: int,
        canvas_h: int,
        *,
        content_width: int,
        align: str,
    ) -> tuple[int, int]:
        """Vertical band (pill_y0, pbh) for the tight capsule in an extended horizontal patch."""
        cvs_w = max(1, int(canvas_w))
        cvs_h = max(1, int(canvas_h))
        core_w = max(1, min(int(content_width), cvs_w))
        pad = max(2, int(round(min(core_w, cvs_h) * _PAD_FRAC)))
        mw = max(4, core_w - 2 * pad)
        mh = max(4, cvs_h - 2 * pad)
        font_path = _pick_font_path()
        s = self._text
        n = max(1, len(s))
        font = _fit_font_fixed_cells(
            font_path, n, self._tabular_char_set, mw, mh
        )
        probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        draw = ImageDraw.Draw(probe)
        _, row_h = _cell_metrics(draw, font, self._tabular_char_set)
        cy = pad + mh // 2
        py_pad = max(3, int(round(row_h * 0.22)))
        pill_y0 = max(0, cy - row_h // 2 - py_pad)
        pill_y1 = min(cvs_h, cy + row_h // 2 + py_pad + 2)
        pbh = max(1, pill_y1 - pill_y0)
        return pill_y0, pbh

    def _rgba_image_text_only_extend_horizontal(
        self,
        canvas_w: int,
        canvas_h: int,
        *,
        content_width: int,
        align: str,
        cy_center: int | None = None,
        glyph_pad_left: int = 0,
    ) -> Image.Image:
        """Like :meth:`_rgba_image_extend_horizontal` but no pill — only glyphs (transparent bg)."""
        cvs_w = max(1, int(canvas_w))
        cvs_h = max(1, int(canvas_h))
        core_w = max(1, min(int(content_width), cvs_w))
        img = Image.new("RGBA", (cvs_w, cvs_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        pad = max(2, int(round(min(core_w, cvs_h) * _PAD_FRAC)))
        mw = max(4, core_w - 2 * pad)
        mh = max(4, cvs_h - 2 * pad)
        font_path = _pick_font_path()
        s = self._text
        n = max(1, len(s))
        font = _fit_font_fixed_cells(
            font_path, n, self._tabular_char_set, mw, mh
        )
        char_w, row_h = _cell_metrics(draw, font, self._tabular_char_set)
        block_w = n * char_w
        extra_l = max(0, int(glyph_pad_left))
        if align == "right":
            x_off = cvs_w - core_w
            block_x = x_off + pad + max(0, (mw - block_w) // 2)
        elif align == "center":
            block_x = max(0, (cvs_w - block_w) // 2)
        else:
            block_x = pad + max(0, (mw - block_w) // 2) + extra_l
        cy = pad + mh // 2 if cy_center is None else int(cy_center)
        cy = max(row_h // 2 + 1, min(cvs_h - row_h // 2 - 1, cy))

        use_anchor = "anchor" in inspect.signature(draw.text).parameters
        if use_anchor:
            for i, glyph in enumerate(s):
                cx = block_x + i * char_w + char_w // 2
                draw.text((cx, cy), glyph, font=font, anchor="mm", fill=_FILL)
        else:
            for i, glyph in enumerate(s):
                l, t, r, b = _char_bbox(draw, font, glyph)
                gw, gh = r - l, b - t
                x = block_x + i * char_w + (char_w - gw) // 2 - l
                y = cy - gh // 2 - t
                draw.text((x, y), glyph, font=font, fill=_FILL)
        return img

    def bgra_patch_extend_under_bar(
        self,
        *,
        canvas_w: int,
        canvas_h: int,
        content_width: int,
        align: str,
    ) -> np.ndarray:
        """Wider patch for status bar: pill runs to the bar; height stays text-tight."""
        rgba = np.asarray(
            self._rgba_image_extend_horizontal(
                canvas_w, canvas_h, content_width=content_width, align=align
            )
        )
        return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)

    def bgra_text_only_extend_under_bar(
        self,
        *,
        canvas_w: int,
        canvas_h: int,
        content_width: int,
        align: str,
        cy_center: int | None = None,
        glyph_pad_left: int = 0,
    ) -> np.ndarray:
        """Transparent patch with timecode glyphs only (for compositing on a shared pill)."""
        rgba = np.asarray(
            self._rgba_image_text_only_extend_horizontal(
                canvas_w,
                canvas_h,
                content_width=content_width,
                align=align,
                cy_center=cy_center,
                glyph_pad_left=glyph_pad_left,
            )
        )
        return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)

    def bgra_patch(self) -> np.ndarray:
        wx, wy, w, h = rect_for_span_at_cell(
            self._span[0],
            self._span[1],
            row_1based=self._anchor_row,
            col_1based=self._anchor_col,
        )
        rgba = np.asarray(self._rgba_image(w, h))
        return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)

    def render(self, canvas_bgr: np.ndarray) -> None:
        wx, wy, w, h = rect_for_span_at_cell(
            self._span[0],
            self._span[1],
            row_1based=self._anchor_row,
            col_1based=self._anchor_col,
        )
        roi = canvas_bgr[wy : wy + h, wx : wx + w]
        rgba = np.asarray(self._rgba_image(w, h))
        bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
        roi[:] = alpha_blend_bgra_over_bgr(roi, bgra)
