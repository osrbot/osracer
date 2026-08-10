#!/usr/bin/env python3
"""Proven internal serial/configuration engine for the unified client.

This module is not a customer command.  Its legacy ``main`` function remains
only for regression tests while the supported entry point is
``osracer-firmware-client``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import socket
import stat
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol


TOOL_VERSION = "5"
MANIFEST_SCHEMA = 1
CATALOG_SCHEMA = 1
SUPPORTED_PROTOCOL = "1.1"
DEFAULT_CATALOG_URL = "https://raw.githubusercontent.com/osrbot/osracer/main/firmware/catalog.json"
DEFAULT_PORT = "/dev/osrbot_base"
DEFAULT_BAUD = 460800
DEFAULT_CHUNK_SIZE = 128
MIN_CHUNK_SIZE = 1
MAX_CHUNK_SIZE = 384
DEFAULT_RESPONSE_TIMEOUT = 1.5
DEFAULT_RECONNECT_TIMEOUT = 20.0
DEFAULT_RECONNECT_INTERVAL = 0.25
DEFAULT_POST_BATTERY_TIMEOUT = 5.0
DEFAULT_POST_BATTERY_INTERVAL = 0.25
DEFAULT_LEVEL_CALIBRATION_TIMEOUT = 5.0
DEFAULT_LEVEL_CALIBRATION_INTERVAL = 0.25
DEFAULT_DOWNLOAD_TIMEOUT = 10.0
MAX_CATALOG_BYTES = 256 * 1024
MAX_PACKAGE_DOWNLOAD_BYTES = 16 * 1024 * 1024
MAX_ZIP_MEMBERS = 32
MAX_MANIFEST_BYTES = 64 * 1024
MAX_APP_BYTES = 8 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200.0
MAX_RESUME_AUDIT_BYTES = 2 * 1024 * 1024
MAX_RESUME_AUDIT_LINES = 10_000
DOWNLOAD_READ_SIZE = 64 * 1024
CONFIRM_TEXT = "UPDATE"
RESTORE_CONFIRM_TEXT = "RESTORE"
CONFIG_BACKUP_SCHEMA = 1
CONFIG_HASH_DOMAIN = "OSRVCFG1"
CONFIG_RESTORE_HASH_DOMAIN = "OSRRESTORE1"
CONFIG_ITEM_COUNT = 24
DEFAULT_CONFIG_TIMEOUT = 10.0
TRANSPORT_RECOVERY_SOURCE_PROJECT_VERSION = "OSRF-C03-T004-se2f117ee56df"
TRANSPORT_RECOVERY_TARGET_PROJECT_VERSION = "OSRF-C03-T005-s1c7ef7e8766a"
TRANSPORT_RECOVERY_PACKAGE_SHA256 = (
    "a6caf4a70349484ebbf108836cecdfe82ea26ae62711982ea1dd8808b383713f"
)
TRANSPORT_RECOVERY_MANIFEST_SHA256 = (
    "cbf7b1caad7490aaa66fc59899e8587c235f74c49cf69fe6bb4ba85dc4e30234"
)
TRANSPORT_RECOVERY_APP_SHA256 = (
    "9894dc245e1f5b287559f82d14397b919f4e78679536792c3ad6db682d4bd52f"
)
TRANSPORT_RECOVERY_SOURCE_TREE_SHA256 = (
    "1c7ef7e8766ae73f67d6dfe99d9efdf12f9b837976254f7c06daee3af413fc9e"
)
TRANSPORT_RECOVERY_PACKAGE_SIZE = 246_672
TRANSPORT_RECOVERY_APP_SIZE = 429_424
CORRECTIVE_SOURCE_VERSION_RE = re.compile(r"OSRF-C03-T002-g[0-9a-f]{7,40}")
CORRECTIVE_TARGET_VERSION_RE = re.compile(r"OSRF-C03-T003-g[0-9a-f]{7,40}")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONFIGURATION_FIELDS = (
    "battery",
    "level_offset",
    "magnetometer",
    "odom_scale",
    "pid",
    "serial_number",
    "speed_deadband_us",
    "steering_trim",
)

# The order, wire type, and byte size are part of the OSRVCFG1 contract.  The
# list deliberately excludes profile/migration metadata and unknown NVS keys.
VEHICLE_CONFIG_FIELDS = (
    ("pid_params.kp", "BLOB", 4),
    ("pid_params.ki", "BLOB", 4),
    ("pid_params.kd", "BLOB", 4),
    ("pid_params.init", "U8", 1),
    ("pid_params.profile", "U32", 4),
    ("mag_calib.hi", "BLOB", 12),
    ("mag_calib.si", "BLOB", 36),
    ("mag_calib.init", "U8", 1),
    ("cf_params.alpha_s", "BLOB", 4),
    ("cf_params.alpha_m", "BLOB", 4),
    ("cf_params.spd_thr", "BLOB", 4),
    ("cf_params.init", "U8", 1),
    ("level_cal.ox", "BLOB", 4),
    ("level_cal.oy", "BLOB", 4),
    ("level_cal.oz", "BLOB", 4),
    ("level_cal.init", "U8", 1),
    ("chassis_cal.odom_scale", "BLOB", 4),
    ("chassis_cal.steer_trim_deg", "BLOB", 4),
    ("chassis_cal.steer_trim", "I32", 4),
    ("chassis_cal.init", "U8", 1),
    ("speed_cal.deadband_us", "I32", 4),
    ("speed_cal.init", "U8", 1),
    ("battery_cal.scale", "BLOB", 4),
    ("battery_cal.init", "U8", 1),
)
LEVEL_CALIBRATION_OFFSET_FIELDS = (
    "level_cal.ox",
    "level_cal.oy",
    "level_cal.oz",
)
LEVEL_CALIBRATION_INIT_FIELD = "level_cal.init"
NON_LEVEL_CONFIG_ITEM_COUNT = CONFIG_ITEM_COUNT - len(LEVEL_CALIBRATION_OFFSET_FIELDS) - 1


class FirmwareUpdateError(Exception):
    """Base class for errors that are safe to show to an operator."""

    exit_code = 4

    def __init__(self, message: str):
        super().__init__(message)
        self.audit_path: Path | None = None


class PackageValidationError(FirmwareUpdateError):
    exit_code = 2


class CatalogError(PackageValidationError):
    pass


class DownloadError(PackageValidationError):
    pass


class UserCancelledError(FirmwareUpdateError):
    exit_code = 2


class AuditError(FirmwareUpdateError):
    exit_code = 2


class SerialUnavailableError(FirmwareUpdateError):
    exit_code = 3


class DevicePreflightError(FirmwareUpdateError):
    exit_code = 3


class ProtocolError(FirmwareUpdateError):
    exit_code = 4


class ResponseTimeoutError(ProtocolError):
    pass


class SerialCommunicationError(ProtocolError):
    pass


class DeviceRejectedError(ProtocolError):
    def __init__(self, message: str, *, stage: str = "unknown", device_reason: str = "unspecified"):
        super().__init__(message)
        self.stage = stage
        self.device_reason = device_reason


class ReconnectTimeoutError(ProtocolError):
    pass


class PostInstallError(DevicePreflightError):
    def __init__(
        self,
        message: str,
        *,
        outcome: str,
        stage: str,
        recovery_required: bool = True,
    ):
        super().__init__(message)
        self.outcome = outcome
        self.stage = stage
        self.recovery_required = recovery_required


class UpdateInterruptedError(FirmwareUpdateError):
    exit_code = 130


class SerialConnection(Protocol):
    is_open: bool

    def write(self, data: bytes) -> int | None: ...

    def flush(self) -> None: ...

    def readline(self) -> bytes: ...

    def reset_input_buffer(self) -> None: ...

    def reset_output_buffer(self) -> None: ...

    def close(self) -> None: ...


SerialFactory = Callable[..., SerialConnection]
UrlOpener = Callable[..., Any]


@dataclass(frozen=True)
class TargetProfile:
    profile_id: str
    hardware: str
    nvs_schema: int
    project_version: str
    protocol: str


@dataclass(frozen=True)
class CatalogCandidate:
    candidate_id: str
    channel: str
    asset: str
    package_sha256: str
    package_size: int
    target: TargetProfile
    release_ready: bool
    source_dirty: bool
    signature: str
    app_sha256: str | None = None
    app_size: int | None = None
    source_tree_sha256: str | None = None
    bootstrap_source_project_version: str | None = None

    def public_summary(self) -> dict[str, Any]:
        result = {
            "id": self.candidate_id,
            "channel": self.channel,
            "asset": self.asset,
            "sha256": self.package_sha256,
            "size": self.package_size,
            "profile": {
                "id": self.target.profile_id,
                "hardware": self.target.hardware,
                "nvs_schema": self.target.nvs_schema,
                "project_version": self.target.project_version,
                "protocol": self.target.protocol,
            },
            "release_ready": self.release_ready,
            "source_dirty": self.source_dirty,
            "signature": self.signature,
        }
        if self.app_sha256 is not None:
            result["app"] = {"sha256": self.app_sha256, "size": self.app_size}
        if self.source_tree_sha256 is not None:
            result["source"] = {
                "tree_sha256": self.source_tree_sha256,
                "bootstrap_project_version": self.bootstrap_source_project_version,
            }
        return result

    def risk_label(self) -> str:
        readiness = "release-ready" if self.release_ready else "not release-ready"
        return (
            f"{self.channel.upper()} FIRMWARE; source_dirty={str(self.source_dirty).lower()}; "
            f"unsigned; release_ready={str(self.release_ready).lower()} ({readiness})"
        )


@dataclass(frozen=True)
class CatalogDocument:
    sha256: str
    channels: dict[str, tuple[CatalogCandidate, ...]]


@dataclass(frozen=True)
class UpdateSource:
    kind: str = "local"
    candidate: CatalogCandidate | None = None
    catalog_sha256: str | None = None

    def audit_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {"source_kind": self.kind}
        if self.candidate is not None:
            fields.update(
                {
                    "catalog_sha256": self.catalog_sha256,
                    "candidate_id": self.candidate.candidate_id,
                    "channel": self.candidate.channel,
                    "package_sha256": self.candidate.package_sha256,
                    "package_bytes": self.candidate.package_size,
                    "release_ready": self.candidate.release_ready,
                    "source_dirty": self.candidate.source_dirty,
                    "signature": self.candidate.signature,
                }
            )
        return fields


@dataclass(frozen=True)
class ReleasePackage:
    manifest_sha256: str
    app_sha256: str
    app_member: str
    app_bytes: bytes
    target: TargetProfile
    package_sha256: str
    package_size: int
    source_tree_sha256: str | None = None
    bootstrap_source_project_version: str | None = None
    config_item_count: int | None = None

    @property
    def app_size(self) -> int:
        return len(self.app_bytes)

    def public_summary(self) -> dict[str, Any]:
        result = {
            "schema": MANIFEST_SCHEMA,
            "manifest_sha256": self.manifest_sha256,
            "app_sha256": self.app_sha256,
            "app_bytes": self.app_size,
            "app_member": self.app_member,
            "package_sha256": self.package_sha256,
            "package_bytes": self.package_size,
            "profile": {
                "id": self.target.profile_id,
                "hardware": self.target.hardware,
                "nvs_schema": self.target.nvs_schema,
                "project_version": self.target.project_version,
                "protocol": self.target.protocol,
            },
        }
        if self.source_tree_sha256 is not None:
            result["source_tree_sha256"] = self.source_tree_sha256
        if self.bootstrap_source_project_version is not None:
            result["bootstrap_source_project_version"] = self.bootstrap_source_project_version
        if self.config_item_count is not None:
            result["config_items"] = self.config_item_count
        return result


@dataclass(frozen=True)
class FirmwareVersion:
    project_version: str
    protocol: str | None
    format: str = "compact"
    product: str | None = None
    firmware: str | None = None
    hardware: str | None = None
    release: str | None = None
    git: str | None = None
    dirty: str | None = None
    build: str | None = None


@dataclass(frozen=True)
class ProfileStatus:
    profile_id: str
    nvs_schema: int
    state: str
    motion_ok: bool
    writes_ok: bool


@dataclass(frozen=True)
class FirmwareStatus:
    active: bool
    written: int
    size: int
    next_seq: int
    running: str
    next_partition: str


@dataclass(frozen=True)
class DeviceSnapshot:
    version: FirmwareVersion
    profile: ProfileStatus | None
    voltage: float
    firmware_status: FirmwareStatus | None = None
    configuration: dict[str, Any] | None = None
    unavailable_fields: tuple[str, ...] = ()
    snapshot_sha256: str | None = None

    def audit_fields(self, prefix: str) -> dict[str, Any]:
        return {
            f"{prefix}_project_version": self.version.project_version,
            f"{prefix}_protocol": self.version.protocol,
            f"{prefix}_version_format": self.version.format,
            f"{prefix}_profile_id": self.profile.profile_id if self.profile else None,
            f"{prefix}_nvs_schema": self.profile.nvs_schema if self.profile else None,
            f"{prefix}_profile_state": self.profile.state if self.profile else None,
            f"{prefix}_profile_motion": self.profile.motion_ok if self.profile else None,
            f"{prefix}_profile_writes": self.profile.writes_ok if self.profile else None,
            f"{prefix}_voltage": self.voltage,
            f"{prefix}_snapshot_sha256": self.snapshot_sha256,
            f"{prefix}_configuration_fields": sorted((self.configuration or {}).keys()),
            f"{prefix}_unavailable_fields": list(self.unavailable_fields),
        }


@dataclass(frozen=True)
class UpdateConfig:
    port: str = DEFAULT_PORT
    baud: int = DEFAULT_BAUD
    chunk_size: int = DEFAULT_CHUNK_SIZE
    response_timeout: float = DEFAULT_RESPONSE_TIMEOUT
    reconnect_timeout: float = DEFAULT_RECONNECT_TIMEOUT
    reconnect_interval: float = DEFAULT_RECONNECT_INTERVAL
    post_battery_timeout: float = DEFAULT_POST_BATTERY_TIMEOUT
    post_battery_interval: float = DEFAULT_POST_BATTERY_INTERVAL
    level_calibration_timeout: float = DEFAULT_LEVEL_CALIBRATION_TIMEOUT
    level_calibration_interval: float = DEFAULT_LEVEL_CALIBRATION_INTERVAL
    reinstall: bool = False
    log_dir: Path | None = None
    snapshot_dir: Path | None = None
    transaction_dir: Path | None = None
    resume_audit: Path | None = None
    corrective_recovery: bool = False

    def validate(self) -> None:
        if not self.port:
            raise PackageValidationError("serial port must not be empty")
        if self.baud != DEFAULT_BAUD:
            raise PackageValidationError(f"serial baud must be {DEFAULT_BAUD}")
        if not MIN_CHUNK_SIZE <= self.chunk_size <= MAX_CHUNK_SIZE:
            raise PackageValidationError(
                f"chunk size must be between {MIN_CHUNK_SIZE} and {MAX_CHUNK_SIZE} bytes"
            )
        if self.response_timeout <= 0:
            raise PackageValidationError("response timeout must be positive")
        if self.reconnect_timeout <= 0:
            raise PackageValidationError("reconnect timeout must be positive")
        if self.reconnect_interval <= 0:
            raise PackageValidationError("reconnect interval must be positive")
        if self.post_battery_timeout <= 0:
            raise PackageValidationError("post battery timeout must be positive")
        if not 0.2 <= self.post_battery_interval <= 0.25:
            raise PackageValidationError("post battery interval must be between 0.2 and 0.25 seconds")
        if self.level_calibration_timeout <= 0:
            raise PackageValidationError("level calibration timeout must be positive")
        if not 0.2 <= self.level_calibration_interval <= 0.25:
            raise PackageValidationError(
                "level calibration interval must be between 0.2 and 0.25 seconds"
            )
        if self.corrective_recovery and self.resume_audit is None:
            raise PackageValidationError("corrective recovery requires the exact original --resume-audit")
        if self.corrective_recovery and self.reinstall:
            raise PackageValidationError("corrective recovery cannot be combined with --reinstall")


@dataclass(frozen=True)
class UpdateResult:
    status: str
    audit_path: Path
    pre_snapshot: DeviceSnapshot
    post_snapshot: DeviceSnapshot | None
    operation: str = "app_ota"


@dataclass(frozen=True)
class MigrationTransaction:
    target: TargetProfile
    manifest_sha256: str
    app_sha256: str
    app_bytes: int
    device_serial_sha256: str
    source_snapshot_sha256: str
    source_project_version: str


@dataclass
class OtaProgress:
    begin_may_have_been_sent: bool = False
    begin_rejected: bool = False
    session_active: bool = False
    data_delivery_unknown: bool = False
    data_committed_bytes: int = 0
    end_may_have_been_sent: bool = False
    end_acknowledged: bool = False


@dataclass(frozen=True)
class VehicleConfigItem:
    name: str
    state: str
    value_type: str
    value: str


@dataclass(frozen=True)
class VehicleConfigExport:
    source: TargetProfile
    target: TargetProfile
    items: tuple[VehicleConfigItem, ...]
    backup_sha256: str


@dataclass(frozen=True)
class VehicleConfigSemanticComparison:
    non_level_mismatches: tuple[str, ...]
    level_init_status: str
    level_offset_status: str
    changed_level_fields: tuple[str, ...]
    invalid_level_fields: tuple[str, ...]

    @property
    def matches(self) -> bool:
        return not self.non_level_mismatches and not self.invalid_level_fields


@dataclass(frozen=True)
class ManagedDeviceIdentity:
    version: FirmwareVersion
    profile: ProfileStatus
    firmware_status: FirmwareStatus


@dataclass(frozen=True)
class ConfigImportStatus:
    phase: str
    received: int
    expected: int
    result: str
    source: TargetProfile | None
    target: TargetProfile
    backup_sha256: str | None
    transaction_sha256: str | None
    pending_transaction_sha256: str | None
    recovery_required: bool


class ConsoleRenderer:
    """Small dependency-free renderer with stable plain-text output."""

    def __init__(
        self,
        output_func: Callable[[str], None] = print,
        *,
        tty: bool | None = None,
        no_color: bool | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.output = output_func
        self.tty = bool(sys.stdout.isatty()) if tty is None else bool(tty)
        self.no_color = bool(os.environ.get("NO_COLOR")) if no_color is None else bool(no_color)
        self.monotonic = monotonic
        self.started = monotonic()
        self._last_percent = -1

    def _style(self, text: str, code: str) -> str:
        if not self.tty or self.no_color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def header(self, title: str) -> None:
        if self.tty:
            border = "─" * max(28, len(title) + 4)
            self.output(self._style(f"┌{border}┐", "36"))
            self.output(self._style(f"│  {title.ljust(len(border) - 2)}│", "1;36"))
            self.output(self._style(f"└{border}┘", "36"))
        else:
            self.output(f"=== {title} ===")

    def summary(self, label: str, value: str) -> None:
        self.output(f"  {label:<15} {value}")

    def phase(self, index: int, total: int, title: str, detail: str | None = None) -> None:
        line = f"[{index}/{total}] {title}"
        if detail:
            line += f" — {detail}"
        self.output(self._style(line, "1;34"))

    def progress(self, written: int, total: int) -> None:
        percent = 100 if total <= 0 else min(100, int(written * 100 / total))
        if written != total and percent < self._last_percent + 5:
            return
        self._last_percent = percent
        elapsed = self.monotonic() - self.started
        spinner = "|/-\\"[(percent // 5) % 4]
        self.output(
            f"  [{spinner}] Flashing App  {percent:3d}%  "
            f"{written:,}/{total:,} bytes  elapsed {elapsed:.1f}s"
        )

    def path(self, label: str, path: Path, digest: str | None = None) -> None:
        self.output(f"  {label}: {path}")
        if digest:
            self.output(f"  {label} SHA256: {digest}")

    def result(self, success: bool, lines: list[tuple[str, str]]) -> None:
        title = "RESULT: SUCCESS" if success else "RESULT: ACTION REQUIRED"
        self.output(self._style(f"=== {title} ===", "1;32" if success else "1;31"))
        for label, value in lines:
            self.summary(label, value)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_member_name(name: str, *, directory_allowed: bool) -> None:
    if not name or "\x00" in name:
        raise PackageValidationError("ZIP contains an invalid member name")
    if "\\" in name:
        raise PackageValidationError("ZIP member names must use forward slashes")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise PackageValidationError("ZIP contains an absolute member path")

    is_directory = name.endswith("/")
    if is_directory and not directory_allowed:
        raise PackageValidationError("manifest App path must identify a file")
    trimmed = name[:-1] if is_directory else name
    parts = trimmed.split("/")
    if not trimmed or any(part in {"", ".", ".."} for part in parts):
        raise PackageValidationError("ZIP contains a directory traversal or ambiguous member path")
    if PurePosixPath(trimmed).is_absolute():
        raise PackageValidationError("ZIP contains an absolute member path")


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _forbidden_app_name(name: str) -> bool:
    return any(
        marker in part.casefold()
        for part in PurePosixPath(name).parts
        for marker in ("full", "merged")
    )


def _validate_zip_resource_limits(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > MAX_ZIP_MEMBERS:
        raise PackageValidationError(f"ZIP contains more than {MAX_ZIP_MEMBERS} members")

    total_uncompressed = 0
    total_compressed = 0
    for info in infos:
        if info.file_size < 0 or info.compress_size < 0:
            raise PackageValidationError("ZIP contains an invalid member size")
        total_uncompressed += info.file_size
        total_compressed += info.compress_size
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise PackageValidationError(
                f"ZIP exceeds the {MAX_TOTAL_UNCOMPRESSED_BYTES}-byte host uncompressed limit"
            )
        if not info.is_dir() and info.file_size:
            if info.compress_size == 0 or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                raise PackageValidationError("ZIP member compression ratio exceeds the host safety limit")

    if total_uncompressed and (
        total_compressed == 0 or total_uncompressed / total_compressed > MAX_COMPRESSION_RATIO
    ):
        raise PackageValidationError("ZIP compression ratio exceeds the host safety limit")


def _read_zip_member_bounded(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    limit: int,
    label: str,
) -> bytes:
    if info.file_size > limit:
        raise PackageValidationError(f"{label} exceeds the {limit}-byte host safety limit")
    chunks: list[bytes] = []
    total = 0
    with archive.open(info, "r") as member:
        while True:
            chunk = member.read(min(DOWNLOAD_READ_SIZE, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise PackageValidationError(f"{label} exceeds the {limit}-byte host safety limit")
    if total != info.file_size:
        raise PackageValidationError(f"{label} size does not match ZIP metadata")
    return b"".join(chunks)


def _required_mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise PackageValidationError(f"manifest field '{key}' must be an object")
    return value


def _required_text(parent: dict[str, Any], key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise PackageValidationError(f"manifest field '{key}' must be a non-empty string")
    if any(character in value for character in ("\r", "\n", "\x00", ",")):
        raise PackageValidationError(f"manifest field '{key}' contains unsupported characters")
    return value


def _required_nonnegative_int(parent: dict[str, Any], key: str) -> int:
    value = parent.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PackageValidationError(f"manifest field '{key}' must be a non-negative integer")
    return value


def _parse_manifest(
    raw: bytes,
) -> tuple[TargetProfile, str, str, str | None, str | None, int | None]:
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PackageValidationError("manifest.json is not valid UTF-8 JSON") from None
    if not isinstance(manifest, dict):
        raise PackageValidationError("manifest.json root must be an object")

    schema = manifest.get("schema")
    if isinstance(schema, bool) or schema != MANIFEST_SCHEMA:
        raise PackageValidationError(f"manifest schema must be integer {MANIFEST_SCHEMA}")

    profile = _required_mapping(manifest, "profile")
    flash = _required_mapping(manifest, "flash")
    sha256_section = _required_mapping(manifest, "sha256")

    target = TargetProfile(
        profile_id=_required_text(profile, "id"),
        hardware=_required_text(profile, "hardware"),
        nvs_schema=_required_nonnegative_int(profile, "nvs_schema"),
        project_version=_required_text(profile, "project_version"),
        protocol=_required_text(profile, "protocol"),
    )
    if target.protocol != SUPPORTED_PROTOCOL:
        raise PackageValidationError(f"manifest protocol must be {SUPPORTED_PROTOCOL}")

    app_member = _required_text(flash, "package_app")
    _validate_member_name(app_member, directory_allowed=False)
    if _forbidden_app_name(app_member):
        raise PackageValidationError("manifest App path identifies a full or merged image")

    expected_app_sha = _required_text(sha256_section, "app")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_app_sha):
        raise PackageValidationError("manifest sha256.app must be a lowercase SHA256 digest")

    source_tree_sha256: str | None = None
    bootstrap_source: str | None = None
    config_items: int | None = None
    if "build" in manifest:
        build = _required_mapping(manifest, "build")
        if "source_tree_sha256" in build:
            source_tree_sha256 = _required_text(build, "source_tree_sha256")
            if not re.fullmatch(r"[0-9a-f]{64}", source_tree_sha256):
                raise PackageValidationError(
                    "manifest build.source_tree_sha256 must be a lowercase SHA256 digest"
                )
    if "bootstrap" in manifest:
        bootstrap = _required_mapping(manifest, "bootstrap")
        bootstrap_source = _required_text(bootstrap, "source_project_version")
    if "config_transfer" in manifest:
        transfer = _required_mapping(manifest, "config_transfer")
        config_items = _required_nonnegative_int(transfer, "item_count")
        if config_items != CONFIG_ITEM_COUNT:
            raise PackageValidationError(
                f"manifest config_transfer.item_count must be {CONFIG_ITEM_COUNT}"
            )
        if _required_text(transfer, "backup_hash_domain") != CONFIG_HASH_DOMAIN:
            raise PackageValidationError("manifest config transfer hash domain is unsupported")
        if _required_text(transfer, "transaction_hash_domain") != CONFIG_RESTORE_HASH_DOMAIN:
            raise PackageValidationError("manifest restore transaction hash domain is unsupported")
    if any(value is not None for value in (bootstrap_source, config_items)) and not all(
        value is not None for value in (source_tree_sha256, bootstrap_source, config_items)
    ):
        raise PackageValidationError("managed update manifest metadata is incomplete")
    return (
        target,
        app_member,
        expected_app_sha,
        source_tree_sha256,
        bootstrap_source,
        config_items,
    )


def load_release_package(package: str | os.PathLike[str]) -> ReleasePackage:
    """Validate a local ZIP completely and retain only the declared App bytes."""

    package_path = Path(package)
    if not package_path.is_file():
        raise PackageValidationError("release package is not a regular file")
    try:
        if not zipfile.is_zipfile(package_path):
            raise PackageValidationError("release package is not a valid ZIP file")
        with zipfile.ZipFile(package_path, "r") as archive:
            infos = archive.infolist()
            _validate_zip_resource_limits(infos)
            members: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                _validate_member_name(info.filename, directory_allowed=True)
                if info.filename in members:
                    raise PackageValidationError("ZIP contains a duplicate member")
                if _is_symlink(info):
                    raise PackageValidationError("ZIP symbolic links are not allowed")
                if info.flag_bits & 0x1:
                    raise PackageValidationError("encrypted ZIP members are not supported")
                members[info.filename] = info

            manifest_info = members.get("manifest.json")
            if manifest_info is None or manifest_info.is_dir():
                raise PackageValidationError("ZIP must contain top-level manifest.json")
            manifest_raw = _read_zip_member_bounded(
                archive,
                manifest_info,
                limit=MAX_MANIFEST_BYTES,
                label="manifest.json",
            )
            (
                target,
                app_member,
                expected_app_sha,
                source_tree_sha256,
                bootstrap_source,
                config_items,
            ) = _parse_manifest(manifest_raw)

            app_info = members.get(app_member)
            if app_info is None or app_info.is_dir():
                raise PackageValidationError("manifest App file is missing from ZIP")
            if app_info.file_size <= 0:
                raise PackageValidationError("manifest App file must not be empty")
            app_bytes = _read_zip_member_bounded(
                archive,
                app_info,
                limit=MAX_APP_BYTES,
                label="manifest App file",
            )
    except PackageValidationError:
        raise
    except Exception:
        raise PackageValidationError("release package could not be read safely") from None

    actual_app_sha = _sha256(app_bytes)
    if actual_app_sha != expected_app_sha:
        raise PackageValidationError("manifest App SHA256 does not match ZIP content")
    return ReleasePackage(
        manifest_sha256=_sha256(manifest_raw),
        app_sha256=actual_app_sha,
        app_member=app_member,
        app_bytes=app_bytes,
        target=target,
        package_sha256=_sha256(package_path.read_bytes()),
        package_size=package_path.stat().st_size,
        source_tree_sha256=source_tree_sha256,
        bootstrap_source_project_version=bootstrap_source,
        config_item_count=config_items,
    )


def _catalog_object(parent: dict[str, Any], key: str, *, context: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise CatalogError(f"{context} field '{key}' must be an object")
    return value


def _catalog_text(parent: dict[str, Any], key: str, *, context: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise CatalogError(f"{context} field '{key}' must be a non-empty string")
    if any(character in value for character in ("\r", "\n", "\x00")):
        raise CatalogError(f"{context} field '{key}' contains unsupported characters")
    return value


def _catalog_boolean(parent: dict[str, Any], key: str, *, context: str) -> bool:
    value = parent.get(key)
    if not isinstance(value, bool):
        raise CatalogError(f"{context} field '{key}' must be a boolean")
    return value


def _catalog_positive_int(parent: dict[str, Any], key: str, *, context: str) -> int:
    value = parent.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CatalogError(f"{context} field '{key}' must be a positive integer")
    return value


def _catalog_nonnegative_int(parent: dict[str, Any], key: str, *, context: str) -> int:
    value = parent.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CatalogError(f"{context} field '{key}' must be a non-negative integer")
    return value


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError("catalog contains a duplicate JSON object key")
        result[key] = value
    return result


def _validate_asset_path(asset: str) -> None:
    parsed = urllib.parse.urlsplit(asset)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise CatalogError("catalog asset must be a plain relative path")
    if urllib.parse.unquote(asset) != asset or "\\" in asset or asset.startswith("/"):
        raise CatalogError("catalog asset contains an unsafe path")
    parts = asset.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise CatalogError("catalog asset contains traversal or ambiguous path components")
    if not asset.endswith(".zip"):
        raise CatalogError("catalog asset must identify a ZIP file")


def parse_catalog(raw: bytes) -> CatalogDocument:
    try:
        catalog = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except CatalogError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CatalogError("catalog is not valid UTF-8 JSON") from None
    if not isinstance(catalog, dict):
        raise CatalogError("catalog root must be an object")
    schema = catalog.get("schema")
    if isinstance(schema, bool) or schema != CATALOG_SCHEMA:
        raise CatalogError(f"catalog schema must be integer {CATALOG_SCHEMA}")

    channels = _catalog_object(catalog, "channels", context="catalog")
    if set(channels) != {"stable", "test"}:
        raise CatalogError("catalog channels must contain exactly stable and test")

    parsed_channels: dict[str, tuple[CatalogCandidate, ...]] = {}
    seen_ids: set[str] = set()
    for channel in ("stable", "test"):
        entries = channels[channel]
        if not isinstance(entries, list):
            raise CatalogError(f"catalog channel '{channel}' must be an array")
        if channel == "stable" and entries:
            raise CatalogError(
                "catalog schema 1 stable channel must remain empty because no signature verifier exists"
            )
        candidates: list[CatalogCandidate] = []
        for index, entry in enumerate(entries):
            context = f"catalog {channel} candidate {index}"
            if not isinstance(entry, dict):
                raise CatalogError(f"{context} must be an object")
            candidate_id = _catalog_text(entry, "id", context=context)
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", candidate_id):
                raise CatalogError(f"{context} id contains unsupported characters")
            if candidate_id in seen_ids:
                raise CatalogError(f"catalog contains duplicate candidate id '{candidate_id}'")
            seen_ids.add(candidate_id)

            declared_channel = _catalog_text(entry, "channel", context=context)
            if declared_channel != channel:
                raise CatalogError(f"{context} channel does not match its containing channel")
            asset = _catalog_text(entry, "asset", context=context)
            _validate_asset_path(asset)
            package_sha256 = _catalog_text(entry, "sha256", context=context)
            if not re.fullmatch(r"[0-9a-f]{64}", package_sha256):
                raise CatalogError(f"{context} sha256 must be a lowercase SHA256 digest")
            package_size = _catalog_positive_int(entry, "size", context=context)
            if package_size > MAX_PACKAGE_DOWNLOAD_BYTES:
                raise CatalogError(
                    f"{context} size exceeds the {MAX_PACKAGE_DOWNLOAD_BYTES}-byte download limit"
                )

            profile = _catalog_object(entry, "profile", context=context)
            target = TargetProfile(
                profile_id=_catalog_text(profile, "id", context=f"{context} profile"),
                hardware=_catalog_text(profile, "hardware", context=f"{context} profile"),
                nvs_schema=_catalog_nonnegative_int(
                    profile,
                    "nvs_schema",
                    context=f"{context} profile",
                ),
                project_version=_catalog_text(
                    profile,
                    "project_version",
                    context=f"{context} profile",
                ),
                protocol=_catalog_text(profile, "protocol", context=f"{context} profile"),
            )
            if target.protocol != SUPPORTED_PROTOCOL:
                raise CatalogError(f"{context} protocol must be {SUPPORTED_PROTOCOL}")

            app_sha256: str | None = None
            app_size: int | None = None
            source_tree_sha256: str | None = None
            bootstrap_source: str | None = None
            if "app" in entry:
                app = _catalog_object(entry, "app", context=context)
                app_sha256 = _catalog_text(app, "sha256", context=f"{context} app")
                if not re.fullmatch(r"[0-9a-f]{64}", app_sha256):
                    raise CatalogError(f"{context} app sha256 must be a lowercase SHA256 digest")
                app_size = _catalog_positive_int(app, "size", context=f"{context} app")
            if "source" in entry:
                source_info = _catalog_object(entry, "source", context=context)
                source_tree_sha256 = _catalog_text(
                    source_info,
                    "tree_sha256",
                    context=f"{context} source",
                )
                if not re.fullmatch(r"[0-9a-f]{64}", source_tree_sha256):
                    raise CatalogError(
                        f"{context} source tree_sha256 must be a lowercase SHA256 digest"
                    )
                bootstrap_source = _catalog_text(
                    source_info,
                    "bootstrap_project_version",
                    context=f"{context} source",
                )

            candidates.append(
                CatalogCandidate(
                    candidate_id=candidate_id,
                    channel=channel,
                    asset=asset,
                    package_sha256=package_sha256,
                    package_size=package_size,
                    target=target,
                    release_ready=_catalog_boolean(entry, "release_ready", context=context),
                    source_dirty=_catalog_boolean(entry, "source_dirty", context=context),
                    signature=_catalog_text(entry, "signature", context=context),
                    app_sha256=app_sha256,
                    app_size=app_size,
                    source_tree_sha256=source_tree_sha256,
                    bootstrap_source_project_version=bootstrap_source,
                )
            )
            if candidates[-1].signature != "none":
                raise CatalogError(f"{context} signature must be 'none' for catalog schema 1")
        parsed_channels[channel] = tuple(candidates)
    return CatalogDocument(sha256=_sha256(raw), channels=parsed_channels)


def select_catalog_candidate(
    catalog: CatalogDocument,
    *,
    channel: str,
    candidate_id: str,
) -> CatalogCandidate:
    if channel not in {"stable", "test"}:
        raise CatalogError("catalog channel must be stable or test")
    candidates = catalog.channels[channel]
    if not candidates:
        raise CatalogError(f"catalog channel '{channel}' has no candidates")
    candidate = next((item for item in candidates if item.candidate_id == candidate_id), None)
    if candidate is None:
        raise CatalogError(f"candidate '{candidate_id}' is not present in catalog channel '{channel}'")
    if channel == "stable":
        raise CatalogError("stable channel is unavailable because catalog schema 1 has no signature verifier")
    return candidate


def _validate_https_url(url: str, *, label: str) -> urllib.parse.SplitResult:
    if not isinstance(url, str) or not url or url != url.strip():
        raise DownloadError(f"{label} URL must be a non-empty string")
    if any(character.isspace() or ord(character) < 32 for character in url):
        raise DownloadError(f"{label} URL contains unsupported characters")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        raise DownloadError(f"{label} URL is invalid") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise DownloadError(f"{label} URL must be an HTTPS URL without credentials, query, or fragment")
    return parsed


def _resolve_asset_url(catalog_url: str, asset: str) -> str:
    catalog_parts = _validate_https_url(catalog_url, label="catalog")
    _validate_asset_path(asset)
    resolved = urllib.parse.urljoin(catalog_url, asset)
    resolved_parts = _validate_https_url(resolved, label="asset")
    if (resolved_parts.scheme, resolved_parts.hostname, resolved_parts.port) != (
        catalog_parts.scheme,
        catalog_parts.hostname,
        catalog_parts.port,
    ):
        raise DownloadError("catalog asset URL must remain on the catalog origin")
    return resolved


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


def default_url_opener(request: urllib.request.Request, *, timeout: float) -> Any:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def _download_bytes(
    url: str,
    *,
    opener: UrlOpener,
    timeout: float,
    limit: int,
    label: str,
) -> bytes:
    _validate_https_url(url, label=label)
    request = urllib.request.Request(url, headers={"User-Agent": f"osracer-firmware-update/{TOOL_VERSION}"})
    response: Any = None
    try:
        response = opener(request, timeout=timeout)
        effective_url = response.geturl() if hasattr(response, "geturl") else url
        if effective_url != url:
            raise DownloadError(f"{label} download redirected; redirects are not accepted")
        status = response.getcode() if hasattr(response, "getcode") else 200
        if status != 200:
            raise DownloadError(f"{label} download returned HTTP status {status}")

        headers = getattr(response, "headers", None)
        content_length = headers.get("Content-Length") if headers is not None else None
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except (TypeError, ValueError):
                raise DownloadError(f"{label} download has an invalid Content-Length") from None
            if declared_length < 0 or declared_length > limit:
                raise DownloadError(f"{label} download exceeds the {limit}-byte limit")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(min(DOWNLOAD_READ_SIZE, limit + 1 - total))
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise DownloadError(f"{label} download returned non-binary data")
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise DownloadError(f"{label} download exceeds the {limit}-byte limit")
        if content_length is not None and total != declared_length:
            raise DownloadError(f"{label} download was truncated")
        return b"".join(chunks)
    except DownloadError:
        raise
    except (TimeoutError, socket.timeout, urllib.error.URLError, OSError):
        raise DownloadError(f"{label} download failed or timed out") from None
    except Exception:
        raise DownloadError(f"{label} download failed safely") from None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _default_cache_directory() -> Path:
    configured = os.environ.get("XDG_CACHE_HOME")
    root = Path(configured).expanduser() if configured else Path.home() / ".cache"
    if not root.is_absolute():
        root = Path.home() / ".cache"
    return root / "osracer" / "firmware-update"


def _inside_repository(path: Path) -> bool:
    try:
        return path.resolve(strict=False).is_relative_to(REPOSITORY_ROOT)
    except OSError:
        return True


def _ensure_cache_directory(directory: Path) -> Path:
    directory = directory.expanduser()
    if not directory.is_absolute():
        raise DownloadError("firmware cache directory must be absolute")
    if _inside_repository(directory):
        raise DownloadError("firmware cache directory must remain outside the source repository")
    current = Path(directory.anchor)
    for part in directory.parts[1:]:
        current /= part
        if current.is_symlink():
            raise DownloadError("firmware cache directory must not traverse symbolic links")
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        raise DownloadError("could not create the private firmware cache directory") from None
    if directory.is_symlink() or not directory.is_dir():
        raise DownloadError("firmware cache path is not a private directory")
    return directory


def _validate_outer_package(data: bytes, candidate: CatalogCandidate) -> None:
    if len(data) != candidate.package_size:
        raise DownloadError("downloaded package size does not match the catalog")
    if _sha256(data) != candidate.package_sha256:
        raise DownloadError("downloaded package SHA256 does not match the catalog")


def _validate_catalog_manifest(release: ReleasePackage, candidate: CatalogCandidate) -> None:
    comparisons = (
        ("ProfileID", release.target.profile_id, candidate.target.profile_id),
        ("hardware", release.target.hardware, candidate.target.hardware),
        ("NVS schema", release.target.nvs_schema, candidate.target.nvs_schema),
        ("Proto", release.target.protocol, candidate.target.protocol),
        ("ProjectVer", release.target.project_version, candidate.target.project_version),
    )
    for label, manifest_value, catalog_value in comparisons:
        if manifest_value != catalog_value:
            raise CatalogError(f"downloaded manifest {label} does not match the catalog")
    optional_comparisons = (
        ("App SHA256", release.app_sha256, candidate.app_sha256),
        ("App size", release.app_size, candidate.app_size),
        ("source tree SHA256", release.source_tree_sha256, candidate.source_tree_sha256),
        (
            "bootstrap source ProjectVer",
            release.bootstrap_source_project_version,
            candidate.bootstrap_source_project_version,
        ),
    )
    for label, manifest_value, catalog_value in optional_comparisons:
        if catalog_value is not None and manifest_value != catalog_value:
            raise CatalogError(f"downloaded manifest {label} does not match the catalog")


def _load_valid_cached_package(
    destination: Path,
    candidate: CatalogCandidate,
) -> ReleasePackage | None:
    if destination.is_symlink():
        raise DownloadError("firmware cache destination must not be a symbolic link")
    if not destination.exists():
        return None
    if not destination.is_file():
        raise DownloadError("firmware cache destination is not a regular file")
    try:
        if destination.stat().st_size != candidate.package_size:
            return None
        data = destination.read_bytes()
    except OSError:
        raise DownloadError("could not read the firmware cache safely") from None
    if _sha256(data) != candidate.package_sha256:
        return None
    release = load_release_package(destination)
    _validate_catalog_manifest(release, candidate)
    return release


def _stage_package_atomically(
    data: bytes,
    destination: Path,
    candidate: CatalogCandidate,
) -> ReleasePackage:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".partial-", suffix=".zip", dir=destination.parent)
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        release = load_release_package(temporary_path)
        _validate_catalog_manifest(release, candidate)
        if destination.is_symlink():
            raise DownloadError("firmware cache destination must not be a symbolic link")
        os.replace(temporary_path, destination)
        temporary_path = None
        return release
    except FirmwareUpdateError:
        raise
    except OSError:
        raise DownloadError("could not atomically store the validated firmware package") from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _emit_catalog_warning(candidate: CatalogCandidate, output_func: Callable[[str], None]) -> None:
    output_func(f"WARNING: candidate {candidate.candidate_id}: {candidate.risk_label()}")


def acquire_catalog_release(
    *,
    catalog_url: str,
    channel: str,
    candidate_id: str,
    opener: UrlOpener = default_url_opener,
    cache_directory: Path | None = None,
    timeout: float = DEFAULT_DOWNLOAD_TIMEOUT,
    output_func: Callable[[str], None] = print,
) -> tuple[ReleasePackage, UpdateSource]:
    catalog_raw = _download_bytes(
        catalog_url,
        opener=opener,
        timeout=timeout,
        limit=MAX_CATALOG_BYTES,
        label="catalog",
    )
    catalog = parse_catalog(catalog_raw)
    candidate = select_catalog_candidate(catalog, channel=channel, candidate_id=candidate_id)
    if candidate.channel == "test":
        _emit_catalog_warning(candidate, output_func)

    directory = _ensure_cache_directory(cache_directory or _default_cache_directory())
    destination = directory / f"{candidate.candidate_id}-{candidate.package_sha256[:16]}.zip"
    cached = _load_valid_cached_package(destination, candidate)
    if cached is not None:
        return cached, UpdateSource(kind="catalog", candidate=candidate, catalog_sha256=catalog.sha256)

    asset_url = _resolve_asset_url(catalog_url, candidate.asset)
    package_data = _download_bytes(
        asset_url,
        opener=opener,
        timeout=timeout,
        limit=MAX_PACKAGE_DOWNLOAD_BYTES,
        label="package",
    )
    _validate_outer_package(package_data, candidate)
    release = _stage_package_atomically(package_data, destination, candidate)
    return release, UpdateSource(kind="catalog", candidate=candidate, catalog_sha256=catalog.sha256)


class AuditLogger:
    """Append-only, private JSONL audit log that never records package paths or data payloads."""

    def __init__(self, directory: Path | None):
        if directory is None:
            configured = os.environ.get("XDG_STATE_HOME")
            state_root = Path(configured).expanduser() if configured else Path.home() / ".local" / "state"
            if not state_root.is_absolute():
                state_root = Path.home() / ".local" / "state"
            directory = state_root / "osracer" / "firmware-update"
        directory = directory.expanduser()
        if not directory.is_absolute() or _inside_repository(directory):
            raise AuditError("firmware audit directory must be absolute and outside the source repository")
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            filename = f"firmware-update-{timestamp}-{uuid.uuid4().hex[:8]}.jsonl"
            self.path = directory / filename
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self._handle = os.fdopen(descriptor, "w", encoding="utf-8")
        except OSError:
            raise AuditError("could not create the firmware update audit log") from None

    def event(self, step: str, status: str, **details: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "step": step,
            "status": status,
            **details,
        }
        try:
            self._handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())
        except OSError:
            raise AuditError("could not write the firmware update audit log") from None

    def close(self) -> None:
        try:
            self._handle.close()
        except OSError:
            pass


class SerialTransport:
    def __init__(
        self,
        connection: SerialConnection,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.connection = connection
        self.monotonic = monotonic
        self.sleep = sleep

    def send_line(self, command: str) -> None:
        try:
            payload = (command + "\n").encode("ascii")
            written = self.connection.write(payload)
            if written is not None and written != len(payload):
                raise OSError("partial serial write")
            self.connection.flush()
        except Exception:
            raise SerialCommunicationError("serial write failed during firmware update") from None

    def read_line(self) -> str | None:
        try:
            raw = self.connection.readline()
        except Exception:
            raise SerialCommunicationError("serial read failed during firmware update") from None
        if not raw:
            return None
        if isinstance(raw, bytes):
            return raw.decode("ascii", errors="replace").rstrip("\r\n")
        return str(raw).rstrip("\r\n")

    def wait_for(
        self,
        *,
        label: str,
        prefixes: tuple[str, ...],
        parser: Callable[[str], Any],
        timeout: float,
    ) -> Any:
        deadline = self.monotonic() + timeout
        while self.monotonic() < deadline:
            line = self.read_line()
            if not line:
                self.sleep(min(0.005, max(0.0, deadline - self.monotonic())))
                continue
            if line.startswith("ERROR"):
                reason = re.sub(r"^ERROR(?::|\s+fw|\s+migrate)?\s*", "", line).strip()
                reason = reason or "unspecified"
                raise DeviceRejectedError(
                    f"device rejected operation while waiting for {label}: {reason}",
                    stage=label,
                    device_reason=reason,
                )
            if line.startswith(prefixes):
                return parser(line)
        raise ResponseTimeoutError(f"timed out waiting for {label}")


def parse_firmware_version(line: str) -> FirmwareVersion:
    if not line.startswith("FW_VERSION:"):
        raise ProtocolError("malformed fw version response")
    payload = line[len("FW_VERSION:") :].strip()
    fields: dict[str, str] = {}
    for item in payload.split(","):
        if "=" not in item:
            raise ProtocolError("malformed fw version response")
        key, value = (part.strip() for part in item.split("=", 1))
        if not key or not value or key in fields:
            raise ProtocolError("malformed or duplicate fw version field")
        fields[key] = value
    project_version = fields.get("ProjectVer")
    if not project_version:
        raise ProtocolError("fw version response is missing ProjectVer")
    allowed = {"ProjectVer", "Proto", "Product", "Firmware", "Hardware", "Release", "Git", "Dirty", "Build"}
    if set(fields) - allowed:
        raise ProtocolError("fw version response contains an unsupported field")
    legacy = any(name in fields for name in ("Product", "Firmware", "Hardware", "Release", "Git", "Dirty", "Build"))
    return FirmwareVersion(
        project_version=project_version,
        protocol=fields.get("Proto"),
        format="legacy_long" if legacy else "compact",
        product=fields.get("Product"),
        firmware=fields.get("Firmware"),
        hardware=fields.get("Hardware"),
        release=fields.get("Release"),
        git=fields.get("Git"),
        dirty=fields.get("Dirty"),
        build=fields.get("Build"),
    )


def parse_profile_status(line: str) -> ProfileStatus:
    match = re.fullmatch(
        r"PROFILE:\s*ID=([^,]+),\s*Schema=(\d+),\s*State=([^,\s]+),\s*"
        r"Motion=(Yes|No),\s*Writes=(Yes|No)",
        line,
    )
    if not match:
        raise ProtocolError("malformed profile response")
    return ProfileStatus(
        profile_id=match.group(1).strip(),
        nvs_schema=int(match.group(2)),
        state=match.group(3),
        motion_ok=match.group(4) == "Yes",
        writes_ok=match.group(5) == "Yes",
    )


def _config_value_bytes(item: VehicleConfigItem, expected_size: int) -> bytes:
    if item.state == "UNSET":
        if item.value != "-":
            raise ProtocolError("UNSET configuration item must use '-' as its value")
        return b""
    if item.state != "SET":
        raise ProtocolError("configuration export contains a non-restorable item")
    try:
        if item.value_type == "U8":
            value = int(item.value, 10)
            if not 0 <= value <= 0xFF:
                raise ValueError
            encoded = bytes((value,))
        elif item.value_type == "U32":
            value = int(item.value, 10)
            if not 0 <= value <= 0xFFFFFFFF:
                raise ValueError
            encoded = value.to_bytes(4, "big")
        elif item.value_type == "I32":
            value = int(item.value, 10)
            if not -(2**31) <= value < 2**31:
                raise ValueError
            encoded = value.to_bytes(4, "big", signed=True)
        elif item.value_type == "BLOB":
            if not re.fullmatch(r"[0-9A-Fa-f]+", item.value) or len(item.value) % 2:
                raise ValueError
            encoded = bytes.fromhex(item.value)
        else:
            raise ValueError
    except ValueError:
        raise ProtocolError(f"configuration item {item.name} has an invalid typed value") from None
    if len(encoded) != expected_size:
        raise ProtocolError(f"configuration item {item.name} has an invalid value size")
    return encoded


def calculate_vehicle_config_sha256(
    source_project_version: str,
    source_profile: str,
    source_schema: int,
    items: tuple[VehicleConfigItem, ...] | list[VehicleConfigItem],
) -> str:
    """Rebuild the firmware's OSRVCFG1 digest byte-for-byte."""

    if len(items) != CONFIG_ITEM_COUNT:
        raise ProtocolError(f"configuration backup must contain exactly {CONFIG_ITEM_COUNT} items")
    digest = hashlib.sha256()

    def string(value: str) -> None:
        try:
            digest.update(value.encode("utf-8") + b"\x00")
        except UnicodeEncodeError:
            raise ProtocolError("configuration identity is not valid UTF-8") from None

    def u32(value: int) -> None:
        if isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFF:
            raise ProtocolError("configuration schema is out of range")
        digest.update(value.to_bytes(4, "big"))

    string(CONFIG_HASH_DOMAIN)
    string(source_project_version)
    string(source_profile)
    u32(source_schema)
    u32(CONFIG_ITEM_COUNT)
    state_ids = {"SET": 1, "UNSET": 2, "ERROR": 3}
    type_ids = {"U8": 0, "U32": 1, "I32": 2, "BLOB": 3}
    for index, (item, descriptor) in enumerate(zip(items, VEHICLE_CONFIG_FIELDS)):
        expected_name, expected_type, expected_size = descriptor
        if item.name != expected_name or item.value_type != expected_type:
            raise ProtocolError(f"configuration item {index + 1} does not match the allowlist order")
        if item.state not in state_ids:
            raise ProtocolError(f"configuration item {item.name} has an invalid state")
        value = _config_value_bytes(item, expected_size)
        string(item.name)
        digest.update(bytes((state_ids[item.state], type_ids[item.value_type])))
        u32(len(value))
        digest.update(value)
    return digest.hexdigest()


def compare_vehicle_config_semantics(
    expected: tuple[VehicleConfigItem, ...] | list[VehicleConfigItem],
    current: tuple[VehicleConfigItem, ...] | list[VehicleConfigItem],
) -> VehicleConfigSemanticComparison:
    """Compare persistent config while honoring boot-time level recalibration."""

    if len(expected) != CONFIG_ITEM_COUNT or len(current) != CONFIG_ITEM_COUNT:
        raise ProtocolError(
            f"configuration comparison requires exactly {CONFIG_ITEM_COUNT} items"
        )
    non_level_mismatches: list[str] = []
    changed_level_fields: list[str] = []
    invalid_level_fields: list[str] = []
    level_init_status = "invalid"

    for index, (expected_item, current_item, descriptor) in enumerate(
        zip(expected, current, VEHICLE_CONFIG_FIELDS)
    ):
        expected_name, expected_type, expected_size = descriptor
        if (
            expected_item.name != expected_name
            or current_item.name != expected_name
            or expected_item.value_type != expected_type
            or current_item.value_type != expected_type
        ):
            raise ProtocolError(
                f"configuration comparison item {index + 1} does not match the allowlist order"
            )
        expected_value = _config_value_bytes(expected_item, expected_size)
        current_value = _config_value_bytes(current_item, expected_size)

        if expected_name in LEVEL_CALIBRATION_OFFSET_FIELDS:
            if current_item.state != "SET":
                invalid_level_fields.append(expected_name)
            else:
                level_offset = struct.unpack("<f", current_value)[0]
                if not math.isfinite(level_offset) or not -2.0 <= level_offset <= 2.0:
                    invalid_level_fields.append(expected_name)
                elif expected_item.state != current_item.state or expected_value != current_value:
                    changed_level_fields.append(expected_name)
            continue

        if expected_name == LEVEL_CALIBRATION_INIT_FIELD:
            current_healthy = current_item.state == "SET" and current_value == b"\x01"
            if not current_healthy:
                invalid_level_fields.append(expected_name)
            elif expected_item.state == "UNSET":
                level_init_status = "refreshed"
                changed_level_fields.append(expected_name)
            elif expected_item.state == "SET" and expected_value == current_value == b"\x01":
                level_init_status = "unchanged"
            else:
                invalid_level_fields.append(expected_name)
            continue

        if expected_item.state != current_item.state or expected_value != current_value:
            non_level_mismatches.append(expected_name)

    level_offset_status = (
        "invalid"
        if any(name in LEVEL_CALIBRATION_OFFSET_FIELDS for name in invalid_level_fields)
        else (
            "refreshed"
            if any(name in LEVEL_CALIBRATION_OFFSET_FIELDS for name in changed_level_fields)
            else "unchanged"
        )
    )
    return VehicleConfigSemanticComparison(
        tuple(non_level_mismatches),
        level_init_status,
        level_offset_status,
        tuple(changed_level_fields),
        tuple(invalid_level_fields),
    )


def _audit_vehicle_config_comparison(
    audit: AuditLogger,
    comparison: VehicleConfigSemanticComparison,
    *,
    phase: str,
) -> None:
    audit.event(
        "config_semantic_compare",
        "ok" if comparison.matches else "mismatch",
        phase=phase,
        non_level_item_count=NON_LEVEL_CONFIG_ITEM_COUNT,
        non_level_match=not comparison.non_level_mismatches,
        non_level_mismatch_fields=list(comparison.non_level_mismatches),
        level_init_status=comparison.level_init_status,
        level_offset_status=comparison.level_offset_status,
        changed_level_fields=list(comparison.changed_level_fields),
        invalid_level_fields=list(comparison.invalid_level_fields),
    )


def _vehicle_config_result_rows(
    comparison: VehicleConfigSemanticComparison,
) -> list[tuple[str, str]]:
    return [
        (
            "Non-level config",
            (
                f"MATCH ({NON_LEVEL_CONFIG_ITEM_COUNT} items)"
                if not comparison.non_level_mismatches
                else "MISMATCH"
            ),
        ),
        ("Level calibration init", comparison.level_init_status.upper()),
        ("Level calibration offsets", comparison.level_offset_status.upper()),
    ]


def calculate_vehicle_restore_sha256(
    backup_sha256: str,
    source: TargetProfile,
    target: TargetProfile,
) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", backup_sha256):
        raise ProtocolError("restore backup SHA256 is invalid")
    digest = hashlib.sha256()

    def string(value: str) -> None:
        digest.update(value.encode("utf-8") + b"\x00")

    def u32(value: int) -> None:
        if isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFF:
            raise ProtocolError("restore schema is out of range")
        digest.update(value.to_bytes(4, "big"))

    string(CONFIG_RESTORE_HASH_DOMAIN)
    string(backup_sha256)
    string(source.project_version)
    string(source.profile_id)
    u32(source.nvs_schema)
    string(target.project_version)
    string(target.profile_id)
    u32(target.nvs_schema)
    u32(CONFIG_ITEM_COUNT)
    return digest.hexdigest()


def _parse_config_identity(line: str, prefix: str, protocol: str) -> TargetProfile:
    match = re.fullmatch(
        rf"{re.escape(prefix)}: ProjectVer=([^,\s]+), Profile=([^,\s]+), Schema=(\d+)",
        line,
    )
    if not match:
        raise ProtocolError(f"malformed {prefix.lower()} response")
    if len(match.group(1)) > 31 or len(match.group(2)) > 15:
        raise ProtocolError(f"{prefix.lower()} identity exceeds the firmware command limits")
    return TargetProfile(
        profile_id=match.group(2),
        hardware="",
        nvs_schema=int(match.group(3)),
        project_version=match.group(1),
        protocol=protocol,
    )


def parse_vehicle_config_export_lines(lines: list[str]) -> VehicleConfigExport:
    """Strictly parse one complete export; unrelated telemetry may be omitted by the caller."""

    expected_lines = CONFIG_ITEM_COUNT + 5
    if len(lines) != expected_lines:
        raise ProtocolError(
            f"configuration export must contain exactly {expected_lines} protocol lines"
        )
    begin = re.fullmatch(
        r"CONFIG_EXPORT_BEGIN: ConfigSchema=(\d+), Proto=([^,\s]+), Items=(\d+)",
        lines[0],
    )
    if not begin or int(begin.group(1)) != CONFIG_BACKUP_SCHEMA:
        raise ProtocolError("malformed configuration export begin response")
    protocol = begin.group(2)
    if protocol != SUPPORTED_PROTOCOL or int(begin.group(3)) != CONFIG_ITEM_COUNT:
        raise ProtocolError("configuration export contract is unsupported")
    source = _parse_config_identity(lines[1], "CONFIG_EXPORT_SOURCE", protocol)
    target = _parse_config_identity(lines[2], "CONFIG_EXPORT_TARGET", protocol)
    hash_match = re.fullmatch(r"CONFIG_EXPORT_HASH: BackupSHA=([0-9a-f]{64}|none)", lines[3])
    if not hash_match:
        raise ProtocolError("malformed configuration export hash response")

    items: list[VehicleConfigItem] = []
    item_pattern = re.compile(
        r"CONFIG_ITEM: Name=([a-z0-9_.]+), State=(SET|UNSET|ERROR), "
        r"Type=(U8|U32|I32|BLOB), Value=([^,\s]+)(?:, Code=([A-Z0-9_]+))?"
    )
    for index, line in enumerate(lines[4 : 4 + CONFIG_ITEM_COUNT]):
        match = item_pattern.fullmatch(line)
        if not match:
            raise ProtocolError(f"malformed configuration item {index + 1}")
        item = VehicleConfigItem(
            name=match.group(1),
            state=match.group(2),
            value_type=match.group(3),
            value=match.group(4),
        )
        expected_name, expected_type, _expected_size = VEHICLE_CONFIG_FIELDS[index]
        if item.name != expected_name or item.value_type != expected_type:
            raise ProtocolError(f"configuration item {index + 1} is out of order or has the wrong type")
        if item.state == "ERROR" and not match.group(5):
            raise ProtocolError(f"configuration item {item.name} is missing its error code")
        if item.state != "ERROR" and match.group(5):
            raise ProtocolError(f"configuration item {item.name} has an unexpected error code")
        items.append(item)

    end = re.fullmatch(
        r"CONFIG_EXPORT_END: Result=(OK|INCOMPLETE|ERROR), Items=(\d+), "
        r"BackupSHA=([0-9a-f]{64}|none), Reason=([^,\s]+)",
        lines[-1],
    )
    if not end:
        raise ProtocolError("malformed configuration export end response")
    if end.group(1) != "OK":
        raise DevicePreflightError(
            f"configuration export is not restorable: {end.group(1)} ({end.group(4)})"
        )
    if end.group(4) != "ok":
        raise ProtocolError("successful configuration export must end with Reason=ok")
    if int(end.group(2)) != CONFIG_ITEM_COUNT:
        raise ProtocolError("configuration export item count changed at completion")
    declared_hash = hash_match.group(1)
    if declared_hash == "none" or end.group(3) != declared_hash:
        raise ProtocolError("configuration export hash fields do not match")
    computed_hash = calculate_vehicle_config_sha256(
        source.project_version,
        source.profile_id,
        source.nvs_schema,
        items,
    )
    if computed_hash != declared_hash:
        raise ProtocolError("configuration export BackupSHA does not match the host canonical hash")
    return VehicleConfigExport(source, target, tuple(items), computed_hash)


def _receive_vehicle_config_export(
    transport: SerialTransport,
    config: UpdateConfig,
) -> VehicleConfigExport:
    transport.send_line("config export")
    deadline = transport.monotonic() + max(config.response_timeout, DEFAULT_CONFIG_TIMEOUT)
    protocol_lines: list[str] = []
    started = False
    while transport.monotonic() < deadline:
        line = transport.read_line()
        if not line:
            transport.sleep(min(0.005, max(0.0, deadline - transport.monotonic())))
            continue
        if line.startswith("ERROR"):
            reason = re.sub(r"^ERROR\s*", "", line).strip() or "unspecified"
            raise DeviceRejectedError(
                f"device rejected configuration export: {reason}",
                stage="config_export",
                device_reason=reason,
            )
        if line.startswith("OK config"):
            raise ProtocolError("unexpected configuration acknowledgement during export")
        if not line.startswith("CONFIG_"):
            continue
        if line.startswith("CONFIG_EXPORT_BEGIN:"):
            if started:
                raise ProtocolError("configuration export contains a duplicate begin response")
            started = True
        elif not started:
            if line.startswith("CONFIG_EXPORT_END:"):
                terminal = re.fullmatch(
                    r"CONFIG_EXPORT_END: Result=(ERROR|INCOMPLETE), Items=0, "
                    r"BackupSHA=none, Reason=([^,\s]+)",
                    line,
                )
                if terminal:
                    raise DevicePreflightError(
                        f"configuration export failed before transfer: {terminal.group(2)}"
                    )
            raise ProtocolError("configuration export protocol lines arrived before begin")
        if not (
            line.startswith("CONFIG_EXPORT_")
            or line.startswith("CONFIG_ITEM:")
        ):
            raise ProtocolError("configuration export contains an unexpected protocol line")
        protocol_lines.append(line)
        if line.startswith("CONFIG_EXPORT_END:"):
            return parse_vehicle_config_export_lines(protocol_lines)
    raise ResponseTimeoutError("timed out waiting for complete configuration export")


def _receive_vehicle_config_export_when_ready(
    transport: SerialTransport,
    config: UpdateConfig,
) -> VehicleConfigExport:
    deadline = transport.monotonic() + config.level_calibration_timeout
    while True:
        try:
            return _receive_vehicle_config_export(transport, config)
        except (DeviceRejectedError, DevicePreflightError) as error:
            message = str(error)
            retryable = any(
                reason in message
                for reason in ("vehicle_writers_active", "vehicle_nvs_busy")
            )
            remaining = deadline - transport.monotonic()
            if not retryable or remaining <= 0:
                raise
            transport.sleep(min(config.level_calibration_interval, remaining))


def parse_battery_reading(line: str) -> float:
    match = re.fullmatch(r"b\s+([^\s]+)", line)
    if not match:
        raise ProtocolError("malformed battery response")
    try:
        voltage = float(match.group(1))
    except ValueError:
        raise ProtocolError("invalid battery voltage") from None
    if not math.isfinite(voltage):
        raise ProtocolError("invalid battery voltage")
    return voltage


def parse_battery_voltage(line: str) -> float:
    try:
        voltage = parse_battery_reading(line)
    except ProtocolError:
        raise DevicePreflightError("battery voltage is invalid; firmware update refused") from None
    if voltage <= 0:
        raise DevicePreflightError("battery voltage is invalid; firmware update refused")
    return voltage


def _finite_number(text: str, *, label: str, minimum: float, maximum: float) -> float:
    try:
        value = float(text)
    except ValueError:
        raise ProtocolError(f"invalid {label}") from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ProtocolError(f"{label} is outside the accepted range")
    return value


def parse_serial_number(line: str) -> str:
    match = re.fullmatch(r"SN:\s*([0-9A-F]{12})", line)
    if not match:
        raise ProtocolError("malformed serial number response")
    return match.group(1)


def parse_pid(line: str) -> list[float]:
    match = re.fullmatch(r"PID:\s*(\S+)\s+(\S+)\s+(\S+)", line)
    if not match:
        raise ProtocolError("malformed PID response")
    return [_finite_number(value, label="PID value", minimum=-100000.0, maximum=100000.0) for value in match.groups()]


def parse_mag_calibration(line: str) -> list[float]:
    match = re.fullmatch(r"MC:\s*(\S+(?:\s+\S+){11})", line)
    if not match:
        raise ProtocolError("malformed magnetometer calibration response")
    return [
        _finite_number(value, label="magnetometer calibration value", minimum=-100.0, maximum=100.0)
        for value in match.group(1).split()
    ]


def parse_battery_calibration(line: str) -> dict[str, Any]:
    match = re.fullmatch(r"BATTERY:\s*Voltage=(\S+)V,\s*Cal=(User|Default),\s*Scale=(\S+)", line)
    if not match:
        raise ProtocolError("malformed battery calibration response")
    _finite_number(match.group(1), label="battery calibration voltage", minimum=0.01, maximum=100.0)
    return {
        "calibration": match.group(2),
        "scale": _finite_number(match.group(3), label="battery scale", minimum=0.8, maximum=1.2),
    }


def parse_odom_scale(line: str) -> float:
    match = re.fullmatch(r"ODOM_SCALE:\s*(\S+)\s+range=(\S+)\.\.(\S+)", line)
    if not match:
        raise ProtocolError("malformed odometry scale response")
    minimum = _finite_number(match.group(2), label="odometry minimum", minimum=0.01, maximum=10.0)
    maximum = _finite_number(match.group(3), label="odometry maximum", minimum=0.01, maximum=10.0)
    value = _finite_number(match.group(1), label="odometry scale", minimum=0.01, maximum=10.0)
    if minimum > maximum or not minimum <= value <= maximum:
        raise ProtocolError("odometry scale is outside the reported range")
    return value


def parse_steering_trim(line: str) -> dict[str, Any]:
    match = re.fullmatch(r"TRIM:\s*(\S+)deg\s+center_pwm=(\d+)us\s+range=(\S+)\.\.(\S+)deg", line)
    if not match:
        raise ProtocolError("malformed steering trim response")
    minimum = _finite_number(match.group(3), label="trim minimum", minimum=-90.0, maximum=90.0)
    maximum = _finite_number(match.group(4), label="trim maximum", minimum=-90.0, maximum=90.0)
    value = _finite_number(match.group(1), label="steering trim", minimum=-90.0, maximum=90.0)
    center_pwm = int(match.group(2))
    if minimum > maximum or not minimum <= value <= maximum or not 500 <= center_pwm <= 2500:
        raise ProtocolError("steering trim is outside the reported range")
    return {"degrees": value, "center_pwm_us": center_pwm}


def parse_speed_deadband(line: str) -> int:
    match = re.fullmatch(r"SPEED_DEADBAND:\s*(\d+)us\s+range=(\d+)\.\.(\d+)us", line)
    if not match:
        raise ProtocolError("malformed speed deadband response")
    value, minimum, maximum = (int(part) for part in match.groups())
    if minimum > maximum or not minimum <= value <= maximum or maximum > 10000:
        raise ProtocolError("speed deadband is outside the reported range")
    return value


def parse_level_offset(line: str) -> list[float]:
    match = re.fullmatch(r"LEVEL:\s*offset=\[(\S+)\s+(\S+)\s+(\S+)\]g", line)
    if not match:
        raise ProtocolError("malformed level offset response")
    return [_finite_number(value, label="level offset", minimum=-2.0, maximum=2.0) for value in match.groups()]


def parse_level_calibration_status(line: str) -> bool:
    match = re.fullmatch(
        r"IMU:\s*BiasReady=(?:Yes|No),\s*LevelCal=(Yes|No),\s*"
        r"GyroBias=[^,]+,[^,]+,[^,]+,\s*LevelOffset=[^,]+,[^,]+,[^,]+",
        line,
    )
    if not match:
        raise ProtocolError("malformed IMU calibration status response")
    return match.group(1) == "Yes"


def parse_vehicle_static_status(line: str) -> bool:
    match = re.fullmatch(
        r"Status:\s*Speed=[^,]+,\s*Target=[^,]+,\s*Voltage=[^,]+,\s*"
        r"Control=[^,]+,\s*SpeedMode=[^,]+,\s*Static=(Yes|No)",
        line,
    )
    if not match:
        raise ProtocolError("malformed vehicle status response")
    return match.group(1) == "Yes"


def parse_migration_status(line: str) -> tuple[bool, str, str]:
    match = re.fullmatch(r"MIGRATION:\s*Enabled=(Yes|No),\s*State=([^,\s]+),\s*Hash=([0-9a-f]{12}|none)", line)
    if not match:
        raise ProtocolError("malformed migration status response")
    return match.group(1) == "Yes", match.group(2), match.group(3)


def parse_migration_validate(line: str) -> str:
    match = re.fullmatch(r"OK migrate validate hash=([0-9a-f]{12})", line)
    if not match:
        raise ProtocolError("malformed migration validate response")
    return match.group(1)


def parse_migration_apply(line: str) -> tuple[str, str]:
    match = re.fullmatch(
        r"OK migrate apply state=READY reboot_required=Yes cleanup=(Done|Deferred) hash=([0-9a-f]{12})",
        line,
    )
    if not match:
        raise ProtocolError("malformed migration apply response")
    return match.group(1), match.group(2)


def parse_firmware_status(line: str) -> FirmwareStatus:
    match = re.fullmatch(
        r"FW:\s*active=(Yes|No)\s+written=(\d+)\s+size=(\d+)\s+next_seq=(\d+)\s+"
        r"running=([^\s]+)\s+next=([^\s]+)",
        line,
    )
    if not match:
        raise ProtocolError("malformed fw status response")
    status = FirmwareStatus(
        active=match.group(1) == "Yes",
        written=int(match.group(2)),
        size=int(match.group(3)),
        next_seq=int(match.group(4)),
        running=match.group(5),
        next_partition=match.group(6),
    )
    if status.written > status.size:
        raise ProtocolError("fw status reports written bytes beyond image size")
    return status


def parse_begin_ack(line: str) -> tuple[str, int]:
    match = re.fullmatch(r"OK fw begin part=([^\s]+) size=(\d+)", line)
    if not match:
        raise ProtocolError("malformed fw begin response")
    return match.group(1), int(match.group(2))


def parse_data_ack(line: str) -> tuple[int, int]:
    match = re.fullmatch(r"OK fw data (\d+) (\d+)", line)
    if not match:
        raise ProtocolError("malformed fw data response")
    return int(match.group(1)), int(match.group(2))


def parse_end_ack(line: str) -> bool:
    if line != "OK fw reboot":
        raise ProtocolError("malformed fw end response")
    return True


def parse_abort_ack(line: str) -> bool:
    if line != "OK fw abort":
        raise ProtocolError("malformed fw abort response")
    return True


def default_serial_factory(**kwargs: Any) -> SerialConnection:
    try:
        import serial
    except ImportError:
        raise SerialUnavailableError("pyserial is not installed; install python3-serial") from None
    return serial.Serial(**kwargs)


def _close_quietly(connection: SerialConnection | None) -> None:
    if connection is None:
        return
    try:
        connection.close()
    except Exception:
        pass


def _serial_device_in_use(port: str) -> bool:
    """Check Linux process descriptors for an already-open character device."""

    if not sys.platform.startswith("linux"):
        return False
    try:
        target = os.stat(port)
    except OSError:
        return False
    if not stat.S_ISCHR(target.st_mode):
        return False

    proc_root = Path("/proc")
    try:
        processes = tuple(proc_root.iterdir())
    except OSError:
        return False
    own_pid = os.getpid()
    for process in processes:
        if not process.name.isdigit() or int(process.name) == own_pid:
            continue
        try:
            descriptors = tuple((process / "fd").iterdir())
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                opened = descriptor.stat()
            except OSError:
                continue
            if stat.S_ISCHR(opened.st_mode) and opened.st_rdev == target.st_rdev:
                return True
    return False


def _open_exclusive(
    config: UpdateConfig,
    serial_factory: SerialFactory,
    *,
    in_use_check: Callable[[str], bool] = _serial_device_in_use,
) -> SerialConnection:
    if in_use_check(config.port):
        raise SerialUnavailableError(
            "serial port is already open by another process; stop chassis and other serial tools, then retry"
        )
    connection: SerialConnection | None = None
    try:
        connection = serial_factory(
            port=config.port,
            baudrate=config.baud,
            timeout=min(0.1, config.response_timeout),
            write_timeout=config.response_timeout,
            exclusive=True,
        )
        if not getattr(connection, "is_open", True):
            raise OSError("closed")
        if in_use_check(config.port):
            raise OSError("serial port became busy")
        connection.reset_input_buffer()
        connection.reset_output_buffer()
        return connection
    except FirmwareUpdateError:
        _close_quietly(connection)
        raise
    except Exception:
        _close_quietly(connection)
        raise SerialUnavailableError(
            "could not open the serial port exclusively; stop chassis and other serial tools, then retry"
        ) from None


def _drain_safe_stop(transport: SerialTransport, audit: AuditLogger, *, phase: str) -> None:
    while True:
        line = transport.read_line()
        if not line:
            return
        if line.startswith("ERROR"):
            audit.event("safe_stop", "warning", phase=phase, device_rejected=True)


def _query_value(
    transport: SerialTransport,
    config: UpdateConfig,
    *,
    command: str,
    label: str,
    prefix: str,
    parser: Callable[[str], Any],
) -> Any:
    transport.send_line(command)
    return transport.wait_for(
        label=label,
        prefixes=(prefix,),
        parser=parser,
        timeout=config.response_timeout,
    )


def _query_configuration(transport: SerialTransport, config: UpdateConfig) -> dict[str, Any]:
    return {
        "serial_number": _query_value(transport, config, command="sn get", label="serial number", prefix="SN:", parser=parse_serial_number),
        "pid": _query_value(transport, config, command="pid get", label="PID", prefix="PID:", parser=parse_pid),
        "magnetometer": _query_value(transport, config, command="mc get", label="magnetometer calibration", prefix="MC:", parser=parse_mag_calibration),
        "battery": _query_value(transport, config, command="battery get", label="battery calibration", prefix="BATTERY:", parser=parse_battery_calibration),
        "odom_scale": _query_value(transport, config, command="odom scale get", label="odometry scale", prefix="ODOM_SCALE:", parser=parse_odom_scale),
        "steering_trim": _query_value(transport, config, command="trim get", label="steering trim", prefix="TRIM:", parser=parse_steering_trim),
        "speed_deadband_us": _query_value(transport, config, command="speed deadband get", label="speed deadband", prefix="SPEED_DEADBAND:", parser=parse_speed_deadband),
        "level_offset": _query_value(transport, config, command="level get", label="level offset", prefix="LEVEL:", parser=parse_level_offset),
    }


def _snapshot_document(snapshot: DeviceSnapshot) -> dict[str, Any]:
    firmware_status = snapshot.firmware_status
    return {
        "schema": 1,
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "device_serial": (snapshot.configuration or {}).get("serial_number"),
        "firmware": {
            "project_version": snapshot.version.project_version,
            "protocol": snapshot.version.protocol,
            "format": snapshot.version.format,
            "product": snapshot.version.product,
            "firmware": snapshot.version.firmware,
            "hardware": snapshot.version.hardware,
            "release": snapshot.version.release,
            "git": snapshot.version.git,
            "dirty": snapshot.version.dirty,
            "build": snapshot.version.build,
        },
        "profile": None if snapshot.profile is None else {
            "id": snapshot.profile.profile_id,
            "nvs_schema": snapshot.profile.nvs_schema,
            "state": snapshot.profile.state,
            "motion_ok": snapshot.profile.motion_ok,
            "writes_ok": snapshot.profile.writes_ok,
        },
        "fw_status": None if firmware_status is None else {
            "active": firmware_status.active,
            "written": firmware_status.written,
            "size": firmware_status.size,
            "next_seq": firmware_status.next_seq,
            "running": firmware_status.running,
            "next_partition": firmware_status.next_partition,
        },
        "battery_voltage": snapshot.voltage,
        "configuration": snapshot.configuration,
        "unavailable_fields": list(snapshot.unavailable_fields),
    }


def _default_snapshot_directory() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    state_root = Path(configured).expanduser() if configured else Path.home() / ".local" / "state"
    if not state_root.is_absolute():
        state_root = Path.home() / ".local" / "state"
    return state_root / "osracer" / "firmware-update" / "snapshots"


def _default_transaction_directory() -> Path:
    return _default_snapshot_directory().parent / "transactions"


def _write_snapshot(snapshot: DeviceSnapshot, directory: Path | None) -> tuple[str, Path]:
    directory = (directory or _default_snapshot_directory()).expanduser()
    if not directory.is_absolute() or _inside_repository(directory):
        raise AuditError("firmware snapshot directory must be absolute and outside the source repository")
    try:
        if directory.is_symlink():
            raise OSError("snapshot directory is a symlink")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink():
            raise OSError("snapshot directory is a symlink")
        directory = directory.resolve()
        if _inside_repository(directory):
            raise OSError("snapshot directory resolves inside repository")
        os.chmod(directory, 0o700)
        raw = (json.dumps(_snapshot_document(snapshot), sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")
        digest = _sha256(raw)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        final = directory / f"device-snapshot-{timestamp}-{digest[:12]}-{uuid.uuid4().hex[:8]}.json"
        descriptor, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=directory)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, final)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return digest, final
    except OSError:
        raise AuditError("could not create the private firmware configuration snapshot") from None


def _transaction_document(transaction: MigrationTransaction) -> dict[str, Any]:
    return {
        "schema": 1,
        "target": {
            "profile_id": transaction.target.profile_id,
            "hardware": transaction.target.hardware,
            "nvs_schema": transaction.target.nvs_schema,
            "project_version": transaction.target.project_version,
            "protocol": transaction.target.protocol,
        },
        "package": {
            "manifest_sha256": transaction.manifest_sha256,
            "app_sha256": transaction.app_sha256,
            "app_bytes": transaction.app_bytes,
        },
        "device_serial_sha256": transaction.device_serial_sha256,
        "source_snapshot_sha256": transaction.source_snapshot_sha256,
        "source_project_version": transaction.source_project_version,
    }


def _parse_transaction(raw: bytes) -> MigrationTransaction:
    try:
        document = json.loads(raw.decode("utf-8"))
        target = document["target"]
        package = document["package"]
        transaction = MigrationTransaction(
            target=TargetProfile(
                profile_id=target["profile_id"],
                hardware=target["hardware"],
                nvs_schema=target["nvs_schema"],
                project_version=target["project_version"],
                protocol=target["protocol"],
            ),
            manifest_sha256=package["manifest_sha256"],
            app_sha256=package["app_sha256"],
            app_bytes=package["app_bytes"],
            device_serial_sha256=document["device_serial_sha256"],
            source_snapshot_sha256=document["source_snapshot_sha256"],
            source_project_version=document["source_project_version"],
        )
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise PostInstallError(
            "migration transaction is malformed",
            outcome="migration_pending",
            stage="migration_transaction",
        ) from None
    if (
        not re.fullmatch(r"[0-9a-f]{64}", transaction.manifest_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", transaction.app_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", transaction.device_serial_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", transaction.source_snapshot_sha256)
        or isinstance(transaction.app_bytes, bool)
        or not isinstance(transaction.app_bytes, int)
        or transaction.app_bytes <= 0
        or not transaction.source_project_version
    ):
        raise PostInstallError(
            "migration transaction fields are invalid",
            outcome="migration_pending",
            stage="migration_transaction",
        )
    return transaction


def _transaction_matches_release(
    transaction: MigrationTransaction,
    release: ReleasePackage,
    device_serial_sha256: str,
) -> bool:
    return (
        transaction.target == release.target
        and transaction.manifest_sha256 == release.manifest_sha256
        and transaction.app_sha256 == release.app_sha256
        and transaction.app_bytes == release.app_size
        and transaction.device_serial_sha256 == device_serial_sha256
    )


def _ensure_private_directory(directory: Path, *, label: str) -> Path:
    directory = directory.expanduser()
    if not directory.is_absolute() or _inside_repository(directory) or directory.is_symlink():
        raise AuditError(f"{label} directory must be absolute, private, and outside the source repository")
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink():
            raise OSError("symlink")
        directory = directory.resolve()
        if _inside_repository(directory) or directory.stat().st_uid != os.getuid():
            raise OSError("inside repository")
        os.chmod(directory, 0o700)
        return directory
    except OSError:
        raise AuditError(f"could not prepare the private {label} directory") from None


def _default_config_backup_directory() -> Path:
    return _default_snapshot_directory().parent / "backups"


def _vehicle_backup_document(
    exported: VehicleConfigExport,
    release: ReleasePackage | None,
    audit_path: Path | None = None,
) -> dict[str, Any]:
    package: dict[str, Any] | None = None
    if release is not None:
        package = {
            "package_sha256": release.package_sha256,
            "package_bytes": release.package_size,
            "manifest_sha256": release.manifest_sha256,
            "app_sha256": release.app_sha256,
            "app_bytes": release.app_size,
            "source_tree_sha256": release.source_tree_sha256,
        }
    return {
        "schema": CONFIG_BACKUP_SCHEMA,
        "kind": "osracer_vehicle_config_backup",
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "hash_domain": CONFIG_HASH_DOMAIN,
        "source": {
            "project_version": exported.source.project_version,
            "profile_id": exported.source.profile_id,
            "nvs_schema": exported.source.nvs_schema,
            "protocol": exported.source.protocol,
        },
        "target": {
            "project_version": exported.target.project_version,
            "profile_id": exported.target.profile_id,
            "nvs_schema": exported.target.nvs_schema,
            "protocol": exported.target.protocol,
        },
        "item_count": CONFIG_ITEM_COUNT,
        "items": [
            {
                "name": item.name,
                "state": item.state,
                "type": item.value_type,
                "value": item.value,
            }
            for item in exported.items
        ],
        "backup_sha256": exported.backup_sha256,
        "package": package,
        "audit": None if audit_path is None else {"path": str(audit_path)},
    }


def _write_vehicle_config_backup(
    exported: VehicleConfigExport,
    release: ReleasePackage | None,
    directory: Path | None,
    *,
    audit_path: Path | None = None,
) -> tuple[Path, str]:
    directory = _ensure_private_directory(
        directory or _default_config_backup_directory(),
        label="vehicle configuration backup",
    )
    raw = (
        json.dumps(
            _vehicle_backup_document(exported, release, audit_path),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    ).encode("utf-8")
    file_sha256 = _sha256(raw)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = directory / (
        f"vehicle-config-{timestamp}-{exported.backup_sha256[:12]}-{uuid.uuid4().hex[:8]}.json"
    )
    descriptor, temporary = tempfile.mkstemp(prefix=".vehicle-config-", dir=directory)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, final)
        os.chmod(final, 0o600)
        directory_descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise AuditError("could not atomically store the vehicle configuration backup") from None
    loaded, loaded_file_sha = _load_vehicle_config_backup(final)
    if loaded != exported or loaded_file_sha != file_sha256:
        raise AuditError("persisted vehicle configuration backup failed readback verification")
    return final, file_sha256


def _load_vehicle_config_backup(path: Path) -> tuple[VehicleConfigExport, str]:
    path = path.expanduser()
    try:
        if (
            not path.is_absolute()
            or _inside_repository(path)
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_uid != os.getuid()
            or path.stat().st_mode & 0o077
        ):
            raise OSError("unsafe backup")
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AuditError(
            "vehicle configuration backup must be a private 0600 JSON file outside the repository"
        ) from None
    try:
        if (
            not isinstance(document, dict)
            or document.get("schema") != CONFIG_BACKUP_SCHEMA
            or document.get("kind") != "osracer_vehicle_config_backup"
            or document.get("hash_domain") != CONFIG_HASH_DOMAIN
            or document.get("item_count") != CONFIG_ITEM_COUNT
        ):
            raise ValueError
        source_document = document["source"]
        target_document = document["target"]
        item_documents = document["items"]
        if not isinstance(source_document, dict) or not isinstance(target_document, dict):
            raise ValueError
        if not isinstance(item_documents, list) or len(item_documents) != CONFIG_ITEM_COUNT:
            raise ValueError

        def identity(value: dict[str, Any]) -> TargetProfile:
            project = value["project_version"]
            profile_id = value["profile_id"]
            schema = value["nvs_schema"]
            protocol = value["protocol"]
            if (
                not isinstance(project, str)
                or not project
                or not isinstance(profile_id, str)
                or not profile_id
                or isinstance(schema, bool)
                or not isinstance(schema, int)
                or schema < 0
                or protocol != SUPPORTED_PROTOCOL
            ):
                raise ValueError
            return TargetProfile(profile_id, "", schema, project, protocol)

        items: list[VehicleConfigItem] = []
        for entry in item_documents:
            if not isinstance(entry, dict) or set(entry) != {"name", "state", "type", "value"}:
                raise ValueError
            if not all(isinstance(entry[key], str) for key in entry):
                raise ValueError
            items.append(
                VehicleConfigItem(entry["name"], entry["state"], entry["type"], entry["value"])
            )
        source = identity(source_document)
        target = identity(target_document)
        calculated = calculate_vehicle_config_sha256(
            source.project_version,
            source.profile_id,
            source.nvs_schema,
            items,
        )
        if document.get("backup_sha256") != calculated:
            raise ValueError
    except (KeyError, TypeError, ValueError, ProtocolError):
        raise AuditError("vehicle configuration backup content is invalid or has been altered") from None
    return VehicleConfigExport(source, target, tuple(items), calculated), _sha256(raw)


def _validate_vehicle_backup_release(path: Path, release: ReleasePackage) -> None:
    try:
        document = json.loads(path.expanduser().read_text(encoding="utf-8"))
        package = document["package"]
        expected = {
            "package_sha256": release.package_sha256,
            "package_bytes": release.package_size,
            "manifest_sha256": release.manifest_sha256,
            "app_sha256": release.app_sha256,
            "app_bytes": release.app_size,
            "source_tree_sha256": release.source_tree_sha256,
        }
        if package != expected:
            raise ValueError
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise AuditError("vehicle configuration backup is not bound to the selected package") from None


def _write_transaction(transaction: MigrationTransaction, directory: Path | None) -> Path:
    directory = _ensure_private_directory(
        directory or _default_transaction_directory(),
        label="migration transaction",
    )
    raw = (
        json.dumps(_transaction_document(transaction), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    filename = (
        f"migration-{transaction.device_serial_sha256[:12]}-"
        f"{transaction.app_sha256[:12]}-{transaction.source_snapshot_sha256[:12]}.json"
    )
    final = directory / filename
    if final.exists():
        try:
            if (
                final.is_symlink()
                or not final.is_file()
                or final.stat().st_uid != os.getuid()
                or final.stat().st_mode & 0o077
                or final.read_bytes() != raw
            ):
                raise OSError("unsafe existing transaction")
            return final
        except OSError:
            raise AuditError("existing migration transaction does not match the current binding") from None
    descriptor, temporary = tempfile.mkstemp(prefix=".transaction-", dir=directory)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, final)
        return final
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise AuditError("could not atomically store the migration transaction") from None


def _find_transactions(
    directory: Path | None,
    release: ReleasePackage,
    device_serial_sha256: str,
) -> list[MigrationTransaction]:
    directory = (directory or _default_transaction_directory()).expanduser()
    if not directory.exists():
        return []
    directory = _ensure_private_directory(directory, label="migration transaction")
    matches: list[MigrationTransaction] = []
    try:
        for path in directory.glob("migration-*.json"):
            if path.is_symlink() or not path.is_file():
                continue
            metadata = path.stat()
            if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                continue
            transaction = _parse_transaction(path.read_bytes())
            if _transaction_matches_release(transaction, release, device_serial_sha256):
                matches.append(transaction)
    except OSError:
        raise PostInstallError(
            "could not safely read migration transactions",
            outcome="migration_pending",
            stage="migration_transaction",
        ) from None
    return matches


def _find_corrective_source_transactions(
    directory: Path | None,
    release: ReleasePackage,
    device_serial_sha256: str,
    current_project_version: str,
) -> list[MigrationTransaction]:
    directory = (directory or _default_transaction_directory()).expanduser()
    if not directory.exists():
        return []
    directory = _ensure_private_directory(directory, label="migration transaction")
    matches: list[MigrationTransaction] = []
    try:
        for path in directory.glob("migration-*.json"):
            if path.is_symlink() or not path.is_file():
                continue
            metadata = path.stat()
            if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                continue
            transaction = _parse_transaction(path.read_bytes())
            same_profile_contract = (
                transaction.target.profile_id == release.target.profile_id
                and transaction.target.hardware == release.target.hardware
                and transaction.target.nvs_schema == release.target.nvs_schema
                and transaction.target.protocol == release.target.protocol
            )
            if (
                transaction.device_serial_sha256 == device_serial_sha256
                and transaction.target.project_version == current_project_version
                and same_profile_contract
            ):
                matches.append(transaction)
    except OSError:
        raise PostInstallError(
            "could not safely read corrective source transactions",
            outcome="migration_pending",
            stage="corrective_transaction",
        ) from None
    return matches


def _transaction_from_prior_audit(
    path: Path,
    release: ReleasePackage | None,
    device_serial_sha256: str,
    *,
    current_audit_path: Path,
    expected_transaction: MigrationTransaction | None = None,
) -> MigrationTransaction | None:
    if release is None and expected_transaction is None:
        raise ValueError("release or expected transaction is required")
    expected_target = expected_transaction.target if expected_transaction else release.target
    expected_manifest_sha256 = (
        expected_transaction.manifest_sha256 if expected_transaction else release.manifest_sha256
    )
    expected_app_sha256 = expected_transaction.app_sha256 if expected_transaction else release.app_sha256
    expected_app_bytes = expected_transaction.app_bytes if expected_transaction else release.app_size
    try:
        path = path.expanduser()
        if (
            not path.is_absolute()
            or _inside_repository(path)
            or path.is_symlink()
            or not path.is_file()
            or path.resolve() == current_audit_path.resolve()
        ):
            return None
        metadata = path.stat()
        if (
            metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or metadata.st_size > MAX_RESUME_AUDIT_BYTES
        ):
            return None
        session: dict[str, Any] | None = None
        snapshot: dict[str, Any] | None = None
        pre_device: dict[str, Any] | None = None
        state = 0
        expected_seq = 0
        last_cumulative = 0
        preflight_seen = False
        confirmation_seen = False
        begin_sent_seen = False
        end_sent_seen = False
        with path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index >= MAX_RESUME_AUDIT_LINES:
                    return None
                record = json.loads(line)
                if not isinstance(record, dict):
                    return None
                step = record.get("step")
                status = record.get("status")
                if state == 7:
                    return None
                if step == "session":
                    if status != "started" or state != 0:
                        return None
                    session = record
                    state = 1
                elif step == "device_snapshot" and record.get("phase") == "pre":
                    if status != "ok" or state != 1:
                        return None
                    pre_device = record
                    state = 2
                elif step == "configuration_snapshot":
                    if status != "created" or state != 2:
                        return None
                    snapshot = record
                    state = 3
                elif step == "preflight":
                    if status != "ok" or state != 3 or preflight_seen:
                        return None
                    preflight_seen = True
                elif step == "confirmation":
                    if status != "ok" or state != 3 or confirmation_seen:
                        return None
                    confirmation_seen = True
                elif step == "begin":
                    if status == "sent":
                        if state != 3 or begin_sent_seen:
                            return None
                        begin_sent_seen = True
                    elif status == "ok":
                        if state != 3:
                            return None
                        state = 4
                    else:
                        return None
                elif step == "data":
                    if state not in {4, 5}:
                        return None
                    if status in {"committed", "committed_by_status"}:
                        seq = record.get("seq")
                        cumulative = record.get("cumulative_written")
                        if (
                            isinstance(seq, bool)
                            or not isinstance(seq, int)
                            or seq != expected_seq
                            or isinstance(cumulative, bool)
                            or not isinstance(cumulative, int)
                            or cumulative <= last_cumulative
                            or cumulative > expected_app_bytes
                        ):
                            return None
                        expected_seq += 1
                        last_cumulative = cumulative
                        state = 5
                    elif status not in {"sent", "controlled_resend"}:
                        return None
                elif step == "end":
                    if status == "sent":
                        if state != 5 or end_sent_seen or last_cumulative != expected_app_bytes:
                            return None
                        end_sent_seen = True
                    elif status == "acknowledged":
                        if state != 5 or last_cumulative != expected_app_bytes:
                            return None
                        state = 6
                    else:
                        return None
                elif step == "result":
                    if status != "failed" or state != 6:
                        return None
                    state = 7
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    if session is None or snapshot is None or pre_device is None:
        return None
    source_hash = snapshot.get("snapshot_sha256")
    source_version = pre_device.get("pre_project_version")
    if not isinstance(source_version, str):
        return None
    if (
        session.get("manifest_sha256") != expected_manifest_sha256
        or session.get("app_sha256") != expected_app_sha256
        or session.get("app_bytes") != expected_app_bytes
        or session.get("target_profile_id") != expected_target.profile_id
        or session.get("target_hardware") != expected_target.hardware
        or session.get("target_nvs_schema") != expected_target.nvs_schema
        or session.get("target_project_version") != expected_target.project_version
        or session.get("target_protocol") != expected_target.protocol
        or snapshot.get("device_serial_sha256") != device_serial_sha256
        or snapshot.get("fields") != list(CONFIGURATION_FIELDS)
        or source_version == expected_target.project_version
        or not isinstance(source_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", source_hash)
        or state != 7
        or expected_seq == 0
        or last_cumulative != expected_app_bytes
    ):
        return None
    transaction = MigrationTransaction(
        target=expected_target,
        manifest_sha256=expected_manifest_sha256,
        app_sha256=expected_app_sha256,
        app_bytes=expected_app_bytes,
        device_serial_sha256=device_serial_sha256,
        source_snapshot_sha256=source_hash,
        source_project_version=source_version,
    )
    if expected_transaction is not None and transaction != expected_transaction:
        return None
    return transaction


def _recover_legacy_transaction(
    explicit_audit: Path,
    release: ReleasePackage,
    device_serial_sha256: str,
    *,
    current_audit_path: Path,
) -> MigrationTransaction:
    transaction = _transaction_from_prior_audit(
        explicit_audit,
        release,
        device_serial_sha256,
        current_audit_path=current_audit_path,
    )
    if transaction is None:
        raise PostInstallError(
            "explicit prior OTA audit does not contain complete matching evidence; migration recovery refused",
            outcome="migration_pending",
            stage="migration_transaction_recovery",
        )
    return transaction


def _validate_transaction_snapshot(
    transaction: MigrationTransaction,
    snapshot_directory: Path | None,
    current_configuration: dict[str, Any],
) -> dict[str, Any]:
    directory = (snapshot_directory or _default_snapshot_directory()).expanduser()
    if not directory.is_absolute() or _inside_repository(directory):
        raise PostInstallError(
            "bound source snapshot directory is unsafe",
            outcome="migration_pending",
            stage="migration_snapshot_recovery",
        )
    try:
        directory = directory.resolve(strict=True)
        if (
            not directory.is_dir()
            or directory.stat().st_uid != os.getuid()
            or directory.stat().st_mode & 0o077
        ):
            raise OSError("unsafe snapshot directory")
    except OSError:
        raise PostInstallError(
            "bound source snapshot directory is unavailable or not private",
            outcome="migration_pending",
            stage="migration_snapshot_recovery",
        ) from None
    documents: list[dict[str, Any]] = []
    try:
        for path in directory.glob(
            f"device-snapshot-*-{transaction.source_snapshot_sha256[:12]}-*.json"
        ):
            if path.is_symlink() or not path.is_file():
                continue
            metadata = path.stat()
            if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                continue
            raw = path.read_bytes()
            if _sha256(raw) != transaction.source_snapshot_sha256:
                continue
            document = json.loads(raw.decode("utf-8"))
            if isinstance(document, dict):
                documents.append(document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise PostInstallError(
            "could not safely validate the bound source snapshot",
            outcome="migration_pending",
            stage="migration_snapshot_recovery",
        ) from None
    if len(documents) != 1:
        raise PostInstallError(
            "bound source snapshot is missing or ambiguous",
            outcome="migration_pending",
            stage="migration_snapshot_recovery",
        )
    document = documents[0]
    serial = document.get("device_serial")
    original_configuration = document.get("configuration")
    try:
        serial_sha256 = _sha256(serial.encode("ascii", errors="strict")) if isinstance(serial, str) else None
    except UnicodeEncodeError:
        serial_sha256 = None
    if (
        not isinstance(serial, str)
        or serial_sha256 != transaction.device_serial_sha256
        or not isinstance(original_configuration, dict)
        or sorted(original_configuration) != list(CONFIGURATION_FIELDS)
        or sorted(current_configuration) != list(CONFIGURATION_FIELDS)
    ):
        raise PostInstallError(
            "bound source snapshot identity or fields do not match the current device",
            outcome="migration_pending",
            stage="migration_snapshot_recovery",
        )
    return original_configuration


def _resolve_recovery_transaction(
    config: UpdateConfig,
    release: ReleasePackage,
    device_serial_sha256: str,
    current_configuration: dict[str, Any],
    snapshot_directory: Path | None,
    transaction_directory: Path | None,
    audit: AuditLogger,
) -> tuple[MigrationTransaction, dict[str, Any]]:
    matches = _find_transactions(transaction_directory, release, device_serial_sha256)
    if len(matches) > 1:
        raise PostInstallError(
            "multiple matching migration transactions exist; recovery refused",
            outcome="migration_pending",
            stage="migration_transaction",
        )
    transaction = matches[0] if matches else None
    if config.resume_audit is not None:
        imported = _recover_legacy_transaction(
            config.resume_audit,
            release,
            device_serial_sha256,
            current_audit_path=audit.path,
        )
        if transaction is not None and transaction != imported:
            raise PostInstallError(
                "the explicit prior audit conflicts with the stored migration transaction",
                outcome="migration_pending",
                stage="migration_transaction_recovery",
            )
        transaction = imported
    elif transaction is None:
        raise PostInstallError(
            "no bound migration transaction exists; provide the exact original audit with --resume-audit",
            outcome="migration_pending",
            stage="migration_transaction_recovery",
        )
    source_configuration = _validate_transaction_snapshot(
        transaction,
        snapshot_directory,
        current_configuration,
    )
    transaction_path = _write_transaction(transaction, transaction_directory)
    audit.event(
        "migration_transaction",
        "imported" if config.resume_audit is not None and not matches else "recovered",
        manifest_sha256=transaction.manifest_sha256,
        app_sha256=transaction.app_sha256,
        app_bytes=transaction.app_bytes,
        device_serial_sha256=transaction.device_serial_sha256,
        source_snapshot_sha256=transaction.source_snapshot_sha256,
        transaction_file_sha256=_sha256(transaction_path.read_bytes()),
    )
    audit.event(
        "configuration_comparison",
        "deferred",
        reason="profile_guard_suppresses_persistent_parameter_loads",
        fields=sorted(source_configuration),
    )
    return transaction, source_configuration


def _resolve_corrective_transaction(
    config: UpdateConfig,
    release: ReleasePackage,
    pre_snapshot: DeviceSnapshot,
    device_serial_sha256: str,
    current_configuration: dict[str, Any],
    snapshot_directory: Path | None,
    transaction_directory: Path | None,
    audit: AuditLogger,
) -> tuple[MigrationTransaction, MigrationTransaction, dict[str, Any]]:
    matches = _find_corrective_source_transactions(
        transaction_directory,
        release,
        device_serial_sha256,
        pre_snapshot.version.project_version,
    )
    if len(matches) != 1:
        raise PostInstallError(
            "corrective recovery requires exactly one original T002 migration transaction",
            outcome="migration_pending",
            stage="corrective_transaction",
        )
    original = matches[0]
    if config.resume_audit is None:
        raise PostInstallError(
            "corrective recovery requires the exact original T002 audit",
            outcome="migration_pending",
            stage="corrective_audit",
        )
    imported = _transaction_from_prior_audit(
        config.resume_audit,
        None,
        device_serial_sha256,
        current_audit_path=audit.path,
        expected_transaction=original,
    )
    if imported != original:
        raise PostInstallError(
            "the original T002 audit does not exactly bind the stored transaction",
            outcome="migration_pending",
            stage="corrective_audit",
        )
    source_configuration = _validate_transaction_snapshot(
        original,
        snapshot_directory,
        current_configuration,
    )
    corrective = MigrationTransaction(
        target=release.target,
        manifest_sha256=release.manifest_sha256,
        app_sha256=release.app_sha256,
        app_bytes=release.app_size,
        device_serial_sha256=device_serial_sha256,
        source_snapshot_sha256=original.source_snapshot_sha256,
        source_project_version=original.source_project_version,
    )
    audit.event(
        "configuration_comparison",
        "deferred",
        reason="profile_guard_suppresses_persistent_parameter_loads",
        fields=sorted(source_configuration),
    )
    return original, corrective, source_configuration


def _validate_corrective_entry(pre_snapshot: DeviceSnapshot, release: ReleasePackage) -> None:
    target = release.target
    profile = pre_snapshot.profile
    if not CORRECTIVE_TARGET_VERSION_RE.fullmatch(target.project_version):
        raise PackageValidationError("corrective recovery accepts only an OSRF-C03-T003 package")
    if (
        target.profile_id != "red"
        or target.hardware != "OSCORE_ESP32S3_RevA"
        or target.nvs_schema != 1
        or target.protocol != SUPPORTED_PROTOCOL
    ):
        raise PackageValidationError("corrective T003 package does not match the approved Red contract")
    if not CORRECTIVE_SOURCE_VERSION_RE.fullmatch(pre_snapshot.version.project_version):
        raise DevicePreflightError("corrective recovery requires the installed T002 firmware")
    if pre_snapshot.version.protocol != target.protocol:
        raise DevicePreflightError("T002 protocol does not match the approved T003 package")
    if (
        profile is None
        or profile.profile_id != target.profile_id
        or profile.nvs_schema != target.nvs_schema
        or profile.state != "MIGRATION_INCOMPLETE"
        or profile.motion_ok
        or profile.writes_ok
    ):
        raise DevicePreflightError(
            "corrective recovery requires Red MIGRATION_INCOMPLETE with Motion/Writes disabled"
        )
    if pre_snapshot.firmware_status is None or pre_snapshot.firmware_status.active:
        raise DevicePreflightError("corrective recovery requires App OTA to be inactive")


def _validate_corrective_journal(
    transport: SerialTransport,
    config: UpdateConfig,
    audit: AuditLogger,
    snapshot_sha256: str,
) -> None:
    enabled, state, stored_hash = _query_value(
        transport,
        config,
        command="profile migrate status",
        label="corrective migration status",
        prefix="MIGRATION:",
        parser=parse_migration_status,
    )
    expected = snapshot_sha256[:12]
    if (
        not enabled
        or state != "MIGRATION_INCOMPLETE"
        or stored_hash not in {"none", expected}
    ):
        raise PostInstallError(
            "T002 partial migration status does not match the original transaction",
            outcome="migration_pending",
            stage="corrective_journal",
        )
    validated = _query_value(
        transport,
        config,
        command=f"profile migrate validate {snapshot_sha256}",
        label="corrective journal validate",
        prefix="OK migrate validate",
        parser=parse_migration_validate,
    )
    if validated != expected:
        raise ProtocolError("corrective journal validation hash does not match the original snapshot")
    audit.event(
        "corrective_journal",
        "validated",
        snapshot_sha256=snapshot_sha256,
        metadata_hash_state="none" if stored_hash == "none" else "present",
    )


def _query_post_battery(
    transport: SerialTransport,
    config: UpdateConfig,
    audit: AuditLogger,
    *,
    reconnect_deadline: float | None,
) -> float:
    deadline = transport.monotonic() + config.post_battery_timeout
    if reconnect_deadline is not None:
        deadline = min(deadline, reconnect_deadline)
    attempts = 0
    last_voltage: float | None = None
    last_reason = "not_ready"
    while transport.monotonic() < deadline:
        attempts += 1
        remaining = deadline - transport.monotonic()
        if remaining <= 0:
            break
        transport.send_line("b")
        try:
            voltage = transport.wait_for(
                label="post battery telemetry",
                prefixes=("b ",),
                parser=parse_battery_reading,
                timeout=min(config.response_timeout, remaining),
            )
        except ResponseTimeoutError:
            last_reason = "response_timeout"
            audit.event("post_battery", "waiting", attempt=attempts, reason=last_reason)
        except ProtocolError as error:
            last_reason = "malformed_response"
            audit.event("post_battery", "waiting", attempt=attempts, reason=last_reason)
            if isinstance(error, DeviceRejectedError):
                break
        else:
            last_voltage = voltage
            if 1.0 < voltage <= 100.0:
                audit.event("post_battery", "ok", attempts=attempts, voltage=voltage)
                return voltage
            else:
                last_reason = "not_ready"
            audit.event(
                "post_battery",
                "waiting",
                attempt=attempts,
                reason=last_reason,
                voltage=voltage,
            )
        remaining = deadline - transport.monotonic()
        if remaining > 0:
            transport.sleep(min(config.post_battery_interval, remaining))
    detail = f"last voltage={last_voltage:.2f}V" if last_voltage is not None else last_reason
    raise PostInstallError(
        "target App post-verification is pending: battery telemetry did not become ready "
        f"within the bounded wait ({detail}); do not reflash",
        outcome="post_verification_pending",
        stage="post_battery_telemetry",
        recovery_required=True,
    )


def _wait_for_level_calibration(
    transport: SerialTransport,
    config: UpdateConfig,
    audit: AuditLogger,
    *,
    reconnect_deadline: float | None,
) -> None:
    def drain_previous_generation() -> tuple[int, bool]:
        """Drain only at a command boundary, before issuing the next status request."""

        drained = 0
        while drained < 256:
            if transport.read_line() is None:
                return drained, True
            drained += 1
        return drained, False

    def is_whitelisted_async_telemetry(line: str) -> bool:
        return line.startswith(("s ", "m ", "r ", "b ", "o "))

    def collect_status_generation(timeout: float) -> tuple[bool | None, bool | None, str | None]:
        response_deadline = transport.monotonic() + timeout
        stationary: bool | None = None
        while transport.monotonic() < response_deadline:
            line = transport.read_line()
            if not line:
                transport.sleep(min(0.005, max(0.0, response_deadline - transport.monotonic())))
                continue
            if is_whitelisted_async_telemetry(line):
                continue
            if line.startswith("ERROR"):
                return None, None, "device_error"
            if stationary is None:
                if line.startswith("IMU:"):
                    return None, None, "imu_before_status"
                if not line.startswith("Status:"):
                    return None, None, "unknown_before_status"
                try:
                    stationary = parse_vehicle_static_status(line)
                except ProtocolError:
                    return None, None, "malformed_status"
                continue
            if line.startswith("Status:"):
                return None, None, "duplicate_status"
            if not line.startswith("IMU:"):
                return None, None, "unknown_between_status_and_imu"
            try:
                ready = parse_level_calibration_status(line)
            except ProtocolError:
                return None, None, "malformed_imu"
            return stationary, ready, None
        return None, None, "response_timeout"

    deadline = transport.monotonic() + config.level_calibration_timeout
    if reconnect_deadline is not None:
        deadline = min(deadline, reconnect_deadline)
    attempts = 0
    last_reason = "not_ready"
    while transport.monotonic() < deadline:
        attempts += 1
        remaining = deadline - transport.monotonic()
        if remaining <= 0:
            break
        drained, quiescent = drain_previous_generation()
        if drained:
            audit.event(
                "level_calibration_generation",
                "discarded_stale",
                attempt=attempts,
                lines=drained,
            )
        if not quiescent:
            last_reason = "stale_input_not_quiescent"
            audit.event("level_calibration", "waiting", attempt=attempts, reason=last_reason)
            remaining = deadline - transport.monotonic()
            if remaining > 0:
                transport.sleep(min(config.level_calibration_interval, remaining))
            continue
        transport.send_line("status")
        stationary, ready, generation_error = collect_status_generation(
            min(config.response_timeout, remaining)
        )
        if generation_error is None:
            if ready and stationary:
                trailing, _quiescent = drain_previous_generation()
                if trailing:
                    audit.event(
                        "level_calibration_generation",
                        "discarded_trailing",
                        attempt=attempts,
                        lines=trailing,
                    )
                audit.event("level_calibration", "ok", attempts=attempts, dynamic_field=True)
                return
            last_reason = "not_ready" if stationary else "vehicle_not_static"
        else:
            last_reason = generation_error
        audit.event("level_calibration", "waiting", attempt=attempts, reason=last_reason)
        remaining = deadline - transport.monotonic()
        if remaining > 0:
            transport.sleep(min(config.level_calibration_interval, remaining))
    raise PostInstallError(
        "target profile is READY but boot-time level calibration is still pending; "
        "keep the vehicle stationary on level ground and retry verification",
        outcome="post_verification_pending",
        stage="level_calibration",
        recovery_required=True,
    )


def _receive_ready_vehicle_config_after_level_calibration(
    transport: SerialTransport,
    config: UpdateConfig,
    audit: AuditLogger,
    *,
    phase: str,
) -> VehicleConfigExport:
    _wait_for_level_calibration(
        transport,
        config,
        audit,
        reconnect_deadline=None,
    )
    exported = _receive_vehicle_config_export_when_ready(transport, config)
    audit.event(
        "ready_config_export",
        "ok",
        phase=phase,
        item_count=len(exported.items),
        backup_sha256=exported.backup_sha256,
    )
    return exported


def _query_snapshot(
    transport: SerialTransport,
    config: UpdateConfig,
    audit: AuditLogger,
    *,
    phase: str,
    reconnect_deadline: float | None = None,
) -> DeviceSnapshot:
    audit.event("safe_stop", "started", phase=phase)
    transport.send_line("v 0.00 0.00")
    transport.send_line("stream off")
    transport.sleep(0.05)
    _drain_safe_stop(transport, audit, phase=phase)
    audit.event("safe_stop", "ok", phase=phase)

    transport.send_line("fw version")
    try:
        version = transport.wait_for(
            label="fw version",
            prefixes=("FW_VERSION:",),
            parser=parse_firmware_version,
            timeout=config.response_timeout,
        )
    except FirmwareUpdateError as error:
        audit.event("snapshot_version", "failed", phase=phase, reason=type(error).__name__)
        raise
    audit.event(
        "snapshot_version",
        "ok",
        phase=phase,
        project_version=version.project_version,
        protocol=version.protocol,
        format=version.format,
    )
    profile: ProfileStatus | None
    try:
        profile = _query_value(transport, config, command="profile get", label="profile", prefix="PROFILE:", parser=parse_profile_status)
    except (ResponseTimeoutError, DeviceRejectedError) as error:
        if phase != "pre":
            audit.event("snapshot_profile", "failed", phase=phase, reason=type(error).__name__)
            raise
        profile = None
        audit.event("source_profile", "unavailable")
    except FirmwareUpdateError as error:
        audit.event("snapshot_profile", "failed", phase=phase, reason=type(error).__name__)
        raise
    audit.event(
        "snapshot_profile",
        "ok" if profile is not None else "unavailable",
        phase=phase,
        profile_id=profile.profile_id if profile else None,
        nvs_schema=profile.nvs_schema if profile else None,
        state=profile.state if profile else None,
        motion=profile.motion_ok if profile else None,
        writes=profile.writes_ok if profile else None,
    )
    try:
        firmware_status = _query_fw_status(transport, config)
    except FirmwareUpdateError as error:
        audit.event("snapshot_fw_status", "failed", phase=phase, reason=type(error).__name__)
        raise
    audit.event(
        "snapshot_fw_status",
        "ok",
        phase=phase,
        active=firmware_status.active,
        running=firmware_status.running,
        next_partition=firmware_status.next_partition,
    )
    if firmware_status.active:
        raise DevicePreflightError("an App OTA session is already active; use an explicit recovery procedure")
    if phase == "post":
        voltage = _query_post_battery(
            transport,
            config,
            audit,
            reconnect_deadline=reconnect_deadline,
        )
    else:
        transport.send_line("b")
        voltage = transport.wait_for(
            label="battery voltage",
            prefixes=("b ",),
            parser=parse_battery_voltage,
            timeout=config.response_timeout,
        )
        audit.event("snapshot_battery", "ok", phase=phase, voltage=voltage)
    if (
        phase == "post"
        and profile is not None
        and profile.state == "READY"
        and profile.motion_ok
        and profile.writes_ok
    ):
        _wait_for_level_calibration(
            transport,
            config,
            audit,
            reconnect_deadline=reconnect_deadline,
        )
    configuration = _query_configuration(transport, config)
    unavailable = ["complementary_filter"]
    if profile is None:
        unavailable.append("source_profile")
    if version.protocol is None:
        unavailable.append("source_protocol")
    snapshot = DeviceSnapshot(
        version=version,
        profile=profile,
        voltage=voltage,
        firmware_status=firmware_status,
        configuration=configuration,
        unavailable_fields=tuple(unavailable),
    )
    audit.event("device_snapshot", "ok", phase=phase, **snapshot.audit_fields(phase))
    return snapshot


def _validate_snapshot(snapshot: DeviceSnapshot, target: TargetProfile, *, require_target_version: bool) -> None:
    if snapshot.profile is None:
        raise DevicePreflightError("device profile metadata is unavailable")
    if snapshot.version.protocol != target.protocol:
        raise DevicePreflightError("device protocol does not match the release manifest")
    if snapshot.profile.profile_id != target.profile_id:
        raise DevicePreflightError("device ProfileID does not match the release manifest")
    if snapshot.profile.nvs_schema != target.nvs_schema:
        raise DevicePreflightError("device NVS schema does not match the release manifest")
    if snapshot.profile.state != "READY":
        raise DevicePreflightError("device profile state is not READY")
    if not snapshot.profile.motion_ok or not snapshot.profile.writes_ok:
        raise DevicePreflightError("device profile Motion/Writes checks are not healthy")
    if require_target_version and snapshot.version.project_version != target.project_version:
        raise DevicePreflightError("post-reboot firmware version does not match the release manifest")


def _query_fw_status(transport: SerialTransport, config: UpdateConfig) -> FirmwareStatus:
    transport.send_line("fw status")
    return transport.wait_for(
        label="fw status",
        prefixes=("FW:",),
        parser=parse_firmware_status,
        timeout=config.response_timeout,
    )


def _best_effort_abort(
    connection: SerialConnection | None,
    config: UpdateConfig,
    audit: AuditLogger,
    *,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> bool:
    if connection is None or not getattr(connection, "is_open", False):
        audit.event("abort", "failed", reason="serial_unavailable")
        return False
    transport = SerialTransport(connection, monotonic=monotonic, sleep=sleep)
    try:
        transport.send_line("fw abort")
        transport.wait_for(
            label="fw abort",
            prefixes=("OK fw",),
            parser=parse_abort_ack,
            timeout=config.response_timeout,
        )
        audit.event("abort", "ok")
        return True
    except FirmwareUpdateError:
        audit.event("abort", "failed", reason="no_valid_ack")
        return False


def _send_data_chunk(
    transport: SerialTransport,
    config: UpdateConfig,
    audit: AuditLogger,
    *,
    seq: int,
    chunk: bytes,
    previous_written: int,
    image_size: int,
) -> int:
    expected_written = previous_written + len(chunk)
    resend_count = 0
    while True:
        transport.send_line(f"fw data {seq} {chunk.hex()}")
        audit.event("data", "sent", seq=seq, bytes=len(chunk), resend=resend_count > 0)
        try:
            ack_seq, ack_written = transport.wait_for(
                label=f"fw data {seq}",
                prefixes=("OK fw",),
                parser=parse_data_ack,
                timeout=config.response_timeout,
            )
        except ResponseTimeoutError:
            audit.event("data_ack", "timeout", seq=seq)
            status = _query_fw_status(transport, config)
            audit.event(
                "data_status",
                "ok",
                seq=seq,
                active=status.active,
                written=status.written,
                size=status.size,
                next_seq=status.next_seq,
            )
            if not status.active or status.size != image_size:
                raise ProtocolError("fw status is inconsistent with the active App update")
            if status.next_seq == seq + 1 and status.written == expected_written:
                audit.event("data", "committed_by_status", seq=seq, cumulative_written=expected_written)
                return expected_written
            if status.next_seq == seq and status.written == previous_written and resend_count == 0:
                resend_count = 1
                audit.event("data", "controlled_resend", seq=seq)
                continue
            raise ProtocolError("fw status does not permit continuing or resending the current chunk")

        if ack_seq != seq or ack_written != expected_written:
            raise ProtocolError("fw data ACK sequence or cumulative byte count is inconsistent")
        audit.event("data", "committed", seq=seq, cumulative_written=expected_written)
        return expected_written


def _perform_ota(
    connection: SerialConnection,
    release: ReleasePackage,
    config: UpdateConfig,
    audit: AuditLogger,
    progress: OtaProgress,
    *,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    progress_func: Callable[[int, int], None] | None = None,
) -> None:
    transport = SerialTransport(connection, monotonic=monotonic, sleep=sleep)
    progress.begin_may_have_been_sent = True
    try:
        transport.send_line(f"fw begin {release.app_size} {release.app_sha256}")
        audit.event("begin", "sent", app_bytes=release.app_size)
        partition, acknowledged_size = transport.wait_for(
            label="fw begin",
            prefixes=("OK fw",),
            parser=parse_begin_ack,
            timeout=config.response_timeout,
        )
    except DeviceRejectedError:
        progress.begin_rejected = True
        raise
    if acknowledged_size != release.app_size:
        raise ProtocolError("fw begin ACK size does not match the App image")
    progress.session_active = True
    audit.event("begin", "ok", partition=partition, app_bytes=acknowledged_size)

    written = 0
    if progress_func is not None:
        progress_func(0, release.app_size)
    for seq, offset in enumerate(range(0, release.app_size, config.chunk_size)):
        chunk = release.app_bytes[offset : offset + config.chunk_size]
        progress.data_delivery_unknown = True
        try:
            written = _send_data_chunk(
                transport,
                config,
                audit,
                seq=seq,
                chunk=chunk,
                previous_written=written,
                image_size=release.app_size,
            )
        except DeviceRejectedError as error:
            if error.stage == f"fw data {seq}":
                progress.data_delivery_unknown = False
            raise
        progress.data_delivery_unknown = False
        progress.data_committed_bytes = written
        if progress_func is not None:
            progress_func(written, release.app_size)
    if written != release.app_size:
        raise ProtocolError("host byte count does not match the App image size")

    progress.end_may_have_been_sent = True
    transport.send_line("fw end")
    audit.event("end", "sent")
    try:
        transport.wait_for(
            label="fw end",
            prefixes=("OK fw",),
            parser=parse_end_ack,
            timeout=config.response_timeout,
        )
        progress.end_acknowledged = True
        audit.event("end", "acknowledged")
    except (ResponseTimeoutError, SerialCommunicationError):
        audit.event("end", "ack_lost_reconnect_required")


def _reconnect_and_verify(
    release: ReleasePackage,
    config: UpdateConfig,
    serial_factory: SerialFactory,
    audit: AuditLogger,
    *,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[SerialConnection, DeviceSnapshot]:
    deadline = monotonic() + config.reconnect_timeout
    attempts = 0
    while monotonic() < deadline:
        attempts += 1
        connection: SerialConnection | None = None
        try:
            connection = _open_exclusive(config, serial_factory)
            transport = SerialTransport(connection, monotonic=monotonic, sleep=sleep)
            snapshot = _query_snapshot(
                transport,
                config,
                audit,
                phase="post",
                reconnect_deadline=deadline,
            )
            if snapshot.version.project_version != release.target.project_version:
                raise PostInstallError(
                    "App image booted but ProjectVer does not match the release manifest",
                    outcome="app_installed_not_ready",
                    stage="app_installed",
                )
            if snapshot.version.protocol != release.target.protocol:
                raise PostInstallError(
                    "App image booted but protocol does not match the release manifest",
                    outcome="app_installed_not_ready",
                    stage="app_installed",
                )
            audit.event("reconnect", "ok", attempts=attempts)
            return connection, snapshot
        except (SerialUnavailableError, ResponseTimeoutError, SerialCommunicationError):
            _close_quietly(connection)
            remaining = deadline - monotonic()
            if remaining > 0:
                sleep(min(config.reconnect_interval, remaining))
        except FirmwareUpdateError:
            _close_quietly(connection)
            raise
    raise ReconnectTimeoutError("device did not reconnect with valid firmware metadata before timeout")


def _configuration_mismatches(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []

    def compare(name: str, old: Any, new: Any, tolerance: float | None = None) -> None:
        if tolerance is None:
            if old != new:
                mismatches.append(name)
            return
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            mismatches.append(name)
            return
        if not math.isclose(float(old), float(new), rel_tol=0.0, abs_tol=tolerance + 1e-12):
            mismatches.append(name)

    compare("serial_number", before.get("serial_number"), after.get("serial_number"))
    for index, (old, new) in enumerate(zip(before.get("pid", []), after.get("pid", []))):
        compare(f"pid[{index}]", old, new, 0.005)
    if len(before.get("pid", [])) != len(after.get("pid", [])):
        mismatches.append("pid")
    for index, (old, new) in enumerate(zip(before.get("magnetometer", []), after.get("magnetometer", []))):
        compare(f"magnetometer[{index}]", old, new, 0.0000005)
    if len(before.get("magnetometer", [])) != len(after.get("magnetometer", [])):
        mismatches.append("magnetometer")
    old_battery = before.get("battery", {})
    new_battery = after.get("battery", {})
    compare("battery.calibration", old_battery.get("calibration"), new_battery.get("calibration"))
    compare("battery.scale", old_battery.get("scale"), new_battery.get("scale"), 0.00005)
    compare("odom_scale", before.get("odom_scale"), after.get("odom_scale"), 0.00005)
    old_trim = before.get("steering_trim", {})
    new_trim = after.get("steering_trim", {})
    compare("steering_trim.degrees", old_trim.get("degrees"), new_trim.get("degrees"), 0.005)
    compare("speed_deadband_us", before.get("speed_deadband_us"), after.get("speed_deadband_us"))
    return sorted(set(mismatches))


def _level_offset_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    old_level = before.get("level_offset", [])
    new_level = after.get("level_offset", [])
    if len(old_level) != len(new_level):
        return True
    return any(
        not isinstance(old, (int, float))
        or not isinstance(new, (int, float))
        or not math.isclose(float(old), float(new), rel_tol=0.0, abs_tol=0.00005 + 1e-12)
        for old, new in zip(old_level, new_level)
    )


def _derived_configuration_warnings(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    old_trim = before.get("steering_trim", {})
    new_trim = after.get("steering_trim", {})
    if old_trim.get("center_pwm_us") != new_trim.get("center_pwm_us"):
        return ["steering_trim.center_pwm_changed_derived_from_degrees"]
    return []


def _run_profile_migration(
    transport: SerialTransport,
    config: UpdateConfig,
    audit: AuditLogger,
    snapshot_sha256: str,
    *,
    required_stored_hash: str | None = None,
) -> None:
    enabled, state, stored_hash = _query_value(
        transport,
        config,
        command="profile migrate status",
        label="migration status",
        prefix="MIGRATION:",
        parser=parse_migration_status,
    )
    if not enabled or state not in {"UNCLAIMED", "MIGRATION_INCOMPLETE", "METADATA_INVALID"}:
        raise PostInstallError(
            "target firmware is not eligible for controlled profile migration",
            outcome="app_installed_not_ready",
            stage="migration_status",
        )
    if required_stored_hash is not None and stored_hash != required_stored_hash:
        raise PostInstallError(
            "migration hash changed after corrective post-install verification",
            outcome="migration_pending",
            stage="corrective_post_hash",
        )
    expected = snapshot_sha256[:12]
    validated = _query_value(
        transport,
        config,
        command=f"profile migrate validate {snapshot_sha256}",
        label="migration validate",
        prefix="OK migrate validate",
        parser=parse_migration_validate,
    )
    if validated != expected:
        raise ProtocolError("migration validate hash does not match the private snapshot")
    audit.event("migration_validated", "ok", snapshot_sha256=snapshot_sha256)
    cleanup, applied = _query_value(
        transport,
        config,
        command=f"profile migrate apply {snapshot_sha256}",
        label="migration apply",
        prefix="OK migrate apply",
        parser=parse_migration_apply,
    )
    if applied != expected:
        raise ProtocolError("migration apply hash does not match the private snapshot")
    audit.event("migration_applied", "ok", snapshot_sha256=snapshot_sha256, cleanup=cleanup)


def _request_reset(transport: SerialTransport, config: UpdateConfig, audit: AuditLogger) -> None:
    transport.send_line("reset")
    try:
        transport.wait_for(
            label="reset acknowledgement",
            prefixes=("INFO: rebooting...",),
            parser=lambda line: line == "INFO: rebooting...",
            timeout=min(config.response_timeout, 0.5),
        )
        audit.event("reset", "acknowledged")
    except (ResponseTimeoutError, SerialCommunicationError):
        audit.event("reset", "disconnect_observed")


def _confirm_update(
    release: ReleasePackage,
    snapshot: DeviceSnapshot,
    input_func: Callable[[str], str],
    source: UpdateSource,
    *,
    migration_recovery: bool,
    corrective_recovery: bool,
) -> None:
    risk_prefix = ""
    if source.candidate is not None:
        risk_prefix = f"[{source.candidate.risk_label()}] "
    if corrective_recovery:
        prompt = (
            f"{risk_prefix}Corrective App OTA {snapshot.version.project_version} -> "
            f"{release.target.project_version} using original source snapshot "
            f"{(snapshot.snapshot_sha256 or '')[:12]}; NVS is preserved. "
            f"Type {CONFIRM_TEXT} to continue: "
        )
    elif migration_recovery:
        prompt = (
            f"{risk_prefix}Resume {release.target.profile_id.capitalize()} profile migration "
            f"using verified source snapshot {(snapshot.snapshot_sha256 or '')[:12]}; "
            "no App data will be sent. "
            f"Type {CONFIRM_TEXT} to continue: "
        )
    else:
        prompt = (
            f"{risk_prefix}Update {snapshot.version.project_version} -> {release.target.project_version} "
            f"for ProfileID {release.target.profile_id}. Type {CONFIRM_TEXT} to continue: "
        )
    try:
        answer = input_func(prompt)
    except EOFError:
        answer = ""
    if answer.strip() != CONFIRM_TEXT:
        raise UserCancelledError("firmware update was not confirmed")


def run_app_ota(
    release: ReleasePackage,
    config: UpdateConfig,
    *,
    source: UpdateSource | None = None,
    serial_factory: SerialFactory = default_serial_factory,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> UpdateResult:
    """Run the complete App OTA workflow with an injectable serial transport."""

    config.validate()
    source = source or UpdateSource()
    audit = AuditLogger(config.log_dir)
    connection: SerialConnection | None = None
    pre_snapshot: DeviceSnapshot | None = None
    ota_progress = OtaProgress()
    app_write_completed = False
    app_installed = False
    resume_migration = False
    corrective_recovery = config.corrective_recovery
    try:
        audit.event(
            "session",
            "started",
            tool_version=TOOL_VERSION,
            port=config.port,
            baud=config.baud,
            chunk_size=config.chunk_size,
            manifest_sha256=release.manifest_sha256,
            app_sha256=release.app_sha256,
            app_bytes=release.app_size,
            target_profile_id=release.target.profile_id,
            target_hardware=release.target.hardware,
            target_nvs_schema=release.target.nvs_schema,
            target_project_version=release.target.project_version,
            target_protocol=release.target.protocol,
            reinstall=config.reinstall,
            corrective_recovery=corrective_recovery,
            **source.audit_fields(),
        )
        connection = _open_exclusive(config, serial_factory)
        audit.event("serial_open", "ok", port=config.port, exclusive=True)
        transport = SerialTransport(connection, monotonic=monotonic, sleep=sleep)
        pre_snapshot = _query_snapshot(transport, config, audit, phase="pre")
        if corrective_recovery:
            _validate_corrective_entry(pre_snapshot, release)
        snapshot_directory = config.snapshot_dir
        if snapshot_directory is None and config.log_dir is not None:
            snapshot_directory = config.log_dir / "snapshots"
        transaction_directory = config.transaction_dir
        if transaction_directory is None and snapshot_directory is not None:
            transaction_directory = snapshot_directory.parent / "transactions"
        already_installed = pre_snapshot.version.project_version == release.target.project_version
        profile = pre_snapshot.profile
        resume_migration_profile = bool(
            already_installed
            and profile is not None
            and profile.profile_id == release.target.profile_id
            and profile.nvs_schema == release.target.nvs_schema
            and profile.state in {"UNCLAIMED", "MIGRATION_INCOMPLETE", "METADATA_INVALID", "READY"}
            and not profile.motion_ok
            and not profile.writes_ok
        )
        resume_migration = resume_migration_profile
        if resume_migration_profile and pre_snapshot.version.protocol != release.target.protocol:
            raise PostInstallError(
                "installed fail-closed target protocol does not match the release manifest; "
                "migration recovery refused",
                outcome="migration_pending",
                stage="migration_protocol",
            )
        source_warnings: list[str] = []
        if pre_snapshot.version.protocol is None:
            source_warnings.append("source_protocol_unavailable")
        elif pre_snapshot.version.protocol != release.target.protocol:
            source_warnings.append("source_protocol_differs")
        if profile is None:
            source_warnings.append("source_profile_unavailable")
        elif (
            profile.profile_id != release.target.profile_id
            or profile.nvs_schema != release.target.nvs_schema
        ):
            source_warnings.append("source_profile_differs")
        audit.event("preflight", "ok", warnings=source_warnings)

        if already_installed and not config.reinstall and not resume_migration:
            if config.resume_audit is not None:
                raise PackageValidationError(
                    "--resume-audit is only valid for a fail-closed installed target awaiting migration"
                )
            audit.event("result", "skipped", reason="target_version_already_installed", exit_code=0)
            return UpdateResult(
                status="skipped",
                audit_path=audit.path,
                pre_snapshot=pre_snapshot,
                post_snapshot=None,
                operation="skipped",
            )

        configuration = pre_snapshot.configuration or {}
        device_serial = configuration.get("serial_number")
        if not isinstance(device_serial, str):
            raise DevicePreflightError("device serial number is unavailable; firmware update refused")
        device_serial_sha256 = _sha256(device_serial.encode("ascii"))
        if corrective_recovery:
            original_transaction, transaction, source_configuration = _resolve_corrective_transaction(
                config,
                release,
                pre_snapshot,
                device_serial_sha256,
                configuration,
                snapshot_directory,
                transaction_directory,
                audit,
            )
            snapshot_sha256 = original_transaction.source_snapshot_sha256
            _validate_corrective_journal(
                transport,
                config,
                audit,
                snapshot_sha256,
            )
            transaction_path = _write_transaction(transaction, transaction_directory)
            audit.event(
                "corrective_evidence",
                "inherited",
                source_target_project_version=original_transaction.target.project_version,
                source_manifest_sha256=original_transaction.manifest_sha256,
                source_app_sha256=original_transaction.app_sha256,
                source_snapshot_sha256=snapshot_sha256,
                corrective_manifest_sha256=transaction.manifest_sha256,
                corrective_app_sha256=transaction.app_sha256,
                corrective_transaction_file_sha256=_sha256(transaction_path.read_bytes()),
            )
            snapshot_status = "inherited"
            configuration = source_configuration
        elif resume_migration:
            if profile is not None and profile.state == "UNCLAIMED":
                enabled, migration_state, stored_hash = _query_value(
                    transport,
                    config,
                    command="profile migrate status",
                    label="unclaimed migration recovery status",
                    prefix="MIGRATION:",
                    parser=parse_migration_status,
                )
                audit.event(
                    "migration_status",
                    "checked",
                    state=migration_state,
                    enabled=enabled,
                    hash_state="none" if stored_hash == "none" else "present",
                )
                if not enabled or migration_state != "UNCLAIMED" or stored_hash != "none":
                    raise PostInstallError(
                        "UNCLAIMED migration recovery requires Enabled=Yes, State=UNCLAIMED, Hash=none",
                        outcome="migration_pending",
                        stage="migration_status",
                    )
            transaction, source_configuration = _resolve_recovery_transaction(
                config,
                release,
                device_serial_sha256,
                configuration,
                snapshot_directory,
                transaction_directory,
                audit,
            )
            snapshot_status = "recovered"
            snapshot_sha256 = transaction.source_snapshot_sha256
            configuration = source_configuration
        else:
            if config.resume_audit is not None:
                raise PackageValidationError(
                    "--resume-audit is only valid for a fail-closed installed target awaiting migration"
                )
            snapshot_sha256, _snapshot_path = _write_snapshot(pre_snapshot, snapshot_directory)
            snapshot_status = "created"
            transaction = MigrationTransaction(
                target=release.target,
                manifest_sha256=release.manifest_sha256,
                app_sha256=release.app_sha256,
                app_bytes=release.app_size,
                device_serial_sha256=device_serial_sha256,
                source_snapshot_sha256=snapshot_sha256,
                source_project_version=pre_snapshot.version.project_version,
            )
            transaction_path = _write_transaction(transaction, transaction_directory)
            audit.event(
                "migration_transaction",
                "created",
                manifest_sha256=transaction.manifest_sha256,
                app_sha256=transaction.app_sha256,
                app_bytes=transaction.app_bytes,
                device_serial_sha256=transaction.device_serial_sha256,
                source_snapshot_sha256=transaction.source_snapshot_sha256,
                transaction_file_sha256=_sha256(transaction_path.read_bytes()),
            )
        pre_snapshot = replace(
            pre_snapshot,
            snapshot_sha256=snapshot_sha256,
            configuration=configuration,
        )
        audit.event(
            "configuration_snapshot",
            snapshot_status,
            snapshot_sha256=snapshot_sha256,
            device_serial_sha256=device_serial_sha256,
            fields=sorted(configuration.keys()),
        )

        if source.candidate is not None:
            _emit_catalog_warning(source.candidate, output_func)
            audit.event(
                "candidate_notice",
                "shown",
                candidate_id=source.candidate.candidate_id,
                channel=source.candidate.channel,
                release_ready=source.candidate.release_ready,
                source_dirty=source.candidate.source_dirty,
                signature=source.candidate.signature,
            )
        _confirm_update(
            release,
            pre_snapshot,
            input_func,
            source,
            migration_recovery=resume_migration,
            corrective_recovery=corrective_recovery,
        )
        audit.event(
            "confirmation",
            "ok",
            mode="operator_input",
            operation=(
                "corrective_recovery"
                if corrective_recovery
                else ("migration_recovery" if resume_migration else "app_ota")
            ),
        )

        if resume_migration:
            post_snapshot = pre_snapshot
            app_installed = True
            audit.event("app_installed", "resume", project_version=post_snapshot.version.project_version)
        else:
            _perform_ota(
                connection,
                release,
                config,
                audit,
                ota_progress,
                monotonic=monotonic,
                sleep=sleep,
            )
            app_write_completed = True
            _close_quietly(connection)
            connection = None

            connection, post_snapshot = _reconnect_and_verify(
                release,
                config,
                serial_factory,
                audit,
                monotonic=monotonic,
                sleep=sleep,
            )
            audit.event("app_installed", "ok", project_version=post_snapshot.version.project_version)
            app_installed = True
        profile = post_snapshot.profile
        if profile is None:
            raise PostInstallError(
                "target App is installed but profile metadata is unavailable",
                outcome="app_installed_not_ready",
                stage="profile_state",
            )
        if corrective_recovery and not (
            profile.state == "MIGRATION_INCOMPLETE"
            and not profile.motion_ok
            and not profile.writes_ok
        ):
            raise PostInstallError(
                "T003 corrective App must remain fail-closed in MIGRATION_INCOMPLETE before migration",
                outcome="migration_pending",
                stage="corrective_post_state",
            )
        if profile.state == "READY" and not profile.motion_ok and not profile.writes_ok:
            transport = SerialTransport(connection, monotonic=monotonic, sleep=sleep)
            enabled, migration_state, stored_hash = _query_value(
                transport,
                config,
                command="profile migrate status",
                label="committed migration recovery status",
                prefix="MIGRATION:",
                parser=parse_migration_status,
            )
            if (
                not enabled
                or migration_state != "READY"
                or stored_hash != snapshot_sha256[:12]
            ):
                raise PostInstallError(
                    "READY target is fail-closed without proof of a committed migration",
                    outcome="app_installed_not_ready",
                    stage="migration_reset_recovery",
                )
            audit.event("migration_applied", "recovered", snapshot_sha256=snapshot_sha256)
            _request_reset(transport, config, audit)
            _close_quietly(connection)
            connection = None
            connection, post_snapshot = _reconnect_and_verify(
                release,
                config,
                serial_factory,
                audit,
                monotonic=monotonic,
                sleep=sleep,
            )
            audit.event("reboot_verified", "ok", project_version=post_snapshot.version.project_version)
        elif profile.state in {"UNCLAIMED", "MIGRATION_INCOMPLETE", "METADATA_INVALID"} and not profile.motion_ok and not profile.writes_ok:
            transport = SerialTransport(connection, monotonic=monotonic, sleep=sleep)
            if profile.state != "UNCLAIMED":
                enabled, migration_state, stored_hash = _query_value(
                    transport,
                    config,
                    command="profile migrate status",
                    label="migration status recovery",
                    prefix="MIGRATION:",
                    parser=parse_migration_status,
                )
                if (
                    not enabled
                    or migration_state != profile.state
                    or stored_hash != snapshot_sha256[:12]
                ):
                    raise PostInstallError(
                        "target migration state cannot be matched to a prior snapshot",
                        outcome="migration_pending",
                        stage="migration_snapshot_recovery",
                    )
                audit.event("migration_snapshot_recovered", "ok", snapshot_sha256=snapshot_sha256)
            try:
                _run_profile_migration(
                    transport,
                    config,
                    audit,
                    snapshot_sha256,
                    required_stored_hash=(
                        snapshot_sha256[:12] if corrective_recovery else None
                    ),
                )
            except FirmwareUpdateError as error:
                if isinstance(error, DeviceRejectedError):
                    error.stage = "migration_pending"
                raise
            _request_reset(transport, config, audit)
            _close_quietly(connection)
            connection = None
            connection, post_snapshot = _reconnect_and_verify(
                release,
                config,
                serial_factory,
                audit,
                monotonic=monotonic,
                sleep=sleep,
            )
            audit.event("reboot_verified", "ok", project_version=post_snapshot.version.project_version)
        elif not (profile.state == "READY" and profile.motion_ok and profile.writes_ok):
            raise PostInstallError(
                "target App is installed but profile state is neither migratable UNCLAIMED nor healthy READY",
                outcome="app_installed_not_ready",
                stage="profile_state",
            )

        try:
            _validate_snapshot(post_snapshot, release.target, require_target_version=True)
        except DevicePreflightError as error:
            raise PostInstallError(
                str(error),
                outcome="app_installed_not_ready",
                stage="reboot_verified",
            ) from None
        mismatches = _configuration_mismatches(
            pre_snapshot.configuration or {},
            post_snapshot.configuration or {},
        )
        level_changed = _level_offset_changed(
            pre_snapshot.configuration or {},
            post_snapshot.configuration or {},
        )
        derived_warnings = _derived_configuration_warnings(
            pre_snapshot.configuration or {},
            post_snapshot.configuration or {},
        )
        audit.event(
            "config_compared",
            "ok_with_warnings" if not mismatches and (level_changed or derived_warnings) else (
                "ok" if not mismatches else "mismatch"
            ),
            mismatch_fields=mismatches,
            level_offset_dynamic=True,
            level_offset_changed=level_changed,
            derived_warnings=derived_warnings,
        )
        if mismatches:
            raise PostInstallError(
                "target App is ready but persistent configuration does not match the private snapshot",
                outcome="ready_config_mismatch",
                stage="config_compared",
            )
        audit.event("result", "success", exit_code=0)
        return UpdateResult(
            status="success",
            audit_path=audit.path,
            pre_snapshot=pre_snapshot,
            post_snapshot=post_snapshot,
            operation=(
                "corrective_recovery"
                if corrective_recovery
                else ("migration_recovery" if resume_migration else "app_ota")
            ),
        )
    except KeyboardInterrupt:
        if ota_progress.session_active and not ota_progress.end_may_have_been_sent:
            _best_effort_abort(
                connection,
                config,
                audit,
                monotonic=monotonic,
                sleep=sleep,
            )
        error = UpdateInterruptedError("firmware update interrupted by operator")
        outcome = (
            "app_write_status_unknown"
            if ota_progress.end_may_have_been_sent and not ota_progress.end_acknowledged
            else ("app_installed_not_ready" if ota_progress.end_acknowledged else "not_written")
        )
        audit.event(
            "result",
            "failed",
            reason=str(error),
            exit_code=error.exit_code,
            outcome=outcome,
            recovery_required=outcome in {"app_write_status_unknown", "app_installed_not_ready"},
        )
        error.audit_path = audit.path
        error.no_app_reflash = resume_migration or (corrective_recovery and app_installed)
        raise error from None
    except FirmwareUpdateError as error:
        if ota_progress.session_active and not ota_progress.end_may_have_been_sent:
            _best_effort_abort(
                connection,
                config,
                audit,
                monotonic=monotonic,
                sleep=sleep,
            )
        default_outcome = (
            "app_write_status_unknown"
            if ota_progress.end_may_have_been_sent and not ota_progress.end_acknowledged and not app_installed
            else (
                "migration_pending"
                if app_installed
                else ("app_installed_not_ready" if app_write_completed else "not_written")
            )
        )
        outcome = getattr(error, "outcome", default_outcome)
        stage = getattr(error, "stage", "unknown")
        recovery_required = getattr(
            error,
            "recovery_required",
            outcome in {"app_write_status_unknown", "post_verification_pending", "migration_pending"}
            or (ota_progress.end_acknowledged and not app_installed),
        )
        details: dict[str, Any] = {
            "reason": str(error),
            "exit_code": error.exit_code,
            "outcome": outcome,
            "failure_stage": stage,
            "recovery_required": recovery_required,
        }
        if isinstance(error, DeviceRejectedError):
            details["device_reason"] = error.device_reason
        audit.event("result", "failed", **details)
        error.audit_path = audit.path
        error.no_app_reflash = resume_migration or (corrective_recovery and app_installed)
        raise
    except Exception:
        if ota_progress.session_active and not ota_progress.end_may_have_been_sent:
            _best_effort_abort(
                connection,
                config,
                audit,
                monotonic=monotonic,
                sleep=sleep,
            )
        error = ProtocolError("unexpected host error; firmware update stopped")
        outcome = (
            "app_write_status_unknown"
            if ota_progress.end_may_have_been_sent and not ota_progress.end_acknowledged
            else ("app_installed_not_ready" if ota_progress.end_acknowledged else "not_written")
        )
        audit.event(
            "result",
            "failed",
            reason=str(error),
            exit_code=error.exit_code,
            outcome=outcome,
            recovery_required=outcome in {"app_write_status_unknown", "app_installed_not_ready"},
        )
        error.audit_path = audit.path
        error.no_app_reflash = resume_migration
        raise error from None
    finally:
        _close_quietly(connection)
        audit.close()


def _query_managed_identity(
    transport: SerialTransport,
    config: UpdateConfig,
    audit: AuditLogger,
    *,
    phase: str,
) -> ManagedDeviceIdentity:
    audit.event("managed_identity", "started", phase=phase)
    transport.send_line("v 0.00 0.00")
    transport.send_line("stream off")
    transport.sleep(0.05)
    _drain_safe_stop(transport, audit, phase=phase)
    version = _query_value(
        transport,
        config,
        command="fw version",
        label="fw version",
        prefix="FW_VERSION:",
        parser=parse_firmware_version,
    )
    profile = _query_value(
        transport,
        config,
        command="profile get",
        label="profile",
        prefix="PROFILE:",
        parser=parse_profile_status,
    )
    firmware_status = _query_fw_status(transport, config)
    if firmware_status.active:
        raise DevicePreflightError(
            "an App OTA session is already active; use resume only after the device reboots"
        )
    audit.event(
        "managed_identity",
        "ok",
        phase=phase,
        project_version=version.project_version,
        protocol=version.protocol,
        profile_id=profile.profile_id,
        nvs_schema=profile.nvs_schema,
        profile_state=profile.state,
        motion=profile.motion_ok,
        writes=profile.writes_ok,
    )
    return ManagedDeviceIdentity(version, profile, firmware_status)


def _managed_manifest_contract(release: ReleasePackage) -> None:
    if (
        release.source_tree_sha256 is None
        or release.bootstrap_source_project_version is None
        or release.config_item_count != CONFIG_ITEM_COUNT
    ):
        raise PackageValidationError(
            "package does not declare the managed backup bootstrap contract"
        )
    expected_suffix = f"-s{release.source_tree_sha256[:12]}"
    if not release.target.project_version.endswith(expected_suffix):
        raise PackageValidationError("ProjectVer is not bound to the declared source tree SHA256")
    if release.target.protocol != SUPPORTED_PROTOCOL:
        raise PackageValidationError("managed update protocol is unsupported")


def _transport_recovery_package_contract(release: ReleasePackage) -> bool:
    """Return whether this is the one approved T004-to-T005 recovery asset."""

    is_t005_package = (
        release.target.project_version.startswith("OSRF-C03-T005-")
        or release.bootstrap_source_project_version
        == TRANSPORT_RECOVERY_SOURCE_PROJECT_VERSION
    )
    if not is_t005_package:
        return False
    exact = (
        release.target.project_version == TRANSPORT_RECOVERY_TARGET_PROJECT_VERSION
        and release.target.profile_id == "red"
        and release.target.hardware == "OSCORE_ESP32S3_RevA"
        and release.target.nvs_schema == 1
        and release.target.protocol == SUPPORTED_PROTOCOL
        and release.bootstrap_source_project_version
        == TRANSPORT_RECOVERY_SOURCE_PROJECT_VERSION
        and release.source_tree_sha256 == TRANSPORT_RECOVERY_SOURCE_TREE_SHA256
        and release.package_sha256 == TRANSPORT_RECOVERY_PACKAGE_SHA256
        and release.package_size == TRANSPORT_RECOVERY_PACKAGE_SIZE
        and release.manifest_sha256 == TRANSPORT_RECOVERY_MANIFEST_SHA256
        and release.app_member == "images/application.bin"
        and release.app_sha256 == TRANSPORT_RECOVERY_APP_SHA256
        and release.app_size == TRANSPORT_RECOVERY_APP_SIZE
        and release.config_item_count == CONFIG_ITEM_COUNT
    )
    if not exact:
        raise PackageValidationError(
            "T005 transport recovery requires the approved exact package asset"
        )
    return True


def _validate_managed_target_identity(
    identity: ManagedDeviceIdentity,
    release: ReleasePackage,
    *,
    required_state: str,
    motion_ok: bool,
    writes_ok: bool,
) -> None:
    if identity.version.project_version != release.target.project_version:
        raise PostInstallError(
            "ProjectVer does not match the selected package",
            outcome="app_installed_not_ready",
            stage="project_version",
        )
    if identity.version.protocol != release.target.protocol:
        raise PostInstallError(
            "device Proto does not match the selected package",
            outcome="app_installed_not_ready",
            stage="protocol",
        )
    profile = identity.profile
    if profile.profile_id != release.target.profile_id or profile.nvs_schema != release.target.nvs_schema:
        raise PostInstallError(
            "device ProfileID or NVS schema does not match the selected package",
            outcome="app_installed_not_ready",
            stage="profile_identity",
        )
    if (
        profile.state != required_state
        or profile.motion_ok != motion_ok
        or profile.writes_ok != writes_ok
    ):
        raise PostInstallError(
            f"target profile must be {required_state} with Motion="
            f"{'Yes' if motion_ok else 'No'} and Writes={'Yes' if writes_ok else 'No'}",
            outcome="app_installed_not_ready",
            stage="profile_state",
        )


def _reconnect_managed_identity(
    release: ReleasePackage,
    config: UpdateConfig,
    serial_factory: SerialFactory,
    audit: AuditLogger,
    *,
    phase: str,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[SerialConnection, ManagedDeviceIdentity]:
    deadline = monotonic() + config.reconnect_timeout
    attempts = 0
    while monotonic() < deadline:
        attempts += 1
        connection: SerialConnection | None = None
        try:
            connection = _open_exclusive(config, serial_factory)
            transport = SerialTransport(connection, monotonic=monotonic, sleep=sleep)
            identity = _query_managed_identity(transport, config, audit, phase=phase)
            if identity.version.project_version not in {
                release.target.project_version,
                release.bootstrap_source_project_version,
            }:
                raise PostInstallError(
                    "device reconnected with an unexpected ProjectVer",
                    outcome="app_installed_not_ready",
                    stage="project_version",
                )
            audit.event("managed_reconnect", "ok", phase=phase, attempts=attempts)
            return connection, identity
        except (SerialUnavailableError, ResponseTimeoutError, SerialCommunicationError):
            _close_quietly(connection)
            remaining = deadline - monotonic()
            if remaining > 0:
                sleep(min(config.reconnect_interval, remaining))
        except FirmwareUpdateError:
            _close_quietly(connection)
            raise
    raise ReconnectTimeoutError("device did not reconnect with valid ProjectVer/Profile metadata")


def _validate_export_binding(
    exported: VehicleConfigExport,
    release: ReleasePackage,
    *,
    bootstrap: bool,
) -> None:
    expected_source = (
        release.bootstrap_source_project_version if bootstrap else release.target.project_version
    )
    if (
        exported.source.project_version != expected_source
        or exported.source.profile_id != release.target.profile_id
        or exported.source.nvs_schema != release.target.nvs_schema
        or exported.source.protocol != release.target.protocol
    ):
        raise ProtocolError("configuration export source identity does not match the package bootstrap")
    if (
        exported.target.project_version != release.target.project_version
        or exported.target.profile_id != release.target.profile_id
        or exported.target.nvs_schema != release.target.nvs_schema
        or exported.target.protocol != release.target.protocol
    ):
        raise ProtocolError("configuration export target identity does not match the package")


def _validate_ready_export_identity(
    exported: VehicleConfigExport,
    target: TargetProfile,
) -> None:
    for label, identity in (("source", exported.source), ("target", exported.target)):
        if (
            identity.project_version != target.project_version
            or identity.profile_id != target.profile_id
            or identity.nvs_schema != target.nvs_schema
            or identity.protocol != target.protocol
        ):
            raise ProtocolError(
                f"READY configuration export {label} identity does not match the running target"
            )


def _running_target(identity: ManagedDeviceIdentity) -> TargetProfile:
    return TargetProfile(
        identity.profile.profile_id,
        "",
        identity.profile.nvs_schema,
        identity.version.project_version,
        identity.version.protocol,
    )


def _validate_backup_export_identity(
    exported: VehicleConfigExport,
    identity: ManagedDeviceIdentity,
) -> None:
    target = _running_target(identity)
    if exported.target != target:
        raise ProtocolError("configuration export target does not match the running device identity")
    if identity.profile.state == "READY":
        _validate_ready_export_identity(exported, target)
        return
    if identity.profile.state == "BACKUP_REQUIRED":
        if (
            exported.source.profile_id != target.profile_id
            or exported.source.nvs_schema != target.nvs_schema
            or exported.source.protocol != target.protocol
        ):
            raise ProtocolError(
                "configuration export source metadata does not match the running target"
            )
        return
    raise DevicePreflightError(
        "configuration backup requires READY or BACKUP_REQUIRED profile state"
    )


def _confirm_bootstrap_backup(
    transport: SerialTransport,
    config: UpdateConfig,
    release: ReleasePackage,
    exported: VehicleConfigExport,
) -> None:
    command = (
        "config backup confirm "
        f"{exported.source.project_version} {exported.source.profile_id} "
        f"{exported.source.nvs_schema} {release.target.project_version} "
        f"{release.target.profile_id} {release.target.nvs_schema} "
        f"{CONFIG_ITEM_COUNT} {exported.backup_sha256}"
    )
    transport.send_line(command)

    def parse(line: str) -> str:
        match = re.fullmatch(
            r"OK config backup confirmed BackupSHA=([0-9a-f]{12}) "
            r"state=READY reboot_required=Yes",
            line,
        )
        if not match:
            raise ProtocolError("malformed backup confirmation response")
        return match.group(1)

    confirmed = transport.wait_for(
        label="config backup confirmation",
        prefixes=("OK config backup confirmed",),
        parser=parse,
        timeout=max(config.response_timeout, DEFAULT_CONFIG_TIMEOUT),
    )
    if confirmed != exported.backup_sha256[:12]:
        raise ProtocolError("device confirmed a different vehicle configuration backup")


def run_managed_update(
    release: ReleasePackage,
    config: UpdateConfig,
    *,
    source: UpdateSource | None = None,
    backup_dir: Path | None = None,
    resume_backup_path: Path | None = None,
    serial_factory: SerialFactory = default_serial_factory,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
    renderer: ConsoleRenderer | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    resume_only: bool = False,
) -> UpdateResult:
    """Run a managed App-only bootstrap and durable config confirmation."""

    config.validate()
    _managed_manifest_contract(release)
    transport_recovery_package = _transport_recovery_package_contract(release)
    source = source or UpdateSource()
    renderer = renderer or ConsoleRenderer(output_func, monotonic=monotonic)
    audit = AuditLogger(config.log_dir)
    connection: SerialConnection | None = None
    pre_identity: ManagedDeviceIdentity | None = None
    app_write_completed = False
    backup_path: Path | None = None
    backup_file_sha: str | None = None
    ota_progress = OtaProgress()
    transport_recovery = False
    config_comparison: VehicleConfigSemanticComparison | None = None
    try:
        renderer.header("OSRacer Managed Firmware Update")
        renderer.summary("Package", release.target.project_version)
        renderer.summary("Profile", f"{release.target.profile_id} / schema {release.target.nvs_schema}")
        renderer.summary("App", f"{release.app_size:,} bytes  SHA256 {release.app_sha256}")
        renderer.summary("Package SHA", release.package_sha256)
        renderer.path("Audit log", audit.path)
        audit.event(
            "managed_session",
            "started",
            tool_version=TOOL_VERSION,
            port=config.port,
            package_sha256=release.package_sha256,
            package_bytes=release.package_size,
            manifest_sha256=release.manifest_sha256,
            app_sha256=release.app_sha256,
            app_bytes=release.app_size,
            source_tree_sha256=release.source_tree_sha256,
            bootstrap_source_project_version=release.bootstrap_source_project_version,
            target_project_version=release.target.project_version,
            target_profile_id=release.target.profile_id,
            target_nvs_schema=release.target.nvs_schema,
            target_protocol=release.target.protocol,
            **source.audit_fields(),
        )
        renderer.phase(1, 8, "Verify package", "manifest, ZIP, App, and source-tree binding passed")
        connection = _open_exclusive(config, serial_factory)
        transport = SerialTransport(connection, monotonic=monotonic, sleep=sleep)
        renderer.phase(2, 8, "Inspect device", f"exclusive serial {config.port}")
        pre_identity = _query_managed_identity(transport, config, audit, phase="pre")
        renderer.summary("Source", pre_identity.version.project_version)
        renderer.summary(
            "Device state",
            f"{pre_identity.profile.state} / Motion="
            f"{'Yes' if pre_identity.profile.motion_ok else 'No'} / Writes="
            f"{'Yes' if pre_identity.profile.writes_ok else 'No'}",
        )

        target_installed = pre_identity.version.project_version == release.target.project_version
        transport_recovery = bool(
            transport_recovery_package
            and not target_installed
            and pre_identity.version.project_version
            == TRANSPORT_RECOVERY_SOURCE_PROJECT_VERSION
            and pre_identity.version.protocol == SUPPORTED_PROTOCOL
            and pre_identity.profile.profile_id == "red"
            and pre_identity.profile.nvs_schema == 1
            and pre_identity.profile.state == "BACKUP_REQUIRED"
            and not pre_identity.profile.motion_ok
            and not pre_identity.profile.writes_ok
        )
        resume_backup = target_installed and pre_identity.profile.state == "BACKUP_REQUIRED"
        target_ready = bool(
            target_installed
            and pre_identity.profile.state == "READY"
            and pre_identity.profile.motion_ok
            and pre_identity.profile.writes_ok
        )
        verification_only = target_ready and resume_backup_path is not None
        confirmed_reboot_pending = bool(
            target_installed
            and pre_identity.profile.state == "READY"
            and not pre_identity.profile.motion_ok
            and not pre_identity.profile.writes_ok
        )
        if target_installed and pre_identity.profile.state == "APP_VALIDATION_FAILED":
            raise PostInstallError(
                "installed App failed device-side validation; keep motion locked and inspect the device",
                outcome="app_installed_not_ready",
                stage="app_validation",
            )
        if target_installed and pre_identity.profile.state == "CONFIG_RESTORE_INCOMPLETE":
            raise PostInstallError(
                "a configuration restore transaction is pending; resume restore with its exact backup",
                outcome="config_restore_pending",
                stage="config_restore",
            )
        if resume_backup_path is not None and not (target_ready or confirmed_reboot_pending):
            raise DevicePreflightError(
                "--backup is accepted only for a target READY state; remove it for a fresh "
                "update or BACKUP_REQUIRED resume"
            )
        if resume_only and not (resume_backup or confirmed_reboot_pending or verification_only):
            raise DevicePreflightError(
                "resume requires BACKUP_REQUIRED without --backup, or a READY state with "
                "the exact --backup file"
            )
        if target_ready and verification_only:
            selected_backup, backup_file_sha = _load_vehicle_config_backup(resume_backup_path)
            _validate_vehicle_backup_release(resume_backup_path, release)
            if (
                selected_backup.source.project_version != release.bootstrap_source_project_version
                or selected_backup.source.profile_id != release.target.profile_id
                or selected_backup.source.nvs_schema != release.target.nvs_schema
                or selected_backup.target.project_version != release.target.project_version
                or selected_backup.target.profile_id != release.target.profile_id
                or selected_backup.target.nvs_schema != release.target.nvs_schema
            ):
                raise PostInstallError(
                    "selected backup is not bound to this package bootstrap and target",
                    outcome="ready_config_mismatch",
                    stage="config_verify",
                )
            backup_path = resume_backup_path.expanduser()
            renderer.phase(3, 8, "Preflight", "READY target requires only final verification")
            renderer.path("Backup file", backup_path, backup_file_sha)
            renderer.phase(4, 8, "App OTA", "not performed")
            renderer.phase(5, 8, "Configuration write", "not performed")
            renderer.phase(6, 8, "Load verified backup", "exact package binding passed")
            renderer.phase(7, 8, "Device state", "READY / Motion=Yes / Writes=Yes")
            renderer.phase(8, 8, "Verify final configuration", "fresh read-only export")
            current = _receive_ready_vehicle_config_after_level_calibration(
                transport,
                config,
                audit,
                phase="ready_verification_only",
            )
            _validate_export_binding(current, release, bootstrap=False)
            config_comparison = compare_vehicle_config_semantics(
                selected_backup.items,
                current.items,
            )
            _audit_vehicle_config_comparison(
                audit,
                config_comparison,
                phase="ready_verification_only",
            )
            if not config_comparison.matches:
                raise PostInstallError(
                    "READY configuration does not satisfy the selected backup semantic contract",
                    outcome="ready_config_mismatch",
                    stage="config_verify",
                )
            audit.event("result", "success", exit_code=0, operation="ready_verification_only")
            renderer.result(
                True,
                [
                    ("Firmware", f"{release.target.project_version} / READY"),
                    ("App write", "not performed"),
                    ("Configuration write", "not performed"),
                    *_vehicle_config_result_rows(config_comparison),
                    ("Backup file", f"{backup_path} (retained)"),
                    ("Audit log", str(audit.path)),
                ],
            )
            snapshot = DeviceSnapshot(
                pre_identity.version,
                pre_identity.profile,
                0.0,
                firmware_status=pre_identity.firmware_status,
            )
            return UpdateResult(
                "success", audit.path, snapshot, snapshot, "managed_update"
            )
        if target_ready:
            renderer.phase(3, 8, "Preflight", "target is already READY")
            renderer.result(
                True,
                [
                    ("Firmware", "already installed and READY"),
                    ("App write", "not performed"),
                    ("NVS writes", "none by this command"),
                    ("24-item comparison", "NOT VERIFIED; exact --backup not provided"),
                    ("Restore", "not evaluated"),
                    ("Audit log", str(audit.path)),
                ],
            )
            audit.event("result", "skipped", reason="target_ready", exit_code=0)
            snapshot = DeviceSnapshot(
                pre_identity.version,
                pre_identity.profile,
                0.0,
                firmware_status=pre_identity.firmware_status,
            )
            return UpdateResult("skipped", audit.path, snapshot, None, "managed_update")
        if confirmed_reboot_pending:
            if resume_backup_path is None:
                raise PostInstallError(
                    "backup confirmation may have completed; rerun resume with the exact printed --backup file",
                    outcome="backup_confirmed_reboot_pending",
                    stage="backup_confirm",
                )
            selected_backup, backup_file_sha = _load_vehicle_config_backup(resume_backup_path)
            _validate_vehicle_backup_release(resume_backup_path, release)
            if (
                selected_backup.source.project_version != release.bootstrap_source_project_version
                or selected_backup.source.profile_id != release.target.profile_id
                or selected_backup.source.nvs_schema != release.target.nvs_schema
                or selected_backup.target.project_version != release.target.project_version
                or selected_backup.target.profile_id != release.target.profile_id
                or selected_backup.target.nvs_schema != release.target.nvs_schema
            ):
                raise PostInstallError(
                    "selected backup is not bound to this package bootstrap and target",
                    outcome="backup_confirmed_reboot_pending",
                    stage="backup_confirm",
                )
            backup_path = resume_backup_path.expanduser()
            renderer.phase(3, 8, "Preflight", "confirmed backup is awaiting the required reboot")
            renderer.path("Backup file", backup_path, backup_file_sha)
            answer = input_func(
                f"Resume the required reboot for {release.target.project_version}; "
                f"no App or configuration data will be sent. Type {CONFIRM_TEXT} to continue: "
            )
            if answer.strip() != CONFIRM_TEXT:
                raise UserCancelledError("managed firmware update resume was not confirmed")
            renderer.phase(4, 8, "App OTA", "skipped; target App is already installed")
            renderer.phase(5, 8, "Export vehicle configuration", "skipped until reboot")
            renderer.phase(6, 8, "Persist and verify backup", "selected private backup verified")
            renderer.phase(7, 8, "Complete required reboot", "no configuration write")
            _request_reset(transport, config, audit)
            _close_quietly(connection)
            connection = None
            connection, ready_identity = _reconnect_managed_identity(
                release,
                config,
                serial_factory,
                audit,
                phase="post_confirm_resume",
                monotonic=monotonic,
                sleep=sleep,
            )
            _validate_managed_target_identity(
                ready_identity,
                release,
                required_state="READY",
                motion_ok=True,
                writes_ok=True,
            )
            renderer.phase(8, 8, "Verify final configuration", "fresh 24-item export")
            current = _receive_ready_vehicle_config_after_level_calibration(
                SerialTransport(connection, monotonic=monotonic, sleep=sleep),
                config,
                audit,
                phase="confirmed_reboot_resume",
            )
            _validate_export_binding(current, release, bootstrap=False)
            config_comparison = compare_vehicle_config_semantics(
                selected_backup.items,
                current.items,
            )
            _audit_vehicle_config_comparison(
                audit,
                config_comparison,
                phase="confirmed_reboot_resume",
            )
            if not config_comparison.matches:
                raise PostInstallError(
                    "READY configuration does not satisfy the selected backup semantic contract",
                    outcome="ready_config_mismatch",
                    stage="config_verify",
                )
            audit.event("result", "success", exit_code=0, operation="confirmed_reboot_resume")
            renderer.result(
                True,
                [
                    ("Firmware", f"{release.target.project_version} / READY"),
                    ("App write", "not performed"),
                    ("Configuration write", "not performed"),
                    *_vehicle_config_result_rows(config_comparison),
                    ("Backup file", f"{backup_path} (retained)"),
                    ("Audit log", str(audit.path)),
                ],
            )
            pre_snapshot = DeviceSnapshot(
                pre_identity.version,
                pre_identity.profile,
                0.0,
                firmware_status=pre_identity.firmware_status,
            )
            post_snapshot = DeviceSnapshot(
                ready_identity.version,
                ready_identity.profile,
                0.0,
                firmware_status=ready_identity.firmware_status,
            )
            return UpdateResult(
                "success", audit.path, pre_snapshot, post_snapshot, "managed_update"
            )
        if target_installed and not resume_backup:
            raise PostInstallError(
                "installed target is not in a supported READY or BACKUP_REQUIRED state",
                outcome="app_installed_not_ready",
                stage="profile_state",
            )
        if resume_backup:
            _validate_managed_target_identity(
                pre_identity,
                release,
                required_state="BACKUP_REQUIRED",
                motion_ok=False,
                writes_ok=False,
            )
        else:
            if transport_recovery_package:
                if not transport_recovery:
                    raise DevicePreflightError(
                        "T005 transport recovery requires the exact T004/red/schema 1/Proto 1.1 "
                        "BACKUP_REQUIRED state with Motion=No and Writes=No"
                    )
            elif (
                pre_identity.version.project_version != release.bootstrap_source_project_version
                or pre_identity.version.protocol != release.target.protocol
                or pre_identity.profile.profile_id != release.target.profile_id
                or pre_identity.profile.nvs_schema != release.target.nvs_schema
                or pre_identity.profile.state != "READY"
                or not pre_identity.profile.motion_ok
                or not pre_identity.profile.writes_ok
            ):
                raise DevicePreflightError(
                    "managed update requires the exact READY bootstrap source declared by the package"
                )
        renderer.phase(
            3,
            8,
            "Preflight",
            (
                "resume BACKUP_REQUIRED without reflashing"
                if resume_backup
                else (
                    "exact locked T004 transport-recovery source accepted"
                    if transport_recovery
                    else "exact bootstrap source accepted"
                )
            ),
        )
        if source.candidate is not None:
            _emit_catalog_warning(source.candidate, output_func)
        if transport_recovery:
            renderer.summary(
                "Recovery mode",
                "transport recovery; T004 configuration export is unreliable",
            )
            renderer.summary("Pre-update backup", "not available; no backup will be fabricated")
            renderer.summary("NVS", "physically preserved by the App-only OTA")
        prompt = (
            f"Resume configuration backup for {release.target.project_version}; no App data will be sent. "
            if resume_backup
            else (
                (
                    f"Transport recovery {pre_identity.version.project_version} -> "
                    f"{release.target.project_version}; no logical backup exists before this update. "
                    "The App-only OTA physically preserves NVS. "
                )
                if transport_recovery
                else (
                    f"Update {pre_identity.version.project_version} -> "
                    f"{release.target.project_version}; App OTA preserves the NVS partition. "
                )
            )
        )
        answer = input_func(prompt + f"Type {CONFIRM_TEXT} to continue: ")
        if answer.strip() != CONFIRM_TEXT:
            raise UserCancelledError("managed firmware update was not confirmed")
        audit.event("confirmation", "ok", operation="managed_resume" if resume_backup else "managed_update")

        if resume_backup:
            renderer.phase(4, 8, "App OTA", "skipped; target App is already installed")
            post_identity = pre_identity
        else:
            renderer.phase(4, 8, "App OTA", "NVS partition is not erased or rewritten")
            _perform_ota(
                connection,
                release,
                config,
                audit,
                ota_progress,
                monotonic=monotonic,
                sleep=sleep,
                progress_func=renderer.progress,
            )
            app_write_completed = True
            _close_quietly(connection)
            connection = None
            connection, post_identity = _reconnect_managed_identity(
                release,
                config,
                serial_factory,
                audit,
                phase="post_app",
                monotonic=monotonic,
                sleep=sleep,
            )
            if post_identity.profile.state == "APP_VALIDATION_FAILED":
                raise PostInstallError(
                    "new App booted but device-side validation failed; do not reflash or confirm backup",
                    outcome="app_installed_not_ready",
                    stage="app_validation",
                )
            _validate_managed_target_identity(
                post_identity,
                release,
                required_state="BACKUP_REQUIRED",
                motion_ok=False,
                writes_ok=False,
            )
            audit.event("app_installed", "ok", project_version=post_identity.version.project_version)

        transport = SerialTransport(connection, monotonic=monotonic, sleep=sleep)
        renderer.phase(5, 8, "Export vehicle configuration", "read-only 24-item snapshot")
        exported = _receive_vehicle_config_export(transport, config)
        _validate_export_binding(exported, release, bootstrap=True)
        audit.event(
            "config_export",
            "verified",
            item_count=len(exported.items),
            backup_sha256=exported.backup_sha256,
        )
        renderer.summary("Backup SHA256", exported.backup_sha256)

        renderer.phase(6, 8, "Persist and verify backup", "atomic private file, mode 0600")
        backup_path, backup_file_sha = _write_vehicle_config_backup(
            exported, release, backup_dir, audit_path=audit.path
        )
        renderer.path("Backup file", backup_path, backup_file_sha)
        audit.event(
            "config_backup",
            "persisted",
            backup_sha256=exported.backup_sha256,
            backup_file_sha256=backup_file_sha,
            backup_file=str(backup_path),
        )

        renderer.phase(7, 8, "Confirm backup and reboot", "device rechecks untouched NVS")
        _confirm_bootstrap_backup(transport, config, release, exported)
        audit.event("config_backup_confirm", "ok", backup_sha256=exported.backup_sha256)
        _request_reset(transport, config, audit)
        _close_quietly(connection)
        connection = None
        connection, ready_identity = _reconnect_managed_identity(
            release,
            config,
            serial_factory,
            audit,
            phase="post_confirm",
            monotonic=monotonic,
            sleep=sleep,
        )
        _validate_managed_target_identity(
            ready_identity,
            release,
            required_state="READY",
            motion_ok=True,
            writes_ok=True,
        )

        renderer.phase(8, 8, "Verify final configuration", "independent export and canonical hash")
        transport = SerialTransport(connection, monotonic=monotonic, sleep=sleep)
        verified = _receive_ready_vehicle_config_after_level_calibration(
            transport,
            config,
            audit,
            phase="post_confirm",
        )
        _validate_export_binding(verified, release, bootstrap=False)
        config_comparison = compare_vehicle_config_semantics(exported.items, verified.items)
        _audit_vehicle_config_comparison(
            audit,
            config_comparison,
            phase="post_confirm",
        )
        if not config_comparison.matches:
            raise PostInstallError(
                "READY configuration does not satisfy the persisted backup semantic contract",
                outcome="ready_config_mismatch",
                stage="config_verify",
            )
        audit.event(
            "config_verify",
            "ok",
            backup_sha256=exported.backup_sha256,
            current_export_sha256=verified.backup_sha256,
            semantic_match=True,
        )
        audit.event("result", "success", exit_code=0, nvs_partition="preserved")
        renderer.result(
            True,
            [
                ("Firmware", f"{release.target.project_version} / READY"),
                ("App OTA", "successful" if app_write_completed else "not repeated"),
                ("NVS partition", "preserved by App OTA"),
                *_vehicle_config_result_rows(config_comparison),
                ("Restore", "not required"),
                ("Unknown NVS keys", "physically preserved; not included in logical verification"),
                ("Backup file", f"{backup_path} (retained)"),
                ("Backup SHA256", exported.backup_sha256),
                ("Audit log", str(audit.path)),
            ],
        )
        pre_snapshot = DeviceSnapshot(
            pre_identity.version,
            pre_identity.profile,
            0.0,
            firmware_status=pre_identity.firmware_status,
        )
        post_snapshot = DeviceSnapshot(
            ready_identity.version,
            ready_identity.profile,
            0.0,
            firmware_status=ready_identity.firmware_status,
        )
        return UpdateResult("success", audit.path, pre_snapshot, post_snapshot, "managed_update")
    except KeyboardInterrupt:
        abort_acknowledged = False
        if ota_progress.session_active and not ota_progress.end_may_have_been_sent:
            abort_acknowledged = _best_effort_abort(
                connection,
                config,
                audit,
                monotonic=monotonic,
                sleep=sleep,
            )
        error = UpdateInterruptedError("managed firmware update interrupted by operator")
        app_delivery_unknown = ota_progress.end_may_have_been_sent and not app_write_completed
        target_app_present = bool(
            pre_identity is not None
            and pre_identity.version.project_version == release.target.project_version
        )
        begin_delivery_unknown = bool(
            ota_progress.begin_may_have_been_sent
            and not ota_progress.begin_rejected
            and not ota_progress.session_active
        )
        session_abort_failed = bool(
            ota_progress.session_active
            and not ota_progress.end_may_have_been_sent
            and not abort_acknowledged
        )
        app_reflash_forbidden = (
            app_write_completed
            or app_delivery_unknown
            or target_app_present
            or begin_delivery_unknown
            or ota_progress.data_delivery_unknown
            or ota_progress.data_committed_bytes > 0
            or session_abort_failed
        )
        audit.event(
            "result",
            "failed",
            reason=str(error),
            exit_code=error.exit_code,
            app_write_completed=app_write_completed,
            app_delivery_unknown=app_delivery_unknown,
            begin_delivery_unknown=begin_delivery_unknown,
            data_delivery_unknown=ota_progress.data_delivery_unknown,
            app_data_committed_bytes=ota_progress.data_committed_bytes,
            abort_acknowledged=abort_acknowledged,
            action_required=app_reflash_forbidden,
            no_app_reflash=app_reflash_forbidden,
        )
        error.audit_path = audit.path
        error.no_app_reflash = app_reflash_forbidden
        retryable_transport_recovery = bool(
            transport_recovery
            and ota_progress.begin_may_have_been_sent
            and not app_reflash_forbidden
        )
        if retryable_transport_recovery:
            result_rows = [
                ("Reason", str(error)),
                ("App status", "not started; zero App data committed"),
                ("NVS", "unchanged by this command"),
                (
                    "Next step",
                    "rerun update when ready; resume does not apply while T004 is installed",
                ),
                ("Audit log", str(audit.path)),
            ]
        else:
            result_rows = [
                ("Reason", str(error)),
                (
                    "App status",
                    "delivery unknown; do not reflash"
                    if app_delivery_unknown
                    else ("do not reflash" if app_reflash_forbidden else "not started"),
                ),
                ("Next step", "resume or inspect the current device state"),
                ("Audit log", str(audit.path)),
            ]
        if config_comparison is not None:
            result_rows[2:2] = _vehicle_config_result_rows(config_comparison)
        renderer.result(False, result_rows)
        raise error from None
    except FirmwareUpdateError as error:
        abort_acknowledged = False
        if ota_progress.session_active and not ota_progress.end_may_have_been_sent:
            abort_acknowledged = _best_effort_abort(
                connection,
                config,
                audit,
                monotonic=monotonic,
                sleep=sleep,
            )
        app_delivery_unknown = ota_progress.end_may_have_been_sent and not app_write_completed
        target_app_present = bool(
            pre_identity is not None
            and pre_identity.version.project_version == release.target.project_version
        )
        begin_delivery_unknown = bool(
            ota_progress.begin_may_have_been_sent
            and not ota_progress.begin_rejected
            and not ota_progress.session_active
        )
        session_abort_failed = bool(
            ota_progress.session_active
            and not ota_progress.end_may_have_been_sent
            and not abort_acknowledged
        )
        app_reflash_forbidden = (
            app_write_completed
            or app_delivery_unknown
            or target_app_present
            or begin_delivery_unknown
            or ota_progress.data_delivery_unknown
            or ota_progress.data_committed_bytes > 0
            or session_abort_failed
        )
        audit.event(
            "result",
            "failed",
            reason=str(error),
            exit_code=error.exit_code,
            app_write_completed=app_write_completed,
            app_delivery_unknown=app_delivery_unknown,
            begin_delivery_unknown=begin_delivery_unknown,
            data_delivery_unknown=ota_progress.data_delivery_unknown,
            app_data_committed_bytes=ota_progress.data_committed_bytes,
            abort_acknowledged=abort_acknowledged,
            backup_file_sha256=backup_file_sha,
            action_required=app_reflash_forbidden,
            no_app_reflash=app_reflash_forbidden,
        )
        error.audit_path = audit.path
        error.no_app_reflash = app_reflash_forbidden
        retryable_transport_recovery = bool(
            transport_recovery
            and ota_progress.begin_may_have_been_sent
            and not app_reflash_forbidden
        )
        if retryable_transport_recovery:
            result_rows = [
                ("Reason", str(error)),
                ("App status", "not started; zero App data committed"),
                ("NVS", "unchanged by this command"),
                ("Backup file", "not created"),
                (
                    "Next step",
                    "fix the device condition, then rerun update; resume does not apply to T004",
                ),
                ("Audit log", str(audit.path)),
            ]
        else:
            result_rows = [
                ("Reason", str(error)),
                (
                    "App status",
                    "delivery unknown; do not reflash"
                    if app_delivery_unknown
                    else ("do not reflash" if error.no_app_reflash else "not started"),
                ),
                ("Backup file", str(backup_path) if backup_path else "not created"),
                (
                    "Next step",
                    "resume or inspect the current device state"
                    if error.no_app_reflash
                    else "resolve the reported preflight error and retry",
                ),
                ("Audit log", str(audit.path)),
            ]
        renderer.result(False, result_rows)
        raise
    finally:
        _close_quietly(connection)
        audit.close()


def _receive_config_protocol_block(
    transport: SerialTransport,
    config: UpdateConfig,
    *,
    label: str,
    prefixes: tuple[str, ...],
) -> list[str]:
    deadline = transport.monotonic() + max(config.response_timeout, DEFAULT_CONFIG_TIMEOUT)
    lines: list[str] = []
    while transport.monotonic() < deadline:
        line = transport.read_line()
        if not line:
            transport.sleep(min(0.005, max(0.0, deadline - transport.monotonic())))
            continue
        if line.startswith("ERROR"):
            reason = re.sub(r"^ERROR\s*", "", line).strip() or "unspecified"
            raise DeviceRejectedError(
                f"device rejected {label}: {reason}",
                stage=label,
                device_reason=reason,
            )
        is_protocol = line.startswith("CONFIG_") or line.startswith("OK config")
        if not is_protocol:
            continue
        expected_prefix = prefixes[len(lines)] if len(lines) < len(prefixes) else None
        if expected_prefix is None or not line.startswith(expected_prefix):
            raise ProtocolError(f"{label} response is out of order or duplicated")
        lines.append(line)
        if len(lines) == len(prefixes):
            return lines
    raise ResponseTimeoutError(f"timed out waiting for complete {label} response")


def _parse_import_identity(line: str, prefix: str, protocol: str) -> TargetProfile | None:
    identity = _parse_config_identity(line, prefix, protocol)
    if (
        identity.project_version == "none"
        and identity.profile_id == "none"
        and identity.nvs_schema == 0
    ):
        return None
    return identity


def _query_config_import_status(
    transport: SerialTransport,
    config: UpdateConfig,
) -> ConfigImportStatus:
    transport.send_line("config import status")
    lines = _receive_config_protocol_block(
        transport,
        config,
        label="config import status",
        prefixes=(
            "CONFIG_IMPORT_STATUS:",
            "CONFIG_IMPORT_SOURCE:",
            "CONFIG_IMPORT_TARGET:",
            "CONFIG_IMPORT_BACKUP:",
            "CONFIG_IMPORT_TRANSACTION:",
            "CONFIG_IMPORT_PENDING:",
        ),
    )
    status_match = re.fullmatch(
        r"CONFIG_IMPORT_STATUS: Phase=(EMPTY|COLLECTING|VALIDATED|APPLIED|READBACK_OK), "
        r"Received=(\d+)/(\d+), Result=(OK|ERROR)",
        lines[0],
    )
    if not status_match:
        raise ProtocolError("malformed config import status response")
    source = _parse_import_identity(lines[1], "CONFIG_IMPORT_SOURCE", SUPPORTED_PROTOCOL)
    target = _parse_import_identity(lines[2], "CONFIG_IMPORT_TARGET", SUPPORTED_PROTOCOL)
    if target is None:
        raise ProtocolError("config import status has no target identity")
    backup_match = re.fullmatch(
        r"CONFIG_IMPORT_BACKUP: BackupSHA=([0-9a-f]{64}|none)", lines[3]
    )
    transaction_match = re.fullmatch(
        r"CONFIG_IMPORT_TRANSACTION: TransactionSHA=([0-9a-f]{64}|none)", lines[4]
    )
    pending_match = re.fullmatch(
        r"CONFIG_IMPORT_PENDING: PendingTransactionSHA=([0-9a-f]{64}|none|error|invalid), "
        r"RecoveryRequired=(Yes|No)",
        lines[5],
    )
    if not backup_match or not transaction_match or not pending_match:
        raise ProtocolError("malformed config import status binding")
    if status_match.group(4) != "OK" or pending_match.group(1) in {"error", "invalid"}:
        raise PostInstallError(
            "device configuration restore journal is invalid or unavailable",
            outcome="config_restore_pending",
            stage="config_import_status",
        )
    expected = int(status_match.group(3))
    received = int(status_match.group(2))
    if expected != CONFIG_ITEM_COUNT or received > expected:
        raise ProtocolError("config import status item counts are invalid")
    backup_hash = None if backup_match.group(1) == "none" else backup_match.group(1)
    transaction_hash = (
        None if transaction_match.group(1) == "none" else transaction_match.group(1)
    )
    pending_hash = None if pending_match.group(1) == "none" else pending_match.group(1)
    recovery_required = pending_match.group(2) == "Yes"
    if (pending_hash is None) == recovery_required:
        raise ProtocolError("config import pending hash and recovery flag are inconsistent")
    phase = status_match.group(1)
    if phase == "EMPTY":
        phase_valid = (
            received == 0
            and source is None
            and backup_hash is None
            and transaction_hash is None
        )
    elif phase == "COLLECTING":
        phase_valid = (
            source is not None
            and backup_hash is not None
            and transaction_hash is None
        )
    elif phase == "VALIDATED":
        phase_valid = (
            received == CONFIG_ITEM_COUNT
            and source is not None
            and backup_hash is not None
            and transaction_hash is not None
        )
    elif phase == "APPLIED":
        phase_valid = (
            received == CONFIG_ITEM_COUNT
            and source is not None
            and backup_hash is not None
            and transaction_hash is not None
            and pending_hash is not None
            and recovery_required
        )
    else:  # READBACK_OK
        phase_valid = (
            received == CONFIG_ITEM_COUNT
            and source is not None
            and backup_hash is not None
            and transaction_hash is not None
            and pending_hash is None
            and not recovery_required
        )
    if not phase_valid:
        error = ProtocolError("config import status fields are inconsistent with its phase")
        error.restore_apply_may_have_occurred = bool(
            phase in {"APPLIED", "READBACK_OK"}
            or pending_hash is not None
            or recovery_required
        )
        error.device_reports_readback_ok = phase == "READBACK_OK"
        error.restore_binding_conflict = True
        raise error
    return ConfigImportStatus(
        phase=phase,
        received=received,
        expected=expected,
        result=status_match.group(4),
        source=source,
        target=target,
        backup_sha256=backup_hash,
        transaction_sha256=transaction_hash,
        pending_transaction_sha256=pending_hash,
        recovery_required=recovery_required,
    )


def _start_config_import(
    transport: SerialTransport,
    config: UpdateConfig,
    exported: VehicleConfigExport,
) -> None:
    command = (
        "config import begin "
        f"{exported.source.project_version} {exported.source.profile_id} "
        f"{exported.source.nvs_schema} {exported.target.project_version} "
        f"{exported.target.profile_id} {exported.target.nvs_schema} "
        f"{CONFIG_ITEM_COUNT} {exported.backup_sha256}"
    )
    transport.send_line(command)
    lines = _receive_config_protocol_block(
        transport,
        config,
        label="config import begin",
        prefixes=(
            "CONFIG_IMPORT_BEGIN:",
            "CONFIG_IMPORT_SOURCE:",
            "CONFIG_IMPORT_TARGET:",
            "CONFIG_IMPORT_BACKUP:",
        ),
    )
    begin = re.fullmatch(
        r"CONFIG_IMPORT_BEGIN: Phase=COLLECTING, ConfigSchema=(\d+), "
        r"Proto=([^,\s]+), Items=(\d+)",
        lines[0],
    )
    if (
        not begin
        or int(begin.group(1)) != CONFIG_BACKUP_SCHEMA
        or begin.group(2) != SUPPORTED_PROTOCOL
        or int(begin.group(3)) != CONFIG_ITEM_COUNT
    ):
        raise ProtocolError("config import begin contract does not match the backup")
    source = _parse_import_identity(lines[1], "CONFIG_IMPORT_SOURCE", SUPPORTED_PROTOCOL)
    target = _parse_import_identity(lines[2], "CONFIG_IMPORT_TARGET", SUPPORTED_PROTOCOL)
    backup = re.fullmatch(r"CONFIG_IMPORT_BACKUP: BackupSHA=([0-9a-f]{64})", lines[3])
    if source != exported.source or target != exported.target or not backup:
        raise ProtocolError("config import begin identity does not match the selected backup")
    if backup.group(1) != exported.backup_sha256:
        raise ProtocolError("config import begin acknowledged a different backup SHA256")


def _send_config_import_items(
    transport: SerialTransport,
    config: UpdateConfig,
    exported: VehicleConfigExport,
) -> None:
    for index, item in enumerate(exported.items, start=1):
        transport.send_line(
            f"config import item {item.name} {item.state} {item.value_type} {item.value}"
        )

        def parse(
            line: str,
            *,
            expected_name: str = item.name,
            expected_count: int = index,
        ) -> None:
            match = re.fullmatch(
                r"OK config import item name=([a-z0-9_.]+) received=(\d+)/(\d+)",
                line,
            )
            if (
                not match
                or match.group(1) != expected_name
                or int(match.group(2)) != expected_count
                or int(match.group(3)) != CONFIG_ITEM_COUNT
            ):
                raise ProtocolError("config import item acknowledgement is inconsistent")

        transport.wait_for(
            label=f"config import item {item.name}",
            prefixes=("OK config import item",),
            parser=parse,
            timeout=max(config.response_timeout, DEFAULT_CONFIG_TIMEOUT),
        )


def _abort_collecting_config_import(
    transport: SerialTransport,
    config: UpdateConfig,
    *,
    persistent_transaction_sha256: str | None,
) -> ConfigImportStatus:
    transport.send_line("config import abort")
    expected_recovery = "Yes" if persistent_transaction_sha256 is not None else "No"

    def parse(line: str) -> None:
        match = re.fullmatch(
            r"OK config import abort RecoveryRequired=(Yes|No) reboot_required=(Yes|No)",
            line,
        )
        if (
            not match
            or match.group(1) != expected_recovery
            or match.group(2) != expected_recovery
        ):
            raise ProtocolError("config import abort acknowledgement is inconsistent")

    transport.wait_for(
        label="config import abort",
        prefixes=("OK config import abort",),
        parser=parse,
        timeout=max(config.response_timeout, DEFAULT_CONFIG_TIMEOUT),
    )
    status = _query_config_import_status(transport, config)
    if (
        status.phase != "EMPTY"
        or status.received != 0
        or status.source is not None
        or status.backup_sha256 is not None
        or status.transaction_sha256 is not None
        or status.pending_transaction_sha256 != persistent_transaction_sha256
        or status.recovery_required != (persistent_transaction_sha256 is not None)
    ):
        raise ProtocolError("config import abort did not return a clean bound EMPTY state")
    return status


def _validate_config_import(
    transport: SerialTransport,
    config: UpdateConfig,
    exported: VehicleConfigExport,
    transaction_sha256: str,
    *,
    persistent_resume: bool = False,
) -> ConfigImportStatus:
    transport.send_line("config import validate")

    def parse(line: str) -> None:
        match = re.fullmatch(
            r"OK config import validate items=(\d+) BackupSHA=([0-9a-f]{12}) "
            r"TransactionSHA=([0-9a-f]{12})",
            line,
        )
        if (
            not match
            or int(match.group(1)) != CONFIG_ITEM_COUNT
            or match.group(2) != exported.backup_sha256[:12]
            or match.group(3) != transaction_sha256[:12]
        ):
            raise ProtocolError("config import validate acknowledgement is inconsistent")

    transport.wait_for(
        label="config import validate",
        prefixes=("OK config import validate",),
        parser=parse,
        timeout=max(config.response_timeout, DEFAULT_CONFIG_TIMEOUT),
    )
    status = _query_config_import_status(transport, config)
    pending_matches_mode = (
        status.pending_transaction_sha256 == transaction_sha256
        and status.recovery_required
        if persistent_resume
        else status.pending_transaction_sha256 is None and not status.recovery_required
    )
    if (
        status.phase != "VALIDATED"
        or status.received != CONFIG_ITEM_COUNT
        or status.source != exported.source
        or status.target != exported.target
        or status.backup_sha256 != exported.backup_sha256
        or status.transaction_sha256 != transaction_sha256
        or not pending_matches_mode
    ):
        raise ProtocolError("full config import transaction binding failed after validate")
    return status


def _apply_config_import(
    transport: SerialTransport,
    config: UpdateConfig,
    transaction_sha256: str,
) -> ConfigImportStatus:
    transport.send_line("config import apply")
    transport.wait_for(
        label="config import apply",
        prefixes=("OK config import apply",),
        parser=lambda line: line
        if line == "OK config import apply readback_required=Yes reboot_required=Yes"
        else (_ for _ in ()).throw(ProtocolError("malformed config import apply response")),
        timeout=max(config.response_timeout, DEFAULT_CONFIG_TIMEOUT),
    )
    status = _query_config_import_status(transport, config)
    if (
        status.phase != "APPLIED"
        or status.transaction_sha256 != transaction_sha256
        or status.pending_transaction_sha256 != transaction_sha256
        or not status.recovery_required
    ):
        raise ProtocolError("persistent restore transaction is not fully bound after apply")
    return status


def _readback_config_import(
    transport: SerialTransport,
    config: UpdateConfig,
    transaction_sha256: str,
) -> ConfigImportStatus:
    transport.send_line("config import readback")

    def parse(line: str) -> None:
        match = re.fullmatch(
            r"OK config import readback result=MATCH TransactionSHA=([0-9a-f]{12}) "
            r"reboot_required=Yes",
            line,
        )
        if not match or match.group(1) != transaction_sha256[:12]:
            raise ProtocolError("config import readback acknowledgement is inconsistent")

    transport.wait_for(
        label="config import readback",
        prefixes=("OK config import readback",),
        parser=parse,
        timeout=max(config.response_timeout, DEFAULT_CONFIG_TIMEOUT),
    )
    status = _query_config_import_status(transport, config)
    if (
        status.phase != "READBACK_OK"
        or status.transaction_sha256 != transaction_sha256
        or status.pending_transaction_sha256 is not None
        or status.recovery_required
    ):
        raise ProtocolError("restore journal was not cleared after exact readback")
    return status


def _status_matches_backup(status: ConfigImportStatus, exported: VehicleConfigExport) -> bool:
    return (
        status.source == exported.source
        and status.target == exported.target
        and status.backup_sha256 == exported.backup_sha256
    )


def run_config_backup(
    config: UpdateConfig,
    *,
    release: ReleasePackage | None = None,
    backup_dir: Path | None = None,
    serial_factory: SerialFactory = default_serial_factory,
    output_func: Callable[[str], None] = print,
    renderer: ConsoleRenderer | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    config.validate()
    renderer = renderer or ConsoleRenderer(output_func, monotonic=monotonic)
    audit = AuditLogger(config.log_dir)
    connection: SerialConnection | None = None
    try:
        renderer.header("OSRacer Vehicle Configuration Backup")
        renderer.path("Audit log", audit.path)
        renderer.phase(1, 3, "Inspect device", f"exclusive serial {config.port}")
        connection = _open_exclusive(config, serial_factory)
        transport = SerialTransport(connection, monotonic=monotonic, sleep=sleep)
        identity = _query_managed_identity(transport, config, audit, phase="config_backup")
        renderer.phase(2, 3, "Export configuration", "24 typed allowlisted items")
        exported = _receive_vehicle_config_export_when_ready(transport, config)
        _validate_backup_export_identity(exported, identity)
        if release is not None:
            _managed_manifest_contract(release)
            _validate_export_binding(
                exported,
                release,
                bootstrap=identity.profile.state == "BACKUP_REQUIRED",
            )
        renderer.phase(3, 3, "Persist and verify", "atomic private file, mode 0600")
        path, file_sha = _write_vehicle_config_backup(
            exported, release, backup_dir, audit_path=audit.path
        )
        renderer.path("Backup file", path, file_sha)
        renderer.result(
            True,
            [
                ("Items", str(CONFIG_ITEM_COUNT)),
                ("Backup SHA256", exported.backup_sha256),
                ("Persistent config writes", "none"),
                ("Audit log", str(audit.path)),
            ],
        )
        audit.event("result", "success", backup_sha256=exported.backup_sha256, backup_file=str(path))
        return path
    except FirmwareUpdateError as error:
        error.audit_path = audit.path
        audit.event("result", "failed", reason=str(error), exit_code=error.exit_code)
        renderer.result(
            False,
            [
                ("Backup result", "NOT CREATED"),
                ("Device configuration", "not modified"),
                ("Restore", "not started"),
                ("Next step", "resolve the reported error, then rerun config backup"),
                ("Audit log", str(audit.path)),
            ],
        )
        raise
    finally:
        _close_quietly(connection)
        audit.close()


def run_config_verify(
    backup_path: Path,
    config: UpdateConfig,
    *,
    serial_factory: SerialFactory = default_serial_factory,
    output_func: Callable[[str], None] = print,
    renderer: ConsoleRenderer | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    config.validate()
    exported, backup_file_sha = _load_vehicle_config_backup(backup_path)
    renderer = renderer or ConsoleRenderer(output_func, monotonic=monotonic)
    audit = AuditLogger(config.log_dir)
    connection: SerialConnection | None = None
    comparison: VehicleConfigSemanticComparison | None = None
    try:
        renderer.header("OSRacer Vehicle Configuration Verification")
        renderer.path("Backup file", backup_path, backup_file_sha)
        renderer.path("Audit log", audit.path)
        renderer.phase(1, 2, "Inspect device", f"target {exported.target.project_version}")
        connection = _open_exclusive(config, serial_factory)
        transport = SerialTransport(connection, monotonic=monotonic, sleep=sleep)
        identity = _query_managed_identity(transport, config, audit, phase="config_verify")
        if (
            identity.version.project_version != exported.target.project_version
            or identity.version.protocol != exported.target.protocol
            or identity.profile.profile_id != exported.target.profile_id
            or identity.profile.nvs_schema != exported.target.nvs_schema
        ):
            raise DevicePreflightError("backup target identity does not match the connected device")
        if (
            identity.profile.state != "READY"
            or not identity.profile.motion_ok
            or not identity.profile.writes_ok
        ):
            raise DevicePreflightError(
                "configuration verification requires READY with Motion=Yes and Writes=Yes"
            )
        renderer.phase(2, 2, "Compare configuration", "fresh export and typed semantic comparison")
        current = _receive_ready_vehicle_config_after_level_calibration(
            transport,
            config,
            audit,
            phase="config_verify",
        )
        _validate_ready_export_identity(current, exported.target)
        comparison = compare_vehicle_config_semantics(exported.items, current.items)
        _audit_vehicle_config_comparison(audit, comparison, phase="config_verify")
        if not comparison.matches:
            raise PostInstallError(
                "current configuration does not satisfy the selected backup semantic contract",
                outcome="ready_config_mismatch",
                stage="config_verify",
            )
        audit.event("result", "success", backup_sha256=exported.backup_sha256)
        renderer.result(
            True,
            [
                *_vehicle_config_result_rows(comparison),
                ("Restore", "not required"),
                ("Backup file", f"{backup_path.expanduser()} (retained)"),
                ("Unknown NVS keys", "outside logical verification"),
                ("Audit log", str(audit.path)),
            ],
        )
        return True
    except FirmwareUpdateError as error:
        error.audit_path = audit.path
        audit.event("result", "failed", reason=str(error), exit_code=error.exit_code)
        mismatch = isinstance(error, PostInstallError) and error.outcome == "ready_config_mismatch"
        level_invalid = bool(comparison and comparison.invalid_level_fields)
        comparison_rows = (
            _vehicle_config_result_rows(comparison)
            if comparison is not None
            else [("Configuration comparison", "NOT VERIFIED")]
        )
        renderer.result(
            False,
            [
                ("Update result", "not applicable; verification is read-only"),
                *comparison_rows,
                ("NVS partition", "not modified by verification"),
                (
                    "Restore",
                    (
                        "not evaluated; level calibration is invalid"
                        if level_invalid
                        else ("operator decision required" if mismatch else "not started")
                    ),
                ),
                (
                    "Next step",
                    (
                        "keep the vehicle stationary on level ground, inspect level calibration "
                        "health, then rerun config verify; do not restore solely for this result"
                        if level_invalid
                        else (
                            "review whether the backup belongs to this vehicle and is the desired "
                            "state; only then run config restore --backup "
                            f"{backup_path.expanduser()}"
                            if mismatch
                            else "resolve the reported identity or protocol error, then rerun config verify"
                        )
                    ),
                ),
                ("Audit log", str(audit.path)),
            ],
        )
        raise
    finally:
        _close_quietly(connection)
        audit.close()


def run_config_restore(
    backup_path: Path,
    config: UpdateConfig,
    *,
    serial_factory: SerialFactory = default_serial_factory,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
    renderer: ConsoleRenderer | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    config.validate()
    exported, backup_file_sha = _load_vehicle_config_backup(backup_path)
    transaction_sha = calculate_vehicle_restore_sha256(
        exported.backup_sha256, exported.source, exported.target
    )
    renderer = renderer or ConsoleRenderer(output_func, monotonic=monotonic)
    audit = AuditLogger(config.log_dir)
    connection: SerialConnection | None = None
    restore_apply_may_have_occurred = False
    device_reports_readback_ok = False
    selected_transaction_readback_verified = False
    restore_binding_conflict = False
    comparison: VehicleConfigSemanticComparison | None = None
    try:
        renderer.header("OSRacer Vehicle Configuration Restore")
        renderer.path("Backup file", backup_path, backup_file_sha)
        renderer.summary("Backup SHA256", exported.backup_sha256)
        renderer.summary("Transaction SHA", transaction_sha)
        renderer.path("Audit log", audit.path)
        renderer.phase(1, 7, "Inspect device and journal", "no writes yet")
        connection = _open_exclusive(config, serial_factory)
        transport = SerialTransport(connection, monotonic=monotonic, sleep=sleep)
        identity = _query_managed_identity(transport, config, audit, phase="config_restore")
        if (
            identity.version.project_version != exported.target.project_version
            or identity.version.protocol != exported.target.protocol
            or identity.profile.profile_id != exported.target.profile_id
            or identity.profile.nvs_schema != exported.target.nvs_schema
        ):
            raise DevicePreflightError("backup target identity does not match the connected device")
        if identity.profile.state not in {
            "READY",
            "PARAMETER_INVALID",
            "WRITE_FAILED",
            "CONFIG_RESTORE_INCOMPLETE",
        }:
            raise DevicePreflightError(
                "device profile state is not eligible for explicit configuration restore"
            )
        status = _query_config_import_status(transport, config)
        restore_apply_may_have_occurred = bool(
            status.phase in {"APPLIED", "READBACK_OK"}
            or status.pending_transaction_sha256 is not None
            or status.recovery_required
        )
        device_reports_readback_ok = status.phase == "READBACK_OK"
        if status.target != exported.target:
            restore_binding_conflict = True
            raise ProtocolError("restore status target does not match the selected backup")
        if status.pending_transaction_sha256 not in {None, transaction_sha}:
            restore_binding_conflict = True
            raise PostInstallError(
                "another persistent restore transaction is pending; the selected backup is not its source",
                outcome="config_restore_pending",
                stage="config_import_status",
            )
        if status.pending_transaction_sha256 == transaction_sha:
            renderer.summary("Pending journal", f"MATCH {transaction_sha}")
        else:
            renderer.summary("Pending journal", "none")
        if status.phase == "READBACK_OK" and (
            not _status_matches_backup(status, exported)
            or status.transaction_sha256 != transaction_sha
            or status.pending_transaction_sha256 is not None
            or status.recovery_required
        ):
            restore_binding_conflict = True
            raise PostInstallError(
                "completed restore journal does not match the selected full transaction",
                outcome="config_restore_pending",
                stage="config_import_status",
            )
        if status.phase == "READBACK_OK":
            selected_transaction_readback_verified = True
        persistent_resume = status.pending_transaction_sha256 == transaction_sha
        if selected_transaction_readback_verified:
            prompt = (
                f"Resume the completed restore for {exported.target.project_version} with reboot "
                "and final verification; no configuration items will be sent. "
                f"Type {RESTORE_CONFIRM_TEXT} to continue: "
            )
        else:
            prompt = (
                f"Restore {CONFIG_ITEM_COUNT} vehicle configuration items to "
                f"{exported.target.project_version}. Type {RESTORE_CONFIRM_TEXT} to continue: "
            )
        answer = input_func(prompt)
        if answer.strip() != RESTORE_CONFIRM_TEXT:
            raise UserCancelledError("vehicle configuration restore was not confirmed")
        audit.event("confirmation", "ok", operation="config_restore")

        if status.phase == "EMPTY":
            renderer.phase(2, 7, "Start bound restore", "source, target, backup, and transaction locked")
            _start_config_import(transport, config, exported)
            status = ConfigImportStatus(
                "COLLECTING", 0, CONFIG_ITEM_COUNT, "OK", exported.source,
                exported.target, exported.backup_sha256, None,
                status.pending_transaction_sha256, status.recovery_required,
            )
        elif not _status_matches_backup(status, exported):
            restore_binding_conflict = True
            raise PostInstallError(
                "active restore session does not match the selected backup",
                outcome="config_restore_pending",
                stage="config_import_status",
            )
        else:
            renderer.phase(2, 7, "Resume bound restore", f"device phase {status.phase}")
            if status.phase == "COLLECTING" and status.received > 0:
                status = _abort_collecting_config_import(
                    transport,
                    config,
                    persistent_transaction_sha256=(
                        transaction_sha if persistent_resume else None
                    ),
                )
                _start_config_import(transport, config, exported)
                status = ConfigImportStatus(
                    "COLLECTING",
                    0,
                    CONFIG_ITEM_COUNT,
                    "OK",
                    exported.source,
                    exported.target,
                    exported.backup_sha256,
                    None,
                    status.pending_transaction_sha256,
                    status.recovery_required,
                )

        if status.phase == "COLLECTING":
            renderer.phase(3, 7, "Transfer configuration", f"{CONFIG_ITEM_COUNT} typed items")
            _send_config_import_items(
                transport,
                config,
                exported,
            )
            renderer.phase(
                4,
                7,
                "Validate full transaction",
                "host requires the full 64-character SHA256 binding",
            )
            status = _validate_config_import(
                transport,
                config,
                exported,
                transaction_sha,
                persistent_resume=persistent_resume,
            )
        elif status.phase == "VALIDATED":
            if status.transaction_sha256 != transaction_sha:
                restore_binding_conflict = True
                raise PostInstallError(
                    "validated restore transaction does not match the selected backup",
                    outcome="config_restore_pending",
                    stage="config_import_status",
                )
            renderer.phase(3, 7, "Transfer configuration", "already complete")
            renderer.phase(4, 7, "Validate full transaction", "existing full SHA256 matches")

        if status.phase == "VALIDATED":
            renderer.phase(5, 7, "Apply configuration", "persistent journal enabled before writes")
            restore_apply_may_have_occurred = True
            status = _apply_config_import(transport, config, transaction_sha)
        elif status.phase == "APPLIED":
            restore_apply_may_have_occurred = True
            if (
                status.transaction_sha256 != transaction_sha
                or status.pending_transaction_sha256 != transaction_sha
            ):
                restore_binding_conflict = True
                raise PostInstallError(
                    "applied restore transaction does not match the selected backup",
                    outcome="config_restore_pending",
                    stage="config_import_status",
                )
            renderer.phase(5, 7, "Apply configuration", "already applied; persistent journal matches")

        if status.phase == "APPLIED":
            renderer.phase(6, 7, "Read back and finalize", "journal clears only after exact match")
            try:
                status = _readback_config_import(transport, config, transaction_sha)
            except DeviceRejectedError as error:
                if "mismatch" not in error.device_reason:
                    raise
                status = _apply_config_import(transport, config, transaction_sha)
                status = _readback_config_import(transport, config, transaction_sha)
            device_reports_readback_ok = True
            selected_transaction_readback_verified = True
        elif status.phase == "READBACK_OK":
            if (
                status.transaction_sha256 != transaction_sha
                or status.pending_transaction_sha256 is not None
                or status.recovery_required
            ):
                restore_binding_conflict = True
                selected_transaction_readback_verified = False
                raise PostInstallError(
                    "completed restore journal does not match the selected full transaction",
                    outcome="config_restore_pending",
                    stage="config_import_status",
                )
            renderer.phase(6, 7, "Read back and finalize", "already complete; journal is clear")
        else:
            raise ProtocolError(f"unsupported config restore resume phase {status.phase}")

        renderer.phase(7, 7, "Reboot and verify", "READY plus fresh 24-item export")
        _request_reset(transport, config, audit)
        _close_quietly(connection)
        connection = None
        # A restore package is represented by its target identity, not an App image.
        pseudo_release = ReleasePackage(
            manifest_sha256="0" * 64,
            app_sha256="0" * 64,
            app_member="images/application.bin",
            app_bytes=b"x",
            target=exported.target,
            package_sha256="0" * 64,
            package_size=1,
            source_tree_sha256="0" * 64,
            bootstrap_source_project_version=exported.source.project_version,
            config_item_count=CONFIG_ITEM_COUNT,
        )
        connection, ready = _reconnect_managed_identity(
            pseudo_release,
            config,
            serial_factory,
            audit,
            phase="post_restore",
            monotonic=monotonic,
            sleep=sleep,
        )
        _validate_managed_target_identity(
            ready,
            pseudo_release,
            required_state="READY",
            motion_ok=True,
            writes_ok=True,
        )
        current = _receive_ready_vehicle_config_after_level_calibration(
            SerialTransport(connection, monotonic=monotonic, sleep=sleep),
            config,
            audit,
            phase="config_restore_verify",
        )
        _validate_ready_export_identity(current, exported.target)
        comparison = compare_vehicle_config_semantics(exported.items, current.items)
        _audit_vehicle_config_comparison(audit, comparison, phase="config_restore_verify")
        if not comparison.matches:
            raise PostInstallError(
                "restored configuration does not satisfy the selected backup semantic contract",
                outcome="ready_config_mismatch",
                stage="config_restore_verify",
            )
        audit.event("result", "success", transaction_sha256=transaction_sha)
        renderer.result(
            True,
            [
                ("Restore", "completed and read back"),
                ("Firmware", f"{exported.target.project_version} / READY"),
                *_vehicle_config_result_rows(comparison),
                ("Backup file", f"{backup_path.expanduser()} (retained)"),
                ("Pending journal", "none"),
                ("Unknown NVS keys", "not modified by logical restore"),
                ("Audit log", str(audit.path)),
            ],
        )
        return True
    except FirmwareUpdateError as error:
        restore_apply_may_have_occurred = bool(
            restore_apply_may_have_occurred
            or getattr(error, "restore_apply_may_have_occurred", False)
        )
        device_reports_readback_ok = bool(
            device_reports_readback_ok
            or getattr(error, "device_reports_readback_ok", False)
        )
        restore_binding_conflict = bool(
            restore_binding_conflict
            or getattr(error, "restore_binding_conflict", False)
        )
        error.audit_path = audit.path
        error.no_app_reflash = True
        audit.event(
            "result",
            "failed",
            reason=str(error),
            exit_code=error.exit_code,
            restore_apply_started=restore_apply_may_have_occurred,
            restore_apply_may_have_occurred=restore_apply_may_have_occurred,
            device_reports_readback_ok=device_reports_readback_ok,
            selected_transaction_readback_verified=selected_transaction_readback_verified,
            restore_readback_completed=selected_transaction_readback_verified,
            restore_binding_conflict=restore_binding_conflict,
            transaction_sha256=transaction_sha,
        )
        comparison_rows = (
            _vehicle_config_result_rows(comparison)
            if comparison is not None
            else []
        )
        renderer.result(
            False,
            [
                ("Reason", str(error)),
                ("App reflash", "do not reflash"),
                *comparison_rows,
                (
                    "Restore state",
                    (
                        "different or invalid restore binding; writes may already have occurred; "
                        "do not continue with the selected backup"
                        if restore_binding_conflict
                        else (
                            "selected transaction already applied and read back; resume "
                            "reboot/final verification"
                            if selected_transaction_readback_verified
                            else (
                                "writes may already have occurred; resume the exact transaction"
                                if restore_apply_may_have_occurred
                                else "no apply started"
                            )
                        )
                    ),
                ),
                ("Audit log", str(audit.path)),
            ],
        )
        raise
    finally:
        _close_quietly(connection)
        audit.close()


def _positive_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def _chunk_size(value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if not MIN_CHUNK_SIZE <= result <= MAX_CHUNK_SIZE:
        raise argparse.ArgumentTypeError(f"must be between {MIN_CHUNK_SIZE} and {MAX_CHUNK_SIZE}")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect, acquire, or apply an OSRacer ESP32 App OTA release package."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="validate a release ZIP without opening serial")
    inspect_parser.add_argument("--package", required=True, help="local App release ZIP")

    catalog_parser = subparsers.add_parser("catalog", help="list one explicit catalog channel")
    catalog_parser.add_argument("--channel", required=True, choices=("stable", "test"))
    catalog_parser.add_argument(
        "--catalog-url",
        default=DEFAULT_CATALOG_URL,
        help="HTTPS catalog URL",
    )

    def add_release_source(command_parser: argparse.ArgumentParser) -> None:
        source_group = command_parser.add_mutually_exclusive_group(required=True)
        source_group.add_argument("--package", help="local App release ZIP")
        source_group.add_argument(
            "--channel",
            choices=("stable", "test"),
            help="explicit catalog channel; test is never selected by default",
        )
        command_parser.add_argument("--candidate", help="explicit catalog candidate id")
        command_parser.add_argument("--catalog-url", default=None, help="HTTPS catalog URL")

    def add_serial_options(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--port", default=DEFAULT_PORT, help=f"serial device (default: {DEFAULT_PORT})"
        )
        command_parser.add_argument(
            "--response-timeout", type=_positive_float, default=DEFAULT_RESPONSE_TIMEOUT
        )
        command_parser.add_argument(
            "--reconnect-timeout", type=_positive_float, default=DEFAULT_RECONNECT_TIMEOUT
        )
        command_parser.add_argument(
            "--log-dir", default=None, help="audit directory; defaults to XDG user state"
        )

    def add_managed_update_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        command_parser = subparsers.add_parser(
            name,
            help=help_text,
            description=help_text,
        )
        add_release_source(command_parser)
        add_serial_options(command_parser)
        command_parser.add_argument("--chunk-size", type=_chunk_size, default=DEFAULT_CHUNK_SIZE)
        command_parser.add_argument(
            "--backup-dir",
            default=None,
            help="private 0600 vehicle-config backup directory; defaults to XDG user state",
        )
        command_parser.add_argument(
            "--backup",
            default=None,
            help=(
                "exact private backup for READY reboot recovery or READY/Yes/Yes "
                "read-only verification"
            ),
        )
        return command_parser

    add_managed_update_parser(
        "update",
        "recommended managed App OTA with visible config backup and verification",
    )
    add_managed_update_parser(
        "resume",
        "resume BACKUP_REQUIRED without reflashing, recover a confirmed READY reboot, "
        "or verify READY against an exact backup",
    )

    ota_parser = subparsers.add_parser("app-ota", help="apply a validated App release over serial")
    add_release_source(ota_parser)
    add_serial_options(ota_parser)
    ota_parser.add_argument("--chunk-size", type=_chunk_size, default=DEFAULT_CHUNK_SIZE)
    ota_parser.add_argument(
        "--backup-dir",
        default=None,
        help="private vehicle-config backup directory for managed packages",
    )
    ota_parser.add_argument(
        "--backup",
        default=None,
        help="exact private backup for managed READY recovery or read-only verification",
    )
    ota_parser.add_argument(
        "--snapshot-dir",
        default=None,
        help="private configuration snapshot directory; defaults to XDG user state",
    )
    ota_parser.add_argument(
        "--resume-audit",
        default=None,
        help=(
            "exact private audit from a completed prior App write; only imports legacy "
            "migration recovery evidence and never selects an audit automatically"
        ),
    )
    ota_parser.add_argument(
        "--corrective-recovery",
        action="store_true",
        help=(
            "explicitly recover the approved T002 partial migration through a T003 App-only OTA; "
            "requires the exact original --resume-audit"
        ),
    )
    ota_parser.add_argument(
        "--reinstall",
        action="store_true",
        help="explicitly reinstall even when the target ProjectVer is already installed",
    )

    config_parser = subparsers.add_parser(
        "config", help="backup, verify, or explicitly restore vehicle configuration"
    )
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    config_backup = config_commands.add_parser("backup", help="export a private 24-item backup")
    add_serial_options(config_backup)
    config_backup.add_argument("--package", default=None, help="optional managed package binding")
    config_backup.add_argument("--backup-dir", default=None, help="private backup directory")
    config_verify = config_commands.add_parser("verify", help="compare a backup with the device")
    add_serial_options(config_verify)
    config_verify.add_argument("--backup", required=True, help="private 0600 backup JSON")
    config_restore = config_commands.add_parser(
        "restore", help="explicitly restore a selected private backup"
    )
    add_serial_options(config_restore)
    config_restore.add_argument("--backup", required=True, help="private 0600 backup JSON")

    report_parser = subparsers.add_parser("report", help="show a safe offline audit or backup summary")
    report_source = report_parser.add_mutually_exclusive_group(required=True)
    report_source.add_argument("--backup", help="private 0600 backup JSON")
    report_source.add_argument("--audit", help="private audit JSONL")
    return parser


def _config_from_args(args: argparse.Namespace, **overrides: Any) -> UpdateConfig:
    values: dict[str, Any] = {
        "port": args.port,
        "response_timeout": args.response_timeout,
        "reconnect_timeout": args.reconnect_timeout,
        "log_dir": Path(args.log_dir).expanduser() if args.log_dir else None,
    }
    values.update(overrides)
    return UpdateConfig(**values)


def _safe_audit_summary(path: Path) -> dict[str, Any]:
    path = path.expanduser()
    try:
        if (
            not path.is_absolute()
            or _inside_repository(path)
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_uid != os.getuid()
            or path.stat().st_mode & 0o077
            or path.stat().st_size > MAX_RESUME_AUDIT_BYTES
        ):
            raise OSError
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AuditError("audit report input must be a private 0600 JSONL file") from None
    if not records or len(records) > MAX_RESUME_AUDIT_LINES or not all(
        isinstance(record, dict) for record in records
    ):
        raise AuditError("audit report input has an invalid record structure")
    session = next(
        (record for record in records if record.get("step") in {"session", "managed_session"}),
        {},
    )
    result = next(
        (record for record in reversed(records) if record.get("step") == "result"),
        {},
    )
    return {
        "records": len(records),
        "operation": session.get("step", "unknown"),
        "target_project_version": session.get(
            "target_project_version", session.get("target_project_version")
        ),
        "app_sha256": session.get("app_sha256"),
        "result": result.get("status", "incomplete"),
        "exit_code": result.get("exit_code"),
        "reason": result.get("reason"),
        "action_required": result.get("action_required", result.get("recovery_required")),
    }


def main(
    argv: list[str] | None = None,
    *,
    opener: UrlOpener = default_url_opener,
    cache_directory: Path | None = None,
    serial_factory: SerialFactory = default_serial_factory,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            release = load_release_package(args.package)
            output_func(json.dumps(release.public_summary(), indent=2, sort_keys=True))
            return 0

        if args.command == "catalog":
            catalog_raw = _download_bytes(
                args.catalog_url,
                opener=opener,
                timeout=DEFAULT_DOWNLOAD_TIMEOUT,
                limit=MAX_CATALOG_BYTES,
                label="catalog",
            )
            catalog = parse_catalog(catalog_raw)
            output_func(
                json.dumps(
                    {
                        "schema": CATALOG_SCHEMA,
                        "catalog_sha256": catalog.sha256,
                        "channel": args.channel,
                        "candidates": [
                            candidate.public_summary() for candidate in catalog.channels[args.channel]
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "report":
            if args.backup:
                exported, file_sha = _load_vehicle_config_backup(Path(args.backup))
                output_func(
                    json.dumps(
                        {
                            "kind": "vehicle_config_backup",
                            "file_sha256": file_sha,
                            "backup_sha256": exported.backup_sha256,
                            "source_project_version": exported.source.project_version,
                            "target_project_version": exported.target.project_version,
                            "profile_id": exported.target.profile_id,
                            "nvs_schema": exported.target.nvs_schema,
                            "items": len(exported.items),
                            "format_valid": True,
                            "device_compatibility": "not_checked",
                            "restore_policy": (
                                "requires live identity/status validation and explicit RESTORE"
                            ),
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                output_func(
                    json.dumps(
                        _safe_audit_summary(Path(args.audit)),
                        indent=2,
                        sort_keys=True,
                    )
                )
            return 0

        if args.command == "config":
            config = _config_from_args(args)
            if args.config_command == "backup":
                release = load_release_package(args.package) if args.package else None
                run_config_backup(
                    config,
                    release=release,
                    backup_dir=Path(args.backup_dir).expanduser() if args.backup_dir else None,
                    serial_factory=serial_factory,
                    output_func=output_func,
                )
            elif args.config_command == "verify":
                run_config_verify(
                    Path(args.backup),
                    config,
                    serial_factory=serial_factory,
                    output_func=output_func,
                )
            else:
                run_config_restore(
                    Path(args.backup),
                    config,
                    serial_factory=serial_factory,
                    input_func=input_func,
                    output_func=output_func,
                )
            return 0

        if args.package is not None:
            if args.candidate is not None or args.catalog_url is not None:
                raise CatalogError("--candidate/--catalog-url cannot be combined with local --package")
            release = load_release_package(args.package)
            source = UpdateSource()
        else:
            if args.candidate is None:
                raise CatalogError("catalog App OTA requires explicit --candidate")
            release, source = acquire_catalog_release(
                catalog_url=args.catalog_url or DEFAULT_CATALOG_URL,
                channel=args.channel,
                candidate_id=args.candidate,
                opener=opener,
                cache_directory=cache_directory,
                output_func=output_func,
            )

        if args.command in {"update", "resume"} and release.config_item_count != CONFIG_ITEM_COUNT:
            raise PackageValidationError(
                "update/resume requires a package with the managed vehicle-config contract"
            )

        if args.command in {"update", "resume"}:
            config = _config_from_args(
                args,
                chunk_size=args.chunk_size,
            )
            run_managed_update(
                release,
                config,
                source=source,
                backup_dir=Path(args.backup_dir).expanduser() if args.backup_dir else None,
                resume_backup_path=Path(args.backup).expanduser() if args.backup else None,
                serial_factory=serial_factory,
                input_func=input_func,
                output_func=output_func,
                resume_only=args.command == "resume",
            )
            return 0

        config = _config_from_args(
            args,
            chunk_size=args.chunk_size,
            reinstall=args.reinstall,
            snapshot_dir=Path(args.snapshot_dir).expanduser() if args.snapshot_dir else None,
            resume_audit=Path(args.resume_audit).expanduser() if args.resume_audit else None,
            corrective_recovery=args.corrective_recovery,
        )
        if release.config_item_count == CONFIG_ITEM_COUNT:
            if args.corrective_recovery or args.resume_audit or args.reinstall:
                raise PackageValidationError(
                    "managed update packages cannot use legacy corrective, audit, or reinstall options"
                )
            result = run_managed_update(
                release,
                config,
                source=source,
                backup_dir=Path(args.backup_dir).expanduser() if args.backup_dir else None,
                resume_backup_path=Path(args.backup).expanduser() if args.backup else None,
                serial_factory=serial_factory,
                input_func=input_func,
                output_func=output_func,
            )
        else:
            if args.backup is not None or args.backup_dir is not None:
                raise PackageValidationError(
                    "legacy app-ota does not use --backup or --backup-dir; remove these options"
                )
            result = run_app_ota(
                release,
                config,
                source=source,
                serial_factory=serial_factory,
                input_func=input_func,
                output_func=output_func,
            )
        if result.operation == "managed_update":
            return 0
        if result.operation == "corrective_recovery":
            output_func("T002 to T003 corrective recovery succeeded with the original snapshot.")
        elif result.operation == "migration_recovery":
            output_func("Profile migration recovery succeeded; no App data was sent.")
        else:
            output_func(f"Firmware update result: {result.status}")
        output_func(f"Audit log: {result.audit_path}")
        return 0
    except FirmwareUpdateError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        if getattr(error, "no_app_reflash", False):
            print(
                "Do not reflash the App; follow the recovery guidance above.",
                file=sys.stderr,
            )
        if error.audit_path is not None:
            print(f"Audit log: {error.audit_path}", file=sys.stderr)
        return error.exit_code
