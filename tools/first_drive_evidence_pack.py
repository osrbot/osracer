#!/usr/bin/env python3
"""Create an OSRacer first-drive evidence archive from a go/no-go report."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

DEFAULT_OUTPUT = Path('/tmp/osracer_first_drive_evidence_pack')
PACKAGE_COPY_NAMES = {
    'manifest.json',
    'SHA256SUMS',
    'README.md',
    'hardware_params.json',
    'measured_overlay.json',
    'source_authority_snapshot.json',
}


def parse_args():
    parser = argparse.ArgumentParser(description='Create a first-drive evidence pack from tools/first_drive_gate.py output.')
    parser.add_argument('--gate-report', required=True, help='JSON produced by tools/first_drive_gate.py --output')
    parser.add_argument('--output-dir', default=str(DEFAULT_OUTPUT), help='Output evidence pack directory')
    parser.add_argument('--overwrite', action='store_true', help='Replace output directory when it exists')
    parser.add_argument('--include-policy-artifact', action='store_true', help='Also copy the deployment policy artifact')
    return parser.parse_args()


def load_json(path):
    with Path(path).open('r', encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f'{path} must contain a JSON object')
    return data


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def copy_file(src, dst):
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {'path': str(dst), 'bytes': dst.stat().st_size, 'sha256': sha256(dst)}


def copy_tree_files(src_dir, dst_dir):
    copied = {}
    src_dir = Path(src_dir)
    if not src_dir.is_dir():
        return copied
    for src in sorted(path for path in src_dir.rglob('*') if path.is_file()):
        rel = src.relative_to(src_dir)
        copied[str(rel)] = copy_file(src, dst_dir / rel)
    return copied


def copy_deployment_package(package_dir, dst_dir, include_policy):
    package_dir = Path(package_dir)
    copied = {}
    manifest = load_json(package_dir / 'manifest.json') if (package_dir / 'manifest.json').is_file() else {}
    policy_artifact = manifest.get('policy_artifact')
    names = set(PACKAGE_COPY_NAMES)
    if include_policy and policy_artifact:
        names.add(policy_artifact)
    for name in sorted(names):
        src = package_dir / name
        if src.is_file():
            copied[name] = copy_file(src, dst_dir / name)
    return copied, {'policy_artifact': policy_artifact, 'policy_artifact_copied': bool(include_policy and policy_artifact)}


def artifact_path(gate, key):
    artifact = gate.get('artifacts', {}).get(key, {})
    path = artifact.get('path')
    return Path(path) if path else None


def build_pack(args):
    gate_path = Path(args.gate_report).resolve()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists():
        if not args.overwrite:
            raise FileExistsError(f'{output_dir} exists; pass --overwrite')
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    gate = load_json(gate_path)
    manifest = {
        'schema_version': 1,
        'gate_overall': gate.get('overall'),
        'gate_failures': gate.get('failures', []),
        'source_gate_report': str(gate_path),
        'files': {},
        'directories': {},
        'deployment_package': {},
        'notes': [
            'This pack is evidence for review and replay. It does not prove calibrated sim2real by itself.',
            'Policy artifact is not copied unless --include-policy-artifact is passed.',
        ],
    }
    manifest['files']['first_drive_gate.json'] = copy_file(gate_path, output_dir / 'first_drive_gate.json')

    file_artifacts = {
        'policy_replay': 'policy_replay.csv',
        'sensor_summary': 'sensor_summary.json',
        'environment_report': 'jetson_environment.json',
        'serial_latency': 'serial_latency.json',
    }
    for key, name in file_artifacts.items():
        src = artifact_path(gate, key)
        if src and src.is_file():
            manifest['files'][name] = copy_file(src, output_dir / name)

    runtime_dir = artifact_path(gate, 'runtime_monitor')
    if runtime_dir and runtime_dir.is_dir():
        manifest['directories']['runtime_monitor'] = copy_tree_files(runtime_dir, output_dir / 'runtime_monitor')

    package_dir = artifact_path(gate, 'deployment_package')
    if package_dir and package_dir.is_dir():
        copied, meta = copy_deployment_package(package_dir, output_dir / 'deployment_package', args.include_policy_artifact)
        manifest['deployment_package'] = {'source': str(package_dir), 'files': copied, **meta}

    (output_dir / 'README.md').write_text(readme_text(manifest), encoding='utf-8')
    (output_dir / 'evidence_manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return output_dir


def readme_text(manifest):
    return '\n'.join([
        '# OSRacer First-Drive Evidence Pack',
        '',
        f"Gate overall: `{manifest['gate_overall']}`",
        '',
        'This directory archives the files referenced by `tools/first_drive_gate.py` for review before or after a low-speed test.',
        '',
        'Policy artifact is not included by default. Use `--include-policy-artifact` only when the review destination is allowed to receive model artifacts.',
        '',
        'Primary files:',
        '',
        '- `first_drive_gate.json`',
        '- `evidence_manifest.json`',
        '- `policy_replay.csv` when supplied',
        '- `sensor_summary.json` when supplied',
        '- `jetson_environment.json` when supplied',
        '- `serial_latency.json` when supplied',
        '- `runtime_monitor/` when supplied',
        '- `deployment_package/` metadata, source authority snapshot, and checksums when supplied',
        '',
    ])


def main():
    args = parse_args()
    output_dir = build_pack(args)
    print(f'wrote {output_dir}')
    print(f'manifest: {output_dir / "evidence_manifest.json"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
