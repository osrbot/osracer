#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib_osracer_demo.sh
source "${SCRIPT_DIR}/lib_osracer_demo.sh"

source_osracer_env
require_ros_pkg osracer_slam

map_topic_found=false
while read -r topic; do
  if [[ "${topic}" == "/map" ]]; then
    map_topic_found=true
    break
  fi
done < <(ros2 topic list 2>/dev/null || true)
if [[ "${map_topic_found}" != "true" ]]; then
  echo "ERROR: No /map topic. Start mapping first, wait until RViz shows a map, then save again."
  exit 1
fi

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
slam_prefix="$(ros2 pkg prefix osracer_slam)"
default_map_path="$(python3 - "${slam_prefix}/../../src/osracer/osracer_slam/maps" <<'PY'
from pathlib import Path
import sys

print(Path(sys.argv[1]).resolve())
PY
)"

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
  echo "Saving ${mode} map to ${default_map_path}/${map_file}.yaml"
  ros2 launch osracer_slam "${launch_file}" \
    map_file:="${map_file}"
  echo "Map saved:"
  echo "  ${default_map_path}/${map_file}.yaml"
  echo "  ${default_map_path}/${map_file}.pgm"
fi
