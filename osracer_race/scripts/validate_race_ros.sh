#!/usr/bin/env bash
set -euo pipefail

RACE_CONFIG="${RACE_CONFIG:-}"
RACELINE_FILE="${RACELINE_FILE:-}"
tmp_profile=""
tmp_eval=""

cleanup() {
  [[ -n "${tmp_profile}" ]] && rm -f "${tmp_profile}"
  [[ -n "${tmp_eval}" ]] && rm -f "${tmp_eval}"
}
trap cleanup EXIT

if ! command -v ros2 >/dev/null 2>&1; then
  echo "ros2 is not available; source ROS 2 Humble and the workspace first." >&2
  exit 1
fi

if [[ -z "${AMENT_PREFIX_PATH:-}" ]]; then
  echo "AMENT_PREFIX_PATH is empty; source /opt/ros/humble/setup.bash and install/setup.bash first." >&2
  exit 1
fi

pkg_prefix="$(ros2 pkg prefix osracer_race)"
share_dir="${pkg_prefix}/share/osracer_race"

if [[ -z "${RACE_CONFIG}" ]]; then
  RACE_CONFIG="${share_dir}/config/race_safe.yaml"
fi
if [[ -z "${RACELINE_FILE}" ]]; then
  RACELINE_FILE="${share_dir}/config/tracks/example_raceline.csv"
fi

echo "[1/6] Package resources"
test -f "${share_dir}/package.xml"
test -f "${share_dir}/config/vehicle.yaml"
test -f "${RACE_CONFIG}"
test -f "${RACELINE_FILE}"

echo "[2/6] CLI tools"
ros2 run osracer_race raceline_tools --help >/dev/null
ros2 run osracer_race race_report_tools --help >/dev/null

echo "[3/6] Launch arguments"
ros2 launch osracer_race gap_follow.launch.py --show-args >/dev/null
ros2 launch osracer_race pure_pursuit.launch.py --show-args >/dev/null
ros2 launch osracer_race stanley.launch.py --show-args >/dev/null
ros2 launch osracer_race mpc.launch.py --show-args >/dev/null
ros2 launch osracer_race track_record.launch.py --show-args >/dev/null
ros2 launch osracer_race vehicle_id.launch.py --show-args >/dev/null
ros2 launch osracer_race race_bringup.launch.py --show-args >/dev/null

echo "[4/6] Offline helper self-check"
bash "${share_dir}/scripts/check_race_package.sh"

echo "[5/6] Raceline/report smoke tests"
tmp_profile="$(mktemp /tmp/osracer_race_profile.XXXXXX)"
tmp_eval="$(mktemp /tmp/osracer_race_eval.XXXXXX)"
ros2 run osracer_race raceline_tools \
  "${RACELINE_FILE}" \
  "${tmp_profile}" \
  --max-speed 2.0 \
  --min-speed 0.8 \
  --max-lateral-accel 3.0 >/dev/null
cat >"${tmp_eval}" <<'CSV'
time_s,x,y,yaw,speed_mps,command_speed_mps,command_steering_rad,track_error_m,heading_error_rad
0.0,0,0,0,0.0,0.0,0.0,,
CSV
ros2 run osracer_race race_report_tools "${tmp_eval}" >/dev/null

echo "[6/6] Optional live topic visibility"
for topic in /scan /odometry/filtered /ackermann_cmd /race/safety_stop; do
  if ros2 topic list | grep -qx "${topic}"; then
    echo "found ${topic}"
  else
    echo "missing ${topic} (ok if bringup is not running)"
  fi
done

echo "osracer_race ROS validation checks passed"
