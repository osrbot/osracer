#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -d "${PKG_DIR}/osracer_race" ]]; then
  CHECK_MODE="source"
  REPO_DIR="$(cd "${PKG_DIR}/.." && pwd)"
  cd "${REPO_DIR}"
  PKG_PATH="osracer_race"
  PYTHONPATH_PREFIX="PYTHONPATH=./osracer_race"
else
  CHECK_MODE="installed"
  cd "${PKG_DIR}"
  PKG_PATH="."
  PYTHONPATH_PREFIX=""
fi

echo "check mode: ${CHECK_MODE}"

echo "[1/7] Self-check script syntax"
for script in "${SCRIPT_DIR}"/*.sh; do
  bash -n "${script}"
done

echo "[2/7] Python syntax"
if [[ "${CHECK_MODE}" == "source" ]]; then
  PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/osracer_pycache}" \
    python3 -m py_compile \
      "${PKG_PATH}"/osracer_race/*.py \
      "${PKG_PATH}"/launch/*.launch.py \
      "${PKG_PATH}"/test/*.py
else
  PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/osracer_pycache}" \
    python3 -m py_compile \
      "${PKG_PATH}"/launch/*.launch.py \
      "${PKG_PATH}"/test/*.py
fi

echo "[3/7] XML and YAML"
PKG_PATH="${PKG_PATH}" python3 - <<'PY'
import os
import xml.etree.ElementTree as ET
from pathlib import Path

pkg_path = Path(os.environ['PKG_PATH'])
ET.parse(pkg_path / 'package.xml')
try:
    import yaml
except Exception as exc:
    raise SystemExit(f'PyYAML is required for config validation: {exc}')

for path in (pkg_path / 'config').rglob('*.yaml'):
    with path.open(encoding='utf-8') as handle:
        yaml.safe_load(handle)
PY

echo "[4/7] Offline unit tests"
if [[ "${CHECK_MODE}" == "source" && -n "${PYTHONPATH_PREFIX}" ]]; then
  env ${PYTHONPATH_PREFIX} python3 "${PKG_PATH}"/test/test_race_math.py
else
  python3 "${PKG_PATH}"/test/test_race_math.py
fi

echo "[5/7] Helper import smoke tests"
if [[ -n "${PYTHONPATH_PREFIX}" ]]; then
  env ${PYTHONPATH_PREFIX} python3 - <<'PY'
modules = [
    'osracer_race.common',
    'osracer_race.eval_tools',
    'osracer_race.gap_follow_tools',
    'osracer_race.mpc_tools',
    'osracer_race.overtake_tools',
    'osracer_race.race_report_tools',
    'osracer_race.raceline_tools',
    'osracer_race.safety_tools',
    'osracer_race.speed_profile_tools',
    'osracer_race.tracking_tools',
    'osracer_race.vehicle_id_tools',
]
for module in modules:
    __import__(module)
PY
else
  python3 - <<'PY'
modules = [
    'osracer_race.common',
    'osracer_race.eval_tools',
    'osracer_race.gap_follow_tools',
    'osracer_race.mpc_tools',
    'osracer_race.overtake_tools',
    'osracer_race.race_report_tools',
    'osracer_race.raceline_tools',
    'osracer_race.safety_tools',
    'osracer_race.speed_profile_tools',
    'osracer_race.tracking_tools',
    'osracer_race.vehicle_id_tools',
]
for module in modules:
    __import__(module)
PY
fi

echo "[6/7] Tool smoke tests"
if [[ -n "${PYTHONPATH_PREFIX}" ]]; then
  env ${PYTHONPATH_PREFIX} python3 -m osracer_race.raceline_tools --help >/dev/null
  env ${PYTHONPATH_PREFIX} python3 -m osracer_race.race_report_tools --help >/dev/null
  env ${PYTHONPATH_PREFIX} python3 -m osracer_race.raceline_tools \
    "${PKG_PATH}"/config/tracks/example_raceline.csv \
    /tmp/osracer_profiled.csv \
    --max-speed 3.0 \
    --min-speed 0.8 \
    --max-lateral-accel 4.5 >/dev/null
else
  python3 -m osracer_race.raceline_tools --help >/dev/null
  python3 -m osracer_race.race_report_tools --help >/dev/null
  python3 -m osracer_race.raceline_tools \
    "${PKG_PATH}"/config/tracks/example_raceline.csv \
    /tmp/osracer_profiled.csv \
    --max-speed 3.0 \
    --min-speed 0.8 \
    --max-lateral-accel 4.5 >/dev/null
fi

python3 - <<'PY'
import csv
from pathlib import Path

path = Path('/tmp/osracer_eval_sample.csv')
with path.open('w', newline='', encoding='utf-8') as handle:
    writer = csv.writer(handle)
    writer.writerow([
        'time_s', 'x', 'y', 'yaw', 'speed_mps', 'command_speed_mps',
        'command_steering_rad', 'track_error_m', 'heading_error_rad'])
    writer.writerow([0, 0, 0, 0, 1.0, 1.2, 0.1, 0.05, 0.02])
    writer.writerow([0.1, 0, 0, 0, 2.0, 2.2, -0.2, -0.15, -0.04])
PY
if [[ -n "${PYTHONPATH_PREFIX}" ]]; then
  env ${PYTHONPATH_PREFIX} python3 -m osracer_race.race_report_tools \
    /tmp/osracer_eval_sample.csv >/dev/null
else
  python3 -m osracer_race.race_report_tools \
    /tmp/osracer_eval_sample.csv >/dev/null
fi

echo "[7/7] ROS environment"
if [[ "${CHECK_MODE}" == "source" && -f /opt/ros/humble/setup.bash ]] && command -v colcon >/dev/null 2>&1; then
  echo "ROS 2 Humble and colcon found; building osracer_race"
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  set -u
  colcon build --symlink-install --packages-select osracer_race
  set +u
  # shellcheck disable=SC1091
  source install/setup.bash
  set -u
  ros2 run osracer_race raceline_tools --help >/dev/null
  ros2 run osracer_race race_report_tools --help >/dev/null
  ros2 launch osracer_race gap_follow.launch.py --show-args >/dev/null
  ros2 launch osracer_race pure_pursuit.launch.py --show-args >/dev/null
  ros2 launch osracer_race stanley.launch.py --show-args >/dev/null
  ros2 launch osracer_race mpc.launch.py --show-args >/dev/null
  ros2 launch osracer_race track_record.launch.py --show-args >/dev/null
  ros2 launch osracer_race vehicle_id.launch.py --show-args >/dev/null
  ros2 launch osracer_race race_bringup.launch.py --show-args >/dev/null
elif [[ "${CHECK_MODE}" == "installed" ]]; then
  echo "Installed package check; skipped source-tree colcon build."
  if command -v ros2 >/dev/null 2>&1; then
    ros2 run osracer_race raceline_tools --help >/dev/null
    ros2 run osracer_race race_report_tools --help >/dev/null
    ros2 launch osracer_race gap_follow.launch.py --show-args >/dev/null
    ros2 launch osracer_race pure_pursuit.launch.py --show-args >/dev/null
    ros2 launch osracer_race stanley.launch.py --show-args >/dev/null
    ros2 launch osracer_race mpc.launch.py --show-args >/dev/null
    ros2 launch osracer_race track_record.launch.py --show-args >/dev/null
    ros2 launch osracer_race vehicle_id.launch.py --show-args >/dev/null
    ros2 launch osracer_race race_bringup.launch.py --show-args >/dev/null
  else
    echo "ros2 not found; skipped installed launch --show-args checks."
  fi
else
  echo "ROS 2 Humble or colcon not found; skipped colcon build."
fi

echo "osracer_race checks passed"
