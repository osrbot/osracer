"""Narrow, testable esptool adapter for the advanced recovery workflow."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from . import core


class RomOperationError(core.FirmwareUpdateError):
    exit_code = 5


@dataclass(frozen=True)
class RomSecurityInfo:
    chip: str
    flash_size: int
    device_identity_sha256: str
    secure_boot: bool
    secure_download: bool
    flash_encryption: bool

    def validate_supported(self, *, expected_flash_size: int) -> None:
        if self.chip != "ESP32-S3":
            raise RomOperationError("ROM target is not ESP32-S3")
        if self.flash_size != expected_flash_size:
            raise RomOperationError("device flash size does not match the recovery bundle")
        if self.secure_boot or self.secure_download or self.flash_encryption:
            raise RomOperationError(
                "device security configuration is not supported by advanced recovery"
            )


class RomSession(Protocol):
    security: RomSecurityInfo

    def read_flash(self, offset: int, size: int) -> bytes: ...

    def erase_flash(self) -> None: ...

    def write_flash(self, offset: int, data: bytes, *, flash_size: int) -> None: ...

    def hard_reset(self) -> None: ...

    def close(self) -> None: ...


class RomFactory(Protocol):
    def open(self, port: str, *, baud: int) -> RomSession: ...


class EsptoolRomSession:
    def __init__(self, rom: Any, stub: Any, security: RomSecurityInfo, commands: Any):
        self._rom = rom
        self._stub = stub
        self._commands = commands
        self.security = security
        self._closed = False

    def _call(self, label: str, function: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except Exception:
            raise RomOperationError(f"ROM operation failed during {label}") from None

    def read_flash(self, offset: int, size: int) -> bytes:
        data = self._call(
            "flash read",
            self._commands.read_flash,
            self._stub,
            offset,
            size,
            output=None,
            flash_size="keep",
            no_progress=True,
        )
        if not isinstance(data, bytes) or len(data) != size:
            raise RomOperationError("ROM flash read returned an incomplete result")
        return data

    def erase_flash(self) -> None:
        self._call("full erase", self._commands.erase_flash, self._stub, force=False)

    def write_flash(self, offset: int, data: bytes, *, flash_size: int) -> None:
        if not data or offset < 0 or offset + len(data) > flash_size:
            raise RomOperationError("ROM flash write range is invalid")
        flash_size_name = f"{flash_size // (1024 * 1024)}MB"
        self._call(
            "flash write",
            self._commands.write_flash,
            self._stub,
            [(offset, data)],
            flash_freq="keep",
            flash_mode="keep",
            flash_size=flash_size_name,
            compress=True,
            no_progress=True,
            force=False,
            encrypt=False,
        )

    def hard_reset(self) -> None:
        self._call("hard reset", self._stub.hard_reset)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for candidate in (self._stub, self._rom):
            port = getattr(candidate, "_port", None)
            if port is None:
                continue
            try:
                port.close()
            except Exception:
                pass


class EsptoolRomFactory:
    """Open one ESP32-S3 ROM session and retain it through erase and restore."""

    def open(self, port: str, *, baud: int) -> EsptoolRomSession:
        try:
            import esptool
            from esptool import cmds
            from esptool.util import flash_size_bytes
        except ImportError:
            raise RomOperationError("esptool is required for advanced recovery") from None

        rom = None
        try:
            rom = esptool.get_default_connected_device(
                [port],
                port,
                connect_attempts=7,
                initial_baud=baud,
                chip="esp32s3",
                trace=False,
                before="default-reset",
            )
            if rom is None:
                raise RuntimeError("no ROM device")
            raw_security = rom.get_security_info(cache=False)
            flags = raw_security.get("parsed_flags", {})
            flash_encryption = bool(rom.get_flash_encryption_enabled())
            detected_size = cmds.detect_flash_size(rom)
            if not isinstance(detected_size, str):
                raise RuntimeError("flash size unavailable")
            flash_size = flash_size_bytes(detected_size)
            mac = rom.read_mac()
            if not isinstance(mac, tuple) or len(mac) != 6:
                raise RuntimeError("device identity unavailable")
            identity = hashlib.sha256(bytes(mac)).hexdigest()
            description = str(rom.get_chip_description())
            chip = "ESP32-S3" if "ESP32-S3" in description.upper() else description
            security = RomSecurityInfo(
                chip=chip,
                flash_size=flash_size,
                device_identity_sha256=identity,
                secure_boot=bool(flags.get("SECURE_BOOT_EN")),
                secure_download=bool(flags.get("SECURE_DOWNLOAD_ENABLE")),
                flash_encryption=flash_encryption,
            )
            stub = rom.run_stub()
            return EsptoolRomSession(rom, stub, security, cmds)
        except RomOperationError:
            raise
        except Exception:
            if rom is not None:
                try:
                    rom._port.close()
                except Exception:
                    pass
            raise RomOperationError(
                "could not enter ESP32-S3 ROM download mode; use BOOT/RESET and retry"
            ) from None
