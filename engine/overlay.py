"""Приоритетный слой уведомлений поверх амбилайта.

Уведомление рендерится в zone-space прямо в конвейере (единый Adalight-поток,
без гонок с WLED). Приоритет: notification > ambilight. По истечении
duration — авто-возврат к обычному режиму.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
import structlog

log = structlog.get_logger(__name__)


@dataclass
class Notification:
    kind: str                     # pulse | flash | gradient_sweep | solid_blink
    color: tuple[int, int, int]
    duration: float = 3.0
    started: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.started >= self.duration


class NotificationOverlay:
    def __init__(self, n_zones: int) -> None:
        self._n = n_zones
        self._active: Notification | None = None
        self._idx = np.arange(n_zones, dtype=np.float32)

    @property
    def active(self) -> bool:
        return self._active is not None

    def trigger(self, kind: str, color: tuple[int, int, int], duration: float) -> None:
        if kind not in ("pulse", "flash", "gradient_sweep", "solid_blink"):
            raise ValueError(f"неизвестный тип уведомления: {kind}")
        self._active = Notification(kind, color, duration)
        log.info("notification_started", kind=kind, color=color, duration=duration)

    # ------------------------------------------------------------------ #
    def render(self, base: np.ndarray) -> np.ndarray:
        """base: (N,3) float32 текущий амбилайт → кадр с уведомлением."""
        n = self._active
        if n is None:
            return base
        if n.expired:
            log.info("notification_finished", kind=n.kind)
            self._active = None
            return base

        t = time.monotonic() - n.started
        col = np.asarray(n.color, dtype=np.float32)

        if n.kind == "pulse":
            level = 0.5 - 0.5 * math.cos(2 * math.pi * t / 1.2)  # 1.2 c период
            return base * (1 - level) + col * level

        if n.kind == "flash":
            on = (t % 0.4) < 0.15
            return np.tile(col, (self._n, 1)) if on else base * 0.2

        if n.kind == "solid_blink":
            on = (t % 1.0) < 0.5
            return np.tile(col, (self._n, 1)) if on else base

        # gradient_sweep: бегущая по периметру волна шириной 20% ленты
        pos = (t / 2.0) % 1.0 * self._n
        dist = np.minimum(np.abs(self._idx - pos), self._n - np.abs(self._idx - pos))
        wave = np.clip(1.0 - dist / (self._n * 0.10), 0.0, 1.0)[:, None]
        return base * (1 - wave) + col * wave
