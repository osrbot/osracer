#!/usr/bin/env bash
set -eo pipefail

SOURCE_DIR="${OSRACER_SOURCE_DIR:-/src/osracer}"
WORKSPACE_DIR="${OSRACER_WORKSPACE_DIR:-/tmp/osracer_ws}"
BUILD_PACKAGES="${OSRACER_BUILD_PACKAGES:-}"
BUILD_PROFILE="${OSRACER_BUILD_PROFILE:-stable}"

STABLE_PACKAGES=(
  lakibeam1
  openslam_gmapping
  slam_gmapping
  camera_calibration
  osracer_bringup
  osracer_calib
  osracer_debug
  osracer_demo
  osracer_description
  osracer_navigation
  osracer_race
  osracer_slam
)

FULL_PACKAGES=(
  "${STABLE_PACKAGES[@]}"
  costmap_converter_msgs
  costmap_converter
  teb_msgs
  teb_local_planner
)

if [[ ! -d "${SOURCE_DIR}" ]]; then
  echo "source directory not found: ${SOURCE_DIR}" >&2
  exit 1
fi

if [[ ! -f "${SOURCE_DIR}/osracer_dependency/Lakibeam_ROS2_Driver/package.xml" ]]; then
  echo "osracer_dependency submodule is not initialized." >&2
  echo "Run: git submodule update --init --recursive" >&2
  exit 1
fi

source /opt/ros/humble/setup.bash
set -u

rm -rf "${WORKSPACE_DIR}"
mkdir -p "${WORKSPACE_DIR}/src"
cp -a "${SOURCE_DIR}" "${WORKSPACE_DIR}/src/osracer"

cd "${WORKSPACE_DIR}"

echo "[1/4] colcon build"
if [[ -n "${BUILD_PACKAGES}" ]]; then
  colcon build --symlink-install --packages-select ${BUILD_PACKAGES}
else
  case "${BUILD_PROFILE}" in
    stable)
      colcon build --symlink-install --packages-select "${STABLE_PACKAGES[@]}"
      ;;
    full)
      colcon build --symlink-install --packages-select "${FULL_PACKAGES[@]}"
      ;;
    *)
      echo "unknown OSRACER_BUILD_PROFILE: ${BUILD_PROFILE}" >&2
      echo "supported values: stable, full" >&2
      exit 1
      ;;
  esac
fi

set +u
source "${WORKSPACE_DIR}/install/setup.bash"
set -u

echo "[2/4] osracer_race installed self-check"
bash "$(ros2 pkg prefix osracer_race)/share/osracer_race/scripts/check_race_package.sh"

echo "[3/4] osracer_race ROS entry checks"
bash "$(ros2 pkg prefix osracer_race)/share/osracer_race/scripts/validate_race_ros.sh"

echo "[4/4] selected workspace launch arguments"
ros2 launch osracer_bringup bringup.launch.py --show-args >/dev/null
ros2 launch osracer_description osracer_description.launch.py --show-args >/dev/null
ros2 launch osracer_bringup lidar.launch.py --show-args >/dev/null
ros2 launch osracer_slam gmapping.launch.py --show-args >/dev/null
ros2 launch osracer_calib rgb_camera_calibration.launch.py --show-args >/dev/null

if [[ "${BUILD_PROFILE}" == "full" || -n "${BUILD_PACKAGES}" ]]; then
  ros2 pkg prefix teb_local_planner >/dev/null
fi

echo "ROS Humble Docker check passed"
