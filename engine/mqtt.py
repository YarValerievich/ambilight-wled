"""Интеграция с Home Assistant по MQTT.

Публикует Discovery-конфиги (switch, select, sensors, binary_sensor),
состояние и метрики; принимает команды и уведомления. LWT обязателен.

Лента как light-entity в HA — зона ответственности самого WLED; этот модуль
описывает только сущности Pi-engine.
"""
from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable

import aiomqtt
import structlog

log = structlog.get_logger(__name__)


class HaMqtt:
    def __init__(self, cfg_m, on_switch: Callable[[bool], None],
                 on_profile: Callable[[str], None],
                 on_notify: Callable[[dict], None]) -> None:
        self._cfg = cfg_m
        self._base = str(cfg_m.base_topic)
        self._disc = str(cfg_m.discovery_prefix)
        self._dev_id = str(cfg_m.device_id)
        self._on_switch = on_switch
        self._on_profile = on_profile
        self._on_notify = on_notify
        self._client: aiomqtt.Client | None = None
        self._avail = f"{self._base}/availability"

    # ------------------------------------------------------------------ #
    async def run(self, stop: asyncio.Event) -> None:
        """Цикл подключения с реконнектом; живёт до stop.set()."""
        while not stop.is_set():
            try:
                async with aiomqtt.Client(
                    hostname=str(self._cfg.host), port=int(self._cfg.port),
                    username=str(self._cfg.username), password=str(self._cfg.password),
                    will=aiomqtt.Will(self._avail, "offline", retain=True),
                ) as client:
                    self._client = client
                    await client.publish(self._avail, "online", retain=True)
                    await self._publish_discovery()
                    await client.subscribe(f"{self._base}/cmd/#")
                    await client.subscribe(f"{self._base}/notify")
                    log.info("mqtt_connected")
                    async for msg in client.messages:
                        self._dispatch(str(msg.topic), msg.payload)
            except aiomqtt.MqttError:
                self._client = None
                log.warning("mqtt_reconnect_in_5s")
                await asyncio.sleep(5)

    def _dispatch(self, topic: str, payload: bytes) -> None:
        try:
            text = payload.decode("utf-8", "replace")
            if topic.endswith("/cmd/switch"):
                self._on_switch(text.upper() == "ON")
            elif topic.endswith("/cmd/profile"):
                self._on_profile(text)
            elif topic.endswith("/notify"):
                self._on_notify(json.loads(text))
        except Exception:  # noqa: BLE001 — плохой payload не роняет engine
            log.error("mqtt_bad_payload", topic=topic, exc_info=True)

    # ------------------------------------------------------------------ #
    async def publish_state(self, state: dict) -> None:
        """Периодическая публикация состояния/метрик (вызывает main)."""
        if self._client is None:
            return
        try:
            await self._client.publish(
                f"{self._base}/state", json.dumps(state), retain=False
            )
        except aiomqtt.MqttError:
            pass  # реконнект сделает run()

    async def _publish_discovery(self) -> None:
        assert self._client is not None
        dev = {
            "identifiers": [self._dev_id],
            "name": str(self._cfg.device_name),
            "manufacturer": "Ambilight Pro",
            "model": "v2.0",
        }
        st = f"{self._base}/state"
        common = {"availability_topic": self._avail, "device": dev}

        entities: dict[str, dict] = {
            f"switch/{self._dev_id}/stream": {
                "name": "Ambilight", "command_topic": f"{self._base}/cmd/switch",
                "state_topic": st, "value_template": "{{ value_json.streaming }}",
                "payload_on": "ON", "payload_off": "OFF", "state_on": True,
                "state_off": False, "unique_id": f"{self._dev_id}_stream", **common,
            },
            f"select/{self._dev_id}/profile": {
                "name": "Ambilight Profile",
                "command_topic": f"{self._base}/cmd/profile", "state_topic": st,
                "value_template": "{{ value_json.profile }}",
                "options": ["auto", "default", "movie", "game", "news", "static"],
                "unique_id": f"{self._dev_id}_profile", **common,
            },
            f"binary_sensor/{self._dev_id}/calibration": {
                "name": "Ambilight Calibration OK", "state_topic": st,
                "value_template": "{{ 'ON' if value_json.calibration_ok else 'OFF' }}",
                "device_class": "problem",
                "unique_id": f"{self._dev_id}_calib", **common,
            },
        }
        sensors = {
            "fps": ("FPS", None, "{{ value_json.fps }}"),
            "latency_ms": ("Latency", "ms", "{{ value_json.latency_ms }}"),
            "cpu_temp": ("CPU Temp", "°C", "{{ value_json.cpu_temp }}"),
            "scene": ("Scene", None, "{{ value_json.scene }}"),
            "power_w": ("LED Power", "W", "{{ value_json.power_w }}"),
            "uptime": ("Uptime", "s", "{{ value_json.uptime }}"),
        }
        for key, (name, unit, tmpl) in sensors.items():
            conf = {
                "name": f"Ambilight {name}", "state_topic": st,
                "value_template": tmpl,
                "unique_id": f"{self._dev_id}_{key}", **common,
            }
            if unit:
                conf["unit_of_measurement"] = unit
            entities[f"sensor/{self._dev_id}/{key}"] = conf

        for suffix, conf in entities.items():
            await self._client.publish(
                f"{self._disc}/{suffix}/config", json.dumps(conf), retain=True
            )
        log.info("mqtt_discovery_published", entities=len(entities))
