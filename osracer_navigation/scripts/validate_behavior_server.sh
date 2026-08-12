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

process_group_alive() {
  [[ -n "$server_pid" ]] && kill -0 -- "-$server_pid" 2>/dev/null
}

cleanup() {
  if process_group_alive; then
    kill -INT -- "-$server_pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      if ! process_group_alive; then
        break
      fi
      sleep 0.1
    done
    if process_group_alive; then
      kill -TERM -- "-$server_pid" 2>/dev/null || true
      for _ in $(seq 1 20); do
        if ! process_group_alive; then
          break
        fi
        sleep 0.1
      done
    fi
    if process_group_alive; then
      kill -KILL -- "-$server_pid" 2>/dev/null || true
    fi
  fi
  [[ -z "$server_pid" ]] || wait "$server_pid" 2>/dev/null || true
  rm -f "$log_file"
}
trap cleanup EXIT

setsid ros2 run nav2_behaviors behavior_server \
  --ros-args --params-file "$params_file" >"$log_file" 2>&1 &
server_pid=$!

for _ in $(seq 1 50); do
  if ! process_group_alive; then
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
