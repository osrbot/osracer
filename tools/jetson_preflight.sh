#!/usr/bin/env bash
set -u

ROS_DISTRO_NAME="${ROS_DISTRO:-jazzy}"
POLICY_PATH=""
OFFLINE_SMOKE=0

ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --offline-smoke)
            OFFLINE_SMOKE=1
            shift
            ;;
        --policy)
            POLICY_PATH="${2:-}"
            shift 2
            ;;
        --help|-h)
            echo "Usage: tools/jetson_preflight.sh [--offline-smoke] [--policy /path/to/policy.pt]"
            echo "       tools/jetson_preflight.sh /path/to/policy.pt"
            exit 0
            ;;
        *)
            if [[ "$1" == -* ]]; then
                fail "Unknown argument: $1"
                exit 2
            elif [[ -z "${POLICY_PATH}" ]]; then
                POLICY_PATH="$1"
                shift
            else
                fail "Unknown argument: $1"
                exit 2
            fi
            ;;
    esac
done

check_cmd() {
    if command -v "$1" >/dev/null 2>&1; then
        ok "$1: $(command -v "$1")"
        return 0
    fi
    warn "$1: not found"
    return 1
}

print_file_value() {
    local label="$1"
    local path="$2"
    if [[ -r "${path}" ]]; then
        ok "${label}: $(tr -d '\0' <"${path}")"
    else
        warn "${label}: not available (${path})"
    fi
}

print_meminfo_value() {
    local key="$1"
    local line
    line="$(awk -v key="${key}:" '$1 == key {print $2 " " $3}' /proc/meminfo 2>/dev/null || true)"
    if [[ -n "${line}" ]]; then
        ok "${key}: ${line}"
    else
        warn "${key}: not found"
    fi
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
check_cmd docker
if [[ -x "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/jetson_performance_profile.sh" ]]; then
    ok "jetson_performance_profile.sh: available"
else
    warn "jetson_performance_profile.sh: not executable"
fi
if [[ -x "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/jetson_sensor_preflight.sh" ]]; then
    ok "jetson_sensor_preflight.sh: available"
else
    warn "jetson_sensor_preflight.sh: not executable"
fi
if [[ -x "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/jetson_sensor_summary.py" ]]; then
    ok "jetson_sensor_summary.py: available"
else
    warn "jetson_sensor_summary.py: not executable"
fi
check_cmd nvidia-smi

if command -v nvpmodel >/dev/null 2>&1; then
    echo "-- nvpmodel --"
    nvpmodel -q 2>/dev/null || warn "nvpmodel query failed; try running with sudo"
fi

echo "-- Platform resources --"
print_meminfo_value MemTotal
print_meminfo_value MemAvailable
print_meminfo_value SwapTotal
print_meminfo_value SwapFree
df -h / /tmp 2>/dev/null || warn "df failed"
if [[ -d /sys/devices/system/cpu/cpu0/cpufreq ]]; then
    print_file_value "CPU governor" /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
    print_file_value "CPU min freq" /sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq
    print_file_value "CPU max freq" /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq
else
    warn "CPU cpufreq: not available"
fi
if [[ -d /sys/block ]]; then
    zram_count="$(find /sys/block -maxdepth 1 -name 'zram*' 2>/dev/null | wc -l)"
    ok "zram devices: ${zram_count}"
fi

echo "-- NVIDIA runtime --"
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader 2>/dev/null || warn "nvidia-smi query failed"
else
    warn "nvidia-smi not found; this is normal on some Jetson images"
fi
if command -v docker >/dev/null 2>&1; then
    docker info --format 'Docker runtimes: {{json .Runtimes}}' 2>/dev/null || warn "docker info failed; user may need docker group or root"
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

TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPLAY_TOOL="${TOOLS_DIR}/policy_replay_csv.py"
SUMMARY_TOOL="${TOOLS_DIR}/policy_replay_summary.py"
BENCHMARK_TOOL="${TOOLS_DIR}/benchmark_policy_inference.py"

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

echo "-- Offline replay tools --"
if [[ -x "${REPLAY_TOOL}" ]]; then
    ok "policy_replay_csv.py: ${REPLAY_TOOL}"
    python3 "${REPLAY_TOOL}" --help >/dev/null || warn "policy_replay_csv.py --help failed"
else
    fail "policy_replay_csv.py not executable: ${REPLAY_TOOL}"
fi

if [[ -x "${SUMMARY_TOOL}" ]]; then
    ok "policy_replay_summary.py: ${SUMMARY_TOOL}"
    python3 "${SUMMARY_TOOL}" --help >/dev/null || warn "policy_replay_summary.py --help failed"
else
    fail "policy_replay_summary.py not executable: ${SUMMARY_TOOL}"
fi

if [[ -x "${BENCHMARK_TOOL}" ]]; then
    ok "benchmark_policy_inference.py: ${BENCHMARK_TOOL}"
    python3 "${BENCHMARK_TOOL}" --help >/dev/null || warn "benchmark_policy_inference.py --help failed"
else
    fail "benchmark_policy_inference.py not executable: ${BENCHMARK_TOOL}"
fi

if [[ "${OFFLINE_SMOKE}" -eq 1 ]]; then
    echo "-- Offline replay smoke --"
    if [[ -z "${POLICY_PATH}" || ! -f "${POLICY_PATH}" ]]; then
        warn "Skipping offline smoke; pass --policy /path/to/policy.pt"
    else
        SMOKE_DIR="${TMPDIR:-/tmp}/osracer_preflight"
        mkdir -p "${SMOKE_DIR}"
        SMOKE_OBS="${SMOKE_DIR}/observations.csv"
        SMOKE_REPLAY="${SMOKE_DIR}/policy_replay.csv"
        cat >"${SMOKE_OBS}" <<'CSV'
px,py,pz,roll,pitch,yaw,vx,vy,vz,wx,wy,wz,last_speed,last_steering
0,0,0,0,0,0,0,0,0,0,0,0,0,0
0.1,0,0,0,0,0.05,0.2,0,0,0,0,0.1,0,0
CSV
        if python3 - <<'PY' >/dev/null 2>&1
import importlib.util
raise SystemExit(0 if importlib.util.find_spec("torch") else 1)
PY
        then
            POLICY_RUNNER=(python3)
        elif [[ -x "/home/osrbot/rlgpu_ws/IsaacLab/isaaclab.sh" ]]; then
            POLICY_RUNNER=(/home/osrbot/rlgpu_ws/IsaacLab/isaaclab.sh -p)
        else
            POLICY_RUNNER=(python3)
            warn "torch not found in python3 and IsaacLab runner not found; replay may fail"
        fi
        "${POLICY_RUNNER[@]}" "${REPLAY_TOOL}" \
            --policy "${POLICY_PATH}" \
            --input "${SMOKE_OBS}" \
            --output "${SMOKE_REPLAY}" \
            --max-speed-mps 0.3 \
            --max-steering-rad 0.488
        python3 "${SUMMARY_TOOL}" "${SMOKE_REPLAY}" \
            --min-rows 2 \
            --max-speed-cmd 0.3 \
            --max-abs-steering-cmd 0.488
        "${POLICY_RUNNER[@]}" "${BENCHMARK_TOOL}" \
            --policy "${POLICY_PATH}" \
            --warmup 5 \
            --iterations 20 \
            --output "${SMOKE_DIR}/policy_benchmark.json"
    fi
fi

echo "-- ROS package hints --"
if command -v apt-cache >/dev/null 2>&1; then
    apt-cache policy "ros-${ROS_DISTRO_NAME}-ackermann-msgs" 2>/dev/null | sed -n '1,8p'
fi

echo "-- Performance profile hint --"
echo "Run tools/jetson_performance_profile.sh before changing power/clocks."
echo "Run tools/jetson_sensor_preflight.sh after starting sensors to record device/topic rates."
echo "After selecting the correct nvpmodel mode on the target Jetson, apply with:"
echo "  tools/jetson_performance_profile.sh --apply --nvpmodel MODE_ID --jetson-clocks --set-cpu-governor"

echo "== Preflight complete =="
