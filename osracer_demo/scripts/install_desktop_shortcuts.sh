#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$HOME/.local/share/applications"
BIN_DIR="$HOME/.local/bin"
if command -v xdg-user-dir >/dev/null 2>&1; then
  DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
DESKTOP_DIR="${DESKTOP_DIR:-${XDG_DESKTOP_DIR:-$HOME/Desktop}}"
LAUNCHER="${BIN_DIR}/osracer-demo-launch"
DESKTOP_FILE="${APP_DIR}/osracer-demo.desktop"
DESKTOP_COPY="${DESKTOP_DIR}/OSRacer Demo.desktop"

mkdir -p "${APP_DIR}" "${BIN_DIR}" "${DESKTOP_DIR}"

cat >"${LAUNCHER}" <<'EOF'
#!/usr/bin/env bash
set -u

LOG_DIR="${OSRACER_DEMO_LOG_DIR:-$HOME/osracer_demo/logs}"
LOG_FILE="$LOG_DIR/osracer-demo-launch.log"
mkdir -p "$LOG_DIR"

notify_error() {
  local message="$1"
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "OSRacer Demo failed to start" "$message" >/dev/null 2>&1 || true
  fi
  if command -v zenity >/dev/null 2>&1; then
    zenity --error --title="OSRacer Demo failed to start" --text="$message\n\nLog: $LOG_FILE" >/dev/null 2>&1 || true
  fi
}

fail() {
  local message="$1"
  echo "ERROR: $message"
  notify_error "$message"
  exit 1
}

source_setup() {
  local setup="$1"
  if [[ -f "$setup" ]]; then
    echo "source $setup"
    set +u
    # shellcheck disable=SC1090
    source "$setup"
    set -u
    return 0
  fi
  return 1
}

{
  echo
  echo "===== $(date '+%F %T') OSRacer Demo ====="
  echo "HOME=$HOME"
  echo "DISPLAY=${DISPLAY:-}"
  echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-}"

  source_setup /opt/ros/humble/setup.bash || fail "ROS 2 Humble setup was not found: /opt/ros/humble/setup.bash"

  if [[ -n "${OSRACER_WS:-}" ]]; then
    source_setup "${OSRACER_WS}/install/setup.bash" || fail "OSRACER_WS is set, but ${OSRACER_WS}/install/setup.bash was not found"
  else
    source_setup "$HOME/osracer_ws/install/setup.bash" ||
      source_setup "$HOME/osracer/install/setup.bash" ||
      source_setup "$HOME/Documents/osracer/osracer/install/setup.bash" ||
      source_setup "$HOME/Desktop/osracer/osracer/install/setup.bash" ||
      fail "OSRacer workspace install/setup.bash was not found. Set OSRACER_WS=/path/to/workspace and reinstall the desktop launcher."
  fi

  command -v ros2 >/dev/null 2>&1 || fail "ros2 is not available; the ROS environment was not loaded correctly."
  ros2 pkg prefix osracer_demo >/dev/null 2>&1 || fail "The osracer_demo package was not found. Rebuild with colcon and source install/setup.bash."

  echo "Launching: ros2 run osracer_demo leader_demo"
  ros2 run osracer_demo leader_demo
} >>"$LOG_FILE" 2>&1

status=$?
if [[ "$status" -ne 0 ]]; then
  notify_error "Launch command exited with status: $status"
fi
exit "$status"
EOF
chmod +x "${LAUNCHER}"

cat >"${DESKTOP_FILE}" <<EOF
[Desktop Entry]
Type=Application
Name=OSRacer Demo
Comment=OSRacer field demo control panel
Exec=${LAUNCHER}
Terminal=false
Categories=Utility;Robotics;
StartupNotify=true
EOF

cp "${DESKTOP_FILE}" "${DESKTOP_COPY}"
chmod +x "${DESKTOP_FILE}" "${DESKTOP_COPY}"

if command -v gio >/dev/null 2>&1; then
  gio set "${DESKTOP_COPY}" metadata::trusted true >/dev/null 2>&1 || true
fi

echo "Installed:"
echo "  ${DESKTOP_COPY}"
echo "  ${DESKTOP_FILE}"
echo "  ${LAUNCHER}"
echo
echo "If Ubuntu asks whether to trust the launcher, choose 'Trust and Launch'."
echo "If double-click does nothing, inspect: ${OSRACER_DEMO_LOG_DIR:-${HOME}/osracer_demo/logs}/osracer-demo-launch.log"
