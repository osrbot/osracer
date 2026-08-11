#!/usr/bin/env python3
"""Generate one deterministic provenance document for a packaged client."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
from importlib.metadata import version as dependency_version
from pathlib import Path


DEPENDENCIES = ("esptool", "pyserial", "pyinstaller")


def _git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *arguments],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    init_text = (root / "osracer_firmware_client/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', init_text, re.MULTILINE)
    if match is None:
        raise SystemExit("client version is unavailable")
    source_commit = os.environ.get("OSRACER_SOURCE_COMMIT")
    if source_commit is None:
        source_commit = _git(root, "rev-parse", "HEAD")
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise SystemExit("source commit is invalid")
    dirty_override = os.environ.get("OSRACER_SOURCE_DIRTY")
    if dirty_override is None:
        source_dirty = bool(_git(root, "status", "--porcelain"))
    elif dirty_override in {"0", "1"}:
        source_dirty = dirty_override == "1"
    else:
        raise SystemExit("source dirty flag must be 0 or 1")
    bundle_manifest = root / "osracer_firmware_client/resources/bundles.json"
    document = {
        "version": match.group(1),
        "package_kind": "self-contained-onefile",
        "source_commit": source_commit,
        "source_dirty": source_dirty,
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "libc": list(platform.libc_ver()),
        },
        "python": platform.python_version(),
        "bundle_manifest_sha256": hashlib.sha256(bundle_manifest.read_bytes()).hexdigest(),
        "dependencies": {name: dependency_version(name) for name in DEPENDENCIES},
    }
    encoded = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
