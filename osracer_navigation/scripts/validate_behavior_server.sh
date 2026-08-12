#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <nav2-params.yaml>" >&2
  exit 2
fi

params_file=$1
if [[ ! -f "$params_file" ]]; then
  echo "Nav2 parameter file not found: $params_file" >&2
  exit 2
fi

log_file=$(mktemp)
server_pid=

cleanup() {
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill -INT "$server_pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      if ! kill -0 "$server_pid" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
    if kill -0 "$server_pid" 2>/dev/null; then
      kill -TERM "$server_pid" 2>/dev/null || true
      for _ in $(seq 1 20); do
        if ! kill -0 "$server_pid" 2>/dev/null; then
          break
        fi
        sleep 0.1
      done
    fi
    if kill -0 "$server_pid" 2>/dev/null; then
      kill -KILL "$server_pid" 2>/dev/null || true
    fi
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -f "$log_file"
}
trap cleanup EXIT

ros2 run nav2_behaviors behavior_server \
  --ros-args --params-file "$params_file" >"$log_file" 2>&1 &
server_pid=$!

for _ in $(seq 1 50); do
  if ! kill -0 "$server_pid" 2>/dev/null; then
    cat "$log_file" >&2
    exit 1
  fi
  if ros2 lifecycle get /behavior_server 2>/dev/null | grep -q "unconfigured"; then
    break
  fi
  sleep 0.2
done

ros2 lifecycle get /behavior_server | grep -q "unconfigured"
ros2 lifecycle set /behavior_server configure
ros2 lifecycle get /behavior_server | grep -q "inactive"
ros2 lifecycle set /behavior_server activate
ros2 lifecycle get /behavior_server | grep -q "active"

echo "Behavior Server configure/activate passed: $params_file"
