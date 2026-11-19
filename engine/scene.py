"""Эвристический определитель сцен (вместо ML).

Признаки считаются на готовом нормализованном кадре 640×360 раз в
interval_ms; стоимость < 1 мс. Смена сцены — с гистерезисом hysteresis_s.

Сцены и логика:
  movie  — letterbox-полосы сверху и снизу;
  game   — высокая межкадровая динамика + стабильные яркие блоки в углах (HUD);
  news   — статичная нижняя треть при статичном верхе, умеренная динамика;
  static — глобальная динамика ~0 продолжительное время (пауза/меню);
  default — всё остальное.
"""
from __future__ import annotations

import time

import numpy as np
import structlog

log = structlog.get_logger(__name__)


class SceneDetector:
    def __init__(self, cfg_scene) -> None:
        self.enabled = bool(cfg_scene.enabled)
        self._interval = float(cfg_scene.interval_ms) / 1000.0
        self._hyst = float(cfg_scene.hysteresis_s)
        self.profiles = {k: dict(v) for k, v in cfg_scene.profiles.raw.items()}

        self.current = "default"
        self._candidate = "default"
        self._candidate_since = 0.0
        self._last_run = 0.0
        self._prev_small: np.ndarray | None = None
        self._static_since: float | None = None

    # ------------------------------------------------------------------ #
    def maybe_update(self, frame: np.ndarray) -> str:
        """Вызывается на каждом кадре; сам решает, когда пересчитать."""
        now = time.monotonic()
        if not self.enabled or now - self._last_run < self._interval:
            return self.current
        self._last_run = now

        detected = self._classify(frame, now)
        if detected != self._candidate:
            self._candidate, self._candidate_since = detected, now
        elif detected != self.current and now - self._candidate_since >= self._hyst:
            log.info("scene_changed", scene=detected, prev=self.current)
            self.current = detected
        return self.current

    def profile(self) -> dict:
        return self.profiles.get(self.current, self.profiles["default"])

    # ------------------------------------------------------------------ #
    def _classify(self, frame: np.ndarray, now: float) -> str:
        small = frame[::8, ::8].astype(np.float32).mean(axis=2)  # (45,80) luma

        motion = 0.0
        if self._prev_small is not None:
            motion = float(np.abs(small - self._prev_small).mean())
        self._prev_small = small.copy()

        # static
        if motion < 0.6:
            if self._static_since is None:
                self._static_since = now
            if now - self._static_since > 8.0:
                return "static"
        else:
            self._static_since = None

        # movie: letterbox — верхние и нижние ~10% почти чёрные
        band = max(2, small.shape[0] // 10)
        top_dark = float(small[:band].mean()) < 8.0
        bot_dark = float(small[-band:].mean()) < 8.0
        if top_dark and bot_dark:
            return "movie"

        # news: низ (бегущая строка) статичен и ярок, общая динамика умеренная
        bottom_third = small[-small.shape[0] // 3 :]
        if motion < 3.0 and float(bottom_third.std()) > 20.0 and float(bottom_third.mean()) > 60.0:
            return "news"

        # game: высокая динамика + яркие угловые блоки (HUD)
        c = max(3, small.shape[0] // 8)
        corners = [small[:c, :c], small[:c, -c:], small[-c:, :c], small[-c:, -c:]]
        bright_corners = sum(float(x.mean()) > 50.0 for x in corners)
        if motion > 6.0 and bright_corners >= 2:
            return "game"

        return "default"
