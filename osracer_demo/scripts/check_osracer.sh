#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-${OSRACER_PORT:-/dev/osrbot_base}}"
BAUD="${OSRACER_BAUD:-460800}"

echo "== OSRacer demo check =="
echo "Serial port: ${PORT}"
echo "Serial baud: ${BAUD}"

if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  set -u
else
  echo "ERROR: /opt/ros/humble/setup.bash not found. Install/source ROS 2 Humble first."
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
  echo "WARN: osracer install/setup.bash not found. Build the workspace before ROS launch demos."
fi

echo
echo "ROS:"
ros2 --version || true

echo
echo "Packages:"
ros2 pkg prefix osracer_bringup >/dev/null 2>&1 && echo "OK osracer_bringup" || echo "MISSING osracer_bringup"
ros2 pkg prefix ackermann_msgs >/dev/null 2>&1 && echo "OK ackermann_msgs" || echo "MISSING ackermann_msgs"
ros2 pkg prefix robot_localization >/dev/null 2>&1 && echo "OK robot_localization" || echo "MISSING robot_localization"

echo
echo "Serial device:"
if [[ -e "${PORT}" ]]; then
  ls -l "${PORT}"
else
  echo "MISSING ${PORT}"
  echo "Available candidates:"
  ls /dev/osrbot_base /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true
fi

echo
echo "Control policy:"
echo "OK ROS demos do not require the RC transmitter to be powered on."
echo "OK RC transmitter remains available for emergency override/takeover at the firmware level."
echo "NOTE RC failsafe indicators while the transmitter is off are not treated as a ROS demo blocker."

echo
echo "Active topics, if ROS is already running:"
ros2 topic list 2>/dev/null || true
