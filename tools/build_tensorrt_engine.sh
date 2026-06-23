#!/usr/bin/env bash
set -euo pipefail

ONNX_PATH=""
ENGINE_PATH=""
FP16=0
WORKSPACE_MB=1024
MIN_BATCH=1
OPT_BATCH=1
MAX_BATCH=1
DRY_RUN=0
LOG_PATH=""
REPORT_PATH=""
EXTRA_ARGS=()

fail() { printf '[FAIL] %s\n' "$*" >&2; }
ok() { printf '[OK] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }

usage() {
    cat <<'EOF'
Usage: tools/build_tensorrt_engine.sh --onnx policy.onnx --engine policy.engine [options]

Options:
  --onnx PATH          Input ONNX policy artifact.
  --engine PATH        Output TensorRT engine path.
  --fp16               Build FP16 engine.
  --workspace-mb MB    TensorRT workspace memory in MiB; default: 1024.
  --min-batch N        Dynamic batch minimum; default: 1.
  --opt-batch N        Dynamic batch optimum; default: 1.
  --max-batch N        Dynamic batch maximum; default: 1.
  --trtexec-arg ARG    Extra argument passed to trtexec; repeatable.
  --log PATH           Save trtexec output to PATH.
  --report PATH        Save TensorRT build report JSON.
  --dry-run            Print the trtexec command without executing it.
  -h, --help           Show this help.

Example:
  tools/build_tensorrt_engine.sh \
    --onnx policy.onnx \
    --engine policy_fp16.engine \
    --fp16 \
    --workspace-mb 1024
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --onnx)
            ONNX_PATH="${2:-}"
            shift 2
            ;;
        --engine)
            ENGINE_PATH="${2:-}"
            shift 2
            ;;
        --fp16)
            FP16=1
            shift
            ;;
        --workspace-mb)
            WORKSPACE_MB="${2:-}"
            shift 2
            ;;
        --min-batch)
            MIN_BATCH="${2:-}"
            shift 2
            ;;
        --opt-batch)
            OPT_BATCH="${2:-}"
            shift 2
            ;;
        --max-batch)
            MAX_BATCH="${2:-}"
            shift 2
            ;;
        --trtexec-arg)
            EXTRA_ARGS+=("${2:-}")
            shift 2
            ;;
        --log)
            LOG_PATH="${2:-}"
            shift 2
            ;;
        --report)
            REPORT_PATH="${2:-}"
            if [[ -z "${REPORT_PATH}" ]]; then
                fail "--report requires a path"
                exit 2
            fi
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Unknown argument: $1"
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "${ONNX_PATH}" || -z "${ENGINE_PATH}" ]]; then
    fail "--onnx and --engine are required"
    usage >&2
    exit 2
fi
if [[ ! -f "${ONNX_PATH}" ]]; then
    fail "ONNX file not found: ${ONNX_PATH}"
    exit 1
fi
if ! [[ "${WORKSPACE_MB}" =~ ^[0-9]+$ && "${MIN_BATCH}" =~ ^[0-9]+$ && "${OPT_BATCH}" =~ ^[0-9]+$ && "${MAX_BATCH}" =~ ^[0-9]+$ ]]; then
    fail "workspace and batch values must be integers"
    exit 2
fi
if (( MIN_BATCH < 1 || OPT_BATCH < MIN_BATCH || MAX_BATCH < OPT_BATCH )); then
    fail "Batch sizes must satisfy 1 <= min <= opt <= max"
    exit 2
fi

mkdir -p "$(dirname "${ENGINE_PATH}")"
cmd=(trtexec
    --onnx="${ONNX_PATH}"
    --saveEngine="${ENGINE_PATH}"
    --memPoolSize="workspace:${WORKSPACE_MB}"
    --minShapes="obs:${MIN_BATCH}x14"
    --optShapes="obs:${OPT_BATCH}x14"
    --maxShapes="obs:${MAX_BATCH}x14"
)
if [[ "${FP16}" -eq 1 ]]; then
    cmd+=(--fp16)
fi
cmd+=("${EXTRA_ARGS[@]}")


write_report() {
    local exit_code="$1"
    local status="$2"
    if [[ -z "${REPORT_PATH}" ]]; then
        return 0
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        fail "python3 not found; cannot write --report ${REPORT_PATH}"
        return 1
    fi
    OSRACER_TRT_ONNX="${ONNX_PATH}" \
    OSRACER_TRT_ENGINE="${ENGINE_PATH}" \
    OSRACER_TRT_FP16="${FP16}" \
    OSRACER_TRT_WORKSPACE_MB="${WORKSPACE_MB}" \
    OSRACER_TRT_MIN_BATCH="${MIN_BATCH}" \
    OSRACER_TRT_OPT_BATCH="${OPT_BATCH}" \
    OSRACER_TRT_MAX_BATCH="${MAX_BATCH}" \
    OSRACER_TRT_DRY_RUN="${DRY_RUN}" \
    OSRACER_TRT_LOG="${LOG_PATH}" \
    OSRACER_TRT_EXIT_CODE="${exit_code}" \
    OSRACER_TRT_STATUS="${status}" \
    python3 - "${REPORT_PATH}" "${cmd[@]}" <<'PY_REPORT'
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def file_info(path):
    if not path:
        return {'path': None, 'exists': False}
    p = Path(path)
    info = {'path': str(p), 'exists': p.is_file()}
    if p.is_file():
        h = hashlib.sha256()
        with p.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                h.update(chunk)
        info.update({'bytes': p.stat().st_size, 'sha256': h.hexdigest()})
    return info

report_path = Path(sys.argv[1])
command = sys.argv[2:]
report = {
    'schema_version': 1,
    'created_at': datetime.now(timezone.utc).isoformat(),
    'status': os.environ.get('OSRACER_TRT_STATUS'),
    'exit_code': int(os.environ.get('OSRACER_TRT_EXIT_CODE', '0')),
    'dry_run': os.environ.get('OSRACER_TRT_DRY_RUN') == '1',
    'command': command,
    'trtexec': {'present': shutil.which('trtexec') is not None, 'path': shutil.which('trtexec')},
    'build': {
        'fp16': os.environ.get('OSRACER_TRT_FP16') == '1',
        'workspace_mb': int(os.environ.get('OSRACER_TRT_WORKSPACE_MB', '0')),
        'min_batch': int(os.environ.get('OSRACER_TRT_MIN_BATCH', '0')),
        'opt_batch': int(os.environ.get('OSRACER_TRT_OPT_BATCH', '0')),
        'max_batch': int(os.environ.get('OSRACER_TRT_MAX_BATCH', '0')),
    },
    'onnx': file_info(os.environ.get('OSRACER_TRT_ONNX')),
    'engine': file_info(os.environ.get('OSRACER_TRT_ENGINE')),
    'log': file_info(os.environ.get('OSRACER_TRT_LOG')),
}
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
print(f"[OK] TensorRT build report: {report_path}")
PY_REPORT
}

printf '[INFO] trtexec command:'
printf ' %q' "${cmd[@]}"
printf '\n'

if [[ "${DRY_RUN}" -eq 1 ]]; then
    write_report 0 "dry-run"
    ok "dry run complete"
    exit 0
fi
if ! command -v trtexec >/dev/null 2>&1; then
    write_report 127 "fail"
    fail "trtexec not found; install TensorRT tools from JetPack before building engines"
    exit 1
fi
TRT_EXIT=0
if [[ -n "${LOG_PATH}" ]]; then
    mkdir -p "$(dirname "${LOG_PATH}")"
    set +e
    "${cmd[@]}" 2>&1 | tee "${LOG_PATH}"
    TRT_EXIT=${PIPESTATUS[0]}
    set -e
else
    set +e
    "${cmd[@]}"
    TRT_EXIT=$?
    set -e
fi
if [[ "${TRT_EXIT}" -ne 0 ]]; then
    write_report "${TRT_EXIT}" "fail"
    fail "trtexec failed with exit code ${TRT_EXIT}"
    exit "${TRT_EXIT}"
fi
if [[ -s "${ENGINE_PATH}" ]]; then
    ok "wrote TensorRT engine: ${ENGINE_PATH} ($(stat -c%s "${ENGINE_PATH}" 2>/dev/null || wc -c <"${ENGINE_PATH}") bytes)"
    write_report 0 "pass"
else
    write_report 1 "fail"
    fail "TensorRT engine was not created or is empty: ${ENGINE_PATH}"
    exit 1
fi
