#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib_osracer_demo.sh
source "${SCRIPT_DIR}/lib_osracer_demo.sh"

source_osracer_env
start_robot_base_bg
sleep 5

if ! ros2 pkg prefix slam_toolbox >/dev/null 2>&1; then
  echo "ERROR: Nav2 online-SLAM navigation needs slam_toolbox."
  echo "Cartographer/GMapping are useful for mapping display, but this osracer Nav2 launch connects online navigation through slam_toolbox."
  exit 1
fi

echo "Starting Nav2 online SLAM navigation"
PARAMS_FILE="$("${SCRIPT_DIR}/make_slow_nav_params.sh")"
echo "Using low-speed Nav2 params: ${PARAMS_FILE}"
if process_running "ros2 launch osracer_navigation bringup_launch.py"; then
  echo "Nav2 bringup is already running; skip duplicate start."
else
  start_demo_bg "nav2-slam" ros2 launch osracer_navigation bringup_launch.py \
    slam:=True \
    planner:=teb \
    params_file:="${PARAMS_FILE}" \
    use_composition:=False \
    use_rviz:=False
fi

sleep 6
open_rviz_exclusive \
  "online SLAM navigation" \
  "ros2 launch osracer_navigation rviz_launch.py" \
  ros2 launch osracer_navigation rviz_launch.py \
    rviz_config:="$(ros2 pkg prefix osracer_debug)/share/osracer_debug/config/navigation.rviz"

cat <<'EOF'

Online SLAM navigation demo started.

How to show it:
1. Let RViz show /map, /scan, TF and Nav2 status.
2. Click "Nav2 Goal" and choose a nearby safe target.
3. The car can navigate while SLAM updates the map. Low-speed params limit max_vel_x to 0.60m/s.
4. Keep a hand near STOP or the RC emergency takeover.

EOF

wait_forever_with_stop
