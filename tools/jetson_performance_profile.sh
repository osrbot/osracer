#!/usr/bin/env bash
set -u

APPLY=0
NVP_MODEL=""
RUN_JETSON_CLOCKS=0
CPU_GOVERNOR="performance"
SET_CPU_GOVERNOR=0
JSON_OUTPUT=""

ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }

usage() {
    cat <<'EOF'
Usage: tools/jetson_performance_profile.sh [options]

Default is read-only. Use --apply to change Jetson runtime settings.

Options:
  --apply                 Apply requested settings.
  --nvpmodel MODE_ID      Run nvpmodel -m MODE_ID when applying.
  --jetson-clocks         Run jetson_clocks when applying.
  --cpu-governor GOV      Target CPU governor; default: performance.
  --set-cpu-governor      Apply CPU governor to writable cpu*/cpufreq nodes.
  --json-output PATH       Write machine-readable profile evidence JSON.
  -h, --help              Show this help.

Examples:
  tools/jetson_performance_profile.sh
  tools/jetson_performance_profile.sh --apply --nvpmodel 0 --jetson-clocks --set-cpu-governor --json-output /tmp/osracer_performance_profile.json
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply)
            APPLY=1
            shift
            ;;
        --nvpmodel)
            NVP_MODEL="${2:-}"
            if [[ -z "${NVP_MODEL}" ]]; then
                fail "--nvpmodel requires a mode id"
                exit 2
            fi
            shift 2
            ;;
        --jetson-clocks)
            RUN_JETSON_CLOCKS=1
            shift
            ;;
        --cpu-governor)
            CPU_GOVERNOR="${2:-}"
            if [[ -z "${CPU_GOVERNOR}" ]]; then
                fail "--cpu-governor requires a governor name"
                exit 2
            fi
            shift 2
            ;;
        --set-cpu-governor)
            SET_CPU_GOVERNOR=1
            shift
            ;;
        --json-output)
            JSON_OUTPUT="${2:-}"
            if [[ -z "${JSON_OUTPUT}" ]]; then
                fail "--json-output requires a path"
                exit 2
            fi
            shift 2
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

run_root() {
    if [[ "${APPLY}" -ne 1 ]]; then
        info "dry-run: $*"
        return 0
    fi
    if [[ "${EUID}" -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        fail "sudo not found; run as root to apply: $*"
        return 1
    fi
}

print_file() {
    local label="$1"
    local path="$2"
    if [[ -r "${path}" ]]; then
        ok "${label}: $(tr -d '\0' <"${path}")"
    else
        warn "${label}: not available (${path})"
    fi
}

print_meminfo() {
    local key="$1"
    local line
    line="$(awk -v key="${key}:" '$1 == key {print $2 " " $3}' /proc/meminfo 2>/dev/null || true)"
    if [[ -n "${line}" ]]; then
        ok "${key}: ${line}"
    else
        warn "${key}: not found"
    fi
}


write_json_report() {
    if [[ -z "${JSON_OUTPUT}" ]]; then
        return 0
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        fail "python3 not found; cannot write --json-output ${JSON_OUTPUT}"
        return 1
    fi
    OSRACER_APPLY="${APPLY}" \
    OSRACER_NVP_MODEL="${NVP_MODEL}" \
    OSRACER_JETSON_CLOCKS="${RUN_JETSON_CLOCKS}" \
    OSRACER_CPU_GOVERNOR="${CPU_GOVERNOR}" \
    OSRACER_SET_CPU_GOVERNOR="${SET_CPU_GOVERNOR}" \
    python3 - "${JSON_OUTPUT}" <<'PY_JSON'
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def read_text(path):
    try:
        return Path(path).read_text(encoding='utf-8', errors='replace').replace('\x00', '').strip()
    except OSError:
        return None


def cmd_info(name):
    path = shutil.which(name)
    return {'present': path is not None, 'path': path}


def run_capture(command):
    try:
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        return {'ok': False, 'output': [str(exc)]}
    return {'ok': result.returncode == 0, 'exit_code': result.returncode, 'output': result.stdout.splitlines()}


def meminfo():
    data = {}
    text = read_text('/proc/meminfo')
    if not text:
        return data
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            key = parts[0].rstrip(':')
            if key in {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}:
                data[key] = {'kb': int(parts[1])}
    return data


def cpu_governors(target):
    paths = sorted(Path('/sys/devices/system/cpu').glob('cpu*/cpufreq/scaling_governor'))
    values = {str(path): read_text(path) for path in paths}
    observed = sorted({value for value in values.values() if value})
    all_match = bool(values) and all(value == target for value in values.values())
    return {'paths': values, 'observed': observed, 'requested': target, 'all_match_requested': all_match}


def zram_devices():
    root = Path('/sys/block')
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.glob('zram*'))

output = Path(sys.argv[1])
tools = {name: cmd_info(name) for name in ('nvpmodel', 'jetson_clocks', 'tegrastats')}
report = {
    'schema_version': 1,
    'created_at': datetime.now(timezone.utc).isoformat(),
    'apply_requested': os.environ.get('OSRACER_APPLY') == '1',
    'requested': {
        'nvpmodel': os.environ.get('OSRACER_NVP_MODEL') or None,
        'jetson_clocks': os.environ.get('OSRACER_JETSON_CLOCKS') == '1',
        'cpu_governor': os.environ.get('OSRACER_CPU_GOVERNOR') or None,
        'set_cpu_governor': os.environ.get('OSRACER_SET_CPU_GOVERNOR') == '1',
    },
    'tools': tools,
    'jetson': {
        'is_jetson': Path('/etc/nv_tegra_release').is_file(),
        'nv_tegra_release': read_text('/etc/nv_tegra_release'),
        'device_model': read_text('/proc/device-tree/model'),
    },
    'memory': meminfo(),
    'zram_devices': zram_devices(),
    'cpu_governors': cpu_governors(os.environ.get('OSRACER_CPU_GOVERNOR') or 'performance'),
}
if tools['nvpmodel']['present']:
    report['nvpmodel_query'] = run_capture(['nvpmodel', '-q'])
if tools['jetson_clocks']['present']:
    report['jetson_clocks_show'] = run_capture(['jetson_clocks', '--show'])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
print(f"[OK] JSON profile: {output}")
PY_JSON
}

check_cmd() {
    if command -v "$1" >/dev/null 2>&1; then
        ok "$1: $(command -v "$1")"
        return 0
    fi
    warn "$1: not found"
    return 1
}

echo "== OSRacer Jetson performance profile =="
if [[ -r /etc/nv_tegra_release ]]; then
    ok "Jetson Linux: $(head -1 /etc/nv_tegra_release)"
else
    warn "/etc/nv_tegra_release not found; running in compatibility check mode"
fi
if [[ -r /proc/device-tree/model ]]; then
    print_file "Device model" /proc/device-tree/model
fi

check_cmd nvpmodel
check_cmd jetson_clocks
check_cmd tegrastats

echo "-- Current power and clocks --"
if command -v nvpmodel >/dev/null 2>&1; then
    nvpmodel -q 2>/dev/null || warn "nvpmodel query failed; try running with sudo"
else
    warn "nvpmodel unavailable; cannot query or set power profile"
fi
if command -v jetson_clocks >/dev/null 2>&1; then
    jetson_clocks --show 2>/dev/null || warn "jetson_clocks --show failed; try running with sudo"
else
    warn "jetson_clocks unavailable; cannot lock clocks"
fi

echo "-- Memory and storage --"
print_meminfo MemTotal
print_meminfo MemAvailable
print_meminfo SwapTotal
print_meminfo SwapFree
df -h / /tmp 2>/dev/null || warn "df failed"
if [[ -r /proc/sys/vm/swappiness ]]; then
    print_file "vm.swappiness" /proc/sys/vm/swappiness
fi
if [[ -d /sys/block ]]; then
    zram_count="$(find /sys/block -maxdepth 1 -name 'zram*' 2>/dev/null | wc -l)"
    ok "zram devices: ${zram_count}"
fi

echo "-- CPU governors --"
shopt -s nullglob
cpu_governors=(/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor)
if [[ "${#cpu_governors[@]}" -eq 0 ]]; then
    warn "CPU cpufreq governors not available"
else
    for path in "${cpu_governors[@]}"; do
        print_file "${path}" "${path}"
    done
fi
shopt -u nullglob

echo "-- Requested profile --"
if [[ -n "${NVP_MODEL}" ]]; then
    info "nvpmodel target: ${NVP_MODEL}"
else
    info "nvpmodel target: unchanged; pass --nvpmodel MODE_ID after checking nvpmodel -q output"
fi
info "jetson_clocks: $([[ ${RUN_JETSON_CLOCKS} -eq 1 ]] && echo requested || echo not-requested)"
info "cpu governor: $([[ ${SET_CPU_GOVERNOR} -eq 1 ]] && echo "${CPU_GOVERNOR}" || echo unchanged)"

if [[ "${APPLY}" -ne 1 ]]; then
    write_json_report
    echo "== Dry run complete; pass --apply to change settings =="
    exit 0
fi

echo "-- Applying profile --"
if [[ -n "${NVP_MODEL}" ]]; then
    if command -v nvpmodel >/dev/null 2>&1; then
        run_root nvpmodel -m "${NVP_MODEL}"
    else
        fail "nvpmodel not found"
        exit 1
    fi
fi
if [[ "${RUN_JETSON_CLOCKS}" -eq 1 ]]; then
    if command -v jetson_clocks >/dev/null 2>&1; then
        run_root jetson_clocks
    else
        fail "jetson_clocks not found"
        exit 1
    fi
fi
if [[ "${SET_CPU_GOVERNOR}" -eq 1 ]]; then
    if [[ "${#cpu_governors[@]}" -eq 0 ]]; then
        warn "No CPU governor files to update"
    else
        for path in "${cpu_governors[@]}"; do
            run_root sh -c "printf '%s' '${CPU_GOVERNOR}' > '${path}'"
        done
    fi
fi

write_json_report
echo "== Apply complete; run tools/jetson_preflight.sh and tools/jetson_runtime_monitor.sh next =="
