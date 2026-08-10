"""Strict access to the two embedded official firmware bundles.

Assets and target metadata are fixed and validated before use to prevent an
accidental cross-product update.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from . import core


CATALOG_SCHEMA = 1
EXPECTED_BUNDLE_IDS = ("B01", "B02")
PARTITION_TABLE_OFFSET = 0x8000
PARTITION_TABLE_SIZE = 0xC00
PARTITION_ENTRY_SIZE = 32
PARTITION_MAGIC = 0x50AA
PARTITION_END_MAGIC = 0xFFFF
NVS_PARTITION_TYPE = 0x01
NVS_PARTITION_SUBTYPE = 0x02
MAX_RESOURCE_BYTES = 32 * 1024 * 1024
OFFICIAL_RESOURCE_RE = re.compile(r"b\d{2}/(?:app|recovery)\.bin")


class BundleValidationError(core.PackageValidationError):
    """An embedded bundle or its manifest is not the exact approved asset."""


@dataclass(frozen=True)
class EmbeddedAsset:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class PartitionEntry:
    type: int
    subtype: int
    offset: int
    size: int
    label: str
    flags: int


@dataclass(frozen=True)
class FirmwareBundle:
    bundle_id: str
    display_name: str
    app: EmbeddedAsset
    recovery: EmbeddedAsset
    target: core.TargetProfile
    source_version_patterns: tuple[str, ...]
    logical_backup: str
    nvs_offset: int
    nvs_size: int
    flash_size: int
    app_bytes: bytes
    recovery_bytes: bytes
    manifest_sha256: str

    def matches_source_version(self, project_version: str) -> bool:
        return any(re.match(pattern, project_version) for pattern in self.source_version_patterns)

    def as_release_package(self) -> core.ReleasePackage:
        """Adapt an embedded App to the proven serial OTA transport."""

        return core.ReleasePackage(
            manifest_sha256=self.manifest_sha256,
            app_sha256=self.app.sha256,
            app_member=self.app.path,
            app_bytes=self.app_bytes,
            target=self.target,
            package_sha256=self.manifest_sha256,
            package_size=len(self.app_bytes),
        )

    def safe_summary(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "display_name": self.display_name,
            "app": {"bytes": self.app.size, "sha256": self.app.sha256},
            "recovery": {
                "bytes": self.recovery.size,
                "sha256": self.recovery.sha256,
            },
            "nvs": {"offset": self.nvs_offset, "size": self.nvs_size},
            "flash_bytes": self.flash_size,
        }


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleValidationError(f"bundle manifest contains duplicate key {key!r}")
        result[key] = value
    return result


def _mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise BundleValidationError(f"bundle manifest field {key!r} must be an object")
    return value


def _text(parent: dict[str, Any], key: str, *, pattern: str | None = None) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value or len(value) > 256:
        raise BundleValidationError(f"bundle manifest field {key!r} must be text")
    if pattern is not None and re.fullmatch(pattern, value) is None:
        raise BundleValidationError(f"bundle manifest field {key!r} is invalid")
    return value


def _positive_int(parent: dict[str, Any], key: str) -> int:
    value = parent.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BundleValidationError(f"bundle manifest field {key!r} must be positive")
    return value


def _asset(parent: dict[str, Any], key: str, *, expected_path: str) -> EmbeddedAsset:
    raw = _mapping(parent, key)
    if set(raw) != {"path", "size", "sha256"}:
        raise BundleValidationError(f"bundle asset {key!r} has unexpected fields")
    path = _text(raw, "path")
    pure = PurePosixPath(path)
    if (
        path != expected_path
        or pure.is_absolute()
        or ".." in pure.parts
        or OFFICIAL_RESOURCE_RE.fullmatch(path) is None
    ):
        raise BundleValidationError(f"bundle asset {key!r} does not use its expected path")
    size = _positive_int(raw, "size")
    if size > MAX_RESOURCE_BYTES:
        raise BundleValidationError(f"bundle asset {key!r} exceeds the resource limit")
    digest = _text(raw, "sha256", pattern=r"[0-9a-f]{64}")
    return EmbeddedAsset(path, size, digest)


def _read_resource(resource_root: Any, asset: EmbeddedAsset) -> bytes:
    resource = resource_root.joinpath(*PurePosixPath(asset.path).parts)
    try:
        data = resource.read_bytes()
    except (FileNotFoundError, OSError):
        raise BundleValidationError(f"embedded asset {asset.path!r} is unavailable") from None
    if len(data) != asset.size:
        raise BundleValidationError(f"embedded asset {asset.path!r} has the wrong size")
    if hashlib.sha256(data).hexdigest() != asset.sha256:
        raise BundleValidationError(f"embedded asset {asset.path!r} failed SHA256 validation")
    return data


def parse_partition_table(full_flash: bytes) -> tuple[PartitionEntry, ...]:
    """Parse the ESP-IDF partition table embedded in a merged flash image."""

    end = PARTITION_TABLE_OFFSET + PARTITION_TABLE_SIZE
    if len(full_flash) < end:
        raise BundleValidationError("recovery image does not contain a complete partition table")
    table = full_flash[PARTITION_TABLE_OFFSET:end]
    entries: list[PartitionEntry] = []
    for offset in range(0, len(table), PARTITION_ENTRY_SIZE):
        raw = table[offset : offset + PARTITION_ENTRY_SIZE]
        magic = struct.unpack_from("<H", raw)[0]
        if magic == PARTITION_END_MAGIC:
            break
        if magic == 0xEBEB:  # optional partition-table MD5 trailer
            break
        if magic != PARTITION_MAGIC:
            raise BundleValidationError("recovery partition table contains an invalid entry")
        _magic, ptype, subtype, address, size, raw_label, flags = struct.unpack(
            "<HBBII16sI", raw
        )
        label_bytes = raw_label.split(b"\x00", 1)[0]
        try:
            label = label_bytes.decode("ascii")
        except UnicodeDecodeError:
            raise BundleValidationError("recovery partition label is not ASCII") from None
        if not label or address % 0x1000 or size <= 0 or address + size < address:
            raise BundleValidationError("recovery partition table contains invalid bounds")
        entries.append(PartitionEntry(ptype, subtype, address, size, label, flags))
    if not entries:
        raise BundleValidationError("recovery partition table is empty")
    return tuple(entries)


def _validate_recovery_layout(bundle: FirmwareBundle) -> None:
    entries = parse_partition_table(bundle.recovery_bytes)
    nvs = [
        entry
        for entry in entries
        if entry.type == NVS_PARTITION_TYPE
        and entry.subtype == NVS_PARTITION_SUBTYPE
        and entry.label == "nvs"
    ]
    if len(nvs) != 1:
        raise BundleValidationError("recovery image must contain exactly one NVS partition")
    if (nvs[0].offset, nvs[0].size) != (bundle.nvs_offset, bundle.nvs_size):
        raise BundleValidationError("recovery NVS layout does not match the bundle manifest")
    for entry in entries:
        if entry.offset + entry.size > bundle.flash_size:
            raise BundleValidationError("recovery partition extends beyond the declared flash")
    if len(bundle.recovery_bytes) > bundle.flash_size:
        raise BundleValidationError("recovery image exceeds the declared flash size")


def _resource_root(override: Path | None = None) -> Any:
    if override is not None:
        return override
    return resources.files("osracer_firmware_client").joinpath("resources")


def load_bundles(resource_root: Path | None = None) -> dict[str, FirmwareBundle]:
    root = _resource_root(resource_root)
    try:
        manifest_raw = root.joinpath("bundles.json").read_bytes()
        document = json.loads(manifest_raw, object_pairs_hook=_reject_duplicate_keys)
    except BundleValidationError:
        raise
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise BundleValidationError("embedded bundle manifest is unavailable or invalid") from None
    if not isinstance(document, dict) or set(document) != {"schema", "client", "bundles"}:
        raise BundleValidationError("embedded bundle manifest has unexpected top-level fields")
    if document.get("schema") != CATALOG_SCHEMA or document.get("client") != "osracer-firmware-client":
        raise BundleValidationError("embedded bundle manifest schema or client is unsupported")
    raw_bundles = _mapping(document, "bundles")
    if tuple(raw_bundles) != EXPECTED_BUNDLE_IDS:
        raise BundleValidationError("embedded bundle manifest must contain B01 and B02 in order")

    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    result: dict[str, FirmwareBundle] = {}
    for bundle_id in EXPECTED_BUNDLE_IDS:
        raw = _mapping(raw_bundles, bundle_id)
        if set(raw) != {
            "display_name",
            "app",
            "recovery",
            "target",
            "source_version_patterns",
            "logical_backup",
            "nvs",
            "flash_size",
        }:
            raise BundleValidationError(f"bundle {bundle_id} has unexpected fields")
        display_name = _text(raw, "display_name")
        if display_name != f"Official package {bundle_id}":
            raise BundleValidationError(f"bundle {bundle_id} display name is invalid")
        app = _asset(raw, "app", expected_path=f"{bundle_id.lower()}/app.bin")
        recovery = _asset(
            raw,
            "recovery",
            expected_path=f"{bundle_id.lower()}/recovery.bin",
        )
        target_raw = _mapping(raw, "target")
        if set(target_raw) != {"project_version", "profile_id", "nvs_schema", "protocol"}:
            raise BundleValidationError(f"bundle {bundle_id} target has unexpected fields")
        target = core.TargetProfile(
            profile_id=_text(target_raw, "profile_id", pattern=r"[a-z0-9_-]{1,15}"),
            hardware="OSCORE_ESP32S3_RevA",
            nvs_schema=_positive_int(target_raw, "nvs_schema"),
            project_version=_text(target_raw, "project_version", pattern=r"[^\s]{1,31}"),
            protocol=_text(target_raw, "protocol", pattern=r"\d+\.\d+"),
        )
        patterns = raw.get("source_version_patterns")
        if (
            not isinstance(patterns, list)
            or not patterns
            or not all(isinstance(pattern, str) and 1 <= len(pattern) <= 128 for pattern in patterns)
        ):
            raise BundleValidationError(f"bundle {bundle_id} source patterns are invalid")
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error:
                raise BundleValidationError(
                    f"bundle {bundle_id} contains an invalid source pattern"
                ) from None
        backup = _text(raw, "logical_backup")
        if backup not in {"legacy", "managed"}:
            raise BundleValidationError(f"bundle {bundle_id} backup mode is unsupported")
        nvs = _mapping(raw, "nvs")
        if set(nvs) != {"offset", "size"}:
            raise BundleValidationError(f"bundle {bundle_id} NVS layout has unexpected fields")
        nvs_offset = _positive_int(nvs, "offset")
        nvs_size = _positive_int(nvs, "size")
        flash_size = _positive_int(raw, "flash_size")
        if nvs_offset % 0x1000 or nvs_size % 0x1000 or nvs_offset + nvs_size > flash_size:
            raise BundleValidationError(f"bundle {bundle_id} NVS layout is invalid")

        bundle = FirmwareBundle(
            bundle_id=bundle_id,
            display_name=display_name,
            app=app,
            recovery=recovery,
            target=target,
            source_version_patterns=tuple(patterns),
            logical_backup=backup,
            nvs_offset=nvs_offset,
            nvs_size=nvs_size,
            flash_size=flash_size,
            app_bytes=_read_resource(root, app),
            recovery_bytes=_read_resource(root, recovery),
            manifest_sha256=manifest_sha256,
        )
        _validate_recovery_layout(bundle)
        result[bundle_id] = bundle
    return result


def match_official_bundle(
    project_version: str,
    bundles: dict[str, FirmwareBundle],
) -> FirmwareBundle | None:
    matches = [bundle for bundle in bundles.values() if bundle.matches_source_version(project_version)]
    if len(matches) > 1:
        raise BundleValidationError("device identity matches more than one official bundle")
    return matches[0] if matches else None
