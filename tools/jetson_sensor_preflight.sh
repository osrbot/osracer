#!/usr/bin/env bash
set -u

ROS_DISTRO_NAME="${ROS_DISTRO:-jazzy}"
OUTPUT_DIR="${TMPDIR:-/tmp}/osracer_sensor_preflight_$(date +%Y%m%d_%H%M%S)"
DURATION_S=5
CAMERA_TOPIC="/rgb/image_raw"
LIDAR_TOPIC="/scan"
IMU_TOPIC="/imu_filter"
ODOM_TOPIC="/odometry/filtered"
EXTRA_TOPICS=()

usage() {
    cat <<'EOF'
Usage: tools/jetson_sensor_preflight.sh [options]

Read-only Jetson sensor/runtime probe. It records device visibility, camera
formats, network interfaces, ROS topic metadata, and short topic-rate samples.

Options:
  --output-dir DIR       Directory for logs and summary.
  --duration SECONDS     Seconds per ros2 topic hz sample. Default: 5.
  --ros-distro NAME      ROS distro to source. Default: $ROS_DISTRO or jazzy.
  --camera-topic TOPIC   Camera image topic. Default: /rgb/image_raw.
  --lidar-topic TOPIC    Lidar scan topic. Default: /scan.
  --imu-topic TOPIC      IMU topic. Default: /imu_filter.
  --odom-topic TOPIC     Odometry topic. Default: /odometry/filtered.
  --topic TOPIC          Additional topic to inspect. Repeatable.
  -h, --help             Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir)
            OUTPUT_DIR="${2:-}"
            shift 2
            ;;
        --duration)
            DURATION_S="${2:-}"
            shift 2
            ;;
        --ros-distro)
            ROS_DISTRO_NAME="${2:-}"
            shift 2
            ;;
        --camera-topic)
            CAMERA_TOPIC="${2:-}"
            shift 2
            ;;
        --lidar-topic)
            LIDAR_TOPIC="${2:-}"
            shift 2
            ;;
        --imu-topic)
            IMU_TOPIC="${2:-}"
            shift 2
            ;;
        --odom-topic)
            ODOM_TOPIC="${2:-}"
            shift 2
            ;;
        --topic)
            EXTRA_TOPICS+=("${2:-}")
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "[FAIL] Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

mkdir -p "${OUTPUT_DIR}"
SUMMARY="${OUTPUT_DIR}/summary.md"
: >"${SUMMARY}"

log() { printf '%s\n' "$*" | tee -a "${SUMMARY}"; }
run_capture() {
    local name="$1"
    shift
    local out="${OUTPUT_DIR}/${name}.log"
    log "- ${name}: ${out}"
    {
        printf '$'
        printf ' %q' "$@"
        printf '\n'
        "$@"
    } >"${out}" 2>&1 || true
}

safe_name() {
    printf '%s' "$1" | sed 's#[^A-Za-z0-9_.-]#_#g; s#^_*##'
}

sample_topic() {
    local topic="$1"
    local name
    name="$(safe_name "${topic}")"
    [[ -z "${name}" ]] && name="root"
    run_capture "ros2_topic_info_${name}" ros2 topic info "${topic}" --verbose
    if command -v timeout >/dev/null 2>&1; then
        run_capture "ros2_topic_hz_${name}" timeout "${DURATION_S}" ros2 topic hz "${topic}"
    else
        log "- ros2_topic_hz_${name}: skipped, timeout command not found"
    fi
}

log "# OSRacer Jetson Sensor Preflight"
log ""
log "Output directory: ${OUTPUT_DIR}"
log "Duration per topic: ${DURATION_S}s"
log ""

run_capture uname uname -a
if [[ -r /etc/nv_tegra_release ]]; then
    run_capture nv_tegra_release head -1 /etc/nv_tegra_release
fi
if [[ -r /proc/device-tree/model ]]; then
    run_capture device_tree_model tr -d '\0' /proc/device-tree/model
fi
run_capture date date --iso-8601=seconds
run_capture ls_dev_video bash -lc 'ls -l /dev/video* 2>/dev/null || true'
run_capture ls_dev_osrbot bash -lc 'ls -l /dev/osrbot_base /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true'
run_capture usb_devices bash -lc 'lsusb 2>/dev/null || true'
run_capture network_brief bash -lc 'ip -br addr 2>/dev/null || true'
run_capture route_table bash -lc 'ip route 2>/dev/null || true'

if command -v v4l2-ctl >/dev/null 2>&1; then
    run_capture v4l2_devices v4l2-ctl --list-devices
    shopt -s nullglob
    for dev in /dev/video*; do
        base="$(safe_name "${dev}")"
        run_capture "v4l2_${base}_all" v4l2-ctl --device "${dev}" --all
        run_capture "v4l2_${base}_formats" v4l2-ctl --device "${dev}" --list-formats-ext
    done
    shopt -u nullglob
else
    log "- v4l2: v4l2-ctl not found; install v4l-utils to inspect camera formats"
fi

if command -v ethtool >/dev/null 2>&1; then
    for iface in $(ls /sys/class/net 2>/dev/null || true); do
        [[ "${iface}" == "lo" ]] && continue
        run_capture "ethtool_${iface}" ethtool "${iface}"
    done
else
    log "- ethtool: not found; install ethtool to inspect lidar Ethernet link speed"
fi

if [[ -f "/opt/ros/${ROS_DISTRO_NAME}/setup.bash" ]]; then
    set +u
    # shellcheck disable=SC1090
    source "/opt/ros/${ROS_DISTRO_NAME}/setup.bash"
    set -u
    run_capture ros2_doctor ros2 doctor --report
    run_capture ros2_topic_list ros2 topic list -t
    sample_topic "${CAMERA_TOPIC}"
    sample_topic "${LIDAR_TOPIC}"
    sample_topic "${IMU_TOPIC}"
    sample_topic "${ODOM_TOPIC}"
    for topic in "${EXTRA_TOPICS[@]}"; do
        sample_topic "${topic}"
    done
else
    log "- ros2: setup not found at /opt/ros/${ROS_DISTRO_NAME}/setup.bash"
fi

log ""
log "## Next checks"
log "- Confirm camera topic matches AR0234 runtime resolution/fps target."
log "- Confirm lidar topic exists, scan frame is laser, and measured hz matches the configured scan rate."
log "- Confirm IMU and odom topic rates are stable before passive policy logging."
log "- Attach this output directory to the real-car measurement record."

printf '[OK] wrote sensor preflight logs to %s\n' "${OUTPUT_DIR}"
