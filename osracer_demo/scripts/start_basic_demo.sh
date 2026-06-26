#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib_osracer_demo.sh
source "${SCRIPT_DIR}/lib_osracer_demo.sh"

source_osracer_env
start_robot_base_bg
sleep 3

open_rviz_exclusive \
  "odometry" \
  "ros2 launch osracer_debug debug_odom.launch.py" \
  ros2 launch osracer_debug debug_odom.launch.py

wait_forever_with_stop
