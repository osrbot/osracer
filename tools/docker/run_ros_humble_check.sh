#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
IMAGE_NAME="${OSRACER_ROS_CHECK_IMAGE:-osracer-ros-check:humble}"
BUILD_PROFILE="${OSRACER_BUILD_PROFILE:-stable}"
BUILD_PACKAGES="${OSRACER_BUILD_PACKAGES:-}"

echo "[1/2] Build Docker image: ${IMAGE_NAME}"
docker build \
  -f "${SCRIPT_DIR}/ros_humble_check.Dockerfile" \
  -t "${IMAGE_NAME}" \
  "${SCRIPT_DIR}"

echo "[2/2] Run ROS Humble workspace check"
docker run --rm \
  --mount "type=bind,source=${REPO_ROOT},target=/src/osracer,readonly" \
  --env "OSRACER_BUILD_PROFILE=${BUILD_PROFILE}" \
  --env "OSRACER_BUILD_PACKAGES=${BUILD_PACKAGES}" \
  "${IMAGE_NAME}"
