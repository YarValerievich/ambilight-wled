"""Фотометрическая калибровка.

Камера под потолком видит верх экрана под меньшим углом, низ — под большим;
VA/QLED-панели дают сдвиг цвета и яркости вне оси. Коррекция — по-зонные
RGB-коэффициенты усиления и чёрное смещение:

    corrected = clip((raw - black) * gain)

Процедура (через веб-мастер): на ТВ последовательно показываются слайды
white100 / white50 / white20 / red / green / blue / black; по каждому
снимаются средние значения зон.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import structlog

log = structlog.get_logger(__name__)

SLIDES = ("white100", "white50", "white20", "red", "green", "blue", "black")


class PhotometricCalibration:
    """Хранение сэмплов слайдов, расчёт и применение коррекции."""

    def __init__(self, n_zones: int, calib_dir: Path) -> None:
        self._n = n_zones
        self._dir = calib_dir
        self._samples: dict[str, np.ndarray] = {}
        self._gain: Optional[np.ndarray] = None    # (N, 3) float32
        self._black: Optional[np.ndarray] = None   # (N, 3) float32
        self._load()

    @property
    def ready(self) -> bool:
        return self._gain is not None

    # ------------------------------------------------------------------ #
    def record_slide(self, name: str, zone_means: np.ndarray) -> None:
        """Сохранить измерение зон для слайда `name` ((N,3) float)."""
        if name not in SLIDES:
            raise ValueError(f"неизвестный слайд: {name}")
        self._samples[name] = zone_means.astype(np.float32).copy()
        log.info("photometric_slide_recorded", slide=name)

    def compute(self) -> dict:
        """Расчёт gain/black из накопленных слайдов.

        Целевое состояние: на белом слайде все зоны дают одинаковый
        (медианный) уровень по каждому каналу.
        """
        missing = [s for s in ("white100", "black") if s not in self._samples]
        if missing:
            raise RuntimeError(f"нет слайдов: {missing}")

        black = self._samples["black"]                      # (N,3)
        white = self._samples["white100"] - black
        white = np.clip(white, 1.0, None)

        target = np.median(white, axis=0, keepdims=True)    # (1,3)
        gain = target / white                               # (N,3)
        gain = np.clip(gain, 0.3, 4.0)                      # защита от бликовых зон

        self._gain = gain.astype(np.float32)
        self._black = black.astype(np.float32)
        np.savez(self._dir / "photometric.npz", gain=self._gain, black=self._black)

        spread = float(gain.max() / gain.min())
        log.info("photometric_computed", gain_spread=round(spread, 2))
        return {"gain_spread": spread, "zones": self._n}

    # ------------------------------------------------------------------ #
    def apply(self, zone_means: np.ndarray) -> np.ndarray:
        """Коррекция (N,3) float32 in-place-безопасно."""
        if self._gain is None:
            return zone_means
        return np.clip((zone_means - self._black) * self._gain, 0.0, 255.0)

    def _load(self) -> None:
        path = self._dir / "photometric.npz"
        if path.exists():
            data = np.load(path)
            if data["gain"].shape[0] == self._n:
                self._gain = data["gain"].astype(np.float32)
                self._black = data["black"].astype(np.float32)
                log.info("photometric_loaded")
            else:
                log.warning("photometric_zone_mismatch", file=int(data["gain"].shape[0]), cfg=self._n)
