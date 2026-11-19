"""Разбиение периметра кадра на зоны и усреднение цвета.

360 зон = 1 LED (прямой маппинг). Полосы выборки лежат по периметру
нормализованного кадра с отступом inset_frac от кромки и глубиной depth_frac.
Усреднение — векторизованное, через reshape (без циклов по зонам).

Порядок зон в выходном массиве соответствует физическому порядку LED ленты
(config strip.order); углы принадлежат горизонтальным сторонам.
"""
from __future__ import annotations

import numpy as np


class ZoneAnalyzer:
    """Предвычисляет геометрию полос; extract() отдаёт (N,3) float32."""

    def __init__(self, cfg) -> None:
        w, h = int(cfg.frame.width), int(cfg.frame.height)
        self._w, self._h = w, h
        z = cfg.zones
        self._nt, self._nb = int(z.top), int(z.bottom)
        self._nl, self._nr = int(z.left), int(z.right)
        self.count = self._nt + self._nb + self._nl + self._nr

        inset_x = int(w * float(z.inset_frac))
        inset_y = int(h * float(z.inset_frac))
        depth_x = max(4, int(w * float(z.depth_frac)))
        depth_y = max(4, int(h * float(z.depth_frac)))

        # Полосы: (y0, y1, x0, x1). Горизонтальные — на всю ширину между
        # inset'ами; вертикальные — между горизонтальными полосами.
        self._band_top = (inset_y, inset_y + depth_y, inset_x, w - inset_x)
        self._band_bot = (h - inset_y - depth_y, h - inset_y, inset_x, w - inset_x)
        self._band_lft = (inset_y + depth_y, h - inset_y - depth_y, inset_x, inset_x + depth_x)
        self._band_rgt = (inset_y + depth_y, h - inset_y - depth_y, w - inset_x - depth_x, w - inset_x)

        self._prep()
        self._order = list(cfg.strip.order)
        if self.count != int(cfg.strip.count):
            raise ValueError(
                f"зон {self.count} != LED {int(cfg.strip.count)}: поправьте config"
            )

    # ------------------------------------------------------------------ #
    def _prep(self) -> None:
        """Обрезка полос до кратности числу зон (для reshape)."""
        def crop(a: int, b: int, n: int) -> tuple[int, int]:
            span = (b - a) // n * n
            pad = (b - a - span) // 2
            return a + pad, a + pad + span

        y0, y1, x0, x1 = self._band_top
        x0, x1 = crop(x0, x1, self._nt)
        self._band_top = (y0, y1, x0, x1)

        y0, y1, x0, x1 = self._band_bot
        x0, x1 = crop(x0, x1, self._nb)
        self._band_bot = (y0, y1, x0, x1)

        y0, y1, x0, x1 = self._band_lft
        y0, y1 = crop(y0, y1, self._nl)
        self._band_lft = (y0, y1, x0, x1)

        y0, y1, x0, x1 = self._band_rgt
        y0, y1 = crop(y0, y1, self._nr)
        self._band_rgt = (y0, y1, x0, x1)

    # ------------------------------------------------------------------ #
    def extract(self, frame: np.ndarray) -> np.ndarray:
        """(H,W,3) uint8 → (N,3) float32 в порядке физической ленты."""
        sides = {
            "top": self._mean_h(frame, self._band_top, self._nt),
            "bottom": self._mean_h(frame, self._band_bot, self._nb),
            "left": self._mean_v(frame, self._band_lft, self._nl),
            "right": self._mean_v(frame, self._band_rgt, self._nr),
        }
        out = []
        for seg in self._order:
            side, direction = seg.rsplit("_", 1)
            arr = sides[side]
            # Естественный порядок: top/bottom — слева направо, left/right —
            # сверху вниз. Директивы: up/down/left/right — направление обхода.
            if side in ("top", "bottom") and direction == "left":
                arr = arr[::-1]
            if side in ("left", "right") and direction == "up":
                arr = arr[::-1]
            out.append(arr)
        return np.concatenate(out, axis=0)

    @staticmethod
    def _mean_h(frame: np.ndarray, band: tuple, n: int) -> np.ndarray:
        y0, y1, x0, x1 = band
        block = frame[y0:y1, x0:x1].astype(np.float32)
        h, w, _ = block.shape
        return block.reshape(h, n, w // n, 3).mean(axis=(0, 2))

    @staticmethod
    def _mean_v(frame: np.ndarray, band: tuple, n: int) -> np.ndarray:
        y0, y1, x0, x1 = band
        block = frame[y0:y1, x0:x1].astype(np.float32)
        h, w, _ = block.shape
        return block.reshape(n, h // n, w, 3).mean(axis=(1, 2))
