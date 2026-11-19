"""Цветовой тракт LED: WB → насыщенность → black threshold → яркость →
лимит мощности → гамма-LUT → uint8.

Все LUT и константы предвычислены; горячий путь — без аллокаций сверх
одного выходного буфера.
"""
from __future__ import annotations

import numpy as np


class ColorPipeline:
    def __init__(self, cfg_c) -> None:
        self._wb = np.asarray(cfg_c.white_balance, dtype=np.float32)
        self._black_thr = float(cfg_c.black_threshold)
        self.brightness = float(cfg_c.brightness)
        self.saturation = 1.0           # управляется профилем сцены
        self.profile_brightness = 1.0
        self.profile_black_thr = self._black_thr

        gamma = float(cfg_c.gamma)
        lut = (np.linspace(0, 1, 256) ** gamma * 255.0 + 0.5).astype(np.uint8)
        self._lut = lut

        p = cfg_c.power
        self._max_w = float(p.max_watts)
        # Вт на единицу канала (0..255) на 1 LED:
        self._w_per_unit = float(p.ma_per_channel) / 1000.0 * float(p.voltage) / 255.0
        self.last_power_w = 0.0

    # ------------------------------------------------------------------ #
    def process(self, zones: np.ndarray) -> np.ndarray:
        """(N,3) float32 [0..255] → (N,3) uint8 готовых значений LED."""
        v = zones * self._wb

        if self.saturation != 1.0:
            luma = v.mean(axis=1, keepdims=True)
            v = luma + (v - luma) * self.saturation

        # Black threshold: плавное гашение тусклых зон (шум камеры).
        luma = v.mean(axis=1, keepdims=True)
        thr = self.profile_black_thr
        fade = np.clip((luma - thr) / max(thr, 1.0), 0.0, 1.0)
        v = v * fade

        v = v * (self.brightness * self.profile_brightness)
        v = np.clip(v, 0.0, 255.0)

        # Лимит мощности: глобальное масштабирование при превышении бюджета.
        power = float(v.sum()) * self._w_per_unit
        self.last_power_w = power
        if power > self._max_w:
            v *= self._max_w / power
            self.last_power_w = self._max_w

        return self._lut[v.astype(np.uint8)]
