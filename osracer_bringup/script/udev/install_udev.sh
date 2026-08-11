#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo usermod -aG video "${USER}"
sudo install -m 0644 "${SCRIPT_DIR}/99-osrbot-led-matrix.rules" /etc/udev/rules.d/99-osrbot-led-matrix.rules
sudo install -m 0644 "${SCRIPT_DIR}/99-osrbot-usb-cam.rules" /etc/udev/rules.d/99-osrbot-usb-cam.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
