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

SCRIPTS_DIR="$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts"
PARAMS_FILE="$("${SCRIPTS_DIR}/make_slow_nav_params.sh")"

echo "Starting OSRacer bringup and Nav2"
ros2 launch osracer_bringup bringup.launch.py &
sleep 5
ros2 launch osracer_navigation nav2.launch.py \
  use_rviz:=true \
  params_file:="${PARAMS_FILE}" &

cat <<'EOF'

导航演示已启动。
在 RViz 中：
1. 使用 "2D Pose Estimate" 设置当前位置。
2. 使用 "Nav2 Goal" 选择近距离安全目标。
3. 先用短距离目标确认方向和避障行为。

EOF

wait
