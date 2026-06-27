#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib_osracer_demo.sh
source "${SCRIPT_DIR}/lib_osracer_demo.sh"

source_osracer_env
require_ros_pkg osracer_debug
open_rviz_exclusive \
  "odometry" \
  "ros2 launch osracer_debug debug_odom.launch.py" \
  ros2 launch osracer_debug debug_odom.launch.py
