#!/usr/bin/env bash
set -euo pipefail

if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  set -u
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

echo "Publishing stop commands"
for _ in 1 2 3 4 5; do
  ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
    "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}" >/dev/null 2>&1 || true
  ros2 topic pub --once /ackermann_cmd ackermann_msgs/msg/AckermannDrive \
    "{speed: 0.0, steering_angle: 0.0}" >/dev/null 2>&1 || true
  sleep 0.05
done

patterns=(
  "ros2 launch osracer_bringup bringup.launch.py"
  "ros2 launch osracer_bringup chassis_ackermann.launch.py"
  "ros2 launch osracer_bringup usb_cam.launch.py"
  "ros2 launch osracer_bringup led_matrix.launch.py"
  "ros2 launch osracer_description robot_description_tf.launch.py"
  "ros2 launch osracer_description osracer_description.launch.py"
  "ros2 launch osracer_bringup lidar.launch.py"
  "ros2 launch osracer_debug"
  "ros2 launch osracer_navigation"
  "ros2 launch osracer_slam"
  "ros2 run osracer_demo drive_demo"
  "ros2 run osracer_demo odom_watch"
  "chassis_ackermann.py"
  "twist_bridge.py"
  "nav2_"
  "bt_navigator"
  "controller_server"
  "planner_server"
  "behavior_server"
  "map_server"
  "amcl"
  "lifecycle_manager"
  "component_container"
  "robot_state_publisher"
  "usb_cam_node"
  "ydlidar_ros2_driver"
  "slam_toolbox"
  "slam_gmapping"
  "cartographer"
  "rviz2"
)

stop_patterns() {
  local signal="$1"
  local pattern
  for pattern in "${patterns[@]}"; do
    pkill "-${signal}" -f "${pattern}" 2>/dev/null || true
  done
}

echo "Stopping common demo ROS processes"
stop_patterns TERM
sleep 1
stop_patterns KILL

echo "Done"
