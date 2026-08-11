from __future__ import annotations

import hashlib
import http.client
import json
import os
import stat
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path

from osracer_firmware_client import core
from osracer_firmware_client.bundles import (
    BundleValidationError,
    load_bundles,
    match_official_bundle,
    parse_partition_table,
)
from osracer_firmware_client.cli import ConsoleEventRenderer
from osracer_firmware_client.images import ImageValidationError, validate_application_file
from osracer_firmware_client.operations import ClientSettings, FirmwareClient
from osracer_firmware_client.rom import RomSecurityInfo
from osracer_firmware_client.storage import read_private_file, write_private_file
from osracer_firmware_client.web import WebApplication, handler_factory
from http.server import ThreadingHTTPServer


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, duration):
        self.value += max(float(duration), 0.0001)


class FakeSerial:
    def __init__(self, handler, writes):
        self.handler = handler
        self.writes = writes
        self.pending = deque()
        self.is_open = True

    def write(self, data):
        line = data.decode("ascii").rstrip("\n")
        self.writes.append(line)
        response = self.handler(line)
        if isinstance(response, BaseException):
            raise response
        if response is None:
            response = []
        if isinstance(response, str):
            response = [response]
        for line_response in response:
            self.pending.append((line_response + "\n").encode("ascii"))
        return len(data)

    def flush(self):
        return None

    def readline(self):
        return self.pending.popleft() if self.pending else b""

    def reset_input_buffer(self):
        self.pending.clear()

    def reset_output_buffer(self):
        return None

    def close(self):
        self.is_open = False


class SequenceFactory:
    def __init__(self, connections):
        self.connections = deque(connections)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if not self.connections:
            raise OSError("no connection")
        connection = self.connections.popleft()
        if isinstance(connection, BaseException):
            raise connection
        return connection


def legacy_values(command):
    return {
        "sn get": "SN: 001122AABBCC",
        "pid get": "PID: 1.000 2.000 3.000",
        "mc get": "MC: 1 0 0 0 1 0 0 0 1 0 0 0",
        "battery get": "BATTERY: Voltage=12.100V, Cal=User, Scale=1.00000",
        "odom scale get": "ODOM_SCALE: 1.00000 range=0.50000..1.50000",
        "trim get": "TRIM: 0.000deg center_pwm=1500us range=-30.000..30.000deg",
        "speed deadband get": "SPEED_DEADBAND: 20us range=0..1000us",
        "level get": "LEVEL: offset=[0.000 0.000 0.000]g",
    }.get(command)


def ready_items():
    items = [
        core.VehicleConfigItem(name, "UNSET", value_type, "-")
        for name, value_type, _size in core.VEHICLE_CONFIG_FIELDS
    ]
    by_name = {item.name: index for index, item in enumerate(items)}
    for name in core.LEVEL_CALIBRATION_OFFSET_FIELDS:
        items[by_name[name]] = core.VehicleConfigItem(name, "SET", "BLOB", "00000000")
    items[by_name[core.LEVEL_CALIBRATION_INIT_FIELD]] = core.VehicleConfigItem(
        core.LEVEL_CALIBRATION_INIT_FIELD,
        "SET",
        "U8",
        "1",
    )
    return tuple(items)


def config_export_lines(project_version):
    items = ready_items()
    digest = core.calculate_vehicle_config_sha256(project_version, "red", 1, items)
    return [
        "CONFIG_EXPORT_BEGIN: ConfigSchema=1, Proto=1.1, Items=24",
        f"CONFIG_EXPORT_SOURCE: ProjectVer={project_version}, Profile=red, Schema=1",
        f"CONFIG_EXPORT_TARGET: ProjectVer={project_version}, Profile=red, Schema=1",
        f"CONFIG_EXPORT_HASH: BackupSHA={digest}",
        *[
            f"CONFIG_ITEM: Name={item.name}, State={item.state}, "
            f"Type={item.value_type}, Value={item.value}"
            for item in items
        ],
        f"CONFIG_EXPORT_END: Result=OK, Items=24, BackupSHA={digest}, Reason=ok",
    ]


def common_response(command, *, project_version, profile):
    if command in {"v 0.00 0.00", "stream off"}:
        return []
    if command == "fw version":
        return f"FW_VERSION: ProjectVer={project_version}, Proto=1.1"
    if command == "profile get":
        if profile is None:
            return "ERROR unknown_command"
        return (
            f"PROFILE: ID={profile}, Schema=1, State=READY, Motion=Yes, Writes=Yes"
        )
    if command == "fw status":
        return "FW: active=No written=0 size=0 next_seq=0 running=ota_0 next=ota_1"
    if command == "b":
        return "b 12.1"
    if command == "status":
        return [
            "Status: Speed=0.000m/s, Target=0.000m/s, Voltage=12.1V, "
            "Control=Serial, SpeedMode=30%, Static=Yes",
            "IMU: BiasReady=Yes, LevelCal=Yes, GyroBias=0,0,0, LevelOffset=0,0,0",
        ]
    return None


def ota_handler(*, project_version, profile, managed=False, writes=None):
    transferred = {"written": 0}
    writes = writes if writes is not None else []

    def handler(command):
        common = common_response(command, project_version=project_version, profile=profile)
        if common is not None:
            return common
        legacy = legacy_values(command)
        if legacy is not None:
            return legacy
        if command == "config export" and managed:
            return config_export_lines(project_version)
        if command.startswith("fw begin "):
            transferred["written"] = 0
            size = int(command.split()[2])
            transferred["size"] = size
            return f"OK fw begin part=ota_1 size={size}"
        if command.startswith("fw data "):
            _fw, _data, seq_text, payload = command.split()
            transferred["written"] += len(bytes.fromhex(payload))
            return f"OK fw data {int(seq_text)} {transferred['written']}"
        if command == "fw end":
            return "OK fw reboot"
        if command == "fw abort":
            return "OK fw abort"
        return "ERROR unsupported"

    return handler


class FakeRomSession:
    def __init__(self, nvs, *, identity="a" * 64, corrupt_readback=False):
        self.security = RomSecurityInfo(
            "ESP32-S3",
            16 * 1024 * 1024,
            identity,
            False,
            False,
            False,
        )
        self.nvs = nvs
        self.corrupt_readback = corrupt_readback
        self.erased = False
        self.writes = []
        self.reset = False
        self.closed = False

    def read_flash(self, offset, size):
        if self.erased and self.writes and self.corrupt_readback:
            return b"\xff" * size
        return self.nvs

    def erase_flash(self):
        self.erased = True

    def write_flash(self, offset, data, *, flash_size):
        self.writes.append((offset, hashlib.sha256(data).hexdigest(), len(data), flash_size))
        if offset == 0x9000:
            self.nvs = data

    def hard_reset(self):
        self.reset = True

    def close(self):
        self.closed = True


class FakeRomFactory:
    def __init__(self, sessions):
        self.sessions = deque(sessions)

    def open(self, port, *, baud):
        return self.sessions.popleft()


class BundleAndImageTest(unittest.TestCase):
    def test_console_renderer_labels_application_and_backup_digests(self):
        lines = []
        renderer = ConsoleEventRenderer(lines.append)

        renderer(
            {
                "phase": "validate",
                "status": "completed",
                "message": "Custom application validated",
                "details": {"sha256": "a" * 64},
            }
        )
        renderer(
            {
                "phase": "backup",
                "status": "completed",
                "message": "Vehicle parameter backup stored",
                "details": {"sha256": "b" * 64},
            }
        )

        self.assertIn(f"  App SHA256: {'a' * 64}", lines)
        self.assertIn(f"  Backup file SHA256: {'b' * 64}", lines)
        self.assertNotIn(f"  Backup SHA256: {'a' * 64}", lines)

    def test_embedded_bundles_are_exact_and_neutrally_named(self):
        bundles = load_bundles()
        self.assertEqual(tuple(bundles), ("B01", "B02"))
        expected = {
            "B01": (388144, "d331c267583a133f064ce4e9103ffc3167a214e18ddddf8a2df0274672056a07"),
            "B02": (433456, "6b174fdb606c4d6bd0f511369d015bfceb31d7659fca5282d351b1981a0a2632"),
        }
        for bundle_id, bundle in bundles.items():
            self.assertEqual((bundle.app.size, bundle.app.sha256), expected[bundle_id])
            self.assertRegex(bundle.app.path, r"^b\d{2}/app\.bin$")
            self.assertRegex(bundle.recovery.path, r"^b\d{2}/recovery\.bin$")
            self.assertNotRegex(bundle.app.path + bundle.recovery.path, r"(?i)neo|red")
            entries = parse_partition_table(bundle.recovery_bytes)
            nvs = [entry for entry in entries if entry.label == "nvs"]
            self.assertEqual([(entry.offset, entry.size) for entry in nvs], [(0x9000, 0x6000)])

    def test_official_source_matching_never_maps_unknown_or_cross_profile(self):
        bundles = load_bundles()
        self.assertEqual(match_official_bundle("NEORACER_V1.1-test", bundles).bundle_id, "B01")
        self.assertEqual(match_official_bundle("OSRF-C03-T006-s754f0664289e", bundles).bundle_id, "B02")
        self.assertIsNone(match_official_bundle("CUSTOM_BUILD", bundles))

    def test_apps_validate_and_recovery_merged_image_is_rejected_as_custom_app(self):
        b01 = REPO_ROOT / "osracer_firmware_client/resources/b01/app.bin"
        b02 = REPO_ROOT / "osracer_firmware_client/resources/b02/app.bin"
        self.assertEqual(validate_application_file(b01).size, 388144)
        self.assertEqual(validate_application_file(b02).size, 433456)
        with self.assertRaisesRegex(ImageValidationError, "bootloader and merged"):
            validate_application_file(
                REPO_ROOT / "osracer_firmware_client/resources/b01/recovery.bin"
            )

    def test_application_parser_rejects_wrong_chip_checksum_and_digest(self):
        source = REPO_ROOT / "osracer_firmware_client/resources/b01/app.bin"
        original = source.read_bytes()
        mutations = {
            "target is not ESP32-S3": (12, b"\x08\x00"),
            "checksum is invalid": (100, bytes([original[100] ^ 1])),
            "validation hash is invalid": (-1, bytes([original[-1] ^ 1])),
        }
        with tempfile.TemporaryDirectory() as directory:
            for expected, (offset, replacement) in mutations.items():
                with self.subTest(expected=expected):
                    changed = bytearray(original)
                    if offset < 0:
                        offset += len(changed)
                    changed[offset : offset + len(replacement)] = replacement
                    path = Path(directory) / (expected.split()[0] + ".bin")
                    path.write_bytes(changed)
                    with self.assertRaisesRegex(ImageValidationError, expected):
                        validate_application_file(path)

    def test_tampered_resource_fails_before_use(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = REPO_ROOT / "osracer_firmware_client/resources"
            (root / "b01").mkdir()
            (root / "b02").mkdir()
            (root / "bundles.json").write_bytes((source / "bundles.json").read_bytes())
            for bundle in ("b01", "b02"):
                for name in ("app.bin", "recovery.bin"):
                    (root / bundle / name).write_bytes((source / bundle / name).read_bytes())
            data = bytearray((root / "b01/app.bin").read_bytes())
            data[100] ^= 1
            (root / "b01/app.bin").write_bytes(data)
            with self.assertRaisesRegex(BundleValidationError, "SHA256"):
                load_bundles(root)

    def test_manifest_nvs_layout_must_match_recovery_partition_table(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = REPO_ROOT / "osracer_firmware_client/resources"
            for bundle in ("b01", "b02"):
                (root / bundle).mkdir(parents=True)
                for name in ("app.bin", "recovery.bin"):
                    (root / bundle / name).write_bytes((source / bundle / name).read_bytes())
            manifest = json.loads((source / "bundles.json").read_text())
            manifest["bundles"]["B01"]["nvs"]["offset"] = 0xA000
            (root / "bundles.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(BundleValidationError, "NVS layout"):
                load_bundles(root)


class PrivateStorageTest(unittest.TestCase):
    def test_private_atomic_file_is_0600_and_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            stored = write_private_file(
                Path(directory) / "state",
                prefix="test-data",
                suffix=".bin",
                data=b"private",
            )
            self.assertEqual(stat.S_IMODE(stored.path.stat().st_mode), 0o600)
            self.assertEqual(read_private_file(stored.path), b"private")
            stored.path.write_bytes(b"changed")
            os.chmod(stored.path, 0o600)
            with self.assertRaisesRegex(core.AuditError, "SHA256"):
                read_private_file(stored.path, expected_sha256=stored.sha256)


class LocalWebInterfaceTest(unittest.TestCase):
    def test_static_ui_has_one_persisted_chinese_english_interface(self):
        html = (REPO_ROOT / "osracer_firmware_client/static/index.html").read_text()
        script = (REPO_ROOT / "osracer_firmware_client/static/app.js").read_text()

        self.assertIn('id="language-select"', html)
        self.assertIn('<option value="zh-CN">中文</option>', html)
        self.assertIn('<option value="en">English</option>', html)
        self.assertIn('data-i18n="runOfficial"', html)
        self.assertIn('data-i18n-placeholder="typePrepare"', html)
        self.assertIn('"osracer-firmware-client-language"', script)
        self.assertIn("navigator.languages", script)
        self.assertIn("window.localStorage.setItem", script)
        self.assertIn('"zh-CN": {', script)
        self.assertIn('clientTitle: "固件更新客户端"', script)
        self.assertIn('clientTitle: "Firmware Client"', script)
        self.assertIn('typeUpdate: "输入 UPDATE"', script)
        self.assertIn('typeUpdate: "Type UPDATE"', script)
        self.assertIn("诊断信息保留英文", script)

    def test_static_ui_is_local_and_api_requires_random_header_token(self):
        with tempfile.TemporaryDirectory() as directory:
            application = WebApplication(
                ClientSettings(state_dir=Path(directory) / "state")
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(application))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/")
                response = connection.getresponse()
                html = response.read().decode()
                self.assertEqual(response.status, 200)
                self.assertNotIn("https://", html)
                self.assertNotRegex(html, r"(?i)\bneo\b|\bred\b")

                connection.request("GET", "/api/state")
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 403)

                connection.request(
                    "GET",
                    "/api/state",
                    headers={"X-Session-Token": application.token},
                )
                response = connection.getresponse()
                state = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertFalse(state["busy"])
                self.assertEqual(state["operation"], "idle")

                body = json.dumps({"confirmation": "UPDATE", "reinstall": False})
                connection.request(
                    "POST",
                    "/api/official",
                    body=body,
                    headers={
                        "X-Session-Token": application.token,
                        "Content-Type": "application/json",
                        "Content-Length": str(len(body)),
                        "Origin": "https://attacker.invalid",
                    },
                )
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class ClientOperationTest(unittest.TestCase):
    def settings(self, directory):
        return ClientSettings(
            state_dir=Path(directory) / "state",
            chunk_size=384,
            response_timeout=0.02,
            reconnect_timeout=0.2,
        )

    def test_official_b01_update_creates_legacy_backup_and_verifies(self):
        with tempfile.TemporaryDirectory() as directory:
            writes = []
            pre = FakeSerial(
                ota_handler(project_version="NEORACER_V1.1-test", profile=None),
                writes,
            )
            post = FakeSerial(
                ota_handler(
                    project_version="NEORACER_V1.2-20260714-gab06ac4",
                    profile=None,
                ),
                writes,
            )
            clock = FakeClock()
            events = []
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([pre, post]),
                event_sink=events.append,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            result = client.official_update(confirmation="UPDATE")
            self.assertEqual(result.status, "success")
            self.assertEqual(result.bundle_id, "B01")
            self.assertEqual(result.logical_backup.kind, "legacy")
            self.assertEqual(stat.S_IMODE(result.logical_backup.path.stat().st_mode), 0o600)
            self.assertIn("fw end", writes)
            self.assertFalse(any("configuration" in json.dumps(event) for event in events))

    def test_audit_never_contains_vehicle_parameter_values(self):
        with tempfile.TemporaryDirectory() as directory:
            writes = []
            sentinel = "123.456"
            base_pre = ota_handler(project_version="NEORACER_V1.1-test", profile=None)
            base_post = ota_handler(
                project_version="NEORACER_V1.2-20260714-gab06ac4",
                profile=None,
            )

            def with_sentinel(base):
                def handler(command):
                    if command == "pid get":
                        return f"PID: {sentinel} 2.000 3.000"
                    return base(command)

                return handler

            clock = FakeClock()
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory(
                    [
                        FakeSerial(with_sentinel(base_pre), writes),
                        FakeSerial(with_sentinel(base_post), writes),
                    ]
                ),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            result = client.official_update(confirmation="UPDATE")
            self.assertNotIn(sentinel, result.audit_path.read_text())
            self.assertIn(sentinel, result.logical_backup.path.read_text())

    def test_already_installed_official_package_skips_without_backup_or_app_write(self):
        with tempfile.TemporaryDirectory() as directory:
            writes = []
            source = FakeSerial(
                ota_handler(
                    project_version="NEORACER_V1.2-20260714-gab06ac4",
                    profile=None,
                ),
                writes,
            )
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([source]),
            )
            result = client.official_update(confirmation="UPDATE")
            self.assertEqual(result.status, "skipped")
            self.assertIsNone(result.logical_backup)
            self.assertFalse(any(command.startswith("fw ") and command != "fw version" and command != "fw status" for command in writes))

    def test_official_b02_reinstall_uses_managed_backup_and_compare(self):
        with tempfile.TemporaryDirectory() as directory:
            writes = []
            version = "OSRF-C03-T006-s754f0664289e"
            pre = FakeSerial(ota_handler(project_version=version, profile="red", managed=True), writes)
            post = FakeSerial(ota_handler(project_version=version, profile="red", managed=True), writes)
            clock = FakeClock()
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([pre, post]),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            result = client.official_update(confirmation="UPDATE", reinstall=True)
            self.assertEqual(result.status, "success")
            self.assertEqual(result.bundle_id, "B02")
            self.assertEqual(result.logical_backup.kind, "managed")
            self.assertIn("20 non-level items match", result.post_verification)
            self.assertGreaterEqual(writes.count("config export"), 2)

    def test_official_update_waits_for_post_reboot_battery_telemetry(self):
        with tempfile.TemporaryDirectory() as directory:
            writes = []
            version = "OSRF-C03-T006-s754f0664289e"
            pre = FakeSerial(
                ota_handler(project_version=version, profile="red", managed=True),
                writes,
            )
            battery_lines = deque(["b 0.00", "b invalid", "b 11.44"])
            base_post = ota_handler(project_version=version, profile="red", managed=True)

            def post_handler(command):
                if command == "b":
                    return battery_lines.popleft()
                return base_post(command)

            post = FakeSerial(post_handler, writes)
            clock = FakeClock()
            settings = ClientSettings(
                state_dir=Path(directory) / "state",
                chunk_size=384,
                response_timeout=0.02,
                reconnect_timeout=1.0,
            )
            client = FirmwareClient(
                settings=settings,
                serial_factory=SequenceFactory([pre, post]),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )

            result = client.official_update(confirmation="UPDATE", reinstall=True)

            self.assertEqual(result.status, "success")
            audit = result.audit_path.read_text()
            self.assertIn('"step":"post_battery"', audit)
            self.assertIn('"status":"waiting"', audit)
            self.assertIn('"voltage":11.44', audit)

    def test_app_progress_events_are_limited_to_displayed_percent_changes(self):
        events = []
        client = FirmwareClient(event_sink=events.append)
        total = 433_456

        client._last_progress_percent = None
        for written in range(128, total, 128):
            client._progress_callback(written, total)
        client._progress_callback(total, total)

        progress_events = [event for event in events if event["status"] == "progress"]
        self.assertLessEqual(len(progress_events), 101)
        self.assertEqual(progress_events[-1]["progress"], 1.0)

    def test_custom_app_allows_cross_product_target_but_keeps_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            writes = []
            pre = FakeSerial(
                ota_handler(project_version="NEORACER_V1.2-20260714-gab06ac4", profile=None),
                writes,
            )
            post = FakeSerial(
                ota_handler(project_version="OSRF-C03-T006-s754f0664289e", profile="red"),
                writes,
            )
            clock = FakeClock()
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([pre, post]),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            result = client.custom_app_update(
                REPO_ROOT / "osracer_firmware_client/resources/b02/app.bin",
                confirmation="FLASH CUSTOM",
            )
            self.assertEqual(result.status, "success")
            self.assertEqual(result.logical_backup.kind, "legacy")
            self.assertEqual(result.post_verification, "custom_identity_matched")
            self.assertIn("fw end", writes)

    def test_custom_confirmation_refusal_never_starts_app_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            writes = []
            source = FakeSerial(
                ota_handler(
                    project_version="NEORACER_V1.2-20260714-gab06ac4",
                    profile=None,
                ),
                writes,
            )
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([source]),
            )
            with self.assertRaises(core.UserCancelledError):
                client.custom_app_update(
                    REPO_ROOT / "osracer_firmware_client/resources/b02/app.bin",
                    confirmation="NO",
                )
            self.assertFalse(any(command.startswith("fw begin ") for command in writes))

    def test_advanced_erase_has_no_erase_before_second_confirmation_and_restores_nvs(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = bytes(range(256)) * 96
            prepare_session = FakeRomSession(raw)
            execute_session = FakeRomSession(raw)
            post_writes = []
            post = FakeSerial(
                ota_handler(
                    project_version="NEORACER_V1.2-20260714-gab06ac4",
                    profile=None,
                ),
                post_writes,
            )
            clock = FakeClock()
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([OSError("custom source"), post]),
                rom_factory=FakeRomFactory([prepare_session, execute_session]),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            preparation = client.prepare_erase("B01", confirmation="PREPARE B01")
            self.assertFalse(prepare_session.erased)
            self.assertTrue(preparation.raw_nvs_backup.data.path.is_file())
            self.assertEqual(stat.S_IMODE(preparation.raw_nvs_backup.data.path.stat().st_mode), 0o600)
            with self.assertRaises(core.UserCancelledError):
                client.execute_erase(
                    preparation.preparation_id,
                    acknowledge_non_nvs_loss=False,
                    confirmation="ERASE AND FLASH B01",
                )
            self.assertFalse(execute_session.erased)
            result = client.execute_erase(
                preparation.preparation_id,
                acknowledge_non_nvs_loss=True,
                confirmation="ERASE AND FLASH B01",
            )
            self.assertTrue(execute_session.erased)
            self.assertTrue(execute_session.reset)
            self.assertEqual([entry[0] for entry in execute_session.writes], [0, 0x9000])
            self.assertEqual(result.status, "success")
            self.assertEqual(result.raw_nvs_backup.data.sha256, hashlib.sha256(raw).hexdigest())

    def test_advanced_erase_rejects_tampered_raw_backup_before_reconnecting(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = b"\x31" * 0x6000
            prepare_session = FakeRomSession(raw)
            execute_factory = FakeRomFactory([prepare_session])
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([OSError("custom source")]),
                rom_factory=execute_factory,
            )
            preparation = client.prepare_erase("B01", confirmation="PREPARE B01")
            preparation.raw_nvs_backup.data.path.write_bytes(b"\x00" * 0x6000)
            with self.assertRaisesRegex(core.AuditError, "SHA256"):
                client.execute_erase(
                    preparation.preparation_id,
                    acknowledge_non_nvs_loss=True,
                    confirmation="ERASE AND FLASH B01",
                )
            self.assertFalse(prepare_session.erased)

    def test_official_profile_mismatch_refuses_before_any_app_write(self):
        with tempfile.TemporaryDirectory() as directory:
            writes = []
            source = FakeSerial(
                ota_handler(
                    project_version="OSRF-C03-T006-s754f0664289e",
                    profile="neo",
                    managed=False,
                ),
                writes,
            )
            clock = FakeClock()
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([source]),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            with self.assertRaisesRegex(core.DevicePreflightError, "does not map"):
                client.official_update(confirmation="UPDATE", reinstall=True)
            self.assertFalse(any(command.startswith("fw begin ") for command in writes))

    def test_explicit_begin_rejection_is_safe_to_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            writes = []
            base = ota_handler(project_version="NEORACER_V1.1-test", profile=None)

            def handler(command):
                if command.startswith("fw begin "):
                    return "ERROR fw begin low_voltage"
                return base(command)

            source = FakeSerial(handler, writes)
            clock = FakeClock()
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([source]),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            with self.assertRaises(core.DeviceRejectedError) as caught:
                client.official_update(confirmation="UPDATE")
            self.assertFalse(getattr(caught.exception, "no_app_reflash", False))
            self.assertFalse(any(command.startswith("fw data ") for command in writes))

    def test_data_delivery_unknown_remains_do_not_reflash_even_after_abort_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            writes = []
            base = ota_handler(project_version="NEORACER_V1.1-test", profile=None)
            update_started = {"active": False}

            def handler(command):
                if command.startswith("fw begin "):
                    update_started["active"] = True
                    return base(command)
                if command.startswith("fw data "):
                    return []
                if command == "fw status" and update_started["active"]:
                    return "ERROR fw status unavailable"
                return base(command)

            source = FakeSerial(handler, writes)
            clock = FakeClock()
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([source]),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            with self.assertRaises(core.FirmwareUpdateError) as caught:
                client.official_update(confirmation="UPDATE")
            self.assertTrue(getattr(caught.exception, "no_app_reflash", False))
            self.assertIn("fw abort", writes)

    def test_post_update_parameter_mismatch_does_not_suggest_reflash(self):
        with tempfile.TemporaryDirectory() as directory:
            writes = []
            pre = FakeSerial(
                ota_handler(project_version="NEORACER_V1.1-test", profile=None),
                writes,
            )
            post_base = ota_handler(
                project_version="NEORACER_V1.2-20260714-gab06ac4",
                profile=None,
            )

            def post_handler(command):
                if command == "pid get":
                    return "PID: 9.000 2.000 3.000"
                return post_base(command)

            post = FakeSerial(post_handler, writes)
            clock = FakeClock()
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([pre, post]),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            with self.assertRaises(core.PostInstallError) as caught:
                client.official_update(confirmation="UPDATE")
            self.assertTrue(getattr(caught.exception, "no_app_reflash", False))

    def test_advanced_security_refusal_happens_before_raw_read_or_erase(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = b"\x55" * 0x6000
            session = FakeRomSession(raw)
            session.security = RomSecurityInfo(
                "ESP32-S3",
                16 * 1024 * 1024,
                "a" * 64,
                True,
                False,
                False,
            )
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([OSError("no app protocol")]),
                rom_factory=FakeRomFactory([session]),
            )
            with self.assertRaisesRegex(core.FirmwareUpdateError, "security configuration"):
                client.prepare_erase("B01", confirmation="PREPARE B01")
            self.assertFalse(session.erased)
            self.assertEqual(session.writes, [])

    def test_advanced_reconnect_to_different_device_refuses_before_erase(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = b"\x42" * 0x6000
            prepare_session = FakeRomSession(raw, identity="a" * 64)
            execute_session = FakeRomSession(raw, identity="b" * 64)
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([OSError("no app protocol")]),
                rom_factory=FakeRomFactory([prepare_session, execute_session]),
            )
            preparation = client.prepare_erase("B01", confirmation="PREPARE B01")
            with self.assertRaisesRegex(core.DevicePreflightError, "not the device"):
                client.execute_erase(
                    preparation.preparation_id,
                    acknowledge_non_nvs_loss=True,
                    confirmation="ERASE AND FLASH B01",
                )
            self.assertFalse(execute_session.erased)
            self.assertTrue(preparation.raw_nvs_backup.data.path.is_file())

    def test_raw_nvs_readback_failure_keeps_backup_and_marks_physical_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = b"\xa5" * 0x6000
            prepare_session = FakeRomSession(raw)
            execute_session = FakeRomSession(raw, corrupt_readback=True)
            client = FirmwareClient(
                settings=self.settings(directory),
                serial_factory=SequenceFactory([OSError("no app protocol")]),
                rom_factory=FakeRomFactory([prepare_session, execute_session]),
            )
            preparation = client.prepare_erase("B01", confirmation="PREPARE B01")
            with self.assertRaises(core.PostInstallError) as caught:
                client.execute_erase(
                    preparation.preparation_id,
                    acknowledge_non_nvs_loss=True,
                    confirmation="ERASE AND FLASH B01",
                )
            self.assertTrue(execute_session.erased)
            self.assertTrue(getattr(caught.exception, "physical_recovery_required", False))
            self.assertTrue(preparation.raw_nvs_backup.data.path.is_file())


if __name__ == "__main__":
    unittest.main()
