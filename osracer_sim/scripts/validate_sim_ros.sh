#!/usr/bin/env bash
set -euo pipefail

if ! command -v ros2 >/dev/null 2>&1; then
  echo "ros2 is not available; source ROS 2 Humble and the workspace first." >&2
  exit 1
fi

if [[ -z "${AMENT_PREFIX_PATH:-}" ]]; then
  echo "AMENT_PREFIX_PATH is empty; source /opt/ros/humble/setup.bash and install/setup.bash first." >&2
  exit 1
fi

pkg_prefix="$(ros2 pkg prefix osracer_sim)"
share_dir="${pkg_prefix}/share/osracer_sim"

echo "[1/5] Package resources"
test -f "${share_dir}/package.xml"
test -f "${share_dir}/SIM_DEVELOPMENT_PLAN_zh.md"
test -f "${share_dir}/launch/base_sim.launch.py"
test -f "${share_dir}/launch/gazebo.launch.py"
test -f "${share_dir}/launch/slam_sim.launch.py"
test -f "${share_dir}/launch/navigation_sim.launch.py"
test -f "${share_dir}/launch/race_sim.launch.py"
test -f "${share_dir}/worlds/osracer_rect_track.sdf"
test -f "${share_dir}/worlds/osracer_rect_track_obstacle.sdf"
test -f "${share_dir}/models/osracer_simple/model.sdf"
test -x "${share_dir}/scripts/print_sim_scenarios.sh"

echo "[2/5] Core launch arguments"
ros2 launch osracer_sim base_sim.launch.py --show-args >/dev/null
ros2 launch osracer_sim gazebo.launch.py --show-args >/dev/null
ros2 launch osracer_sim slam_sim.launch.py --show-args >/dev/null
ros2 launch osracer_sim navigation_sim.launch.py --show-args >/dev/null

echo "[3/5] Race-stage launch arguments"
for stage in gap_follow track_record pure_pursuit stanley vehicle_id mpc; do
  ros2 launch osracer_sim race_sim.launch.py stage:="${stage}" --show-args >/dev/null
done

echo "[4/5] Scenario launch arguments"
ros2 launch osracer_sim race_sim.launch.py \
  stage:=gap_follow \
  obstacle_preset:=front \
  obstacle_enabled:=true \
  eval_output_csv:=/tmp/osracer_sim_eval_validate.csv \
  --show-args >/dev/null
ros2 launch osracer_sim gazebo.launch.py \
  use_gz_bridge:=true \
  use_gz_control:=true \
  publish_kinematic_clock:=false \
  --show-args >/dev/null
ros2 launch osracer_sim gazebo.launch.py \
  world:="${share_dir}/worlds/osracer_rect_track_obstacle.sdf" \
  --show-args >/dev/null
"${share_dir}/scripts/print_sim_scenarios.sh" | grep -q 'stage:=mpc'
"${share_dir}/scripts/print_sim_scenarios.sh" | grep -q 'osracer_rect_track_obstacle.sdf'

echo "[5/5] Optional live topic visibility"
for topic in /scan /odometry/filtered /ackermann_cmd /clock; do
  if ros2 topic list | grep -qx "${topic}"; then
    echo "found ${topic}"
  else
    echo "missing ${topic} (ok if simulation is not running)"
  fi
done

echo "osracer_sim ROS validation checks passed"
