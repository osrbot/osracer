#!/usr/bin/env python3
"""Proven internal serial/configuration engine for the unified client.

This module contains only the serial, backup, and App OTA primitives used by
the supported ``osracer-firmware-client`` entry point.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import struct
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol


MANIFEST_SCHEMA = 1
SUPPORTED_PROTOCOL = "1.1"
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
CONFIG_BACKUP_SCHEMA = 1
CONFIG_HASH_DOMAIN = "OSRVCFG1"
CONFIG_ITEM_COUNT = 24
DEFAULT_CONFIG_TIMEOUT = 10.0
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

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
@dataclass(frozen=True)
class TargetProfile:
    profile_id: str
    hardware: str
    nvs_schema: int
    project_version: str
    protocol: str



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



def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside_repository(path: Path) -> bool:
    try:
        return path.resolve(strict=False).is_relative_to(REPOSITORY_ROOT)
    except OSError:
        return True


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
