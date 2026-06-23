#!/usr/bin/env bash
set -u

ROS_DISTRO_NAME="${ROS_DISTRO:-jazzy}"
POLICY_PATH="${1:-}"

ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*"; }

check_cmd() {
    if command -v "$1" >/dev/null 2>&1; then
        ok "$1: $(command -v "$1")"
        return 0
    fi
    warn "$1: not found"
    return 1
}

echo "== OSRacer Jetson runtime preflight =="

if [[ -r /etc/nv_tegra_release ]]; then
    ok "Jetson Linux: $(head -1 /etc/nv_tegra_release)"
else
    warn "/etc/nv_tegra_release not found; this may not be a Jetson image"
fi

if [[ -r /proc/device-tree/model ]]; then
    ok "Device model: $(tr -d '\0' </proc/device-tree/model)"
fi

check_cmd nvpmodel
check_cmd jetson_clocks
check_cmd tegrastats

if command -v nvpmodel >/dev/null 2>&1; then
    echo "-- nvpmodel --"
    nvpmodel -q 2>/dev/null || warn "nvpmodel query failed; try running with sudo"
fi

if [[ -f "/opt/ros/${ROS_DISTRO_NAME}/setup.bash" ]]; then
    ok "ROS setup: /opt/ros/${ROS_DISTRO_NAME}/setup.bash"
    set +u
    # shellcheck disable=SC1090
    source "/opt/ros/${ROS_DISTRO_NAME}/setup.bash"
    set -u
else
    fail "ROS setup not found: /opt/ros/${ROS_DISTRO_NAME}/setup.bash"
fi

check_cmd ros2
check_cmd python3

echo "-- Python runtime --"
python3 - <<'PY'
import importlib.util
import sys

print("python:", sys.executable)
print("version:", sys.version.replace("\n", " "))
for name in ("rclpy", "ackermann_msgs", "nav_msgs", "sensor_msgs", "geometry_msgs", "torch", "onnx", "tensorrt"):
    spec = importlib.util.find_spec(name)
    print(f"{name}: {'OK' if spec else 'MISSING'}")
PY

if [[ -n "${POLICY_PATH}" ]]; then
    if [[ -f "${POLICY_PATH}" ]]; then
        ok "Policy file exists: ${POLICY_PATH}"
        python3 - "${POLICY_PATH}" <<'PY'
import sys
path = sys.argv[1]
try:
    import torch
    model = torch.jit.load(path, map_location="cpu")
    model.eval()
    obs = torch.zeros(1, 14)
    with torch.inference_mode():
        out = model(obs)
    print(f"[OK] TorchScript load/run: output_shape={tuple(out.shape)}")
except Exception as exc:
    print(f"[FAIL] TorchScript load/run: {exc}")
    sys.exit(1)
PY
    else
        fail "Policy file not found: ${POLICY_PATH}"
    fi
else
    warn "No policy path supplied; pass policy.pt to test TorchScript load/run"
fi

echo "-- ROS package hints --"
if command -v apt-cache >/dev/null 2>&1; then
    apt-cache policy "ros-${ROS_DISTRO_NAME}-ackermann-msgs" 2>/dev/null | sed -n '1,8p'
fi

echo "== Preflight complete =="
