#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib_osracer_demo.sh
source "${SCRIPT_DIR}/lib_osracer_demo.sh"

source_osracer_env
start_robot_base_bg
sleep 5

MAP_FILE="${OSRACER_MAP:-}"
if [[ -z "${MAP_FILE}" ]]; then
  NAV_PREFIX="$(ros2 pkg prefix osracer_navigation)"
  MAP_FILE="${NAV_PREFIX}/share/osracer_navigation/maps/map.yaml"
fi

echo "Starting Nav2 with map: ${MAP_FILE}"
PARAMS_FILE="$("${SCRIPT_DIR}/make_slow_nav_params.sh")"
echo "Using low-speed Nav2 params: ${PARAMS_FILE}"
if process_running "ros2 launch osracer_navigation bringup_launch.py"; then
  echo "Nav2 bringup is already running; skip duplicate start."
else
  start_demo_bg "nav2" ros2 launch osracer_navigation bringup_launch.py \
    slam:=False \
    map:="${MAP_FILE}" \
    planner:=teb \
    params_file:="${PARAMS_FILE}" \
    use_composition:=False \
    use_rviz:=False
fi

sleep 5
open_rviz_exclusive \
  "navigation" \
  "ros2 launch osracer_navigation rviz_launch.py" \
  ros2 launch osracer_navigation rviz_launch.py \
    rviz_config:="$(ros2 pkg prefix osracer_debug)/share/osracer_debug/config/navigation.rviz"

cat <<'EOF'

Navigation demo started.

How to show it:
1. In RViz, click "2D Pose Estimate" and set the current car pose.
2. Click "Nav2 Goal" and choose a nearby safe target.
3. This demo uses low-speed Nav2 params: max_vel_x=0.60m/s.
4. Press Ctrl-C in this terminal to stop the demo and publish zero speed.

EOF

wait_forever_with_stop
