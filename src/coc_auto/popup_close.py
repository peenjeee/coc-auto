"""The game's standard close button, a white X on a red square, found on any screen by its red-and-white pattern, so a
popup the bot has never seen (a new season's offer, an event) can still be closed: closing never buys anything. The
pattern ignores the colours around the button, which change from one popup to the next."""
from __future__ import annotations
from .images import read_image

from .paths import RESOURCE_ROOT

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

TEMPLATE = (RESOURCE_ROOT / 'calibration') / 'building_confirm-close-6fc60c22b47f410db7fcd11cf9fdca7c.png'
# Popups keep their X in the top part of the screen; the buttons there come in a few sizes.
SEARCH, SCALES = (0, 0, 1280, 260), (0.7, 0.8, 0.9, 1.0, 1.1, 1.2)
# Seen: every popup's X 0.64-1.0, the village and the builder list's own small X at most 0.52.
CLOSE_MIN_SCORE = 0.6


def pattern(image: np.ndarray) -> np.ndarray:
    """Red as one half, white as one, everything else none."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    red = ((hsv[..., 0] < 10) | (hsv[..., 0] > 170)) & (hsv[..., 1] > 120) & (hsv[..., 2] > 120)
    white = (hsv[..., 2] > 215) & (hsv[..., 1] < 50)
    return (red * 0.5 + white * 1.0).astype(np.float32)


@lru_cache(maxsize=1)
def template_pattern() -> np.ndarray:
    image = read_image(str(TEMPLATE))
    if image is None:
        raise FileNotFoundError(f'{TEMPLATE}: template tombol tutup hilang')
    return pattern(image)


def close_button(image: np.ndarray) -> tuple[tuple[int, int], float] | None:
    """(the button's centre, its score) when a close button shows near the top of the screen, else None."""
    x0, y0, w, h = SEARCH
    area = pattern(image[y0:y0 + h, x0:x0 + w])
    best = None
    for scale in SCALES:
        template = cv2.resize(template_pattern(), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if template.shape[0] > area.shape[0] or template.shape[1] > area.shape[1]:
            continue
        _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED))
        if best is None or score > best[1]:
            best = ((x0 + x + template.shape[1] // 2, y0 + y + template.shape[0] // 2), float(score))
    return best if best is not None and best[1] >= CLOSE_MIN_SCORE else None
