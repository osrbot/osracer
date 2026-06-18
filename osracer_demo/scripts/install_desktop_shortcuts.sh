#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$HOME/.local/share/applications"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
LAUNCHER="${BIN_DIR}/osracer-demo-launch"
DESKTOP_FILE="${APP_DIR}/osracer-demo.desktop"
DESKTOP_COPY="${DESKTOP_DIR}/OSRacer Demo.desktop"

mkdir -p "${APP_DIR}" "${BIN_DIR}" "${DESKTOP_DIR}"

cat >"${LAUNCHER}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  source /opt/ros/humble/setup.bash
  set -u
fi

if [[ -n "${OSRACER_WS:-}" && -f "${OSRACER_WS}/install/setup.bash" ]]; then
  set +u
  source "${OSRACER_WS}/install/setup.bash"
  set -u
elif [[ -f "$HOME/osracer_ws/install/setup.bash" ]]; then
  set +u
  source "$HOME/osracer_ws/install/setup.bash"
  set -u
fi

exec ros2 run osracer_demo leader_demo
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
echo
echo "If Ubuntu asks whether to trust the launcher, choose 'Trust and Launch'."
