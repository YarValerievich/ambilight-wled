# Настройка ESP32 (WLED) для Ambilight Pro

Используется **WLED 16.0.1**.

## 1. Прошивка
1. Открыть https://install.wled.me в Chrome, подключить ESP32 по USB.
2. Выбрать сборку **audioreactive** (для INMP441) или обычную.
3. Настроить WiFi при первом запуске; задать mDNS-имя `wled-tv`
   (Config → WiFi → mDNS address) — оно указано в `config.yaml` → `transport.ddp.host`.
   Рекомендуется статический IP/DHCP-резервация.

## 2. LED (Config → LED Preferences)
- LED count: **360**, тип **WS281x** (или SK6812 для RGBW).
- GPIO данных: **16** (через 74AHCT125 → лента).
- Color order: подобрать по тесту (обычно GRB).
- Max current (ABL): **32000 mA** — второй эшелон защиты БП
  (первый — лимит мощности в engine).
- При фликере на 360 LED: LED Preferences → предпочесть I2S-метод вывода
  (WLED сам выбирает; при проблемах — сборка с `-D USE_I2S_OUTPUT`).

## 3. Приём потока
### Режим DDP (основной, по WiFi)
Ничего настраивать не нужно — WLED принимает DDP на порту 4048 всегда.

### Режим Adalight (альтернативный, по проводу)
- Config → Interfaces → Serial: baud **1500000**.
- Провод Pi GPIO14 (TX) → ESP32 GPIO16 (RX2), общий GND, длина ≤ 30 см.
- На Pi: `dtoverlay=disable-bt` в `/boot/firmware/config.txt`, отключить
  serial console (`raspi-config` → Interface → Serial: console No, port Yes).

## 4. Watchdog / fallback
- Config → Sync Interfaces → Realtime: **Timeout 2500 ms**.
- Создать пресет id=1 «Fallback»: эффект Solid, чёрный, transition 2000 ms
  (или тёплый ночник — по вкусу).
- Config → LED Preferences → включить «Apply preset at boot» = 1,
  Sync → «Use main segment only», при пропадании потока WLED вернётся
  к пресету — это и есть fade-out при падении Pi.

## 5. Аудио (опционально)
- INMP441 → ESP32: VDD→3V3, GND→GND, SD→GPIO32, WS→GPIO15, SCK→GPIO14.
- Config → Usermods → AudioReactive: I2S digital mic, указанные пины.
- Аудио-режим включается из HA: выключить `switch.ambilight`
  и активировать аудио-пресет WLED (см. ha/automations.yaml).

## 6. Home Assistant
- Штатная интеграция WLED обнаружит устройство автоматически —
  лента появится как `light.wled_tv` со всеми эффектами.
