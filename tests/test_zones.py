"""Зоны: количество, порядок, корректность усреднения."""
import numpy as np

from engine.config import Cfg
from engine.zones import ZoneAnalyzer

CFG = Cfg({
    "frame": {"width": 640, "height": 360},
    "zones": {"top": 120, "bottom": 120, "left": 60, "right": 60,
              "depth_frac": 0.06, "inset_frac": 0.025},
    "strip": {"count": 360, "order": ["left_up", "top_right", "right_down", "bottom_left"]},
})


def test_count_and_solid_color():
    za = ZoneAnalyzer(CFG)
    frame = np.full((360, 640, 3), 100, dtype=np.uint8)
    out = za.extract(frame)
    assert out.shape == (360, 3)
    assert np.allclose(out, 100.0, atol=0.01)


def test_side_separation():
    za = ZoneAnalyzer(CFG)
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:40, :, 0] = 255          # верх — красный
    out = za.extract(frame)
    top = out[60:180]               # left(60) + top(120)
    assert top[:, 0].mean() > 200
    assert out[:60, 0].mean() < 50  # левая сторона не красная
