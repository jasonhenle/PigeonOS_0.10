"""Example widget for testing the shell (replace with real widgets)."""

from __future__ import annotations

import cv2

from pigeon.design import rect_for_span_from_origin


class PlaceholderWidget:
    def __init__(self, squares_wide: int, squares_tall: int) -> None:
        self._span = (squares_wide, squares_tall)

    @property
    def grid_span(self) -> tuple[int, int]:
        return self._span

    def render(self, canvas_bgr: np.ndarray) -> None:
        x, y, w, h = rect_for_span_from_origin(self._span[0], self._span[1])
        cv2.rectangle(canvas_bgr, (x, y), (x + w - 1, y + h - 1), (80, 80, 200), 2, lineType=cv2.LINE_AA)
        cv2.putText(
            canvas_bgr,
            f"{self._span[0]}x{self._span[1]}",
            (x + 8, y + 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (200, 200, 200),
            2,
            lineType=cv2.LINE_AA,
        )
