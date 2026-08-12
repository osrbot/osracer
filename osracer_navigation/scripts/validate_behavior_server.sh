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

if ! python3 - <<'PY'
import time

import rclpy
from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState, GetState


TIMEOUT_S = 20.0
deadline = time.monotonic() + TIMEOUT_S


def remaining():
    return max(0.0, deadline - time.monotonic())


def call(node, client, request, operation):
    if not client.wait_for_service(timeout_sec=remaining()):
        raise RuntimeError(f"timed out waiting for {operation} service")
    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=remaining())
    if not future.done():
        raise RuntimeError(f"timed out waiting for {operation} response")
    result = future.result()
    if result is None:
        raise RuntimeError(f"{operation} returned no response")
    return result


def get_state(node, client):
    return call(node, client, GetState.Request(), "get_state").current_state.label


def change_state(node, client, transition_id, operation):
    request = ChangeState.Request()
    request.transition.id = transition_id
    result = call(node, client, request, operation)
    if not result.success:
        raise RuntimeError(f"{operation} transition was rejected")


rclpy.init()
node = rclpy.create_node("osracer_behavior_server_validator")
try:
    get_state_client = node.create_client(
        GetState, "/behavior_server/get_state"
    )
    change_state_client = node.create_client(
        ChangeState, "/behavior_server/change_state"
    )

    state = get_state(node, get_state_client)
    if state != "unconfigured":
        raise RuntimeError(f"expected unconfigured, got {state}")

    change_state(
        node,
        change_state_client,
        Transition.TRANSITION_CONFIGURE,
        "configure",
    )
    state = get_state(node, get_state_client)
    if state != "inactive":
        raise RuntimeError(f"expected inactive, got {state}")

    change_state(
        node,
        change_state_client,
        Transition.TRANSITION_ACTIVATE,
        "activate",
    )
    state = get_state(node, get_state_client)
    if state != "active":
        raise RuntimeError(f"expected active, got {state}")
finally:
    node.destroy_node()
    rclpy.shutdown()
PY
then
  cat "$log_file" >&2
  exit 1
fi

echo "Behavior Server configure/activate passed: $params_file"
