"""Временное и пространственное сглаживание.

Временное: EMA с привязкой ко времени — alpha = 1 - exp(-dt / tau).
Результат не зависит от текущего FPS. tau адаптируется по движению
(меньше при динамике, больше в статике, с гистерезисом), при смене сцены
EMA сбрасывается мгновенно (без «шлейфа»).

Пространственное: круговая гауссова свёртка вдоль ленты (периметр — цикл).
"""
from __future__ import annotations

import math

import numpy as np


class TemporalSmoother:
    """Time-based EMA с адаптивной постоянной времени."""

    def __init__(self, cfg_t, n_zones: int) -> None:
        self._tau_base = float(cfg_t.tau_ms) / 1000.0
        self._tau_fast = float(cfg_t.tau_fast_ms) / 1000.0
        self._tau_slow = float(cfg_t.tau_slow_ms) / 1000.0
        self._thr_motion = float(cfg_t.motion_threshold)
        self._thr_static = float(cfg_t.static_threshold)
        self._thr_cut = float(cfg_t.scene_cut_threshold)
        self._hyst_s = float(cfg_t.hysteresis_ms) / 1000.0

        self._state: np.ndarray | None = None
        self._tau = self._tau_base
        self._streak_s = 0.0
        self._mode = "base"
        self.tau_scale = 1.0  # управляется профилем сцены
        self.last_motion = 0.0

    def reset(self) -> None:
        self._state = None

    def step(self, zones: np.ndarray, dt: float) -> np.ndarray:
        """Один шаг сглаживания. zones: (N,3) float32, dt: сек."""
        if self._state is None or self._state.shape != zones.shape:
            self._state = zones.copy()
            return self._state

        diff = float(np.abs(zones - self._state).mean())
        self.last_motion = diff

        if diff > self._thr_cut:          # смена сцены — без шлейфа
            self._state[:] = zones
            self._streak_s = 0.0
            return self._state

        self._adapt(diff, dt)
        tau = max(1e-3, self._tau * self.tau_scale)
        alpha = 1.0 - math.exp(-max(dt, 1e-4) / tau)
        self._state += alpha * (zones - self._state)
        return self._state

    def _adapt(self, diff: float, dt: float) -> None:
        """Гистерезис по времени (не по кадрам) — иначе поведение зависит от FPS."""
        want = (
            "fast" if diff > self._thr_motion
            else "slow" if diff < self._thr_static
            else "base"
        )
        if want == self._mode:
            self._streak_s = 0.0
            return
        self._streak_s += dt
        if self._streak_s >= self._hyst_s:
            self._mode, self._streak_s = want, 0.0
            self._tau = {
                "fast": self._tau_fast, "slow": self._tau_slow, "base": self._tau_base
            }[want]


class SpatialSmoother:
    """Круговая 1D-гауссова свёртка вдоль ленты (лента — замкнутый периметр)."""

    def __init__(self, cfg_s, n_zones: int) -> None:
        self._enabled = str(cfg_s.kind) == "gaussian"
        if not self._enabled:
            self._kernel = None
            return
        sigma = float(cfg_s.sigma_leds)
        radius = max(1, int(3 * sigma))
        x = np.arange(-radius, radius + 1, dtype=np.float32)
        k = np.exp(-(x**2) / (2 * sigma**2))
        self._kernel = (k / k.sum()).astype(np.float32)
        self._radius = radius

    def apply(self, zones: np.ndarray) -> np.ndarray:
        if not self._enabled:
            return zones
        r = self._radius
        padded = np.concatenate([zones[-r:], zones, zones[:r]], axis=0)
        out = np.empty_like(zones)
        for c in range(3):  # 3 свёртки по ~370 точек — микросекунды
            out[:, c] = np.convolve(padded[:, c], self._kernel, mode="valid")
        return out
