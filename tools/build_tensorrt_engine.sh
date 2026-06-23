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

printf '[INFO] trtexec command:'
printf ' %q' "${cmd[@]}"
printf '\n'

if [[ "${DRY_RUN}" -eq 1 ]]; then
    ok "dry run complete"
    exit 0
fi
if ! command -v trtexec >/dev/null 2>&1; then
    fail "trtexec not found; install TensorRT tools from JetPack before building engines"
    exit 1
fi
"${cmd[@]}"
if [[ -s "${ENGINE_PATH}" ]]; then
    ok "wrote TensorRT engine: ${ENGINE_PATH} ($(stat -c%s "${ENGINE_PATH}" 2>/dev/null || wc -c <"${ENGINE_PATH}") bytes)"
else
    fail "TensorRT engine was not created or is empty: ${ENGINE_PATH}"
    exit 1
fi
