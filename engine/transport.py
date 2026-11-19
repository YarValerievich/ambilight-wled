"""Транспорт кадров к WLED.

Основной режим — DDP по UDP/WiFi (ESP32 за ТВ у ленты, Pi под потолком,
провод не нужен). Альтернатива — Adalight по UART 1.5 Мбод (если Pi и ESP32
соединены проводом; провод ≤ 30 см).

Общая политика ошибок: кадры НЕ ретраятся. Потерянный UDP-пакет или сбой
записи — дроп, следующий кадр придёт через 16 мс.
"""
from __future__ import annotations

import socket
import struct
from typing import Protocol

import structlog

log = structlog.get_logger(__name__)


class FrameTransport(Protocol):
    def send(self, rgb: bytes) -> bool: ...
    def close(self) -> None: ...


class DDPTransport:
    """DDP (Distributed Display Protocol), порт 4048 — принимается WLED штатно.

    Заголовок 10 байт: flags=0x41 (v1, push), seq, type=0x01 (RGB 8bit),
    dest=0x01, offset u32 BE, length u16 BE. 360 LED = 1080 байт данных —
    один UDP-пакет, MTU не превышаем.
    """

    def __init__(self, host: str, port: int) -> None:
        self._addr = (host, int(port))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._seq = 0
        self.frames_sent = 0
        self.frames_dropped = 0

    def send(self, rgb: bytes) -> bool:
        self._seq = self._seq % 15 + 1
        header = struct.pack(
            "!BBBBIH", 0x41, self._seq, 0x01, 0x01, 0, len(rgb)
        )
        try:
            self._sock.sendto(header + rgb, self._addr)
            self.frames_sent += 1
            return True
        except (BlockingIOError, OSError):
            self.frames_dropped += 1
            return False

    def close(self) -> None:
        self._sock.close()


class AdalightTransport:
    """Adalight по последовательному порту (WLED: Serial, 1.5 Мбод)."""

    def __init__(self, device: str, baud: int) -> None:
        import serial  # локальный импорт: pyserial нужен только в этом режиме

        self._ser = serial.Serial(device, baudrate=int(baud), write_timeout=0.05)
        self.frames_sent = 0
        self.frames_dropped = 0

    def send(self, rgb: bytes) -> bool:
        n = len(rgb) // 3
        hi, lo = (n - 1) >> 8 & 0xFF, (n - 1) & 0xFF
        header = bytes((0x41, 0x64, 0x61, hi, lo, hi ^ lo ^ 0x55))  # 'Ada'
        try:
            self._ser.write(header + rgb)
            self.frames_sent += 1
            return True
        except Exception:  # noqa: BLE001 — write timeout / порт отвалился
            self.frames_dropped += 1
            return False

    def close(self) -> None:
        self._ser.close()


def make_transport(cfg_t) -> FrameTransport:
    mode = str(cfg_t.mode)
    if mode == "ddp":
        log.info("transport", mode="ddp", host=cfg_t.ddp.host)
        return DDPTransport(cfg_t.ddp.host, cfg_t.ddp.port)
    if mode == "adalight":
        log.info("transport", mode="adalight", device=cfg_t.adalight.device)
        return AdalightTransport(cfg_t.adalight.device, cfg_t.adalight.baud)
    raise ValueError(f"transport.mode: {mode}")
