"""Grid overlay drawing at design resolution (shared by main UI and widget shells)."""

from __future__ import annotations

import cv2
import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont

    _PIL_OK = True
except ImportError:
    _PIL_OK = False

from pigeon.font_paths import resolve_ui_font_bold, resolve_ui_font_extrabold, resolve_ui_font_medium


def overlay_grid_metrics(
    width: int, height: int, rows: int, cols: int
) -> tuple[float, int, int, int, int, int]:
    """Same cell size and origin as :func:`build_grid_overlay_bgra` (fit-to-height, centered in X).

    Returns ``(cell_f, x0, y0, grid_w, grid_h, cell_round)`` where ``cell_f`` is the float cell
    edge length (used for line positions), and ``cell_round`` is ``max(1, round(cell_f))`` for
    UI sizing heuristics.
    """
    cell_f = float(height) / float(max(1, rows))
    cell_round = max(1, int(round(cell_f)))
    grid_w = int(round(cell_f * cols))
    grid_h = int(round(cell_f * rows))
    x0 = int(round((width - grid_w) / 2.0))
    y0 = int(round((height - grid_h) / 2.0))
    return (cell_f, x0, y0, grid_w, grid_h, cell_round)


def build_grid_overlay_bgra(width: int, height: int, rows: int, cols: int) -> np.ndarray:
    """BGRA overlay: rows×cols square cells fit-to-height then center-cropped in X.

    This keeps the grid vertically accurate to the full frame (no top/bottom letterbox band),
    while allowing side crop when the target aspect is narrower than the grid design aspect.
    """
    overlay = np.zeros((height, width, 4), dtype=np.uint8)

    cell_f, x0, y0, grid_w, grid_h, cell = overlay_grid_metrics(width, height, rows, cols)

    line_color = (255, 255, 0, 235)
    text_color = (180, 255, 255, 235)
    text_outline_color = (0, 0, 0, 140)

    cv2.rectangle(overlay, (x0, y0), (x0 + grid_w, y0 + grid_h), line_color, 4, lineType=cv2.LINE_AA)

    for c in range(1, cols):
        x = int(round(x0 + c * cell_f))
        cv2.line(overlay, (x, y0), (x, y0 + grid_h), line_color, 3, lineType=cv2.LINE_AA)
    for r in range(1, rows):
        y = int(round(y0 + r * cell_f))
        cv2.line(overlay, (x0, y), (x0 + grid_w, y), line_color, 3, lineType=cv2.LINE_AA)

    # Hershey Plain reads lighter than SIMPLEX at the same nominal scale; bump scale for legibility.
    font = cv2.FONT_HERSHEY_PLAIN
    font_scale = max(0.95, cell / 52.0)
    thickness = 1
    for r in range(rows):
        for c in range(cols):
            label = f"[{r + 1},{c + 1}]"
            (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)
            cx = int(round(x0 + (c + 0.5) * cell_f))
            cy = int(round(y0 + (r + 0.5) * cell_f))
            tx = cx - (tw // 2)
            ty = cy + (th // 2)

            pad_x = max(4, int(cell * 0.04))
            pad_y = max(3, int(cell * 0.03))
            box_x1 = tx - pad_x
            box_y1 = ty - th - pad_y
            box_x2 = tx + tw + pad_x
            box_y2 = ty + baseline + pad_y
            cv2.rectangle(
                overlay,
                (box_x1, box_y1),
                (box_x2, box_y2),
                (0, 0, 0, 110),
                thickness=-1,
                lineType=cv2.LINE_AA,
            )
            for ox, oy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                cv2.putText(
                    overlay,
                    label,
                    (tx + ox, ty + oy),
                    font,
                    font_scale,
                    text_outline_color,
                    thickness,
                    lineType=cv2.LINE_AA,
                )
            cv2.putText(
                overlay, label, (tx, ty), font, font_scale, text_color, thickness, lineType=cv2.LINE_AA
            )

    return overlay


def _draw_line_with_alpha(
    overlay: np.ndarray,
    pt1: tuple[int, int],
    pt2: tuple[int, int],
    bgr: tuple[int, int, int],
    alpha: int,
    thickness: int,
) -> None:
    """Draw line onto BGRA overlay using ``alpha`` directly."""
    color = (int(bgr[0]), int(bgr[1]), int(bgr[2]), int(max(0, min(255, alpha))))
    cv2.line(
        overlay,
        (int(pt1[0]), int(pt1[1])),
        (int(pt2[0]), int(pt2[1])),
        color,
        int(max(1, thickness)),
        lineType=cv2.LINE_AA,
    )


def _draw_centered_label_box(
    overlay: np.ndarray,
    text: str,
    *,
    center_x: int,
    center_y: int,
    font_scale: float,
    text_bgra: tuple[int, int, int, int],
    box_bgra: tuple[int, int, int, int],
    margin_x: int = 4,
    margin_y: int = 3,
) -> None:
    """Draw centered text over a translucent black rectangle."""
    font = cv2.FONT_HERSHEY_PLAIN
    thickness = 1
    (tw, th), baseline = cv2.getTextSize(text, font, font_scale, thickness)
    tx = int(center_x - tw // 2)
    ty = int(center_y + th // 2)
    x1 = tx - int(margin_x)
    y1 = ty - th - int(margin_y)
    x2 = tx + tw + int(margin_x)
    y2 = ty + baseline + int(margin_y)
    cv2.rectangle(
        overlay,
        (x1, y1),
        (x2, y2),
        box_bgra,
        thickness=-1,
        lineType=cv2.LINE_AA,
    )
    cv2.putText(
        overlay,
        text,
        (tx, ty),
        font,
        float(font_scale),
        text_bgra,
        thickness,
        lineType=cv2.LINE_AA,
    )


def build_absolute_grid_overlay_bgra(
    width: int,
    height: int,
    *,
    with_fractional_lines: bool = False,
) -> np.ndarray:
    """
    Absolute-pixel grid for viewFive (built at the true design resolution).

    - A 1 px line exactly on every 50 px coordinate, horizontally and vertically.
    - Every line is labeled with its actual pixel value ("150", "400", …) so
      on-screen coordinates can be read straight off the overlay.
    - Optional minor lines at 12.5/25/37.5 px between majors.
    """
    w = max(1, int(width))
    h = max(1, int(height))
    overlay = np.zeros((h, w, 4), dtype=np.uint8)

    major_step = 50
    major_bgr = (255, 255, 0)
    # 1 px lines: the drawn pixel IS the coordinate (the old 4 px lines read ±2 px off).
    major_thickness = 1
    major_alpha = 165
    hundred_alpha = 235  # multiples of 100 pop for quicker counting

    minor_bgr = (255, 255, 0)
    half_alpha = 96
    quarter_alpha = 64
    minor_thickness = 1

    x_lines = list(range(0, w, major_step))
    y_lines = list(range(0, h, major_step))

    for x in x_lines:
        xc = min(w - 1, x)
        a = hundred_alpha if x % 100 == 0 else major_alpha
        _draw_line_with_alpha(overlay, (xc, 0), (xc, h - 1), major_bgr, a, major_thickness)
        if with_fractional_lines:
            for frac, a2 in ((12.5, quarter_alpha), (25.0, half_alpha), (37.5, quarter_alpha)):
                xf = int(round(x + frac))
                if xf <= 0 or xf >= w - 1:
                    continue
                _draw_line_with_alpha(
                    overlay, (xf, 0), (xf, h - 1), minor_bgr, a2, minor_thickness
                )

    for y in y_lines:
        yc = min(h - 1, y)
        a = hundred_alpha if y % 100 == 0 else major_alpha
        _draw_line_with_alpha(overlay, (0, yc), (w - 1, yc), major_bgr, a, major_thickness)
        if with_fractional_lines:
            for frac, a2 in ((12.5, quarter_alpha), (25.0, half_alpha), (37.5, quarter_alpha)):
                yf = int(round(y + frac))
                if yf <= 0 or yf >= h - 1:
                    continue
                _draw_line_with_alpha(
                    overlay, (0, yf), (w - 1, yf), minor_bgr, a2, minor_thickness
                )

    # Pixel-value labels: x values along the top edge, y values along the left edge.
    font_scale = max(0.85, min(1.15, h / 420.0))
    text_bgra = (180, 255, 255, 235)
    box_bgra = (0, 0, 0, 180)
    font = cv2.FONT_HERSHEY_PLAIN
    (_, label_th), _ = cv2.getTextSize("0123456789", font, font_scale, 1)
    top_y = max(10, label_th // 2 + 6)
    left_x_pad = 6

    for x in x_lines:
        if x == 0:
            continue  # the y-axis "0" label owns the corner
        label = str(x)
        (tw, _), _ = cv2.getTextSize(label, font, font_scale, 1)
        cx = min(x, w - tw // 2 - left_x_pad)
        _draw_centered_label_box(
            overlay,
            label,
            center_x=cx,
            center_y=top_y,
            font_scale=font_scale,
            text_bgra=text_bgra,
            box_bgra=box_bgra,
        )

    for y in y_lines:
        label = str(y)
        (tw, _), _ = cv2.getTextSize(label, font, font_scale, 1)
        cy = max(y, top_y) if y == 0 else min(y, h - label_th)
        _draw_centered_label_box(
            overlay,
            label,
            center_x=left_x_pad + tw // 2 + 4,
            center_y=cy,
            font_scale=font_scale,
            text_bgra=text_bgra,
            box_bgra=box_bgra,
        )

    return overlay


def _draw_view_five_c_test_text(overlay: np.ndarray) -> None:
    """
    Draw testing text for viewFive_c.

    "Pigeon" starts at absolute grid coordinate (5,4), i.e. x=250, y=200 for 50 px cells.
    Target text height is one box (50 px).
    """
    h, w = overlay.shape[:2]
    anchor_col = 5
    anchor_row = 4
    second_anchor_col = 5.25
    second_anchor_row = 5.0
    box = 50
    x = int(round(anchor_col * box))
    y = int(round(anchor_row * box))
    x2 = int(round(float(second_anchor_col) * box))
    y2 = int(round(float(second_anchor_row) * box))
    if x >= w or y >= h:
        return

    text = "Pigeon"
    text2 = "is working"
    target_text_h = box

    if _PIL_OK:
        font_path = resolve_ui_font_extrabold() or resolve_ui_font_bold()
        font_path_medium = resolve_ui_font_medium() or resolve_ui_font_bold()
        # Fit font by lowercase x-height (not cap/ascender height).
        size = max(8, target_text_h)
        font_obj = None
        while size >= 8:
            try:
                font_obj = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
            except Exception:
                font_obj = ImageFont.load_default()
            try:
                l, t, r, b = font_obj.getbbox(text)
                tw, th = int(r - l), int(b - t)
                lx, txh, rx, bxh = font_obj.getbbox("x")
                xh = int(bxh - txh)
            except Exception:
                tw, th = font_obj.getsize(text)
                try:
                    _wx, xh = font_obj.getsize("x")
                except Exception:
                    xh = th
                l, t = 0, 0
            if max(1, xh) <= target_text_h:
                break
            size -= 1

        def _blend_text_line(txt: str, tx: int, ty: int, line_font) -> None:
            if line_font is None:
                return
            try:
                l0, t0, r0, b0 = line_font.getbbox(txt)
                tw0, th0 = int(r0 - l0), int(b0 - t0)
            except Exception:
                tw0, th0 = line_font.getsize(txt)
                l0, t0 = 0, 0
            patch_w0 = max(1, int(tw0 + 4))
            patch_h0 = max(1, int(th0 + 4))
            img0 = Image.new("RGBA", (patch_w0, patch_h0), (0, 0, 0, 0))
            draw0 = ImageDraw.Draw(img0)
            draw0.text((2 - int(l0), 2 - int(t0)), txt, font=line_font, fill=(255, 255, 255, 235))
            rgba0 = np.array(img0, dtype=np.uint8)
            patch0 = rgba0[..., [2, 1, 0, 3]]

            ox = int(tx)
            oy = int(ty)
            if ox + patch_w0 >= w:
                ox = max(0, w - patch_w0)
            if oy + patch_h0 >= h:
                oy = max(0, h - patch_h0)
            if ox >= w or oy >= h:
                return
            x0, y0 = max(0, ox), max(0, oy)
            x1, y1 = min(w, ox + patch_w0), min(h, oy + patch_h0)
            if x1 <= x0 or y1 <= y0:
                return
            src = patch0[(y0 - oy) : (y1 - oy), (x0 - ox) : (x1 - ox)]
            dst = overlay[y0:y1, x0:x1]
            sa = src[:, :, 3:4].astype(np.float32) / 255.0
            dst_rgb = dst[:, :, :3].astype(np.float32)
            src_rgb = src[:, :, :3].astype(np.float32)
            out_rgb = src_rgb * sa + dst_rgb * (1.0 - sa)
            dst[:, :, :3] = np.clip(out_rgb, 0, 255).astype(np.uint8)
            dst[:, :, 3] = np.maximum(dst[:, :, 3], src[:, :, 3])

        if font_obj is not None:
            _blend_text_line(text, x, y, font_obj)
            try:
                font_medium_obj = (
                    ImageFont.truetype(font_path_medium, size)
                    if font_path_medium
                    else ImageFont.load_default()
                )
            except Exception:
                font_medium_obj = ImageFont.load_default()
            _blend_text_line(text2, x2, y2, font_medium_obj)
            return

    # Fallback if PIL/font unavailable: no background box, stroke text only.
    font = cv2.FONT_HERSHEY_PLAIN
    lo, hi = 0.1, 20.0
    for _ in range(24):
        mid = (lo + hi) * 0.5
        (_, th), _bl = cv2.getTextSize(text, font, mid, 1)
        if th < target_text_h:
            lo = mid
        else:
            hi = mid
    font_scale = lo
    (tw, th), baseline = cv2.getTextSize(text, font, font_scale, 1)
    tx = x if (x + tw) < w else max(0, w - tw - 1)
    ty = (y + th) if (y + th + baseline) < h else max(th + 1, h - baseline - 1)
    cv2.putText(overlay, text, (tx, ty), font, font_scale, (255, 255, 255, 235), 1, lineType=cv2.LINE_AA)
    (tw2, th2), baseline2 = cv2.getTextSize(text2, font, font_scale, 1)
    tx2 = x2 if (x2 + tw2) < w else max(0, w - tw2 - 1)
    ty2 = (y2 + th2) if (y2 + th2 + baseline2) < h else max(th2 + 1, h - baseline2 - 1)
    cv2.putText(overlay, text2, (tx2, ty2), font, font_scale, (255, 255, 255, 235), 1, lineType=cv2.LINE_AA)


def build_absolute_grid_overlay_view_five_c_bgra(width: int, height: int) -> np.ndarray:
    """viewFive_c: absoluteLines + test text overlay."""
    overlay = build_absolute_grid_overlay_bgra(
        width,
        height,
        with_fractional_lines=False,
    )
    _draw_view_five_c_test_text(overlay)
    return overlay
