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
if command -v ros2 >/dev/null 2>&1; then
  echo "OK ros2 CLI: $(command -v ros2)"
  echo "ROS_DISTRO: ${ROS_DISTRO:-unknown}"
else
  echo "MISSING ros2 CLI"
fi

echo
echo "Packages:"
required_packages=(
  ackermann_msgs
  geometry_msgs
  nav_msgs
  osracer_bringup
  osracer_debug
  osracer_description
  osracer_navigation
  osracer_slam
  robot_localization
)
for pkg in "${required_packages[@]}"; do
  ros2 pkg prefix "${pkg}" >/dev/null 2>&1 && echo "OK ${pkg}" || echo "MISSING ${pkg}"
done
if ros2 pkg prefix slam_toolbox >/dev/null 2>&1; then
  echo "OK slam_toolbox"
else
  echo "MISSING slam_toolbox (only required for SLAM + Navigation)"
fi
if ros2 pkg prefix cartographer_ros >/dev/null 2>&1; then
  echo "OK cartographer_ros"
else
  echo "MISSING cartographer_ros (optional; active mapping falls back to GMapping)"
fi

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
