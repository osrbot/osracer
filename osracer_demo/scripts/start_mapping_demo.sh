#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib_osracer_demo.sh
source "${SCRIPT_DIR}/lib_osracer_demo.sh"

source_osracer_env
require_ros_pkg osracer_debug
require_ros_pkg osracer_slam
start_robot_base_bg
sleep 5

echo "Starting SLAM mapping"
if ros2 pkg prefix slam_toolbox >/dev/null 2>&1; then
  if process_running "ros2 launch osracer_navigation slam_launch.py"; then
    echo "SLAM toolbox launch is already running; skip duplicate start."
  else
    start_demo_bg "slam-toolbox" ros2 launch osracer_navigation slam_launch.py use_sim_time:=false
  fi
else
  if process_running "ros2 launch osracer_slam gmapping.launch.py"; then
    echo "GMapping launch is already running; skip duplicate start."
  else
    start_demo_bg "gmapping" ros2 launch osracer_slam gmapping.launch.py
  fi
fi

sleep 3
open_rviz_exclusive \
  "mapping" \
  "ros2 launch osracer_debug debug_mapping.launch.py" \
  ros2 launch osracer_debug debug_mapping.launch.py

cat <<'EOF'

Mapping demo started.

How to show it:
1. Put the car in an open area.
2. Use the low-speed demos or keyboard/remote control to drive slowly.
3. Watch /map, /scan, /odom, and TF in RViz.
4. Save the map when finished:
   ros2 launch osracer_slam map_save.launch.xml

EOF

wait_forever_with_stop
