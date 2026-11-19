#!/usr/bin/env bash
# Ambilight Pro v2.0 — установка на Raspberry Pi OS Bookworm 64-bit
set -euo pipefail

echo "== Системные зависимости =="
sudo apt-get update
sudo apt-get install -y python3-picamera2 python3-opencv python3-numpy \
    python3-pip python3-yaml

echo "== Python-зависимости =="
sudo pip3 install --break-system-packages \
    aiomqtt structlog fastapi "uvicorn[standard]" pyserial

echo "== Установка в /opt/ambilight-pro =="
sudo mkdir -p /opt/ambilight-pro
sudo cp -r engine config /opt/ambilight-pro/
sudo mkdir -p /opt/ambilight-pro/calib_data
sudo chown -R pi:pi /opt/ambilight-pro

echo "== systemd =="
sudo cp deploy/ambilight.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ambilight.service

echo
echo "Дальше:"
echo "1. Отредактируйте /opt/ambilight-pro/config/config.yaml (MQTT, host WLED)"
echo "2. sudo systemctl start ambilight"
echo "3. http://<pi>:8000/raw.jpg — проверьте, что экран в кадре целиком"
echo "4. Калибровка: docs/CALIBRATION.md"
