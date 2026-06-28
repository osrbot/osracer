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

mode="${1:-auto}"
case "${mode}" in
  auto)
    if process_running "ros2 launch osracer_slam cartographer.launch.py" ||
      pgrep -f "cartographer" >/dev/null 2>&1 ||
      ros2 node list 2>/dev/null | grep -qi "cartographer"; then
      mode="cartographer"
      launch_file="map_save_cartographer.launch.xml"
    else
      mode="default"
      launch_file="map_save.launch.xml"
    fi
    ;;
  default|gmapping|slam_toolbox)
    launch_file="map_save.launch.xml"
    ;;
  cartographer)
    launch_file="map_save_cartographer.launch.xml"
    ;;
  *)
    echo "ERROR: unknown map save mode: ${mode}"
    echo "Use: auto, default, gmapping, slam_toolbox, or cartographer."
    exit 1
    ;;
esac

stamp="$(date '+%Y%m%d_%H%M%S')"
map_file="map"
archive_file="${OSRACER_MAP_NAME:-osracer_${mode}_${stamp}}"
slam_prefix="$(ros2 pkg prefix osracer_slam)"
default_map_path="$(python3 - "${slam_prefix}/../../src/osracer/osracer_slam/maps" <<'PY'
from pathlib import Path
import sys

print(Path(sys.argv[1]).resolve())
PY
)"

mkdir -p "${default_map_path}"

archive_current_map() {
  local archive_base="$1"
  local image_name="map.pgm"
  if [[ -f "${default_map_path}/map.pgm" ]]; then
    image_name="${archive_base}.pgm"
    cp "${default_map_path}/map.pgm" "${default_map_path}/${archive_base}.pgm"
  fi
  if [[ -f "${default_map_path}/map.yaml" ]]; then
    python3 - "${default_map_path}/map.yaml" "${default_map_path}/${archive_base}.yaml" "${image_name}" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
image_name = sys.argv[3]
lines = src.read_text(encoding="utf-8").splitlines()
updated = []
replaced = False
for line in lines:
    if line.lstrip().startswith("image:"):
        indent = line[: len(line) - len(line.lstrip())]
        updated.append(f"{indent}image: {image_name}")
        replaced = True
    else:
        updated.append(line)
if not replaced:
    updated.insert(0, f"image: {image_name}")
dst.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY
  fi
}

if [[ -f "${default_map_path}/map.yaml" || -f "${default_map_path}/map.pgm" ]]; then
  previous_file="previous_map_${stamp}"
  archive_current_map "${previous_file}"
  echo "Previous default map backed up as ${previous_file}.*"
fi

echo "Saving ${mode} map to ${default_map_path}/${map_file}.yaml"
ros2 launch osracer_slam "${launch_file}" \
  map_path:="${default_map_path}" \
  map_file:="${map_file}"

if [[ ! -f "${default_map_path}/map.yaml" ]]; then
  echo "ERROR: map saver finished but ${default_map_path}/map.yaml was not created."
  exit 1
fi

archive_current_map "${archive_file}"

echo "Map saved:"
echo "  ${default_map_path}/map.yaml"
echo "  ${default_map_path}/map.pgm"
echo "Archive copy:"
echo "  ${default_map_path}/${archive_file}.yaml"
echo "  ${default_map_path}/${archive_file}.pgm"
