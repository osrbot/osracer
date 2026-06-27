#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib_osracer_demo.sh
source "${SCRIPT_DIR}/lib_osracer_demo.sh"

source_osracer_env
require_ros_pkg osracer_slam

mode="${1:-default}"
case "${mode}" in
  default|gmapping|slam_toolbox)
    launch_file="map_save.launch.xml"
    ;;
  cartographer)
    launch_file="map_save_cartographer.launch.xml"
    ;;
  *)
    echo "ERROR: unknown map save mode: ${mode}"
    echo "Use: default, gmapping, slam_toolbox, or cartographer."
    exit 1
    ;;
esac

stamp="$(date '+%Y%m%d_%H%M%S')"
map_file="${OSRACER_MAP_NAME:-osracer_${mode}_${stamp}}"

if [[ -n "${OSRACER_MAP_PATH:-}" ]]; then
  mkdir -p "${OSRACER_MAP_PATH}"
  echo "Saving ${mode} map to ${OSRACER_MAP_PATH}/${map_file}.yaml"
  ros2 launch osracer_slam "${launch_file}" \
    map_path:="${OSRACER_MAP_PATH}" \
    map_file:="${map_file}"
  echo "Map saved:"
  echo "  ${OSRACER_MAP_PATH}/${map_file}.yaml"
  echo "  ${OSRACER_MAP_PATH}/${map_file}.pgm"
else
  echo "Saving ${mode} map as ${map_file} in the osracer_slam default maps directory"
  ros2 launch osracer_slam "${launch_file}" \
    map_file:="${map_file}"
  echo "Map saved in the osracer_slam default maps directory:"
  echo "  ${map_file}.yaml"
  echo "  ${map_file}.pgm"
fi
