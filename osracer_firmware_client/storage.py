"""Private, durable storage for configuration and raw NVS evidence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import core


@dataclass(frozen=True)
class StoredFile:
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class RawNvsBackup:
    data: StoredFile
    metadata: StoredFile
    device_identity_sha256: str
    bundle_id: str
    offset: int
    size: int


def default_state_directory() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    root = Path(configured).expanduser() if configured else Path.home() / ".local" / "state"
    if not root.is_absolute():
        root = Path.home() / ".local" / "state"
    return root / "osracer" / "firmware-client"


def _safe_directory(directory: Path) -> Path:
    directory = directory.expanduser()
    if not directory.is_absolute():
        raise core.AuditError("client state directory must be absolute and outside the repository")
    try:
        directory = directory.resolve(strict=False)
    except OSError:
        raise core.AuditError("could not resolve the private client state directory") from None
    if directory.is_symlink() or core._inside_repository(directory):
        raise core.AuditError("client state directory must be absolute and outside the repository")
    current = Path(directory.anchor)
    try:
        for part in directory.parts[1:]:
            current /= part
            if current.is_symlink():
                raise OSError("symlink")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink():
            raise OSError("symlink")
        directory = directory.resolve()
        if core._inside_repository(directory) or directory.stat().st_uid != os.getuid():
            raise OSError("unsafe owner or location")
        os.chmod(directory, 0o700)
    except OSError:
        raise core.AuditError("could not prepare the private client state directory") from None
    return directory


def write_private_file(
    directory: Path,
    *,
    prefix: str,
    suffix: str,
    data: bytes,
) -> StoredFile:
    if not re_safe_component(prefix) or not re_safe_suffix(suffix):
        raise core.AuditError("private file name is invalid")
    directory = _safe_directory(directory)
    digest = hashlib.sha256(data).hexdigest()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = directory / f"{prefix}-{timestamp}-{digest[:12]}-{uuid.uuid4().hex[:8]}{suffix}"
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{prefix}-", dir=directory)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, final)
        temporary = None
        os.chmod(final, 0o600)
        directory_descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError:
        raise core.AuditError("could not atomically store a private client file") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    loaded = read_private_file(final, expected_size=len(data), expected_sha256=digest)
    return StoredFile(final, len(loaded), digest)


def re_safe_component(value: str) -> bool:
    return bool(value) and len(value) <= 48 and all(
        character.islower() or character.isdigit() or character == "-"
        for character in value
    )


def re_safe_suffix(value: str) -> bool:
    return value in {".bin", ".json", ".jsonl"}


def read_private_file(
    path: Path,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> bytes:
    path = path.expanduser()
    try:
        stat_result = path.stat(follow_symlinks=False)
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not path.is_file()
            or stat_result.st_uid != os.getuid()
            or stat_result.st_mode & 0o077
            or core._inside_repository(path)
        ):
            raise OSError("unsafe private file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)
        finally:
            os.close(descriptor)
    except OSError:
        raise core.AuditError("private client file failed ownership or permission checks") from None
    digest = hashlib.sha256(data).hexdigest()
    if expected_size is not None and len(data) != expected_size:
        raise core.AuditError("private client file size changed")
    if expected_sha256 is not None and digest != expected_sha256:
        raise core.AuditError("private client file SHA256 changed")
    return data


def write_raw_nvs_backup(
    data: bytes,
    *,
    directory: Path,
    bundle_id: str,
    device_identity_sha256: str,
    source_project_version: str | None,
    offset: int,
    size: int,
) -> RawNvsBackup:
    if len(data) != size or not re_full_sha(device_identity_sha256):
        raise core.AuditError("raw NVS backup input is invalid")
    data_file = write_private_file(
        directory,
        prefix=f"nvs-raw-{device_identity_sha256[:12]}",
        suffix=".bin",
        data=data,
    )
    document: dict[str, Any] = {
        "schema": 1,
        "kind": "osracer_raw_nvs_backup",
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "bundle_id": bundle_id,
        "device_identity_sha256": device_identity_sha256,
        "source_project_version": source_project_version,
        "offset": offset,
        "size": size,
        "data_file": data_file.path.name,
        "data_sha256": data_file.sha256,
    }
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    metadata = write_private_file(
        directory,
        prefix=f"nvs-meta-{device_identity_sha256[:12]}",
        suffix=".json",
        data=raw,
    )
    return RawNvsBackup(
        data=data_file,
        metadata=metadata,
        device_identity_sha256=device_identity_sha256,
        bundle_id=bundle_id,
        offset=offset,
        size=size,
    )


def re_full_sha(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
