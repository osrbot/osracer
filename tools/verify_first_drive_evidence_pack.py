#!/usr/bin/env python3
"""Verify an OSRacer first-drive evidence pack after handoff or copy."""

import argparse
import hashlib
import json
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

    if failures:
        print(f'[FAIL] first-drive evidence pack verification failed: {len(failures)} issue(s)')
        return 1
    print('[OK] first-drive evidence pack verification passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
