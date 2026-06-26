#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib_osracer_demo.sh
source "${SCRIPT_DIR}/lib_osracer_demo.sh"

source_osracer_env
echo "Publishing stop commands"
for _ in 1 2 3 4 5; do
  stop_vehicle_once
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
  "lidar.launch.py"
  "chassis_ackermann.launch.py"
  "usb_cam.launch.py"
  "led_matrix.launch.py"
  "robot_description_tf.launch.py"
  "osracer_description.launch.py"
  "ros2 launch osracer_debug"
  "ros2 launch osracer_navigation"
  "ros2 launch osracer_slam"
  "ros2 run osracer_demo drive_demo"
  "ros2 run osracer_demo odom_watch"
  "python3 ros_demo/scripts/drive_demo.py"
  "python3 ros_demo/scripts/odom_watch.py"
  "osracer_chassis"
  "chassis_ackermann.py"
  "twist_bridge.py"
  "base_footprint2base_link"
  "base_link2laser"
  "base_link2imu"
  "base_link2camera"
  "static_transform_publisher"
  "joint_state_publisher"
  "osracer_joint_state_publisher"
  "robot_state_publisher"
  "complementary_filter_node"
  "ekf_node"
  "ekf_filter_node"
  "usb_cam_node_exe"
  "osrbot_led_matrix"
  "led_matrix.py"
  "richbeam_lidar_node0"
  "lakibeam1_scan_node"
  "lakibeam1"
  "nav2_"
  "bt_navigator"
  "controller_server"
  "planner_server"
  "behavior_server"
  "map_server"
  "amcl"
  "lifecycle_manager"
  "component_container"
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
