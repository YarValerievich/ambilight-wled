"""Геометрическая калибровка.

Потолочный монтаж: камера видит экран под наклоном (~35–45°), keystone-
трапеция снимается гомографией. Опционально перед гомографией применяется
undistort по intrinsics (K, D); для CM3 Wide дисторсия умеренная, поэтому
intrinsics — опция, а не требование.

Результат — пара remap-карт (map_x, map_y) float32, объединяющих
undistort + перспективное преобразование в один cv2.remap.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import structlog

log = structlog.get_logger(__name__)

# Нормализованные позиции центров 8 ArUco-маркеров на калибровочном экране:
# 4 угла + середины сторон (id -> (nx, ny), доли ширины/высоты экрана).
MARKER_LAYOUT: dict[int, tuple[float, float]] = {
    0: (0.08, 0.10), 1: (0.92, 0.10),   # верхние углы
    2: (0.92, 0.90), 3: (0.08, 0.90),   # нижние углы
    4: (0.50, 0.10), 5: (0.92, 0.50),   # середины сторон
    6: (0.50, 0.90), 7: (0.08, 0.50),
}


class GeometryCalibration:
    """Построение и применение remap-карт «сенсор → прямоугольник экрана»."""

    def __init__(self, cfg, calib_dir: Path) -> None:
        self._out_w = int(cfg.frame.width)
        self._out_h = int(cfg.frame.height)
        self._dict_name = str(cfg.calibration.aruco_dict)
        self._calib_dir = calib_dir
        self._calib_dir.mkdir(parents=True, exist_ok=True)
        self._map_x: Optional[np.ndarray] = None
        self._map_y: Optional[np.ndarray] = None
        self._K: Optional[np.ndarray] = None
        self._D: Optional[np.ndarray] = None
        self._load()

    # ------------------------------------------------------------------ #
    @property
    def ready(self) -> bool:
        return self._map_x is not None

    def warp(self, frame: np.ndarray) -> np.ndarray:
        """Применить remap; кадр сенсора → нормализованный кадр экрана."""
        if self._map_x is None:
            raise RuntimeError("геометрия не откалибрована")
        return cv2.remap(
            frame, self._map_x, self._map_y,
            interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
        )

    # ------------------------------------------------------------------ #
    def calibrate_from_frame(self, frame: np.ndarray) -> dict:
        """Детекция ArUco на кадре с калибровочным паттерном → гомография.

        Returns:
            dict: found_ids, reprojection_error — для отображения в мастере.
        """
        und = frame
        if self._K is not None:
            und = cv2.undistort(frame, self._K, self._D)

        aruco_dict = cv2.aruco.getPredefinedDictionary(
            getattr(cv2.aruco, self._dict_name)
        )
        detector = cv2.aruco.ArucoDetector(aruco_dict, cv2.aruco.DetectorParameters())
        corners, ids, _ = detector.detectMarkers(und)
        if ids is None or len(ids) < 4:
            raise RuntimeError(f"найдено маркеров: {0 if ids is None else len(ids)} (< 4)")

        src_pts, dst_pts, found = [], [], []
        for marker_corners, marker_id in zip(corners, ids.flatten()):
            mid = int(marker_id)
            if mid not in MARKER_LAYOUT:
                continue
            center = marker_corners[0].mean(axis=0)
            nx, ny = MARKER_LAYOUT[mid]
            src_pts.append(center)
            dst_pts.append([nx * self._out_w, ny * self._out_h])
            found.append(mid)

        if len(found) < 4:
            raise RuntimeError(f"известных маркеров: {len(found)} (< 4)")

        src = np.asarray(src_pts, dtype=np.float64)
        dst = np.asarray(dst_pts, dtype=np.float64)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
        if H is None:
            raise RuntimeError("гомография не сошлась")

        proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
        err = float(np.linalg.norm(proj - dst, axis=1).mean())

        self._build_maps(H, frame.shape[1], frame.shape[0])
        self._save(H)
        log.info("geometry_calibrated", markers=found, reproj_err_px=round(err, 2))
        return {"found_ids": found, "reprojection_error": err}

    def set_intrinsics(self, K: np.ndarray, D: np.ndarray) -> None:
        self._K, self._D = K, D
        np.savez(self._calib_dir / "intrinsics.npz", K=K, D=D)

    # ------------------------------------------------------------------ #
    def _build_maps(self, H: np.ndarray, src_w: int, src_h: int) -> None:
        """Обратная гомография для remap: для каждого выходного пикселя —
        координата в исходном кадре (+ дисторсия, если есть intrinsics)."""
        H_inv = np.linalg.inv(H)
        xs, ys = np.meshgrid(
            np.arange(self._out_w, dtype=np.float64),
            np.arange(self._out_h, dtype=np.float64),
        )
        ones = np.ones_like(xs)
        pts = np.stack([xs, ys, ones], axis=-1) @ H_inv.T
        map_x = (pts[..., 0] / pts[..., 2]).astype(np.float32)
        map_y = (pts[..., 1] / pts[..., 2]).astype(np.float32)

        if self._K is not None:
            # Композиция с дисторсией: undistorted -> distorted координаты.
            und_pts = np.stack([map_x, map_y], axis=-1).reshape(-1, 1, 2)
            norm = cv2.undistortPoints(und_pts, self._K, None)  # уже undistorted
            dist = cv2.projectPoints(
                np.concatenate([norm, np.ones((norm.shape[0], 1, 1))], axis=2),
                np.zeros(3), np.zeros(3), self._K, self._D,
            )[0].reshape(self._out_h, self._out_w, 2)
            map_x = dist[..., 0].astype(np.float32)
            map_y = dist[..., 1].astype(np.float32)

        self._map_x, self._map_y = map_x, map_y

    def _save(self, H: np.ndarray) -> None:
        np.savez(
            self._calib_dir / "geometry.npz",
            H=H, map_x=self._map_x, map_y=self._map_y,
        )

    def _load(self) -> None:
        intr = self._calib_dir / "intrinsics.npz"
        if intr.exists():
            data = np.load(intr)
            self._K, self._D = data["K"], data["D"]
        geo = self._calib_dir / "geometry.npz"
        if geo.exists():
            data = np.load(geo)
            self._map_x = data["map_x"].astype(np.float32)
            self._map_y = data["map_y"].astype(np.float32)
            log.info("geometry_loaded")
