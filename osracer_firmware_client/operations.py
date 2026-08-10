"""Single business core shared by the CLI and the local web interface."""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from . import core
from .bundles import FirmwareBundle, load_bundles, match_official_bundle
from .images import ApplicationImage, validate_application_file
from .rom import EsptoolRomFactory, RomFactory, RomSecurityInfo
from .storage import (
    RawNvsBackup,
    default_state_directory,
    read_private_file,
    write_raw_nvs_backup,
)


EventSink = Callable[[dict[str, Any]], None]


class OperationBusyError(core.FirmwareUpdateError):
    exit_code = 3


class ConfirmationError(core.UserCancelledError):
    pass


@dataclass(frozen=True)
class ClientSettings:
    port: str = core.DEFAULT_PORT
    baud: int = core.DEFAULT_BAUD
    chunk_size: int = core.DEFAULT_CHUNK_SIZE
    response_timeout: float = core.DEFAULT_RESPONSE_TIMEOUT
    reconnect_timeout: float = core.DEFAULT_RECONNECT_TIMEOUT
    state_dir: Path = field(default_factory=default_state_directory)

    def update_config(self) -> core.UpdateConfig:
        config = core.UpdateConfig(
            port=self.port,
            baud=self.baud,
            chunk_size=self.chunk_size,
            response_timeout=self.response_timeout,
            reconnect_timeout=self.reconnect_timeout,
            log_dir=self.audit_dir,
            snapshot_dir=self.backup_dir,
        )
        config.validate()
        return config

    @property
    def audit_dir(self) -> Path:
        return self.state_dir.expanduser() / "audit"

    @property
    def backup_dir(self) -> Path:
        return self.state_dir.expanduser() / "backups"

    @property
    def raw_nvs_dir(self) -> Path:
        return self.state_dir.expanduser() / "nvs-raw"


@dataclass(frozen=True)
class DeviceInspection:
    project_version: str
    protocol: str | None
    profile_id: str | None
    nvs_schema: int | None
    profile_state: str | None
    motion_ok: bool | None
    writes_ok: bool | None
    voltage: float
    firmware_status: core.FirmwareStatus
    official_bundle_id: str | None
    backup_capability: str

    def safe_summary(self) -> dict[str, Any]:
        return {
            "project_version": self.project_version,
            "protocol": self.protocol,
            "profile_id": self.profile_id,
            "nvs_schema": self.nvs_schema,
            "profile_state": self.profile_state,
            "motion_ok": self.motion_ok,
            "writes_ok": self.writes_ok,
            "battery_voltage": self.voltage,
            "ota_session_active": self.firmware_status.active,
            "official_bundle_id": self.official_bundle_id,
            "backup_capability": self.backup_capability,
        }


@dataclass(frozen=True)
class LogicalBackup:
    kind: str
    path: Path
    file_sha256: str
    reference: core.VehicleConfigExport | dict[str, Any]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": str(self.path),
            "file_sha256": self.file_sha256,
        }


@dataclass(frozen=True)
class OperationResult:
    status: str
    operation: str
    audit_path: Path
    message: str
    bundle_id: str | None = None
    app_sha256: str | None = None
    logical_backup: LogicalBackup | None = None
    raw_nvs_backup: RawNvsBackup | None = None
    post_project_version: str | None = None
    post_verification: str = "not_run"
    retry_app_update: bool | None = None

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "operation": self.operation,
            "message": self.message,
            "bundle_id": self.bundle_id,
            "app_sha256": self.app_sha256,
            "audit_path": str(self.audit_path),
            "logical_backup": (
                None if self.logical_backup is None else self.logical_backup.safe_summary()
            ),
            "raw_nvs_backup": (
                None
                if self.raw_nvs_backup is None
                else {
                    "path": str(self.raw_nvs_backup.data.path),
                    "sha256": self.raw_nvs_backup.data.sha256,
                    "offset": self.raw_nvs_backup.offset,
                    "size": self.raw_nvs_backup.size,
                    "metadata_path": str(self.raw_nvs_backup.metadata.path),
                }
            ),
            "post_project_version": self.post_project_version,
            "post_verification": self.post_verification,
            "retry_app_update": self.retry_app_update,
        }


@dataclass(frozen=True)
class ErasePreparation:
    preparation_id: str
    bundle_id: str
    created_monotonic: float
    raw_nvs_backup: RawNvsBackup
    logical_backup: LogicalBackup | None
    source_project_version: str | None
    security: RomSecurityInfo
    audit_path: Path

    def safe_summary(self) -> dict[str, Any]:
        return {
            "preparation_id": self.preparation_id,
            "bundle_id": self.bundle_id,
            "logical_backup": (
                None if self.logical_backup is None else self.logical_backup.safe_summary()
            ),
            "raw_nvs_backup": {
                "path": str(self.raw_nvs_backup.data.path),
                "sha256": self.raw_nvs_backup.data.sha256,
                "offset": self.raw_nvs_backup.offset,
                "size": self.raw_nvs_backup.size,
                "metadata_path": str(self.raw_nvs_backup.metadata.path),
            },
            "non_nvs_data": "will_be_erased",
            "required_confirmation": f"ERASE AND FLASH {self.bundle_id}",
            "audit_path": str(self.audit_path),
        }


class FirmwareClient:
    def __init__(
        self,
        *,
        settings: ClientSettings | None = None,
        serial_factory: core.SerialFactory = core.default_serial_factory,
        rom_factory: RomFactory | None = None,
        event_sink: EventSink | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings or ClientSettings()
        self.serial_factory = serial_factory
        self.rom_factory = rom_factory or EsptoolRomFactory()
        self.event_sink = event_sink or (lambda _event: None)
        self.monotonic = monotonic
        self.sleep = sleep
        self.bundles = load_bundles()
        self._lock = threading.Lock()
        self._preparations: dict[str, ErasePreparation] = {}
        self._last_progress_percent: int | None = None

    def _event(
        self,
        phase: str,
        status: str,
        message: str,
        *,
        progress: float | None = None,
        **details: Any,
    ) -> None:
        event: dict[str, Any] = {
            "phase": phase,
            "status": status,
            "message": message,
            "timestamp": time.time(),
        }
        if progress is not None:
            event["progress"] = max(0.0, min(1.0, float(progress)))
        if details:
            event["details"] = details
        self.event_sink(event)

    @contextmanager
    def _exclusive_operation(self) -> Iterator[None]:
        if not self._lock.acquire(blocking=False):
            raise OperationBusyError("another firmware operation is already active")
        try:
            yield
        finally:
            self._lock.release()

    def _open_serial(self, config: core.UpdateConfig) -> core.SerialConnection:
        return core._open_exclusive(config, self.serial_factory)

    def _inspect_connection(
        self,
        connection: core.SerialConnection,
        config: core.UpdateConfig,
        audit: core.AuditLogger,
        *,
        phase: str,
        reconnect_deadline: float | None = None,
    ) -> DeviceInspection:
        transport = core.SerialTransport(connection, monotonic=self.monotonic, sleep=self.sleep)
        transport.send_line("v 0.00 0.00")
        transport.send_line("stream off")
        transport.sleep(0.05)
        core._drain_safe_stop(transport, audit, phase=phase)
        version = core._query_value(
            transport,
            config,
            command="fw version",
            label="fw version",
            prefix="FW_VERSION:",
            parser=core.parse_firmware_version,
        )
        profile: core.ProfileStatus | None = None
        try:
            profile = core._query_value(
                transport,
                config,
                command="profile get",
                label="profile",
                prefix="PROFILE:",
                parser=core.parse_profile_status,
            )
        except (core.ResponseTimeoutError, core.DeviceRejectedError):
            profile = None
        firmware_status = core._query_fw_status(transport, config)
        if phase == "post":
            voltage = core._query_post_battery(
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
                parser=core.parse_battery_voltage,
                timeout=config.response_timeout,
            )
        bundle = match_official_bundle(version.project_version, self.bundles)
        if bundle is not None and profile is not None and (
            profile.profile_id != bundle.target.profile_id
            or profile.nvs_schema != bundle.target.nvs_schema
        ):
            audit.event(
                "device_identity",
                "mismatch",
                phase=phase,
                bundle_id=bundle.bundle_id,
                profile_id=profile.profile_id,
                nvs_schema=profile.nvs_schema,
            )
            bundle = None
        capability = self._backup_capability(version, profile, bundle)
        inspection = DeviceInspection(
            project_version=version.project_version,
            protocol=version.protocol,
            profile_id=None if profile is None else profile.profile_id,
            nvs_schema=None if profile is None else profile.nvs_schema,
            profile_state=None if profile is None else profile.state,
            motion_ok=None if profile is None else profile.motion_ok,
            writes_ok=None if profile is None else profile.writes_ok,
            voltage=voltage,
            firmware_status=firmware_status,
            official_bundle_id=None if bundle is None else bundle.bundle_id,
            backup_capability=capability,
        )
        audit.event("device_inspection", "ok", phase=phase, **inspection.safe_summary())
        return inspection

    @staticmethod
    def _backup_capability(
        version: core.FirmwareVersion,
        profile: core.ProfileStatus | None,
        bundle: FirmwareBundle | None,
    ) -> str:
        if (
            version.protocol == core.SUPPORTED_PROTOCOL
            and profile is not None
            and profile.profile_id == "red"
            and profile.nvs_schema == 1
        ):
            return "managed"
        if bundle is not None and bundle.logical_backup == "legacy":
            return "legacy"
        if version.project_version.startswith("NEORACER_V1"):
            return "legacy"
        return "unavailable"

    def inspect(self) -> DeviceInspection:
        with self._exclusive_operation():
            config = self.settings.update_config()
            audit = core.AuditLogger(config.log_dir)
            connection = None
            try:
                self._event("inspect", "started", "Inspecting device")
                connection = self._open_serial(config)
                result = self._inspect_connection(connection, config, audit, phase="inspect")
                audit.event("result", "success", operation="inspect")
                self._event("inspect", "completed", "Device inspection completed")
                return result
            except core.FirmwareUpdateError as error:
                audit.event("result", "failed", operation="inspect", reason=str(error))
                error.audit_path = audit.path
                self._event("inspect", "failed", str(error))
                raise
            except Exception:
                error = core.ProtocolError("unexpected local client error during device inspection")
                error.audit_path = audit.path
                audit.event("result", "failed", operation="inspect", reason=str(error))
                self._event("inspect", "failed", str(error))
                raise error from None
            finally:
                core._close_quietly(connection)
                audit.close()

    def _create_logical_backup(
        self,
        connection: core.SerialConnection,
        inspection: DeviceInspection,
        config: core.UpdateConfig,
        audit: core.AuditLogger,
        *,
        release: core.ReleasePackage | None,
    ) -> LogicalBackup | None:
        transport = core.SerialTransport(connection, monotonic=self.monotonic, sleep=self.sleep)
        if inspection.backup_capability == "managed":
            if inspection.profile_state == "READY":
                core._wait_for_level_calibration(
                    transport,
                    config,
                    audit,
                    reconnect_deadline=None,
                )
            exported = core._receive_vehicle_config_export_when_ready(transport, config)
            if (
                exported.source.project_version != inspection.project_version
                or exported.source.profile_id != inspection.profile_id
                or exported.source.nvs_schema != inspection.nvs_schema
            ):
                raise core.DevicePreflightError(
                    "configuration export source identity does not match the inspected device"
                )
            path, file_sha = core._write_vehicle_config_backup(
                exported,
                release,
                self.settings.backup_dir,
                audit_path=audit.path,
            )
            backup = LogicalBackup("managed", path, file_sha, exported)
        elif inspection.backup_capability == "legacy":
            configuration = core._query_configuration(transport, config)
            snapshot = core.DeviceSnapshot(
                version=core.FirmwareVersion(
                    inspection.project_version,
                    inspection.protocol,
                ),
                profile=None,
                voltage=inspection.voltage,
                firmware_status=inspection.firmware_status,
                configuration=configuration,
                unavailable_fields=("managed_config_export",),
            )
            file_sha, path = core._write_snapshot(snapshot, self.settings.backup_dir)
            backup = LogicalBackup("legacy", path, file_sha, configuration)
        else:
            audit.event("logical_backup", "unavailable")
            self._event(
                "backup",
                "unavailable",
                "Logical parameter backup is unavailable; App-only OTA still preserves NVS",
            )
            return None
        audit.event(
            "logical_backup",
            "stored",
            kind=backup.kind,
            path=str(backup.path),
            file_sha256=backup.file_sha256,
        )
        self._event(
            "backup",
            "completed",
            "Vehicle parameter backup stored",
            kind=backup.kind,
            path=str(backup.path),
            sha256=backup.file_sha256,
        )
        return backup

    def _verify_logical_backup(
        self,
        connection: core.SerialConnection,
        backup: LogicalBackup,
        config: core.UpdateConfig,
        audit: core.AuditLogger,
    ) -> str:
        transport = core.SerialTransport(connection, monotonic=self.monotonic, sleep=self.sleep)
        if backup.kind == "managed":
            expected = backup.reference
            if not isinstance(expected, core.VehicleConfigExport):
                raise core.ProtocolError("managed backup reference is invalid")
            current = core._receive_ready_vehicle_config_after_level_calibration(
                transport,
                config,
                audit,
                phase="post",
            )
            comparison = core.compare_vehicle_config_semantics(expected.items, current.items)
            core._audit_vehicle_config_comparison(audit, comparison, phase="post")
            if not comparison.matches:
                raise core.PostInstallError(
                    "post-update configuration differs from the persisted backup",
                    outcome="post_verification_pending",
                    stage="configuration_compare",
                )
            return (
                f"20 non-level items match; level init {comparison.level_init_status}; "
                f"level offsets {comparison.level_offset_status}"
            )
        expected_legacy = backup.reference
        if not isinstance(expected_legacy, dict):
            raise core.ProtocolError("legacy backup reference is invalid")
        current_legacy = core._query_configuration(transport, config)
        mismatches = core._configuration_mismatches(expected_legacy, current_legacy)
        audit.event(
            "legacy_config_compare",
            "ok" if not mismatches else "mismatch",
            mismatch_fields=mismatches,
            level_offsets_dynamic=core._level_offset_changed(expected_legacy, current_legacy),
        )
        if mismatches:
            raise core.PostInstallError(
                "post-update known vehicle parameters differ from the persisted backup",
                outcome="post_verification_pending",
                stage="legacy_configuration_compare",
            )
        return "known legacy parameters match; boot-time level offsets are dynamic"

    def _progress_callback(self, written: int, total: int) -> None:
        percent = 0 if total <= 0 else min(100, written * 100 // total)
        if percent == self._last_progress_percent:
            return
        self._last_progress_percent = percent
        self._event(
            "flash_app",
            "progress",
            "Flashing application",
            progress=0.0 if total <= 0 else written / total,
            written=written,
            total=total,
        )

    def _perform_app_transfer(
        self,
        connection: core.SerialConnection,
        release: core.ReleasePackage,
        config: core.UpdateConfig,
        audit: core.AuditLogger,
    ) -> core.OtaProgress:
        self._last_progress_percent = None
        progress = core.OtaProgress()
        abort_acknowledged = False
        try:
            core._perform_ota(
                connection,
                release,
                config,
                audit,
                progress,
                monotonic=self.monotonic,
                sleep=self.sleep,
                progress_func=self._progress_callback,
            )
            return progress
        except KeyboardInterrupt:
            if progress.session_active and not progress.end_may_have_been_sent:
                abort_acknowledged = core._best_effort_abort(
                    connection,
                    config,
                    audit,
                    monotonic=self.monotonic,
                    sleep=self.sleep,
                )
            error = core.UpdateInterruptedError("firmware operation interrupted by operator")
            error.no_app_reflash = not self._safe_app_retry(progress, abort_acknowledged)
            raise error from None
        except core.FirmwareUpdateError as error:
            if progress.session_active and not progress.end_may_have_been_sent:
                abort_acknowledged = core._best_effort_abort(
                    connection,
                    config,
                    audit,
                    monotonic=self.monotonic,
                    sleep=self.sleep,
                )
            error.no_app_reflash = not self._safe_app_retry(progress, abort_acknowledged)
            raise
        except Exception:
            if progress.session_active and not progress.end_may_have_been_sent:
                abort_acknowledged = core._best_effort_abort(
                    connection,
                    config,
                    audit,
                    monotonic=self.monotonic,
                    sleep=self.sleep,
                )
            error = core.ProtocolError("unexpected host error during App transfer")
            error.no_app_reflash = not self._safe_app_retry(progress, abort_acknowledged)
            raise error from None

    @staticmethod
    def _safe_app_retry(progress: core.OtaProgress, abort_acknowledged: bool) -> bool:
        if progress.begin_rejected:
            return True
        if not progress.begin_may_have_been_sent:
            return True
        if progress.end_may_have_been_sent or progress.data_delivery_unknown:
            return False
        return (
            progress.data_committed_bytes == 0
            and progress.session_active
            and abort_acknowledged
        )

    def _wait_for_official_target(
        self,
        bundle: FirmwareBundle,
        config: core.UpdateConfig,
        audit: core.AuditLogger,
    ) -> tuple[core.SerialConnection, DeviceInspection]:
        deadline = self.monotonic() + config.reconnect_timeout
        last_error: core.FirmwareUpdateError | None = None
        while self.monotonic() < deadline:
            connection = None
            try:
                connection = self._open_serial(config)
                inspection = self._inspect_connection(
                    connection,
                    config,
                    audit,
                    phase="post",
                    reconnect_deadline=deadline,
                )
                if inspection.project_version != bundle.target.project_version:
                    raise core.PostInstallError(
                        "reconnected firmware does not match the selected official package",
                        outcome="app_installed_not_ready",
                        stage="project_version",
                    )
                if inspection.protocol != bundle.target.protocol:
                    raise core.PostInstallError(
                        "reconnected firmware protocol does not match the selected official package",
                        outcome="app_installed_not_ready",
                        stage="protocol",
                    )
                if inspection.profile_id is not None and (
                    inspection.profile_id != bundle.target.profile_id
                    or inspection.nvs_schema != bundle.target.nvs_schema
                ):
                    raise core.PostInstallError(
                        "reconnected firmware profile does not match the selected official package",
                        outcome="app_installed_not_ready",
                        stage="profile",
                    )
                if bundle.bundle_id == "B02" and (
                    inspection.profile_state != "READY"
                    or inspection.motion_ok is not True
                    or inspection.writes_ok is not True
                ):
                    raise core.PostInstallError(
                        "official package B02 booted but is not READY",
                        outcome="app_installed_not_ready",
                        stage="profile_state",
                    )
                return connection, inspection
            except (core.SerialUnavailableError, core.ResponseTimeoutError, core.SerialCommunicationError) as error:
                last_error = error
                core._close_quietly(connection)
                remaining = deadline - self.monotonic()
                if remaining > 0:
                    self.sleep(min(config.reconnect_interval, remaining))
            except core.FirmwareUpdateError:
                core._close_quietly(connection)
                raise
        raise core.ReconnectTimeoutError(
            "device did not reconnect with the selected official firmware"
        ) from last_error

    def official_update(
        self,
        *,
        confirmation: str,
        reinstall: bool = False,
    ) -> OperationResult:
        with self._exclusive_operation():
            config = self.settings.update_config()
            audit = core.AuditLogger(config.log_dir)
            connection = None
            backup: LogicalBackup | None = None
            app_delivery_completed = False
            try:
                self._event("inspect", "started", "Inspecting device")
                connection = self._open_serial(config)
                inspection = self._inspect_connection(connection, config, audit, phase="pre")
                if inspection.firmware_status.active:
                    raise core.DevicePreflightError("an App OTA session is already active")
                if inspection.official_bundle_id is None:
                    raise core.DevicePreflightError(
                        "device identity does not map to one official package; use custom App or advanced recovery"
                    )
                bundle = self.bundles[inspection.official_bundle_id]
                audit.event(
                    "official_session",
                    "started",
                    bundle_id=bundle.bundle_id,
                    source_project_version=inspection.project_version,
                    target_project_version=bundle.target.project_version,
                    app_sha256=bundle.app.sha256,
                )
                if inspection.project_version == bundle.target.project_version and not reinstall:
                    result = OperationResult(
                        "skipped",
                        "official",
                        audit.path,
                        "Selected official firmware is already installed",
                        bundle_id=bundle.bundle_id,
                        app_sha256=bundle.app.sha256,
                        post_project_version=inspection.project_version,
                        post_verification="identity_only",
                        retry_app_update=None,
                    )
                    audit.event("result", "skipped", result=result.safe_summary())
                    self._event(
                        "result",
                        "skipped",
                        result.message,
                        result=result.safe_summary(),
                    )
                    return result
                self._event("validate", "completed", "Embedded official firmware validated")
                backup = self._create_logical_backup(
                    connection,
                    inspection,
                    config,
                    audit,
                    release=bundle.as_release_package(),
                )
                if backup is None:
                    raise core.DevicePreflightError(
                        "official update requires a logical vehicle parameter backup"
                    )
                if confirmation != "UPDATE":
                    raise ConfirmationError("official update was not confirmed with UPDATE")
                audit.event("confirmation", "ok", operation="official", bundle_id=bundle.bundle_id)
                self._event("flash_app", "started", "Starting App-only OTA; NVS is not erased")
                progress = self._perform_app_transfer(
                    connection,
                    bundle.as_release_package(),
                    config,
                    audit,
                )
                app_delivery_completed = True
                core._close_quietly(connection)
                connection = None
                self._event("reconnect", "started", "Waiting for official firmware")
                connection, post = self._wait_for_official_target(bundle, config, audit)
                verification = self._verify_logical_backup(connection, backup, config, audit)
                result = OperationResult(
                    "success",
                    "official",
                    audit.path,
                    "Official App update completed; NVS was preserved and parameters verified",
                    bundle_id=bundle.bundle_id,
                    app_sha256=bundle.app.sha256,
                    logical_backup=backup,
                    post_project_version=post.project_version,
                    post_verification=verification,
                    retry_app_update=False,
                )
                audit.event(
                    "result",
                    "success",
                    end_acknowledged=progress.end_acknowledged,
                    result=result.safe_summary(),
                )
                self._event(
                    "result",
                    "success",
                    result.message,
                    result=result.safe_summary(),
                )
                return result
            except core.FirmwareUpdateError as error:
                error.audit_path = audit.path
                if app_delivery_completed:
                    error.no_app_reflash = True
                retry = not bool(getattr(error, "no_app_reflash", False))
                audit.event(
                    "result",
                    "failed",
                    operation="official",
                    reason=str(error),
                    retry_app_update=retry,
                    backup_path=None if backup is None else str(backup.path),
                )
                self._event(
                    "result",
                    "failed",
                    str(error),
                    retry_app_update=retry,
                    backup_path=None if backup is None else str(backup.path),
                    audit_path=str(audit.path),
                )
                raise
            except Exception:
                error = core.ProtocolError("unexpected local client error during official update")
                error.audit_path = audit.path
                error.no_app_reflash = app_delivery_completed
                audit.event(
                    "result",
                    "failed",
                    operation="official",
                    reason=str(error),
                    retry_app_update=not app_delivery_completed,
                )
                self._event("result", "failed", str(error), audit_path=str(audit.path))
                raise error from None
            finally:
                core._close_quietly(connection)
                audit.close()

    @staticmethod
    def _custom_release(image: ApplicationImage, source: DeviceInspection) -> core.ReleasePackage:
        target = core.TargetProfile(
            profile_id=source.profile_id or "custom",
            hardware="ESP32-S3",
            nvs_schema=source.nvs_schema or 1,
            project_version=image.version[:31],
            protocol=source.protocol or core.SUPPORTED_PROTOCOL,
        )
        return core.ReleasePackage(
            manifest_sha256=image.sha256,
            app_sha256=image.sha256,
            app_member="custom/app.bin",
            app_bytes=image.data,
            target=target,
            package_sha256=image.sha256,
            package_size=image.size,
        )

    def _probe_custom_target(
        self,
        source_version: str,
        image: ApplicationImage,
        config: core.UpdateConfig,
        audit: core.AuditLogger,
    ) -> tuple[str | None, str]:
        deadline = self.monotonic() + config.reconnect_timeout
        while self.monotonic() < deadline:
            connection = None
            try:
                connection = self._open_serial(config)
                transport = core.SerialTransport(
                    connection,
                    monotonic=self.monotonic,
                    sleep=self.sleep,
                )
                transport.send_line("fw version")
                version = transport.wait_for(
                    label="fw version",
                    prefixes=("FW_VERSION:",),
                    parser=core.parse_firmware_version,
                    timeout=config.response_timeout,
                )
                if version.project_version == image.version:
                    return version.project_version, "custom_identity_matched"
                if version.project_version != source_version:
                    return version.project_version, "custom_identity_changed"
                return version.project_version, "source_identity_still_reported"
            except core.FirmwareUpdateError:
                remaining = deadline - self.monotonic()
                if remaining > 0:
                    self.sleep(min(config.reconnect_interval, remaining))
            finally:
                core._close_quietly(connection)
        audit.event("custom_post_probe", "unavailable")
        return None, "unavailable"

    def custom_app_update(
        self,
        image_path: Path,
        *,
        confirmation: str,
    ) -> OperationResult:
        with self._exclusive_operation():
            config = self.settings.update_config()
            audit = core.AuditLogger(config.log_dir)
            connection = None
            backup: LogicalBackup | None = None
            app_delivery_completed = False
            try:
                self._event("validate", "started", "Validating custom ESP32-S3 application")
                image = validate_application_file(image_path.expanduser().absolute())
                audit.event("custom_image", "validated", **image.safe_summary())
                self._event("validate", "completed", "Custom application validated", **image.safe_summary())
                connection = self._open_serial(config)
                inspection = self._inspect_connection(connection, config, audit, phase="pre")
                if inspection.firmware_status.active:
                    raise core.DevicePreflightError("an App OTA session is already active")
                backup = self._create_logical_backup(
                    connection,
                    inspection,
                    config,
                    audit,
                    release=None,
                )
                if confirmation != "FLASH CUSTOM":
                    raise ConfirmationError("custom App update was not confirmed with FLASH CUSTOM")
                audit.event("confirmation", "ok", operation="custom_app")
                self._event("flash_app", "started", "Starting custom App-only OTA; NVS is not erased")
                progress = self._perform_app_transfer(
                    connection,
                    self._custom_release(image, inspection),
                    config,
                    audit,
                )
                app_delivery_completed = True
                core._close_quietly(connection)
                connection = None
                post_version, verification = self._probe_custom_target(
                    inspection.project_version,
                    image,
                    config,
                    audit,
                )
                if not progress.end_acknowledged and verification in {
                    "unavailable",
                    "source_identity_still_reported",
                }:
                    error = core.PostInstallError(
                        "custom App delivery is not provable after the fw end acknowledgement was lost",
                        outcome="app_write_status_unknown",
                        stage="custom_post_probe",
                    )
                    error.no_app_reflash = True
                    raise error
                result = OperationResult(
                    "success",
                    "custom_app",
                    audit.path,
                    "Custom App transfer completed; NVS was preserved; custom behavior is not certified",
                    app_sha256=image.sha256,
                    logical_backup=backup,
                    post_project_version=post_version,
                    post_verification=verification,
                    retry_app_update=False,
                )
                audit.event(
                    "result",
                    "success",
                    end_acknowledged=progress.end_acknowledged,
                    custom_behavior_verified=False,
                    result=result.safe_summary(),
                )
                self._event(
                    "result",
                    "success",
                    result.message,
                    result=result.safe_summary(),
                )
                return result
            except core.FirmwareUpdateError as error:
                error.audit_path = audit.path
                if app_delivery_completed:
                    error.no_app_reflash = True
                retry = not bool(getattr(error, "no_app_reflash", False))
                audit.event(
                    "result",
                    "failed",
                    operation="custom_app",
                    reason=str(error),
                    retry_app_update=retry,
                    backup_path=None if backup is None else str(backup.path),
                )
                self._event(
                    "result",
                    "failed",
                    str(error),
                    retry_app_update=retry,
                    backup_path=None if backup is None else str(backup.path),
                    audit_path=str(audit.path),
                )
                raise
            except Exception:
                error = core.ProtocolError("unexpected local client error during custom App update")
                error.audit_path = audit.path
                error.no_app_reflash = app_delivery_completed
                audit.event(
                    "result",
                    "failed",
                    operation="custom_app",
                    reason=str(error),
                    retry_app_update=not app_delivery_completed,
                )
                self._event("result", "failed", str(error), audit_path=str(audit.path))
                raise error from None
            finally:
                core._close_quietly(connection)
                audit.close()

    def prepare_erase(
        self,
        bundle_id: str,
        *,
        confirmation: str,
    ) -> ErasePreparation:
        with self._exclusive_operation():
            if bundle_id not in self.bundles:
                raise core.PackageValidationError("advanced recovery requires B01 or B02")
            if confirmation != f"PREPARE {bundle_id}":
                raise ConfirmationError(f"advanced recovery preparation requires PREPARE {bundle_id}")
            bundle = self.bundles[bundle_id]
            config = self.settings.update_config()
            audit = core.AuditLogger(config.log_dir)
            connection = None
            session = None
            logical: LogicalBackup | None = None
            source_version: str | None = None
            try:
                audit.event("erase_prepare", "started", bundle_id=bundle_id)
                self._event("backup", "started", "Attempting logical vehicle parameter backup")
                try:
                    connection = self._open_serial(config)
                    inspection = self._inspect_connection(connection, config, audit, phase="pre")
                    source_version = inspection.project_version
                    logical = self._create_logical_backup(
                        connection,
                        inspection,
                        config,
                        audit,
                        release=bundle.as_release_package(),
                    )
                except core.FirmwareUpdateError as logical_error:
                    audit.event(
                        "logical_backup",
                        "unavailable",
                        reason=type(logical_error).__name__,
                    )
                    self._event(
                        "backup",
                        "unavailable",
                        "Logical parameter backup unavailable; raw NVS backup remains mandatory",
                    )
                finally:
                    core._close_quietly(connection)
                    connection = None

                self._event(
                    "rom",
                    "waiting",
                    "Enter ESP32-S3 ROM download mode with BOOT/RESET if automatic reset does not connect",
                )
                session = self.rom_factory.open(self.settings.port, baud=self.settings.baud)
                session.security.validate_supported(expected_flash_size=bundle.flash_size)
                audit.event(
                    "rom_security",
                    "supported",
                    chip=session.security.chip,
                    flash_size=session.security.flash_size,
                    device_identity_sha256=session.security.device_identity_sha256,
                    secure_boot=session.security.secure_boot,
                    secure_download=session.security.secure_download,
                    flash_encryption=session.security.flash_encryption,
                )
                self._event("raw_nvs", "started", "Reading raw NVS partition")
                raw = session.read_flash(bundle.nvs_offset, bundle.nvs_size)
                raw_backup = write_raw_nvs_backup(
                    raw,
                    directory=self.settings.raw_nvs_dir,
                    bundle_id=bundle_id,
                    device_identity_sha256=session.security.device_identity_sha256,
                    source_project_version=source_version,
                    offset=bundle.nvs_offset,
                    size=bundle.nvs_size,
                )
                audit.event(
                    "raw_nvs_backup",
                    "stored",
                    bundle_id=bundle_id,
                    path=str(raw_backup.data.path),
                    sha256=raw_backup.data.sha256,
                    offset=raw_backup.offset,
                    size=raw_backup.size,
                    metadata_path=str(raw_backup.metadata.path),
                )
                preparation = ErasePreparation(
                    preparation_id=uuid.uuid4().hex,
                    bundle_id=bundle_id,
                    created_monotonic=self.monotonic(),
                    raw_nvs_backup=raw_backup,
                    logical_backup=logical,
                    source_project_version=source_version,
                    security=session.security,
                    audit_path=audit.path,
                )
                self._preparations[preparation.preparation_id] = preparation
                audit.event("erase_prepare", "ready", **preparation.safe_summary())
                self._event(
                    "confirm_erase",
                    "ready",
                    "Raw NVS is stored and verified; review paths before destructive confirmation",
                    **preparation.safe_summary(),
                )
                return preparation
            except core.FirmwareUpdateError as error:
                error.audit_path = audit.path
                audit.event("result", "failed", operation="erase_prepare", reason=str(error))
                self._event("result", "failed", str(error), audit_path=str(audit.path))
                raise
            except Exception:
                error = core.ProtocolError("unexpected local client error during erase preparation")
                error.audit_path = audit.path
                audit.event("result", "failed", operation="erase_prepare", reason=str(error))
                self._event("result", "failed", str(error), audit_path=str(audit.path))
                raise error from None
            finally:
                core._close_quietly(connection)
                if session is not None:
                    session.close()
                audit.close()

    def execute_erase(
        self,
        preparation_id: str,
        *,
        acknowledge_non_nvs_loss: bool,
        confirmation: str,
    ) -> OperationResult:
        with self._exclusive_operation():
            preparation = self._preparations.get(preparation_id)
            if preparation is None:
                raise core.PackageValidationError("erase preparation is unknown or already consumed")
            if self.monotonic() - preparation.created_monotonic > 15 * 60:
                del self._preparations[preparation_id]
                raise core.PackageValidationError("erase preparation expired; create a new raw NVS backup")
            expected_confirmation = f"ERASE AND FLASH {preparation.bundle_id}"
            if not acknowledge_non_nvs_loss or confirmation != expected_confirmation:
                raise ConfirmationError(
                    f"advanced recovery requires data-loss acknowledgement and {expected_confirmation}"
                )
            del self._preparations[preparation_id]
            bundle = self.bundles[preparation.bundle_id]
            config = self.settings.update_config()
            audit = core.AuditLogger(config.log_dir)
            session = None
            destructive_started = False
            try:
                audit.event(
                    "erase_execute",
                    "started",
                    preparation_audit=str(preparation.audit_path),
                    bundle_id=bundle.bundle_id,
                    raw_nvs_path=str(preparation.raw_nvs_backup.data.path),
                    raw_nvs_sha256=preparation.raw_nvs_backup.data.sha256,
                )
                raw = read_private_file(
                    preparation.raw_nvs_backup.data.path,
                    expected_size=bundle.nvs_size,
                    expected_sha256=preparation.raw_nvs_backup.data.sha256,
                )
                self._event("rom", "started", "Reconnecting to ESP32-S3 ROM download mode")
                session = self.rom_factory.open(self.settings.port, baud=self.settings.baud)
                session.security.validate_supported(expected_flash_size=bundle.flash_size)
                if (
                    session.security.device_identity_sha256
                    != preparation.security.device_identity_sha256
                ):
                    raise core.DevicePreflightError(
                        "connected ROM device is not the device bound to the raw NVS backup"
                    )
                current_raw = session.read_flash(bundle.nvs_offset, bundle.nvs_size)
                if not hashlib.sha256(current_raw).hexdigest() == preparation.raw_nvs_backup.data.sha256:
                    raise core.DevicePreflightError(
                        "device NVS changed after preparation; create a new raw NVS backup"
                    )
                self._event("erase", "started", "Erasing complete flash; non-NVS data will be lost")
                destructive_started = True
                session.erase_flash()
                audit.event("erase_flash", "completed")
                self._event("recovery_flash", "started", "Writing embedded official recovery image")
                session.write_flash(0, bundle.recovery_bytes, flash_size=bundle.flash_size)
                audit.event(
                    "recovery_image",
                    "written",
                    bundle_id=bundle.bundle_id,
                    sha256=bundle.recovery.sha256,
                    bytes=bundle.recovery.size,
                )
                self._event("nvs_restore", "started", "Restoring raw NVS partition")
                session.write_flash(bundle.nvs_offset, raw, flash_size=bundle.flash_size)
                readback = session.read_flash(bundle.nvs_offset, bundle.nvs_size)
                if readback != raw:
                    raise core.PostInstallError(
                        "raw NVS readback does not match the persisted backup",
                        outcome="physical_recovery_required",
                        stage="raw_nvs_readback",
                    )
                audit.event(
                    "raw_nvs_restore",
                    "verified",
                    path=str(preparation.raw_nvs_backup.data.path),
                    sha256=preparation.raw_nvs_backup.data.sha256,
                    offset=bundle.nvs_offset,
                    size=bundle.nvs_size,
                )
                session.hard_reset()
                session.close()
                session = None
                self._event("reconnect", "started", "Waiting for restored official firmware")
                connection, post = self._wait_for_official_target(bundle, config, audit)
                core._close_quietly(connection)
                result = OperationResult(
                    "success",
                    "erase_restore",
                    audit.path,
                    "Full recovery completed; raw NVS was restored and verified",
                    bundle_id=bundle.bundle_id,
                    app_sha256=bundle.app.sha256,
                    logical_backup=preparation.logical_backup,
                    raw_nvs_backup=preparation.raw_nvs_backup,
                    post_project_version=post.project_version,
                    post_verification="official_identity_and_raw_nvs_readback",
                    retry_app_update=False,
                )
                audit.event("result", "success", result=result.safe_summary())
                self._event(
                    "result",
                    "success",
                    result.message,
                    result=result.safe_summary(),
                )
                return result
            except core.FirmwareUpdateError as error:
                error.audit_path = audit.path
                if destructive_started:
                    error.physical_recovery_required = True
                audit.event(
                    "result",
                    "failed",
                    operation="erase_restore",
                    reason=str(error),
                    destructive_started=destructive_started,
                    physical_recovery_required=destructive_started,
                    raw_nvs_path=str(preparation.raw_nvs_backup.data.path),
                    raw_nvs_sha256=preparation.raw_nvs_backup.data.sha256,
                )
                self._event(
                    "result",
                    "failed",
                    str(error),
                    destructive_started=destructive_started,
                    physical_recovery_required=destructive_started,
                    raw_nvs_path=str(preparation.raw_nvs_backup.data.path),
                    audit_path=str(audit.path),
                )
                raise
            except Exception:
                error = core.ProtocolError("unexpected local client error during physical recovery")
                error.audit_path = audit.path
                error.physical_recovery_required = destructive_started
                audit.event(
                    "result",
                    "failed",
                    operation="erase_restore",
                    reason=str(error),
                    destructive_started=destructive_started,
                    physical_recovery_required=destructive_started,
                    raw_nvs_path=str(preparation.raw_nvs_backup.data.path),
                )
                self._event(
                    "result",
                    "failed",
                    str(error),
                    destructive_started=destructive_started,
                    physical_recovery_required=destructive_started,
                    raw_nvs_path=str(preparation.raw_nvs_backup.data.path),
                    audit_path=str(audit.path),
                )
                raise error from None
            finally:
                if session is not None:
                    session.close()
                audit.close()
