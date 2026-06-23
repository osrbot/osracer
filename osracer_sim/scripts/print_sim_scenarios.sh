#!/usr/bin/env bash
set -euo pipefail

cat <<'EOF'
OSRacer simulation scenario matrix

[0] Base kinematic smoke test
ros2 launch osracer_sim base_sim.launch.py use_rviz:=true

[1] Gap Follow with deterministic front obstacle
ros2 launch osracer_sim race_sim.launch.py \
  stage:=gap_follow \
  obstacle_preset:=front \
  eval_output_csv:=/tmp/osracer_sim_eval_gap_follow.csv

[2] Raceline recording on the rectangular track
ros2 launch osracer_sim race_sim.launch.py \
  stage:=track_record \
  eval_output_csv:=/tmp/osracer_sim_eval_track_record.csv

[3] Pure Pursuit raceline tracking
ros2 launch osracer_sim race_sim.launch.py \
  stage:=pure_pursuit \
  eval_output_csv:=/tmp/osracer_sim_eval_pure_pursuit.csv

[4] Stanley raceline tracking
ros2 launch osracer_sim race_sim.launch.py \
  stage:=stanley \
  eval_output_csv:=/tmp/osracer_sim_eval_stanley.csv

[5] Vehicle identification interface check
ros2 launch osracer_sim race_sim.launch.py \
  stage:=vehicle_id \
  eval_output_csv:=/tmp/osracer_sim_eval_vehicle_id.csv

[6] MPC raceline tracking
ros2 launch osracer_sim race_sim.launch.py \
  stage:=mpc \
  eval_output_csv:=/tmp/osracer_sim_eval_mpc.csv

[7] Gazebo rectangular track with static obstacle world
ros2 launch osracer_sim gazebo.launch.py \
  world:=$(ros2 pkg prefix osracer_sim)/share/osracer_sim/worlds/osracer_rect_track_obstacle.sdf

[8] Gazebo sensor and joint command bridge
ros2 launch osracer_sim gazebo.launch.py \
  use_gz_bridge:=true \
  use_gz_control:=true \
  publish_kinematic_clock:=false

[9] Compare generated race CSV reports
ros2 run osracer_race race_report_tools \
  /tmp/osracer_sim_eval_gap_follow.csv \
  /tmp/osracer_sim_eval_pure_pursuit.csv \
  /tmp/osracer_sim_eval_stanley.csv \
  /tmp/osracer_sim_eval_mpc.csv
EOF
