"""Legacy ``pigeonStartup.mp4`` hook — disabled (splash → viewOne directly)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np


def current_startup_bgra_frame(
    assets_root: Path,
    elapsed_s: float,
    *,
    loop: bool = True,
    max_seconds_cap: float = 10.0,
) -> Optional[np.ndarray]:
    """Always ``None`` — post-splash ``pigeonStartup.mp4`` transition removed."""
    return None
