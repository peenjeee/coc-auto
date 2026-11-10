"""OpenCV image I/O that also works with Unicode installation paths on Windows."""

from pathlib import Path

import cv2
import numpy as np


def read_image(filename, flags=cv2.IMREAD_COLOR):
    filename = str(filename)
    if filename.isascii():
        return cv2.imread(filename, flags)
    try:
        data = Path(filename).read_bytes()
        return (
            cv2.imdecode(np.frombuffer(data, dtype=np.uint8), flags) if data else None
        )
    except (OSError, cv2.error):
        return None


def write_image(filename, image, params=None):
    filename = str(filename)
    if filename.isascii():
        return cv2.imwrite(filename, image, params or [])
    encoded, buffer = cv2.imencode(Path(filename).suffix, image, params or [])
    if not encoded:
        return False
    try:
        Path(filename).write_bytes(buffer.tobytes())
        return True
    except OSError:
        return False
