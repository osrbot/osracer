#!/usr/bin/env bash
set -u

DURATION_S=60
OUTPUT_DIR="${TMPDIR:-/tmp}/osracer_runtime_monitor"
ROS_DISTRO_NAME="${ROS_DISTRO:-jazzy}"
TOPICS=("/ackermann_cmd" "/odometry/filtered" "/imu_filter" "/rgb/image_raw")
PROCESS_PATTERNS=("policy_inference.py" "chassis_ackermann.py" "usb_cam" "ros2")

usage() {
    cat <<'EOF'
Usage: tools/jetson_runtime_monitor.sh [options]

Options:
  --duration SEC       Sampling window for topic/process checks (default: 60)
  --output-dir PATH    Directory for monitor logs (default: /tmp/osracer_runtime_monitor)
  --topic NAME         Add ROS topic to sample with ros2 topic hz
  --process PATTERN    Add process pattern for ps resource snapshots

This script is read-only. It does not change nvpmodel, clocks, ROS nodes, or policy state.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --duration)
            DURATION_S="${2:-}"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="${2:-}"
            shift 2
            ;;
        --topic)
            TOPICS+=("${2:-}")
            shift 2
            ;;
        --process)
            PROCESS_PATTERNS+=("${2:-}")
            shift 2
            ;;
        --help|-h)
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

if ! [[ "${DURATION_S}" =~ ^[0-9]+$ ]] || [[ "${DURATION_S}" -le 0 ]]; then
    echo "[FAIL] --duration must be a positive integer" >&2
    exit 2
fi

mkdir -p "${OUTPUT_DIR}"
SUMMARY_LOG="${OUTPUT_DIR}/summary.log"
TEGRASTATS_LOG="${OUTPUT_DIR}/tegrastats.log"
TOPIC_LOG="${OUTPUT_DIR}/topic_hz.log"
PROCESS_LOG="${OUTPUT_DIR}/process_resources.log"

log() {
    printf '%s %s\n' "$(date -Is)" "$*" | tee -a "${SUMMARY_LOG}"
}

run_topic_hz() {
    local topic="$1"
    if ! command -v ros2 >/dev/null 2>&1; then
        log "[WARN] ros2 not found; skipping topic hz"
        return
    fi
    {
        echo "== ${topic} =="
        timeout "${DURATION_S}" ros2 topic hz "${topic}" 2>&1 || true
        echo
    } >>"${TOPIC_LOG}"
}

snapshot_processes() {
    {
        echo "== $(date -Is) =="
        for pattern in "${PROCESS_PATTERNS[@]}"; do
            echo "-- ${pattern} --"
            ps -eo pid,ppid,pcpu,pmem,rss,etime,cmd | awk -v pat="${pattern}" 'NR == 1 || (index($0, pat) > 0 && index($0, "awk -v pat=") == 0)'
        done
        echo
    } >>"${PROCESS_LOG}"
}

: >"${SUMMARY_LOG}"
: >"${TOPIC_LOG}"
: >"${PROCESS_LOG}"

log "OSRacer Jetson runtime monitor start"
log "duration_s=${DURATION_S}"
log "output_dir=${OUTPUT_DIR}"

if [[ -f "/opt/ros/${ROS_DISTRO_NAME}/setup.bash" ]]; then
    set +u
    # shellcheck disable=SC1090
    source "/opt/ros/${ROS_DISTRO_NAME}/setup.bash"
    set -u
    log "[OK] sourced ROS: /opt/ros/${ROS_DISTRO_NAME}/setup.bash"
else
    log "[WARN] ROS setup not found: /opt/ros/${ROS_DISTRO_NAME}/setup.bash"
fi

if command -v tegrastats >/dev/null 2>&1; then
    log "[OK] starting tegrastats"
    timeout "${DURATION_S}" tegrastats --interval 1000 >"${TEGRASTATS_LOG}" 2>&1 &
    TEGRA_PID=$!
else
    log "[WARN] tegrastats not found"
    TEGRA_PID=""
fi

snapshot_processes
for topic in "${TOPICS[@]}"; do
    run_topic_hz "${topic}"
done
snapshot_processes

if [[ -n "${TEGRA_PID}" ]]; then
    wait "${TEGRA_PID}" || true
fi

log "logs:"
log "  summary=${SUMMARY_LOG}"
log "  tegrastats=${TEGRASTATS_LOG}"
log "  topic_hz=${TOPIC_LOG}"
log "  process_resources=${PROCESS_LOG}"
log "OSRacer Jetson runtime monitor complete"
