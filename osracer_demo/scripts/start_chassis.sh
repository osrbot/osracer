#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-${OSRACER_PORT:-/dev/osrbot_base}}"
BAUD="${OSRACER_BAUD:-460800}"

if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  set -u
else
  echo "ERROR: /opt/ros/humble/setup.bash not found"
  exit 1
fi

if [[ -n "${OSRACER_WS:-}" && -f "${OSRACER_WS}/install/setup.bash" ]]; then
  set +u
  # shellcheck disable=SC1090
  source "${OSRACER_WS}/install/setup.bash"
  set -u
elif [[ -f "$HOME/osracer_ws/install/setup.bash" ]]; then
  set +u
  # shellcheck disable=SC1090
  source "$HOME/osracer_ws/install/setup.bash"
  set -u
elif [[ -f "$HOME/osracer/install/setup.bash" ]]; then
  set +u
  # shellcheck disable=SC1090
  source "$HOME/osracer/install/setup.bash"
  set -u
elif [[ -f "$HOME/Documents/osracer/osracer/install/setup.bash" ]]; then
  set +u
  # shellcheck disable=SC1090
  source "$HOME/Documents/osracer/osracer/install/setup.bash"
  set -u
else
  echo "ERROR: osracer workspace setup not found."
  echo "Set OSRACER_WS=/path/to/osracer or build/source the workspace first."
  exit 1
fi

echo "Starting chassis: ${PORT}@${BAUD}"
echo "Keep this terminal open. Open another terminal to run demo commands."

exec ros2 launch osracer_bringup chassis_ackermann.launch.py \
  port_name:="${PORT}" \
  baud_rate:="${BAUD}" \
  log_level:=info
