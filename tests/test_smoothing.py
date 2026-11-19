"""Инвариантность временного сглаживания к FPS и сброс при смене сцены."""
import numpy as np

from engine.config import Cfg
from engine.smoothing import TemporalSmoother

CFG = Cfg({
    "tau_ms": 80.0, "tau_fast_ms": 30.0, "tau_slow_ms": 200.0,
    "motion_threshold": 12.0, "static_threshold": 2.0,
    "scene_cut_threshold": 60.0, "hysteresis_ms": 100.0,
})


def _run(fps: float, seconds: float) -> float:
    sm = TemporalSmoother(CFG, 4)
    zeros = np.zeros((4, 3), dtype=np.float32)
    target = np.full((4, 3), 50.0, dtype=np.float32)  # < cut threshold
    sm.step(zeros, 1 / fps)
    dt = 1.0 / fps
    for _ in range(int(seconds * fps)):
        out = sm.step(target, dt)
    return float(out.mean())


def test_fps_invariance():
    v30, v60 = _run(30, 0.2), _run(60, 0.2)
    assert abs(v30 - v60) < 2.0, (v30, v60)


def test_scene_cut_resets():
    sm = TemporalSmoother(CFG, 4)
    sm.step(np.zeros((4, 3), np.float32), 0.016)
    out = sm.step(np.full((4, 3), 200.0, np.float32), 0.016)  # > cut
    assert float(out.mean()) == 200.0
