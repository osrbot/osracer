#!/usr/bin/env bash
set -euo pipefail

WS="${1:-${OSRACER_WS:-$HOME/osracer_ws}}"

if [[ ! -d "${WS}" ]]; then
  echo "ERROR: workspace not found: ${WS}"
  echo "Usage: $0 /path/to/osracer_ws"
  echo "Or set: export OSRACER_WS=/path/to/osracer_ws"
  exit 1
fi

if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  set -u
else
  echo "ERROR: /opt/ros/humble/setup.bash not found. Install ROS 2 Humble first."
  exit 1
fi

cd "${WS}"
echo "Building OSRacer workspace: ${WS}"
colcon build --symlink-install

echo
echo "Build finished."
echo "Run this before starting demos:"
echo "  source ${WS}/install/setup.bash"
