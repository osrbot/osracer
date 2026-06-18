#!/usr/bin/env bash
set -euo pipefail

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
fi

cleanup() {
  "$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/stop_all_demo.sh" || true
}
trap cleanup EXIT INT TERM

echo "Starting SLAM navigation demo"
ros2 launch osracer_bringup bringup.launch.py &
sleep 5
ros2 launch osracer_navigation bringup_launch.py \
  slam:=True \
  planner:=teb \
  use_composition:=False \
  use_rviz:=False &
sleep 5
ros2 launch osracer_navigation rviz_launch.py \
  rviz_config:="$(ros2 pkg prefix osracer_debug)/share/osracer_debug/config/navigation.rviz" &

cat <<'EOF'

边建图边导航已启动。
在 RViz 中先设置 2D Pose Estimate，再使用 Nav2 Goal 给近距离安全目标。

EOF

wait
