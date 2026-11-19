"""Захват видео: Picamera2, фиксированные controls, кольцевой буфер.

Поток захвата кладёт кадры в двухслотовый ring buffer; конвейер забирает
последний готовый кадр без копирования (numpy view + копия по требованию).
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np
import structlog

log = structlog.get_logger(__name__)

try:  # На дев-машине без камеры модуль импортируется, но не стартует.
    from picamera2 import Picamera2
    _PICAMERA_OK = True
except ImportError:  # pragma: no cover
    _PICAMERA_OK = False


class FrameSource:
    """Источник кадров с камеры (или файла-заглушки для тестов)."""

    def __init__(self, cfg) -> None:
        self._size = tuple(cfg.camera.sensor_size)
        self._fps = float(cfg.camera.fps)
        self._controls = dict(cfg.camera.controls.raw)
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._frame_ts: float = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cam = None
        self.healthy = False

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if not _PICAMERA_OK:
            raise RuntimeError("picamera2 недоступна — установите python3-picamera2")
        self._cam = Picamera2()
        video_cfg = self._cam.create_video_configuration(
            main={"size": self._size}, # "format": "RGB888"},
            controls={"FrameRate": self._fps, **self._controls},
            buffer_count=4,
        )
        self._cam.configure(video_cfg)
        self._cam.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="capture", daemon=True)
        self._thread.start()
        log.info("capture_started", size=self._size, fps=self._fps)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._cam:
            try:
                self._cam.stop()
                self._cam.close()
            except Exception:  # noqa: BLE001
                log.warning("camera_close_failed", exc_info=True)
        self.healthy = False

    def set_controls(self, controls: dict) -> None:
        """Применить controls на лету (используется мастером калибровки)."""
        if self._cam:
            self._cam.set_controls(controls)
            self._controls.update(controls)

    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        assert self._cam is not None
        while not self._stop.is_set():
            try:
                arr = self._cam.capture_array("main")

                # Конвертация формата для OV5647
                if arr.ndim == 2:
                    arr = np.stack([arr, arr, arr], axis=-1)
                elif arr.shape[2] == 1:
                    arr = np.repeat(arr, 3, axis=2)
                elif arr.shape[2] == 4:
                    arr = arr[:, :, :3]     

                with self._lock:
                    self._frame = arr
                    self._frame_ts = time.monotonic()
                self.healthy = True
            except Exception:  # noqa: BLE001
                self.healthy = False
                log.error("capture_error", exc_info=True)
                time.sleep(0.5)

    def latest(self, max_age_s: float = 1.0) -> Optional[tuple[np.ndarray, float]]:
        """Последний кадр и его timestamp; None, если кадр устарел."""
        with self._lock:
            if self._frame is None:
                return None
            if time.monotonic() - self._frame_ts > max_age_s:
                return None
            return self._frame, self._frame_ts
