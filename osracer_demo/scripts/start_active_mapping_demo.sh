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

echo "Starting active mapping demo"
ros2 launch osracer_bringup bringup.launch.py &
sleep 5

if ros2 pkg prefix cartographer_ros >/dev/null 2>&1; then
  ros2 launch osracer_slam cartographer.launch.py &
elif ros2 pkg prefix slam_toolbox >/dev/null 2>&1; then
  ros2 launch osracer_slam slam_toolbox.launch.py &
else
  ros2 launch osracer_slam gmapping.launch.py &
fi

sleep 3
ros2 launch osracer_debug debug_mapping.launch.py &

cat <<'EOF'

边走边建图已启动。
建议先用 GUI 的低速动作或遥控器慢速移动，小范围确认 /scan、/tf、/map 正常。

EOF

wait
