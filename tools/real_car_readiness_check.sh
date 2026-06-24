#!/usr/bin/env bash
set -u

ROS_DISTRO_NAME="${ROS_DISTRO:-humble}"
POLICY_PATH=""
OBSERVATIONS_CSV=""
REPLAY_CSV=""
REQUIRE_TOPICS=0
TOPIC_TIMEOUT=2

ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*"; }

usage() {
    cat <<'EOF'
Usage: tools/real_car_readiness_check.sh [options]

Options:
  --policy PATH          TorchScript policy.pt to check
  --observations PATH    Recorded observation CSV to check
  --replay PATH          policy_replay.csv to summarize
  --require-topics       Treat missing ROS topics/messages as failures
  --topic-timeout SEC    Wait time for one message from odom/IMU topics (default: 2)
  -h, --help             Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --policy)
            POLICY_PATH="${2:-}"
            shift 2
            ;;
        --observations)
            OBSERVATIONS_CSV="${2:-}"
            shift 2
            ;;
        --replay)
            REPLAY_CSV="${2:-}"
            shift 2
            ;;
        --require-topics)
            REQUIRE_TOPICS=1
            shift
            ;;
        --topic-timeout)
            TOPIC_TIMEOUT="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Unknown argument: $1"
            usage
            exit 2
            ;;
    esac
done

FAILURES=0

record_failure() {
    if [[ "${REQUIRE_TOPICS}" -eq 1 ]]; then
        FAILURES=$((FAILURES + 1))
        fail "$*"
    else
        warn "$*"
    fi
}

check_file() {
    local label="$1"
    local path="$2"
    if [[ -z "${path}" ]]; then
        warn "${label}: not supplied"
    elif [[ -f "${path}" ]]; then
        ok "${label}: ${path}"
    else
        fail "${label}: not found: ${path}"
        FAILURES=$((FAILURES + 1))
    fi
}

check_cmd() {
    if command -v "$1" >/dev/null 2>&1; then
        ok "$1: $(command -v "$1")"
    else
        fail "$1: not found"
        FAILURES=$((FAILURES + 1))
    fi
}

echo "== OSRacer real-car readiness check =="

if [[ -f "/opt/ros/${ROS_DISTRO_NAME}/setup.bash" ]]; then
    ok "ROS setup: /opt/ros/${ROS_DISTRO_NAME}/setup.bash"
    set +u
    # shellcheck disable=SC1090
    source "/opt/ros/${ROS_DISTRO_NAME}/setup.bash"
    set -u
else
    fail "ROS setup not found: /opt/ros/${ROS_DISTRO_NAME}/setup.bash"
    FAILURES=$((FAILURES + 1))
fi

check_cmd ros2
check_cmd python3

TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPLAY_TOOL="${TOOLS_DIR}/policy_replay_csv.py"
SUMMARY_TOOL="${TOOLS_DIR}/policy_replay_summary.py"
PREFLIGHT_TOOL="${TOOLS_DIR}/jetson_preflight.sh"

check_file "policy replay tool" "${REPLAY_TOOL}"
check_file "policy summary tool" "${SUMMARY_TOOL}"
check_file "jetson preflight tool" "${PREFLIGHT_TOOL}"
check_file "policy" "${POLICY_PATH}"
check_file "observations CSV" "${OBSERVATIONS_CSV}"
check_file "policy replay CSV" "${REPLAY_CSV}"

if [[ -n "${REPLAY_CSV}" && -f "${REPLAY_CSV}" && -x "${SUMMARY_TOOL}" ]]; then
    python3 "${SUMMARY_TOOL}" "${REPLAY_CSV}" \
        --max-speed-cmd 0.3 \
        --max-abs-steering-cmd 0.488 || FAILURES=$((FAILURES + 1))
fi

echo "-- ROS topics --"
if command -v ros2 >/dev/null 2>&1; then
    TOPICS="$(ros2 topic list 2>/dev/null || true)"
    for topic in /odometry/filtered /imu_filter /ackermann_cmd; do
        if printf '%s\n' "${TOPICS}" | grep -qx "${topic}"; then
            ok "topic present: ${topic}"
        else
            record_failure "topic missing: ${topic}"
        fi
    done

    for topic in /odometry/filtered /imu_filter; do
        if printf '%s\n' "${TOPICS}" | grep -qx "${topic}"; then
            if timeout "${TOPIC_TIMEOUT}" ros2 topic echo --once "${topic}" >/dev/null 2>&1; then
                ok "message received: ${topic}"
            else
                record_failure "no message within ${TOPIC_TIMEOUT}s: ${topic}"
            fi
        fi
    done
else
    record_failure "ros2 command unavailable; skipping topic checks"
fi

if [[ "${FAILURES}" -gt 0 ]]; then
    fail "readiness check failed: ${FAILURES} issue(s)"
    exit 1
fi

ok "readiness check complete"
