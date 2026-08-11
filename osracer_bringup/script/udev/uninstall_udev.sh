#!/usr/bin/env bash
set -euo pipefail

sudo rm -f \
  /etc/udev/rules.d/99-osrbot-led-matrix.rules \
  /etc/udev/rules.d/99-osrbot-usb-cam.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
