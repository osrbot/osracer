#!/usr/bin/env bash

OSRACER_DEMO_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OSRACER_DEMO_ROOT="$(cd "${OSRACER_DEMO_SCRIPT_DIR}/../.." && pwd)"
OSRACER_DEMO_LOG_DIR="${OSRACER_DEMO_LOG_DIR:-${OSRACER_DEMO_ROOT}/logs}"
OSRACER_DEMO_RUNTIME_DIR="${OSRACER_DEMO_RUNTIME_DIR:-${OSRACER_DEMO_LOG_DIR}/runtime}"
OSRACER_DEMO_PID_FILE="${OSRACER_DEMO_PID_FILE:-${OSRACER_DEMO_RUNTIME_DIR}/demo_pids.txt}"

mkdir -p "${OSRACER_DEMO_RUNTIME_DIR}"

source_osracer_env() {
  if [[ -f /opt/ros/humble/setup.bash ]]; then
    set +u
    # shellcheck disable=SC1091
    source /opt/ros/humble/setup.bash
    set -u
  else
    echo "ERROR: ROS 2 Humble not found at /opt/ros/humble/setup.bash"
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
  elif [[ -f "$HOME/osracer/install/setup.bash" ]]; then
    set +u
    # shellcheck disable=SC1090
    source "$HOME/osracer/install/setup.bash"
    set -u
  elif [[ -f "$HOME/Documents/osracer/osracer/install/setup.bash" ]]; then
    set +u
    # shellcheck disable=SC1090
    source "$HOME/Documents/osracer/osracer/install/setup.bash"
    set -u
  else
    echo "ERROR: osracer workspace setup not found."
    echo "Set OSRACER_WS=/home/racecar/osracer_ws or build/source the workspace first."
    exit 1
  fi
}

process_running() {
  local pattern="$1"
  pgrep -f "${pattern}" >/dev/null 2>&1
}

require_ros_pkg() {
  local pkg="$1"
  if ! ros2 pkg prefix "${pkg}" >/dev/null 2>&1; then
    echo "ERROR: required ROS package '${pkg}' was not found."
    echo "Build/source the OSRacer workspace, then rerun this demo."
    exit 1
  fi
}

record_demo_pid() {
  local label="$1"
  local pid="$2"
  printf '%s %s %s\n' "${pid}" "${label}" "$(date '+%F %T')" >> "${OSRACER_DEMO_PID_FILE}"
}

start_demo_bg() {
  local label="$1"
  shift
  "$@" &
  local pid=$!
  record_demo_pid "${label}" "${pid}"
  echo "Started ${label} pid=${pid}"
}

stop_tracked_demo_pids() {
  local signal="${1:-TERM}"
  [[ -f "${OSRACER_DEMO_PID_FILE}" ]] || return 0

  local pid label rest
  while read -r pid label rest; do
    [[ "${pid}" =~ ^[0-9]+$ ]] || continue
    if kill -0 "${pid}" 2>/dev/null; then
      echo "Stopping tracked ${label:-process} pid=${pid} with ${signal}"
      kill "-${signal}" "${pid}" 2>/dev/null || true
    fi
  done < "${OSRACER_DEMO_PID_FILE}"
}

clear_demo_pid_file() {
  : > "${OSRACER_DEMO_PID_FILE}"
}

close_existing_rviz() {
  pkill -f "ros2 launch osracer_debug debug_odom.launch.py" 2>/dev/null || true
  pkill -f "ros2 launch osracer_debug debug_mapping.launch.py" 2>/dev/null || true
  pkill -f "ros2 launch osracer_debug debug_cartographer.launch.py" 2>/dev/null || true
  pkill -f "ros2 launch osracer_navigation rviz_launch.py" 2>/dev/null || true
  pkill -f "rviz2" 2>/dev/null || true
  sleep 1
}

open_rviz_exclusive() {
  local label="$1"
  local pattern="$2"
  shift 2

  if process_running "${pattern}"; then
    echo "${label} RViz is already open; reusing the existing window."
    return 0
  fi

  echo "Opening ${label} RViz"
  close_existing_rviz
  start_demo_bg "rviz" "$@"
}

start_chassis_node_bg() {
  local port="${OSRACER_PORT:-/dev/osrbot_base}"
  local baud="${OSRACER_BAUD:-460800}"
  local pkg_prefix
  local launch_file

  pkg_prefix="$(ros2 pkg prefix osracer_bringup)"
  launch_file="${pkg_prefix}/share/osracer_bringup/launch/chassis_ackermann.launch.py"

  echo "Starting chassis on ${port}@${baud}"
  if process_running "ros2 launch osracer_bringup chassis_ackermann.launch.py"; then
    echo "Chassis launch is already running; skip duplicate start."
    return 0
  fi

  if grep -q "port_name" "${launch_file}"; then
    start_demo_bg "chassis" ros2 launch osracer_bringup chassis_ackermann.launch.py \
      port_name:="${port}" \
      baud_rate:="${baud}" \
      log_level:=info
  else
    start_demo_bg "chassis" ros2 launch osracer_bringup chassis_ackermann.launch.py \
      serial_port:="${port}" \
      serial_baudrate:="${baud}" \
      log_level:=info
  fi
}

start_robot_base_bg() {
  require_ros_pkg osracer_bringup
  require_ros_pkg osracer_description

  start_chassis_node_bg
  sleep 1

  echo "Starting robot TF"
  if process_running "ros2 launch osracer_description robot_description_tf.launch.py"; then
    echo "Robot TF launch is already running; skip duplicate start."
  else
    start_demo_bg "robot-tf" ros2 launch osracer_description robot_description_tf.launch.py
  fi

  sleep 1
  echo "Starting lidar"
  if process_running "ros2 launch osracer_bringup lidar.launch.py"; then
    echo "Lidar launch is already running; skip duplicate start."
  else
    start_demo_bg "lidar" ros2 launch osracer_bringup lidar.launch.py
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

wait_forever_with_stop() {
  echo
  echo "Running. Close this terminal or press Ctrl-C to stop related demo processes."
  trap 'echo; echo "Stopping demo..."; stop_vehicle_once; stop_tracked_demo_pids TERM; pids="$(jobs -pr)"; if [[ -n "${pids}" ]]; then kill ${pids} 2>/dev/null || true; fi; wait 2>/dev/null || true; exit 0' INT TERM EXIT
  while true; do
    sleep 2
  done
}
