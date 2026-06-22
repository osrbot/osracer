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
  costmap_converter_msgs
  costmap_converter
  teb_msgs
  teb_local_planner
  osracer_bringup
  osracer_calib
  osracer_debug
  osracer_demo
  osracer_description
  osracer_navigation
  osracer_race
  osracer_sim
  osracer_slam
)

FULL_PACKAGES=(
  "${STABLE_PACKAGES[@]}"
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
  colcon build --symlink-install --packages-up-to ${BUILD_PACKAGES}
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
if RACE_PREFIX="$(ros2 pkg prefix osracer_race 2>/dev/null)"; then
  bash "${RACE_PREFIX}/share/osracer_race/scripts/check_race_package.sh"
else
  echo "Skipped osracer_race self-check; package was not built in this profile."
fi

echo "[3/4] osracer_race ROS entry checks"
if [[ -n "${RACE_PREFIX:-}" ]]; then
  bash "${RACE_PREFIX}/share/osracer_race/scripts/validate_race_ros.sh"
else
  echo "Skipped osracer_race ROS entry checks; package was not built in this profile."
fi

echo "[3b/4] osracer_sim installed self-check"
if SIM_PREFIX="$(ros2 pkg prefix osracer_sim 2>/dev/null)"; then
  bash "${SIM_PREFIX}/share/osracer_sim/scripts/check_sim_package.sh"
else
  echo "Skipped osracer_sim self-check; package was not built in this profile."
fi

echo "[4/4] selected workspace launch arguments"
if [[ -z "${BUILD_PACKAGES}" ]]; then
  ros2 launch osracer_bringup bringup.launch.py --show-args >/dev/null
  ros2 launch osracer_description osracer_description.launch.py --show-args >/dev/null
  ros2 launch osracer_bringup lidar.launch.py --show-args >/dev/null
  ros2 launch osracer_slam gmapping.launch.py --show-args >/dev/null
  ros2 launch osracer_calib rgb_camera_calibration.launch.py --show-args >/dev/null
else
  echo "Skipped workspace launch checks for package-limited build: ${BUILD_PACKAGES}"
fi

if [[ -z "${BUILD_PACKAGES}" ]]; then
  ros2 pkg prefix teb_local_planner >/dev/null
fi

echo "ROS Humble Docker check passed"
