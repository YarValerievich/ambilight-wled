"""Генерация калибровочных изображений (открываются на ТВ через браузер).

Раздаются FastAPI по адресам /pattern/geometry.png, /pattern/white100.png и т.д.
"""
from __future__ import annotations

import cv2
import numpy as np

_SOLIDS: dict[str, tuple[int, int, int]] = {
    "white100": (255, 255, 255),
    "white50": (128, 128, 128),
    "white20": (51, 51, 51),
    "red": (0, 0, 255),      # BGR
    "green": (0, 255, 0),
    "blue": (255, 0, 0),
    "black": (0, 0, 0),
}


def solid(name: str, w: int = 1920, h: int = 1080) -> np.ndarray:
    """Сплошной цветной слайд для фотометрии (BGR)."""
    if name not in _SOLIDS:
        raise KeyError(name)
    img = np.empty((h, w, 3), dtype=np.uint8)
    img[:] = _SOLIDS[name]
    return img


def geometry_pattern(
    marker_layout: dict[int, tuple[float, float]],
    aruco_dict_name: str = "DICT_4X4_50",
    w: int = 1920,
    h: int = 1080,
    marker_px: int = 160,
) -> np.ndarray:
    """Белое поле + чёрная рамка + ArUco-маркеры по MARKER_LAYOUT (BGR)."""
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    cv2.rectangle(img, (0, 0), (w - 1, h - 1), (0, 0, 0), thickness=12)

    aruco_dict = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, aruco_dict_name))
    for marker_id, (nx, ny) in marker_layout.items():
        marker = cv2.aruco.generateImageMarker(aruco_dict, marker_id, marker_px)
        cx, cy = int(nx * w), int(ny * h)
        x0, y0 = cx - marker_px // 2, cy - marker_px // 2
        img[y0 : y0 + marker_px, x0 : x0 + marker_px] = marker[..., None]
    return img
