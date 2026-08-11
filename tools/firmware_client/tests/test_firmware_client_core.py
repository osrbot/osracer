#!/usr/bin/env python3
"""Focused regression tests for the supported firmware-client engine."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import tempfile
import unittest
from pathlib import Path

from osracer_firmware_client import core


SOURCE_VERSION = "OSRF-C03-T006-source"
TARGET_VERSION = "OSRF-C03-T006-target"


def ready_items() -> tuple[core.VehicleConfigItem, ...]:
    values = {
        "U8": "1",
        "U32": "2",
        "I32": "0",
    }
    items = []
    for name, value_type, size in core.VEHICLE_CONFIG_FIELDS:
        value = "00000000" if value_type == "BLOB" and size == 4 else "00" * size
        if value_type != "BLOB":
            value = values[value_type]
        items.append(core.VehicleConfigItem(name, "SET", value_type, value))
    return tuple(items)


def independent_config_hash(items: tuple[core.VehicleConfigItem, ...]) -> str:
    digest = hashlib.sha256()

    def add_string(value: str) -> None:
        digest.update(value.encode("utf-8") + b"\x00")

    def add_u32(value: int) -> None:
        digest.update(value.to_bytes(4, "big"))

    add_string("OSRVCFG1")
    add_string(SOURCE_VERSION)
    add_string("red")
    add_u32(1)
    add_u32(24)
    state_ids = {"SET": 1, "UNSET": 2, "ERROR": 3}
    type_ids = {"U8": 0, "U32": 1, "I32": 2, "BLOB": 3}
    for item, (_, _, size) in zip(items, core.VEHICLE_CONFIG_FIELDS):
        if item.value_type == "BLOB":
            value = bytes.fromhex(item.value)
        elif item.value_type == "U8":
            value = int(item.value).to_bytes(1, "big")
        elif item.value_type == "U32":
            value = int(item.value).to_bytes(4, "big")
        else:
            value = int(item.value).to_bytes(4, "big", signed=True)
        assert len(value) == size
        add_string(item.name)
        digest.update(bytes((state_ids[item.state], type_ids[item.value_type])))
        add_u32(len(value))
        digest.update(value)
    return digest.hexdigest()


def export_lines(items: tuple[core.VehicleConfigItem, ...] | None = None) -> list[str]:
    items = items or ready_items()
    backup_sha = independent_config_hash(items)
    return [
        "CONFIG_EXPORT_BEGIN: ConfigSchema=1, Proto=1.1, Items=24",
        f"CONFIG_EXPORT_SOURCE: ProjectVer={SOURCE_VERSION}, Profile=red, Schema=1",
        f"CONFIG_EXPORT_TARGET: ProjectVer={TARGET_VERSION}, Profile=red, Schema=1",
        f"CONFIG_EXPORT_HASH: BackupSHA={backup_sha}",
        *(
            f"CONFIG_ITEM: Name={item.name}, State={item.state}, "
            f"Type={item.value_type}, Value={item.value.upper()}"
            for item in items
        ),
        f"CONFIG_EXPORT_END: Result=OK, Items=24, BackupSHA={backup_sha}, Reason=ok",
    ]


class FakeSerial:
    is_open = True

    def __init__(self, *, written: int | None = None, lines: tuple[bytes, ...] = ()):
        self.written = written
        self.lines = list(lines)
        self.payloads: list[bytes] = []

    def write(self, payload: bytes) -> int | None:
        self.payloads.append(payload)
        return len(payload) if self.written is None else self.written

    def flush(self) -> None:
        return None

    def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""

    def reset_input_buffer(self) -> None:
        return None

    def reset_output_buffer(self) -> None:
        return None

    def close(self) -> None:
        self.is_open = False


class CurrentEngineContractTest(unittest.TestCase):
    def test_export_parser_matches_independent_hash_and_accepts_uppercase_blob(self):
        items = ready_items()
        exported = core.parse_vehicle_config_export_lines(export_lines(items))

        self.assertEqual(len(exported.items), 24)
        self.assertEqual(exported.backup_sha256, independent_config_hash(items))
        self.assertEqual(
            core.calculate_vehicle_config_sha256(SOURCE_VERSION, "red", 1, items),
            independent_config_hash(items),
        )

    def test_export_parser_rejects_wrong_order_duplicate_and_hash(self):
        valid = export_lines()
        cases = []
        wrong_order = list(valid)
        wrong_order[4], wrong_order[5] = wrong_order[5], wrong_order[4]
        cases.append(wrong_order)
        duplicate = list(valid)
        duplicate.insert(5, duplicate[4])
        cases.append(duplicate)
        bad_hash = list(valid)
        bad_hash[3] = f"CONFIG_EXPORT_HASH: BackupSHA={'0' * 64}"
        cases.append(bad_hash)

        for lines in cases:
            with self.subTest(lines=len(lines)):
                with self.assertRaises(core.ProtocolError):
                    core.parse_vehicle_config_export_lines(lines)

    def test_semantic_comparison_allows_only_healthy_level_refresh(self):
        original = ready_items()
        refreshed = list(original)
        for index, value in zip((12, 13, 14), (0.1, -0.1, 0.2)):
            item = refreshed[index]
            refreshed[index] = core.VehicleConfigItem(
                item.name,
                "SET",
                "BLOB",
                struct.pack("<f", value).hex().upper(),
            )
        comparison = core.compare_vehicle_config_semantics(original, refreshed)
        self.assertTrue(comparison.matches)
        self.assertEqual(comparison.level_offset_status, "refreshed")

        changed = list(refreshed)
        changed[3] = core.VehicleConfigItem("pid_params.init", "SET", "U8", "0")
        comparison = core.compare_vehicle_config_semantics(original, changed)
        self.assertFalse(comparison.matches)
        self.assertEqual(comparison.non_level_mismatches, ("pid_params.init",))

        invalid = list(original)
        invalid[12] = core.VehicleConfigItem(
            "level_cal.ox", "SET", "BLOB", struct.pack("<f", 3.0).hex()
        )
        comparison = core.compare_vehicle_config_semantics(original, invalid)
        self.assertFalse(comparison.matches)
        self.assertEqual(comparison.invalid_level_fields, ("level_cal.ox",))

    def test_private_backup_is_read_back_and_tampering_is_rejected(self):
        exported = core.parse_vehicle_config_export_lines(export_lines())
        with tempfile.TemporaryDirectory() as directory:
            path, file_sha = core._write_vehicle_config_backup(
                exported,
                None,
                Path(directory) / "backups",
            )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), file_sha)
            loaded, loaded_sha = core._load_vehicle_config_backup(path)
            self.assertEqual(loaded, exported)
            self.assertEqual(loaded_sha, file_sha)

            document = json.loads(path.read_text(encoding="utf-8"))
            document["items"][0]["value"] = "FFFFFFFF"
            path.write_text(json.dumps(document), encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaises(core.AuditError):
                core._load_vehicle_config_backup(path)

    def test_serial_transport_fails_closed_on_partial_write_and_device_error(self):
        with self.assertRaises(core.SerialCommunicationError):
            core.SerialTransport(FakeSerial(written=1)).send_line("fw status")

        transport = core.SerialTransport(FakeSerial(lines=(b"ERROR low_voltage\n",)))
        with self.assertRaises(core.DeviceRejectedError) as raised:
            transport.wait_for(
                label="fw begin",
                prefixes=("OK fw",),
                parser=core.parse_begin_ack,
                timeout=0.1,
            )
        self.assertEqual(raised.exception.stage, "fw begin")

    def test_retired_updater_entry_points_are_not_present(self):
        for name in (
            "main",
            "build_parser",
            "run_app_ota",
            "run_managed_update",
            "run_config_restore",
            "acquire_catalog_release",
            "calculate_vehicle_restore_sha256",
        ):
            self.assertFalse(hasattr(core, name), name)


if __name__ == "__main__":
    unittest.main()
