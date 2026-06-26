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

LOG_DIR="$HOME/osracer_demo_logs"
LOG_FILE="$LOG_DIR/osracer-demo-launch.log"
mkdir -p "$LOG_DIR"

notify_error() {
  local message="$1"
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "OSRacer Demo 启动失败" "$message" >/dev/null 2>&1 || true
  fi
  if command -v zenity >/dev/null 2>&1; then
    zenity --error --title="OSRacer Demo 启动失败" --text="$message\n\n日志：$LOG_FILE" >/dev/null 2>&1 || true
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

  source_setup /opt/ros/humble/setup.bash || fail "未找到 ROS 2 Humble：/opt/ros/humble/setup.bash"

  if [[ -n "${OSRACER_WS:-}" ]]; then
    source_setup "${OSRACER_WS}/install/setup.bash" || fail "OSRACER_WS 已设置，但未找到 ${OSRACER_WS}/install/setup.bash"
  else
    source_setup "$HOME/osracer_ws/install/setup.bash" ||
      source_setup "$HOME/osracer/install/setup.bash" ||
      source_setup "$HOME/Documents/osracer/osracer/install/setup.bash" ||
      source_setup "$HOME/Desktop/osracer/osracer/install/setup.bash" ||
      fail "未找到 OSRacer 工作区 install/setup.bash。可设置 OSRACER_WS=/path/to/osracer_ws 后重新安装桌面图标。"
  fi

  command -v ros2 >/dev/null 2>&1 || fail "ros2 命令不可用，ROS 环境未正确加载。"
  ros2 pkg prefix osracer_demo >/dev/null 2>&1 || fail "当前 ROS 环境找不到 osracer_demo 包，请重新 colcon build 并 source install/setup.bash。"

  echo "Launching: ros2 run osracer_demo leader_demo"
  ros2 run osracer_demo leader_demo
} >>"$LOG_FILE" 2>&1

status=$?
if [[ "$status" -ne 0 ]]; then
  notify_error "启动命令退出，状态码：$status"
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
echo "If double-click does nothing, inspect: ${HOME}/osracer_demo_logs/osracer-demo-launch.log"
