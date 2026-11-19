"""Ambilight Pro v2.0 — точка входа.

Потоки:
  * capture (внутри FrameSource) — камера → ring buffer;
  * pipeline — remap → зоны → фотометрия → сцена → сглаживание →
    уведомления → цвет → транспорт (DDP/Adalight);
  * asyncio (main thread) — MQTT, FastAPI, публикация метрик.

Graceful shutdown по SIGTERM/SIGINT: остановка конвейера, отправка чёрного
кадра (WLED через realtime-timeout уйдёт в fallback-пресет), offline в MQTT.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import threading
import time
from pathlib import Path

import numpy as np
import structlog
import uvicorn

from engine.calib.geometry import GeometryCalibration
from engine.calib.photometric import PhotometricCalibration
from engine.capture import FrameSource
from engine.color import ColorPipeline
from engine.config import load_config
from engine.mqtt import HaMqtt
from engine.overlay import NotificationOverlay
from engine.scene import SceneDetector
from engine.smoothing import SpatialSmoother, TemporalSmoother
from engine.transport import make_transport
from engine.zones import ZoneAnalyzer

log = structlog.get_logger("main")


class AmbilightEngine:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        calib_dir = Path(cfg.calibration.dir)

        self.source = FrameSource(cfg)
        self.geometry = GeometryCalibration(cfg, calib_dir)
        self.zones = ZoneAnalyzer(cfg)
        self.photometric = PhotometricCalibration(self.zones.count, calib_dir)
        self.temporal = TemporalSmoother(cfg.smoothing.temporal, self.zones.count)
        self.spatial = SpatialSmoother(cfg.smoothing.spatial, self.zones.count)
        self.scene = SceneDetector(cfg.scene)
        self.overlay = NotificationOverlay(self.zones.count)
        self.color = ColorPipeline(cfg.color)
        self.transport = make_transport(cfg.transport)

        self.streaming = True
        self.profile_override: str | None = None  # None == auto
        self.last_warped: np.ndarray | None = None
        self.fps = 0.0
        self.latency_ms = 0.0
        self._t_start = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self.source.start()
        self._thread = threading.Thread(target=self._pipeline, name="pipeline", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        # Чёрный кадр — WLED по realtime-timeout уйдёт в fallback-пресет.
        try:
            self.transport.send(bytes(self.zones.count * 3))
        finally:
            self.transport.close()
        self.source.stop()

    # ------------------------------------------------------------------ #
    def _pipeline(self) -> None:
        target_dt = 1.0 / float(self.cfg.camera.fps)
        prev_ts: float | None = None
        fps_acc, fps_t0 = 0, time.monotonic()

        while not self._stop.is_set():
            t_loop = time.monotonic()
            got = self.source.latest()

            #if got is None or not self.geometry.ready or not self.streaming:
            if got is None or not self.streaming:
                # Уведомления работают и при выключенном амбилайте.
                if self.overlay.active:
                    zones = self.overlay.render(
                        np.zeros((self.zones.count, 3), dtype=np.float32)
                    )
                    self.transport.send(self.color.process(zones).tobytes())
                time.sleep(target_dt)
                continue

            frame, ts = got
            if ts == prev_ts:               # новый кадр ещё не пришёл
                time.sleep(0.001)
                continue
            dt = target_dt if prev_ts is None else max(ts - prev_ts, 1e-4)
            prev_ts = ts

            try:
                #warped = self.geometry.warp(frame)
                # Временно без warp, пока не пройдена калибровка
                warped = frame if not self.geometry.ready else self.geometry.warp(frame)
                self.last_warped = warped

                zones = self.zones.extract(warped)
                #zones = self.photometric.apply(zones)
                zones = zones if not self.photometric.ready else self.photometric.apply(zones)

                scene = (
                    self.scene.maybe_update(warped)
                    if self.profile_override in (None, "auto")
                    else self.profile_override
                )
                prof = self.scene.profiles.get(scene, self.scene.profiles["default"])
                self.temporal.tau_scale = float(prof["tau_scale"])
                self.color.saturation = float(prof["saturation"])
                self.color.profile_brightness = float(prof["brightness"])
                self.color.profile_black_thr = float(prof["black_threshold"])

                zones = self.temporal.step(zones, dt)
                zones = self.spatial.apply(zones)
                zones = self.overlay.render(zones)

                rgb = self.color.process(zones)
                self.transport.send(rgb.tobytes())

                self.latency_ms = (time.monotonic() - ts) * 1000.0
                fps_acc += 1
                now = time.monotonic()
                if now - fps_t0 >= 2.0:
                    self.fps = fps_acc / (now - fps_t0)
                    fps_acc, fps_t0 = 0, now
            except Exception:  # noqa: BLE001 — конвейер не падает
                log.error("pipeline_error", exc_info=True)
                time.sleep(0.1)

            # Не молотим быстрее камеры.
            spent = time.monotonic() - t_loop
            if spent < target_dt * 0.5:
                time.sleep(target_dt * 0.5 - spent)

    # ------------------------------------------------------------------ #
    def status(self) -> dict:
        return {
            "streaming": self.streaming,
            "profile": self.profile_override or "auto",
            "scene": self.scene.current,
            "fps": round(self.fps, 1),
            "latency_ms": round(self.latency_ms, 1),
            "power_w": round(self.color.last_power_w, 1),
            "cpu_temp": _cpu_temp(),
            "uptime": int(time.monotonic() - self._t_start),
            "calibration_ok": self.geometry.ready and self.photometric.ready,
            "camera_ok": self.source.healthy,
            "frames_dropped": getattr(self.transport, "frames_dropped", 0),
        }


def _cpu_temp() -> float:
    try:
        raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text()
        return round(int(raw.strip()) / 1000.0, 1)
    except OSError:
        return 0.0


# ---------------------------------------------------------------------- #
async def async_main(engine: AmbilightEngine) -> None:
    from engine.webui.api import build_app

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    def on_switch(on: bool) -> None:
        engine.streaming = on
        engine.temporal.reset()
        log.info("streaming_set", on=on)

    def on_profile(name: str) -> None:
        engine.profile_override = None if name == "auto" else name
        log.info("profile_set", profile=name)

    def on_notify(payload: dict) -> None:
        engine.overlay.trigger(
            str(payload.get("kind", "pulse")),
            tuple(payload.get("color", [255, 120, 0])),
            float(payload.get("duration", 3.0)),
        )

    mqtt = HaMqtt(engine.cfg.mqtt, on_switch, on_profile, on_notify)

    async def metrics_loop() -> None:
        while not stop.is_set():
            await mqtt.publish_state(engine.status())
            await asyncio.sleep(5)

    server = uvicorn.Server(uvicorn.Config(
        build_app(engine), host=str(engine.cfg.web.host),
        port=int(engine.cfg.web.port), log_level="warning",
    ))

    tasks = [
        asyncio.create_task(mqtt.run(stop)),
        asyncio.create_task(metrics_loop()),
        asyncio.create_task(server.serve()),
    ]
    await stop.wait()
    server.should_exit = True
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def main() -> int:
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else "config/config.yaml")
    logging.basicConfig(level=getattr(logging, str(cfg.logging.level)))
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, str(cfg.logging.level))
        )
    )

    engine = AmbilightEngine(cfg)
    engine.start()
    log.info("engine_started", zones=engine.zones.count,
             calibrated=engine.geometry.ready)
    try:
        asyncio.run(async_main(engine))
    finally:
        engine.stop()
        log.info("engine_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
