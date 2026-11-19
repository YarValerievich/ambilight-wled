"""Веб-API: health-check, живое превью, мастер калибровки.

Калибровочные паттерны раздаются как PNG — их открывают на ТВ через
браузер ТВ / casting по адресу http://<pi>:8000/pattern/<name>.png.
"""
from __future__ import annotations

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response

from engine.calib import patterns
from engine.calib.geometry import MARKER_LAYOUT
from engine.calib.photometric import SLIDES


def build_app(engine) -> FastAPI:  # engine: AmbilightEngine (main.py)
    app = FastAPI(title="Ambilight Pro", version="2.0")

    # ---------------------------- health ----------------------------- #
    @app.get("/healthz")
    def healthz() -> JSONResponse:
        ok = engine.source.healthy
        return JSONResponse(engine.status(), status_code=200 if ok else 503)

    # ---------------------------- превью ----------------------------- #
    @app.get("/preview.jpg")
    def preview() -> Response:
        frame = engine.last_warped
        if frame is None:
            raise HTTPException(503, "нет кадра")
        ok, buf = cv2.imencode(".jpg", frame[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 80])
        return Response(buf.tobytes(), media_type="image/jpeg")

    @app.get("/raw.jpg")
    def raw() -> Response:
        got = engine.source.latest()
        if got is None:
            raise HTTPException(503, "нет кадра")
        ok, buf = cv2.imencode(".jpg", got[0][..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 80])
        return Response(buf.tobytes(), media_type="image/jpeg")

    # ---------------------- калибровочные паттерны ------------------- #
    @app.get("/pattern/{name}.png")
    def pattern(name: str) -> Response:
        if name == "geometry":
            img = patterns.geometry_pattern(MARKER_LAYOUT)
        elif name in SLIDES:
            img = patterns.solid(name)
        else:
            raise HTTPException(404, f"нет паттерна {name}")
        ok, buf = cv2.imencode(".png", img)
        return Response(buf.tobytes(), media_type="image/png")

    # ------------------------ мастер калибровки ---------------------- #
    @app.post("/calibrate/geometry")
    def calibrate_geometry() -> dict:
        """На ТВ открыт /pattern/geometry.png → детекция и построение карт."""
        got = engine.source.latest()
        if got is None:
            raise HTTPException(503, "камера не отдаёт кадры")
        try:
            return engine.geometry.calibrate_from_frame(got[0])
        except RuntimeError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/calibrate/photometric/{slide}")
    def photometric_slide(slide: str) -> dict:
        """На ТВ открыт соответствующий слайд → снимаем средние по зонам."""
        if not engine.geometry.ready:
            raise HTTPException(409, "сначала геометрия")
        got = engine.source.latest()
        if got is None:
            raise HTTPException(503, "камера не отдаёт кадры")
        warped = engine.geometry.warp(got[0])
        means = engine.zones.extract(warped)
        engine.photometric.record_slide(slide, means)
        return {"slide": slide, "mean": means.mean(axis=0).round(1).tolist()}

    @app.post("/calibrate/photometric/compute")
    def photometric_compute() -> dict:
        try:
            return engine.photometric.compute()
        except RuntimeError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/camera/controls")
    def camera_controls(controls: dict) -> dict:
        engine.source.set_controls(controls)
        return {"applied": controls}

    return app
