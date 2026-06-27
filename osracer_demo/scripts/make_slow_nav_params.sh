#!/usr/bin/env bash
set -euo pipefail

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
fi

NAV_PREFIX="$(ros2 pkg prefix osracer_navigation)"
SOURCE_FILE="${NAV_PREFIX}/share/osracer_navigation/params/teb_nav2_params.yaml"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib_osracer_demo.sh
source "${SCRIPT_DIR}/lib_osracer_demo.sh"
OUT_FILE="${OSRACER_SLOW_NAV_PARAMS:-${OSRACER_DEMO_RUNTIME_DIR}/teb_slow_nav2_params.yaml}"

python3 - "${SOURCE_FILE}" "${OUT_FILE}" <<'PY'
from pathlib import Path
import re
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])

replacements = {
    "max_vel_x": "0.60",
    "max_vel_x_backwards": "0.30",
    "max_vel_theta": "0.75",
    "acc_lim_x": "0.80",
    "acc_lim_theta": "0.80",
    "max_rotational_vel": "0.60",
    "min_rotational_vel": "0.20",
    "rotational_acc_lim": "0.80",
}

lines = []
for line in source.read_text(encoding="utf-8").splitlines(keepends=True):
    match = re.match(r"^(\s*)([A-Za-z0-9_]+):\s*.*?(\s*(?:#.*)?\n?)$", line)
    if match and match.group(2) in replacements:
        suffix = match.group(3) if match.group(3).lstrip().startswith("#") else "\n"
        line = f"{match.group(1)}{match.group(2)}: {replacements[match.group(2)]}{suffix}"
    lines.append(line)

target.parent.mkdir(parents=True, exist_ok=True)
target.write_text("".join(lines), encoding="utf-8")
PY

echo "${OUT_FILE}"
