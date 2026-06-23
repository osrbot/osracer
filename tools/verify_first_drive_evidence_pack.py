#!/usr/bin/env python3
"""Verify an OSRacer first-drive evidence pack after handoff or copy."""

import argparse
import hashlib
import json
import math
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description='Verify files in a first-drive evidence pack.')
    parser.add_argument('pack_dir', help='Directory produced by tools/first_drive_evidence_pack.py')
    parser.add_argument('--require-pass', action='store_true', help='Fail unless the archived first-drive gate overall is pass')
    return parser.parse_args()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path):
    with Path(path).open('r', encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f'{path} must contain a JSON object')
    return data


def ok(message):
    print(f'[OK] {message}')


def fail(message, failures):
    print(f'[FAIL] {message}')
    failures.append(message)


def number(value, label, failures, *, min_value=0.001, max_value=10000.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f'camera calibration {label} must be a number', failures)
        return None
    result = float(value)
    if not math.isfinite(result) or result < min_value or result > max_value:
        fail(f'camera calibration {label} out of range: {value!r}', failures)
        return None
    return result


def verify_file(pack_dir, rel_path, metadata, failures):
    path = pack_dir / rel_path
    if not path.is_file():
        fail(f'missing file: {rel_path}', failures)
        return
    expected_bytes = metadata.get('bytes')
    expected_sha = metadata.get('sha256')
    actual_bytes = path.stat().st_size
    actual_sha = sha256(path)
    if expected_bytes == actual_bytes:
        ok(f'bytes {rel_path}')
    else:
        fail(f'bytes mismatch {rel_path}: {actual_bytes} != {expected_bytes}', failures)
    if expected_sha == actual_sha:
        ok(f'sha256 {rel_path}')
    else:
        fail(f'sha256 mismatch {rel_path}: {actual_sha} != {expected_sha}', failures)


def verify_group(pack_dir, prefix, group, failures):
    for rel_path, metadata in sorted(group.items()):
        verify_file(pack_dir, f'{prefix}/{rel_path}' if prefix else rel_path, metadata, failures)


def expected_camera_resolution(hardware):
    runtime = hardware.get('camera_ar0234', {}).get('ros_runtime', {}) if isinstance(hardware, dict) else {}
    resolution = runtime.get('configured_resolution_px')
    if isinstance(resolution, list) and len(resolution) == 2:
        return int(resolution[0]), int(resolution[1])
    return 640, 480


def verify_camera_calibration_overlay(pack_dir, failures):
    failure_count = len(failures)
    package_dir = pack_dir / 'deployment_package'
    manifest_path = package_dir / 'manifest.json'
    if not manifest_path.is_file():
        return
    package_manifest = load_json(manifest_path)
    task = str(package_manifest.get('task', ''))
    if 'Visual' not in task:
        ok('visual camera calibration: not required')
        return

    overlay_meta = package_manifest.get('measured_overlay', {})
    if not overlay_meta.get('included'):
        fail('visual deployment package does not include measured_overlay.json', failures)
        return
    overlay_path = package_dir / overlay_meta.get('artifact', 'measured_overlay.json')
    hardware_path = package_dir / 'hardware_params.json'
    if not overlay_path.is_file():
        fail('visual deployment measured_overlay.json missing from evidence pack', failures)
        return
    if not hardware_path.is_file():
        fail('visual deployment hardware_params.json missing from evidence pack', failures)
        return

    overlay = load_json(overlay_path)
    hardware = load_json(hardware_path)
    measured = overlay.get('measured_overlay', {})
    calibration_group = measured.get('camera_calibration', {}) if isinstance(measured, dict) else {}
    value = calibration_group.get('camera_intrinsics_fx_fy_cx_cy_distortion') if isinstance(calibration_group, dict) else None
    if not isinstance(value, dict):
        fail('visual deployment measured_overlay missing camera calibration object', failures)
        return

    expected_width, expected_height = expected_camera_resolution(hardware)
    width = int(number(value.get('width_px'), 'width_px', failures, min_value=1.0) or 0)
    height = int(number(value.get('height_px'), 'height_px', failures, min_value=1.0) or 0)
    fx = number(value.get('fx'), 'fx', failures)
    fy = number(value.get('fy'), 'fy', failures)
    cx = number(value.get('cx'), 'cx', failures)
    cy = number(value.get('cy'), 'cy', failures)
    if width and height and (width, height) != (expected_width, expected_height):
        fail(
            f'camera calibration resolution {width}x{height} does not match '
            f'runtime {expected_width}x{expected_height}',
            failures,
        )
    if width and cx is not None and not 0.0 <= cx <= width:
        fail('camera calibration cx must be inside image width', failures)
    if height and cy is not None and not 0.0 <= cy <= height:
        fail('camera calibration cy must be inside image height', failures)
    if width and fx is not None and fx > width * 8.0:
        fail('camera calibration fx is implausibly large', failures)
    if height and fy is not None and fy > height * 8.0:
        fail('camera calibration fy is implausibly large', failures)
    model = value.get('distortion_model')
    if not isinstance(model, str) or not model.strip():
        fail('camera calibration distortion_model missing', failures)
    coeffs = value.get('distortion_coeffs')
    if not isinstance(coeffs, list):
        fail('camera calibration distortion_coeffs must be a list', failures)
    else:
        for index, coeff in enumerate(coeffs):
            number(coeff, f'distortion_coeffs[{index}]', failures, min_value=-10.0, max_value=10.0)

    if len(failures) == failure_count:
        ok(f'visual camera calibration: {width}x{height} fx={fx:.3f} fy={fy:.3f} model={model}')


def verify_gate_camera_log(pack_dir, failures):
    gate_path = pack_dir / 'first_drive_gate.json'
    if not gate_path.is_file():
        return
    gate = load_json(gate_path)
    package_log = gate.get('artifacts', {}).get('deployment_package', {}).get('log', [])
    task = None
    package_manifest_path = pack_dir / 'deployment_package' / 'manifest.json'
    if package_manifest_path.is_file():
        task = str(load_json(package_manifest_path).get('task', ''))
    if not task:
        return
    if task and 'Visual' not in task:
        ok('gate visual camera calibration log: not required')
        return
    if any('[OK] camera calibration overlay:' in line for line in package_log):
        ok('gate visual camera calibration log')
    else:
        fail('first_drive_gate deployment log missing camera calibration OK line', failures)


def main():
    args = parse_args()
    pack_dir = Path(args.pack_dir).resolve()
    failures = []
    manifest_path = pack_dir / 'evidence_manifest.json'
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = load_json(manifest_path)
    gate_overall = manifest.get('gate_overall')
    if args.require_pass and gate_overall != 'pass':
        fail(f'gate_overall is not pass: {gate_overall}', failures)
    else:
        ok(f'gate_overall: {gate_overall}')

    verify_group(pack_dir, '', manifest.get('files', {}), failures)
    for dirname, files in sorted(manifest.get('directories', {}).items()):
        verify_group(pack_dir, dirname, files, failures)
    deployment = manifest.get('deployment_package', {})
    verify_group(pack_dir, 'deployment_package', deployment.get('files', {}), failures)
    verify_camera_calibration_overlay(pack_dir, failures)
    verify_gate_camera_log(pack_dir, failures)

    if failures:
        print(f'[FAIL] first-drive evidence pack verification failed: {len(failures)} issue(s)')
        return 1
    print('[OK] first-drive evidence pack verification passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
