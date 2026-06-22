#!/usr/bin/env bash
set -euo pipefail

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${PACKAGE_DIR}/.." && pwd)"
SOURCE_MODE=false
if [[ -d "${PACKAGE_DIR}/osracer_sim" ]]; then
  SOURCE_MODE=true
fi

echo "[1/5] Python syntax"
if [[ "${SOURCE_MODE}" == "true" ]]; then
  python3 -m py_compile \
    "${PACKAGE_DIR}/osracer_sim/"*.py \
    "${PACKAGE_DIR}/launch/"*.launch.py
else
  python3 -m py_compile "${PACKAGE_DIR}/launch/"*.launch.py
fi

echo "[2/5] XML files"
python3 - <<'PY' "${PACKAGE_DIR}"
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

package_dir = Path(sys.argv[1])
ET.parse(package_dir / 'package.xml')
ET.parse(package_dir / 'worlds' / 'osracer_empty.sdf')
PY

echo "[3/5] Offline unit tests"
if [[ "${SOURCE_MODE}" == "true" ]]; then
  PYTHONPATH="${PACKAGE_DIR}:${PYTHONPATH:-}" python3 -m unittest discover \
    -s "${PACKAGE_DIR}/test" -p 'test_*.py'
else
  python3 -m unittest discover -s "${PACKAGE_DIR}/test" -p 'test_*.py'
fi

echo "[4/5] Package metadata"
python3 - <<'PY' "${PACKAGE_DIR}"
import sys
from pathlib import Path

package_dir = Path(sys.argv[1])
readme = (package_dir / 'README_zh.md').read_text(encoding='utf-8')
setup = ''
setup_path = package_dir / 'setup.py'
if setup_path.exists():
    setup = setup_path.read_text(encoding='utf-8')
for token in (
    'ackermann_kinematic_sim_node',
    'base_sim.launch.py',
    'gazebo.launch.py',
    'slam_sim.launch.py',
    'navigation_sim.launch.py',
    'race_sim.launch.py',
):
    if token not in setup and token not in readme:
        raise SystemExit(f'missing package documentation for {token}')
for path in (
    package_dir / 'package.xml',
    package_dir / 'launch' / 'base_sim.launch.py',
    package_dir / 'worlds' / 'osracer_empty.sdf',
    package_dir / 'scripts' / 'check_sim_package.sh',
):
    if not path.exists():
        raise SystemExit(f'missing installed resource: {path}')
PY

echo "[5/5] Optional ROS build"
if [[ "${SOURCE_MODE}" != "true" ]]; then
  echo "Installed package check; skipped source-tree colcon build."
elif [[ -f /opt/ros/humble/setup.bash ]] && command -v colcon >/dev/null 2>&1; then
  set +u
  source /opt/ros/humble/setup.bash
  set -u
  cd "${REPO_ROOT}"
  colcon build --symlink-install --packages-select osracer_sim
else
  echo "ROS 2 Humble or colcon not found; skipped colcon build."
fi

echo "osracer_sim checks passed"
