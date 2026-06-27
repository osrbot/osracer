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

if ros2 pkg prefix cartographer_ros >/dev/null 2>&1; then
  echo "Starting Cartographer mapping"
  if process_running "ros2 launch osracer_slam cartographer.launch.py"; then
    echo "Cartographer launch is already running; skip duplicate start."
  else
    start_demo_bg "cartographer" ros2 launch osracer_slam cartographer.launch.py use_sim_time:=false
  fi

  sleep 3
  open_rviz_exclusive \
    "cartographer mapping" \
    "ros2 launch osracer_debug debug_cartographer.launch.py" \
    ros2 launch osracer_debug debug_cartographer.launch.py
else
  echo "Cartographer is not installed; falling back to GMapping."
  if process_running "ros2 launch osracer_slam gmapping.launch.py"; then
    echo "GMapping launch is already running; skip duplicate start."
  else
    start_demo_bg "gmapping" ros2 launch osracer_slam gmapping.launch.py
  fi

  sleep 3
  open_rviz_exclusive \
    "gmapping" \
    "ros2 launch osracer_debug debug_mapping.launch.py" \
    ros2 launch osracer_debug debug_mapping.launch.py
fi

cat <<'EOF'

Active mapping demo started.

How to show it:
1. Keep the car in an open area.
2. Use the demo control panel "showcase" / "figure 8", or drive manually at low speed.
3. Watch the map grow in RViz.
4. Stop the car before saving a map.

EOF

wait_forever_with_stop
