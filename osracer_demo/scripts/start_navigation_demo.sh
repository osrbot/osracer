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

echo "Starting OSRacer bringup and Nav2"
ros2 launch osracer_bringup bringup.launch.py &
sleep 5
ros2 launch osracer_navigation nav2.launch.py use_rviz:=true &

cat <<'EOF'

Navigation demo started.
In RViz:
1. Use "2D Pose Estimate" to set the current pose.
2. Use "Nav2 Goal" to choose a nearby safe goal.
3. Keep targets short until direction and obstacle behavior are confirmed.

EOF

wait
