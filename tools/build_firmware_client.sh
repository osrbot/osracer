#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
host_os="$(uname -s)"
host_arch="$(uname -m)"

if [[ "${OSRACER_ALLOW_HOST_BUILD:-0}" != "1" ]]; then
  if [[ "$host_os" != "Linux" || "$host_arch" != "aarch64" ]]; then
    echo "Formal customer builds must run on Linux aarch64." >&2
    echo "Set OSRACER_ALLOW_HOST_BUILD=1 only for a local packaging smoke test." >&2
    exit 2
  fi
fi

build_root="${OSRACER_FIRMWARE_CLIENT_BUILD_ROOT:-}"
created_build_root=0
if [[ -z "$build_root" ]]; then
  build_root="$(mktemp -d "${TMPDIR:-/tmp}/osracer-firmware-client.XXXXXX")"
  created_build_root=1
fi

cleanup() {
  if [[ "$created_build_root" == "1" ]]; then
    rm -rf -- "$build_root"
  fi
}
trap cleanup EXIT

python3 -m venv "$build_root/venv"
"$build_root/venv/bin/python" -m pip install --disable-pip-version-check \
  --requirement "$project_root/requirements-firmware-client-build.txt"

rm -rf -- "$project_root/dist/firmware-client" "$build_root/pyinstaller"
mkdir -p "$project_root/dist/firmware-client" "$build_root/pyinstaller"

"$build_root/venv/bin/python" \
  "$project_root/tools/generate_firmware_client_build_info.py" \
  --root "$project_root" \
  --output "$build_root/BUILD_INFO.json"

(
  cd "$project_root"
  OSRACER_FIRMWARE_CLIENT_BUILD_INFO="$build_root/BUILD_INFO.json" \
    "$build_root/venv/bin/python" -m PyInstaller \
    --clean \
    --noconfirm \
    --distpath "$project_root/dist/firmware-client" \
    --workpath "$build_root/pyinstaller" \
    firmware_client.spec
)

artifact="$project_root/dist/firmware-client/osracer-firmware-client"
test -x "$artifact"
"$artifact" --version
"$artifact" build-info >/dev/null
"$artifact" licenses >/dev/null
"$artifact" bundles >/dev/null
"$artifact" --help >/dev/null

(
  cd "$project_root/dist/firmware-client"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum osracer-firmware-client > osracer-firmware-client.sha256
  else
    shasum -a 256 osracer-firmware-client > osracer-firmware-client.sha256
  fi
)

echo "Built: $artifact"
cat "$project_root/dist/firmware-client/osracer-firmware-client.sha256"
