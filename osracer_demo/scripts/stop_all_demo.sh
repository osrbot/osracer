#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/lib_osracer_demo.sh" ]]; then
  # shellcheck source=lib_osracer_demo.sh
  source "${SCRIPT_DIR}/lib_osracer_demo.sh"
else
  echo "WARN: lib_osracer_demo.sh not found; using stop cleanup fallback"
  source_osracer_env() {
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
    elif [[ -f "$HOME/osracer/install/setup.bash" ]]; then
      set +u
      # shellcheck disable=SC1090
      source "$HOME/osracer/install/setup.bash"
      set -u
    fi
  }

  stop_vehicle_once() {
    if command -v ros2 >/dev/null 2>&1; then
      timeout 2 ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
        "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}" >/dev/null 2>&1 || true
      timeout 2 ros2 topic pub --once /ackermann_cmd ackermann_msgs/msg/AckermannDrive \
        "{speed: 0.0, steering_angle: 0.0}" >/dev/null 2>&1 || true
    fi
  }

  stop_tracked_demo_pids() {
    return 0
  }

  clear_demo_pid_file() {
    return 0
  }
fi

source_osracer_env
echo "Publishing stop commands"
for _ in 1 2 3 4 5; do
  stop_vehicle_once
  sleep 0.05
done

echo "Stopping tracked demo process IDs"
stop_tracked_demo_pids TERM
sleep 1
stop_tracked_demo_pids KILL

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
  "__node:=richbeam_lidar_node0"
  "lakibeam1_scan_node"
  "lakibeam1_scan_"
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
  "rqt_gui"
  "rqt_image_view"
  "rqt_topic"
)

process_names=(
  "lakibeam1_scan_"
  "lakibeam1_scan_node"
  "rviz2"
  "rqt_gui"
  "rqt_image_view"
)

stop_patterns() {
  local signal="$1"
  local pattern
  for pattern in "${patterns[@]}"; do
    pkill "-${signal}" -f "${pattern}" 2>/dev/null || true
  done
  for pattern in "${process_names[@]}"; do
    pkill "-${signal}" -x "${pattern}" 2>/dev/null || true
  done
}

echo "Stopping common demo ROS processes"
stop_patterns TERM
sleep 2
stop_patterns KILL
sleep 1
stop_patterns KILL
clear_demo_pid_file

remaining="$(
  pgrep -af "lakibeam|richbeam|rviz2|rqt_|osracer_chassis|cartographer|slam_toolbox|slam_gmapping|nav2_|bt_navigator|controller_server|planner_server|behavior_server|map_server|amcl|lifecycle_manager|usb_cam_node|osrbot_led_matrix" 2>/dev/null || true
)"
if [[ -n "${remaining}" ]]; then
  echo "WARNING: matching demo processes still remain after cleanup:"
  echo "${remaining}"
else
  echo "No matching demo processes remain"
fi

echo "Done"
