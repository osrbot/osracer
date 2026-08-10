"""Read immutable build provenance embedded by the packaging script."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from . import __version__


def load_build_info() -> dict[str, Any]:
    fallback = {
        "version": __version__,
        "package_kind": "source-tree",
        "source_commit": None,
        "source_dirty": True,
    }
    try:
        raw = resources.files("osracer_firmware_client").joinpath("BUILD_INFO.json").read_text(
            encoding="utf-8"
        )
        value = json.loads(raw)
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return fallback
    if not isinstance(value, dict):
        return fallback
    required = {"version", "package_kind", "source_commit", "source_dirty", "dependencies"}
    if not required.issubset(value):
        return fallback
    if value.get("version") != __version__ or not isinstance(value.get("dependencies"), dict):
        return fallback
    return value


def load_third_party_notices() -> str:
    try:
        return resources.files("osracer_firmware_client").joinpath(
            "THIRD_PARTY_NOTICES.txt"
        ).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return "Third-party notices are unavailable in this source-tree run.\n"
