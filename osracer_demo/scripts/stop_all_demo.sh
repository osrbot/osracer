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

echo "Stopping common demo ROS processes"
pkill -f "ros2 launch osracer_bringup bringup.launch.py" 2>/dev/null || true
pkill -f "ros2 launch osracer_bringup chassis_ackermann.launch.py" 2>/dev/null || true
pkill -f "ros2 launch osracer_bringup usb_cam.launch.py" 2>/dev/null || true
pkill -f "ros2 launch osracer_bringup led_matrix.launch.py" 2>/dev/null || true
pkill -f "ros2 launch osracer_description robot_description_tf.launch.py" 2>/dev/null || true
pkill -f "ros2 launch osracer_description osracer_description.launch.py" 2>/dev/null || true
pkill -f "ros2 launch osracer_bringup lidar.launch.py" 2>/dev/null || true
pkill -f "ros2 launch osracer_debug" 2>/dev/null || true
pkill -f "ros2 launch osracer_navigation" 2>/dev/null || true
pkill -f "ros2 launch osracer_slam" 2>/dev/null || true
pkill -f "nav2_" 2>/dev/null || true
pkill -f "bt_navigator" 2>/dev/null || true
pkill -f "controller_server" 2>/dev/null || true
pkill -f "planner_server" 2>/dev/null || true
pkill -f "behavior_server" 2>/dev/null || true
pkill -f "map_server" 2>/dev/null || true
pkill -f "amcl" 2>/dev/null || true
pkill -f "lifecycle_manager" 2>/dev/null || true
pkill -f "component_container" 2>/dev/null || true
pkill -f "robot_state_publisher" 2>/dev/null || true
pkill -f "usb_cam_node" 2>/dev/null || true
pkill -f "ydlidar_ros2_driver" 2>/dev/null || true
pkill -f "slam_toolbox" 2>/dev/null || true
pkill -f "slam_gmapping" 2>/dev/null || true
pkill -f "cartographer" 2>/dev/null || true
pkill -f "rviz2" 2>/dev/null || true
pkill -f "ros2 run osracer_demo drive_demo" 2>/dev/null || true
pkill -f "ros2 run osracer_demo odom_watch" 2>/dev/null || true

echo "Done"
