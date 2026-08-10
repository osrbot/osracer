"""ESP32-S3 application image validation used by every client frontend."""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import core


ESP32S3_IMAGE_CHIP_ID = 9
MAX_APP_BYTES = 0x300000
ESP_IMAGE_MAGIC = 0xE9
ESP_CHECKSUM_MAGIC = 0xEF
ESP_APP_DESC_MAGIC = 0xABCD5432
ESP32S3_DROM_START = 0x3C000000
ESP32S3_DROM_END = 0x3E000000
IMAGE_HEADER_BYTES = 24
APP_DESCRIPTION_BYTES = 256


class ImageValidationError(core.PackageValidationError):
    pass


@dataclass(frozen=True)
class ApplicationImage:
    data: bytes
    size: int
    sha256: str
    project_name: str
    version: str
    idf_version: str

    def safe_summary(self) -> dict[str, Any]:
        return {
            "bytes": self.size,
            "sha256": self.sha256,
            "chip": "ESP32-S3",
            "format": "application",
            "validation_hash": "valid",
        }


def _decode_app_string(value: bytes, label: str) -> str:
    raw = value.split(b"\0", 1)[0]
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ImageValidationError(f"application {label} is invalid") from None
    if not decoded or len(decoded) > 64 or any(ord(character) < 0x20 for character in decoded):
        raise ImageValidationError(f"application {label} is invalid")
    return decoded


def _parse_app_description(segment_address: int, segment_data: bytes) -> dict[str, str]:
    if not ESP32S3_DROM_START <= segment_address < ESP32S3_DROM_END:
        raise ImageValidationError(
            "image is not an ESP-IDF application; bootloader and merged images are refused"
        )
    if len(segment_data) < APP_DESCRIPTION_BYTES:
        raise ImageValidationError("application description is truncated")
    try:
        fields = struct.unpack(
            "<II8s32s32s16s16s32s32sHHB3s72s",
            segment_data[:APP_DESCRIPTION_BYTES],
        )
    except struct.error:
        raise ImageValidationError("application description is truncated") from None
    if fields[0] != ESP_APP_DESC_MAGIC:
        raise ImageValidationError(
            "image is not an ESP-IDF application; bootloader and merged images are refused"
        )
    return {
        "version": _decode_app_string(fields[3], "version"),
        "project_name": _decode_app_string(fields[4], "project name"),
        "idf_version": _decode_app_string(fields[7], "ESP-IDF version"),
    }


def validate_application_bytes(data: bytes) -> ApplicationImage:
    if not isinstance(data, bytes) or not 1024 <= len(data) <= MAX_APP_BYTES:
        raise ImageValidationError(
            f"application image size must be between 1 KiB and {MAX_APP_BYTES} bytes"
        )
    try:
        magic, segment_count, _flash_mode, _flash_size_frequency, _entrypoint = struct.unpack_from(
            "<BBBBI", data, 0
        )
        chip_id = struct.unpack_from("<H", data, 12)[0]
        append_digest = data[23]
    except (IndexError, struct.error):
        raise ImageValidationError("file is not a valid ESP32-S3 image") from None
    if magic != ESP_IMAGE_MAGIC or not 1 <= segment_count <= 16:
        raise ImageValidationError("file is not a valid ESP32-S3 image")
    if chip_id != ESP32S3_IMAGE_CHIP_ID:
        raise ImageValidationError("application image target is not ESP32-S3")
    if append_digest != 1:
        raise ImageValidationError("application image is missing its validation hash")

    cursor = IMAGE_HEADER_BYTES
    checksum = ESP_CHECKSUM_MAGIC
    app_info: dict[str, str] | None = None
    for index in range(segment_count):
        if cursor + 8 > len(data):
            raise ImageValidationError("ESP32-S3 image segment header is truncated")
        address, size = struct.unpack_from("<II", data, cursor)
        cursor += 8
        if size == 0 or size % 4 or cursor + size > len(data):
            raise ImageValidationError("ESP32-S3 image segment is invalid or truncated")
        segment_data = data[cursor : cursor + size]
        if index == 0:
            app_info = _parse_app_description(address, segment_data)
        for value in segment_data:
            checksum ^= value
        cursor += size

    checksum_offset = cursor + ((15 - (cursor % 16)) % 16)
    digest_offset = checksum_offset + 1
    image_end = digest_offset + 32
    if image_end != len(data):
        raise ImageValidationError("ESP32-S3 application image length is inconsistent")
    if data[checksum_offset] != checksum:
        raise ImageValidationError("application image checksum is invalid")
    if data[digest_offset:image_end] != hashlib.sha256(data[:digest_offset]).digest():
        raise ImageValidationError("application image validation hash is invalid")
    if app_info is None:
        raise ImageValidationError("application description is missing")

    return ApplicationImage(
        data=data,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        project_name=app_info["project_name"],
        version=app_info["version"],
        idf_version=app_info["idf_version"],
    )


def validate_application_file(path: Path) -> ApplicationImage:
    path = path.expanduser()
    try:
        if path.is_symlink() or not path.is_file() or not path.is_absolute():
            raise OSError
        size = path.stat().st_size
        if not 1024 <= size <= MAX_APP_BYTES:
            raise ImageValidationError(
                f"application image size must be between 1 KiB and {MAX_APP_BYTES} bytes"
            )
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            chunks: list[bytes] = []
            total = 0
            while total < size:
                chunk = os.read(descriptor, min(1024 * 1024, size - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
        finally:
            os.close(descriptor)
    except ImageValidationError:
        raise
    except OSError:
        raise ImageValidationError(
            "custom application must be an absolute, regular, non-symlink file"
        ) from None
    data = b"".join(chunks)
    if len(data) != size:
        raise ImageValidationError("custom application changed or was truncated while reading")
    return validate_application_bytes(data)
