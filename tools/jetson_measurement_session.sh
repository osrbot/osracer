#!/usr/bin/env bash
set -u

TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${TMPDIR:-/tmp}/osracer_measurement_session_$(date +%Y%m%d_%H%M%S)"
SENSOR_DURATION=10
SERIAL_SAMPLES=5
ROS_DISTRO_NAME="${ROS_DISTRO:-jazzy}"
SKIP_SENSOR=0
SKIP_SERIAL=0
SKIP_ENVIRONMENT=0

usage() {
    cat <<'EOF'
Usage: tools/jetson_measurement_session.sh [options]

Run a read-only real-car evidence collection session on Jetson. It combines
sensor topic preflight, Jetson environment, and serial query latency into one
output directory with a measurement_session.json manifest.

Options:
  --output-dir DIR       Output directory. Default: /tmp/osracer_measurement_session_<timestamp>.
  --sensor-duration SEC  Seconds per ros2 topic hz sample. Default: 10.
  --serial-samples N     Read-only serial query samples. Default: 5.
  --ros-distro NAME      ROS distro for sensor preflight. Default: $ROS_DISTRO or jazzy.
  --skip-sensor          Do not run tools/jetson_sensor_preflight.sh.
  --skip-environment     Do not run tools/jetson_environment_report.py.
  --skip-serial          Do not run tools/serial_latency_probe.py.
  -h, --help             Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir)
            OUTPUT_DIR="${2:-}"
            shift 2
            ;;
        --sensor-duration)
            SENSOR_DURATION="${2:-}"
            shift 2
            ;;
        --serial-samples)
            SERIAL_SAMPLES="${2:-}"
            shift 2
            ;;
        --ros-distro)
            ROS_DISTRO_NAME="${2:-}"
            shift 2
            ;;
        --skip-sensor)
            SKIP_SENSOR=1
            shift
            ;;
        --skip-environment)
            SKIP_ENVIRONMENT=1
            shift
            ;;
        --skip-serial)
            SKIP_SERIAL=1
            shift
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
SESSION_LOG="${OUTPUT_DIR}/session.log"
: >"${SESSION_LOG}"
log() { printf '%s\n' "$*" | tee -a "${SESSION_LOG}"; }

SENSOR_DIR="${OUTPUT_DIR}/sensor_preflight"
SENSOR_SUMMARY="${SENSOR_DIR}/sensor_summary.json"
ENVIRONMENT_REPORT="${OUTPUT_DIR}/jetson_environment.json"
SERIAL_REPORT="${OUTPUT_DIR}/serial_latency.json"
SENSOR_STATUS="skipped"
ENVIRONMENT_STATUS="skipped"
SERIAL_STATUS="skipped"

log "# OSRacer Jetson Measurement Session"
log "output_dir=${OUTPUT_DIR}"
log "started_at=$(date --iso-8601=seconds 2>/dev/null || date)"

if [[ "${SKIP_SENSOR}" -eq 0 ]]; then
    if [[ -x "${TOOLS_DIR}/jetson_sensor_preflight.sh" ]]; then
        log "running sensor preflight"
        if "${TOOLS_DIR}/jetson_sensor_preflight.sh" \
            --output-dir "${SENSOR_DIR}" \
            --duration "${SENSOR_DURATION}" \
            --ros-distro "${ROS_DISTRO_NAME}" >>"${SESSION_LOG}" 2>&1; then
            SENSOR_STATUS="pass"
        else
            SENSOR_STATUS="fail"
        fi
    else
        log "sensor preflight tool not executable"
        SENSOR_STATUS="missing_tool"
    fi
fi

if [[ "${SKIP_ENVIRONMENT}" -eq 0 ]]; then
    if [[ -x "${TOOLS_DIR}/jetson_environment_report.py" ]]; then
        log "running Jetson environment report"
        if "${TOOLS_DIR}/jetson_environment_report.py" \
            --ros-distro "${ROS_DISTRO_NAME}" \
            --output "${ENVIRONMENT_REPORT}" >>"${SESSION_LOG}" 2>&1; then
            ENVIRONMENT_STATUS="pass"
        else
            ENVIRONMENT_STATUS="fail"
        fi
    else
        log "Jetson environment report tool not executable"
        ENVIRONMENT_STATUS="missing_tool"
    fi
fi

if [[ "${SKIP_SERIAL}" -eq 0 ]]; then
    if [[ -x "${TOOLS_DIR}/serial_latency_probe.py" ]]; then
        log "running read-only serial latency probe"
        if "${TOOLS_DIR}/serial_latency_probe.py" \
            --samples "${SERIAL_SAMPLES}" \
            --output "${SERIAL_REPORT}" >>"${SESSION_LOG}" 2>&1; then
            SERIAL_STATUS="pass"
        else
            SERIAL_STATUS="fail"
        fi
    else
        log "serial latency probe not executable"
        SERIAL_STATUS="missing_tool"
    fi
fi

MANIFEST="${OUTPUT_DIR}/measurement_session.json"
python3 - "${MANIFEST}" "${OUTPUT_DIR}" "${SENSOR_STATUS}" "${SENSOR_SUMMARY}" "${ENVIRONMENT_STATUS}" "${ENVIRONMENT_REPORT}" "${SERIAL_STATUS}" "${SERIAL_REPORT}" <<'PY'
import datetime as dt
import json
import sys
from pathlib import Path

manifest, output_dir, sensor_status, sensor_summary, environment_status, environment_report, serial_status, serial_report = sys.argv[1:]
def existing(path):
    p = Path(path)
    return str(p) if p.exists() else None

data = {
    "schema_version": 1,
    "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    "output_dir": str(Path(output_dir).resolve()),
    "tools": {
        "sensor_preflight": {
            "status": sensor_status,
            "sensor_summary": existing(sensor_summary),
        },
        "jetson_environment": {
            "status": environment_status,
            "environment_report": existing(environment_report),
        },
        "serial_latency": {
            "status": serial_status,
            "serial_report": existing(serial_report),
        },
    },
    "import_hint": "Use osracer_lab: MEASUREMENTS_FILE=docs/real_car_measurements.json MEASUREMENT_SESSION_FILE=<this file> scripts/validate_osracer_lab.sh import-measurement-session",
}
Path(manifest).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
print(f"wrote {manifest}")
PY

log "finished_at=$(date --iso-8601=seconds 2>/dev/null || date)"
log "manifest=${MANIFEST}"
printf '[OK] measurement session output: %s\n' "${OUTPUT_DIR}"
