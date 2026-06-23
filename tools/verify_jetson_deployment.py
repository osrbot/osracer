#!/usr/bin/env python3
"""Verify an OSRacer Jetson deployment package before launch."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Verify OSRacer Jetson deployment package.")
    parser.add_argument("package_dir", help="Directory containing manifest.json and SHA256SUMS")
    parser.add_argument("--skip-policy-load", action="store_true", help="Skip TorchScript load/run check")
    parser.add_argument("--obs-dim", type=int, default=14, help="Policy observation dimension for TorchScript smoke")
    return parser.parse_args()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ok(message):
    print(f"[OK] {message}")


def fail(message, failures):
    print(f"[FAIL] {message}")
    failures.append(message)


def load_json(path, failures):
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        fail(f"Cannot parse {path.name}: {exc}", failures)
        return {}


def parse_sha256s(path, failures):
    expected = {}
    if not path.is_file():
        fail("SHA256SUMS missing", failures)
        return expected
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            fail(f"Bad SHA256SUMS line {line_number}: {line}", failures)
            continue
        digest, name = parts
        expected[name.strip()] = digest.strip()
    return expected


def check_sha256s(package_dir, expected, failures):
    for name, expected_digest in sorted(expected.items()):
        path = package_dir / name
        if not path.is_file():
            fail(f"Checksum target missing: {name}", failures)
            continue
        actual = sha256(path)
        if actual == expected_digest:
            ok(f"sha256 {name}")
        else:
            fail(f"sha256 mismatch for {name}: {actual} != {expected_digest}", failures)


def check_manifest(package_dir, manifest, expected_sha, failures):
    artifacts = manifest.get("artifacts", {})
    if not artifacts:
        fail("manifest artifacts missing", failures)
        return None
    policy_name = None
    for name, metadata in sorted(artifacts.items()):
        path = package_dir / name
        if not path.is_file():
            fail(f"manifest artifact missing on disk: {name}", failures)
            continue
        if metadata.get("bytes") != path.stat().st_size:
            fail(f"manifest byte count mismatch for {name}", failures)
        else:
            ok(f"manifest bytes {name}")
        if metadata.get("sha256") != sha256(path):
            fail(f"manifest sha256 mismatch for {name}", failures)
        else:
            ok(f"manifest sha256 {name}")
        if name in expected_sha and expected_sha[name] != metadata.get("sha256"):
            fail(f"SHA256SUMS disagrees with manifest for {name}", failures)
        if name not in {"hardware_params.json", "manifest.json", "README.md", "SHA256SUMS"} and policy_name is None:
            policy_name = name
    return policy_name


def check_hardware_contract(package_dir, manifest, failures):
    hardware = load_json(package_dir / "hardware_params.json", failures)
    if not hardware:
        return
    contract = manifest.get("runtime_contract", {})
    chassis = hardware.get("chassis", {})
    runtime = hardware.get("real_runtime", {})
    comparisons = {
        "initial_max_speed_mps": chassis.get("initial_real_test_max_speed_mps"),
        "max_steering_rad": chassis.get("simulation_max_steering_rad"),
        "ackermann_topic": runtime.get("ackermann_cmd_topic"),
    }
    for key, actual in comparisons.items():
        expected = contract.get(key)
        if actual == expected:
            ok(f"runtime_contract {key}: {actual}")
        else:
            fail(f"runtime_contract {key}: hardware={actual!r} manifest={expected!r}", failures)


def check_policy_load(path, obs_dim, failures):
    code = f"""
import sys
import torch
path = sys.argv[1]
model = torch.jit.load(path, map_location='cpu')
model.eval()
obs = torch.zeros(1, {obs_dim})
with torch.inference_mode():
    out = model(obs)
print(tuple(out.shape))
"""
    try:
        output = subprocess.check_output([sys.executable, "-c", code, str(path)], text=True, stderr=subprocess.STDOUT)
        ok(f"TorchScript load/run {path.name}: output_shape={output.strip()}")
    except Exception as exc:
        fail(f"TorchScript load/run failed for {path.name}: {exc}", failures)


def main():
    args = parse_args()
    package_dir = Path(args.package_dir).resolve()
    failures = []
    if not package_dir.is_dir():
        raise NotADirectoryError(package_dir)

    manifest_path = package_dir / "manifest.json"
    sha_path = package_dir / "SHA256SUMS"
    if not manifest_path.is_file():
        fail("manifest.json missing", failures)
        manifest = {}
    else:
        manifest = load_json(manifest_path, failures)

    expected_sha = parse_sha256s(sha_path, failures)
    check_sha256s(package_dir, expected_sha, failures)
    policy_name = check_manifest(package_dir, manifest, expected_sha, failures)
    check_hardware_contract(package_dir, manifest, failures)

    if policy_name:
        policy_path = package_dir / policy_name
        if args.skip_policy_load:
            ok(f"policy load skipped: {policy_name}")
        else:
            check_policy_load(policy_path, args.obs_dim, failures)
    else:
        fail("policy artifact not found in manifest", failures)

    if failures:
        print(f"[FAIL] deployment package verification failed: {len(failures)} issue(s)")
        return 1
    print("[OK] deployment package verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
