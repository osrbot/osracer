import ast
import contextlib
import copy
import importlib.util
import io
import json
import stat
import sys
import tempfile
import unittest
import urllib.request
import urllib.response
import warnings
import zipfile
from collections import deque
from dataclasses import replace
from email.message import Message
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "osracer_firmware_client" / "core.py"
SPEC = importlib.util.spec_from_file_location("osracer_firmware_client_core", TOOL_PATH)
ota = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ota
SPEC.loader.exec_module(ota)


DEFAULT_APP = b"synthetic-public-test-app\x00\x01\x02"
CATALOG_URL = "https://example.test/firmware/catalog.json"
PACKAGE_URL = "https://example.test/firmware/packages/candidate.zip"
T003_PROJECT_VERSION = "OSRF-C03-T003-g9ebacb3"
T004_PROJECT_VERSION = "OSRF-C03-T004-se2f117ee56df"
T004_SOURCE_TREE_SHA256 = "e2f117ee56df5a4a5b6edcc850bbd2e929ce1ce1cb736b5e240a9fcb11b54440"
T004_PACKAGE_SHA256 = "65744924e0a73b35048e41af67029d352de2b8c31e2401e7adb2a166f0ef0a99"
T004_APP_SHA256 = "22425cd5b9b61d75786ec95b866734b16f4ceb6ef711ff91dcff6572cf66cf43"
T005_PROJECT_VERSION = "OSRF-C03-T005-s1c7ef7e8766a"
T005_SOURCE_TREE_SHA256 = "1c7ef7e8766ae73f67d6dfe99d9efdf12f9b837976254f7c06daee3af413fc9e"
T005_PACKAGE_SHA256 = "a6caf4a70349484ebbf108836cecdfe82ea26ae62711982ea1dd8808b383713f"
T005_APP_SHA256 = "9894dc245e1f5b287559f82d14397b919f4e78679536792c3ad6db682d4bd52f"


def base_manifest(app=DEFAULT_APP, app_member="images/osrcore.bin"):
    return {
        "schema": 1,
        "profile": {
            "id": "TEST_PROFILE",
            "hardware": "OSRACER-TEST",
            "nvs_schema": 7,
            "project_version": "S1-TARGET",
            "protocol": "1.1",
        },
        "flash": {"package_app": app_member},
        "sha256": {"app": ota._sha256(app)},
    }


def write_zip(
    path,
    *,
    manifest=None,
    app=DEFAULT_APP,
    app_member="images/osrcore.bin",
    members=None,
    compression=zipfile.ZIP_DEFLATED,
):
    if manifest is None:
        manifest = base_manifest(app, app_member)
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        if members is None:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr(app_member, app)
        else:
            for name, content in members:
                archive.writestr(name, content)
    return path


def candidate_entry(
    package_data,
    *,
    manifest=None,
    channel="test",
    candidate_id="c03-t001",
    asset="packages/candidate.zip",
    **overrides,
):
    manifest = manifest or base_manifest()
    entry = {
        "id": candidate_id,
        "channel": channel,
        "asset": asset,
        "sha256": ota._sha256(package_data),
        "size": len(package_data),
        "profile": copy.deepcopy(manifest["profile"]),
        "release_ready": False,
        "source_dirty": True,
        "signature": "none",
    }
    entry.update(overrides)
    return entry


def catalog_bytes(*, stable=None, test=None, schema=1, channels=None):
    document = {
        "schema": schema,
        "channels": channels
        if channels is not None
        else {
            "stable": stable or [],
            "test": test or [],
        },
    }
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def unset_vehicle_config_items():
    return tuple(
        ota.VehicleConfigItem(name, "UNSET", value_type, "-")
        for name, value_type, _size in ota.VEHICLE_CONFIG_FIELDS
    )


def ready_vehicle_config_items(items=None, *, offsets=None):
    result = list(items or unset_vehicle_config_items())
    by_name = {item.name: index for index, item in enumerate(result)}
    offset_values = offsets or ("00000000", "00000000", "00000000")
    for name, value in zip(ota.LEVEL_CALIBRATION_OFFSET_FIELDS, offset_values):
        result[by_name[name]] = ota.VehicleConfigItem(name, "SET", "BLOB", value)
    result[by_name[ota.LEVEL_CALIBRATION_INIT_FIELD]] = ota.VehicleConfigItem(
        ota.LEVEL_CALIBRATION_INIT_FIELD,
        "SET",
        "U8",
        "1",
    )
    return tuple(result)


def vehicle_config_export_lines(
    *,
    source_project=T003_PROJECT_VERSION,
    target_project=T004_PROJECT_VERSION,
    items=None,
):
    items = tuple(items or unset_vehicle_config_items())
    backup_sha256 = ota.calculate_vehicle_config_sha256(
        source_project,
        "red",
        1,
        items,
    )
    return [
        "CONFIG_EXPORT_BEGIN: ConfigSchema=1, Proto=1.1, Items=24",
        f"CONFIG_EXPORT_SOURCE: ProjectVer={source_project}, Profile=red, Schema=1",
        f"CONFIG_EXPORT_TARGET: ProjectVer={target_project}, Profile=red, Schema=1",
        f"CONFIG_EXPORT_HASH: BackupSHA={backup_sha256}",
        *(
            f"CONFIG_ITEM: Name={item.name}, State={item.state}, "
            f"Type={item.value_type}, Value={item.value}"
            for item in items
        ),
        f"CONFIG_EXPORT_END: Result=OK, Items=24, BackupSHA={backup_sha256}, Reason=ok",
    ]


def config_import_status_lines(
    exported,
    *,
    phase,
    received,
    source=None,
    backup_sha256=None,
    transaction_sha256=None,
    pending_transaction_sha256=None,
):
    source_line = (
        "CONFIG_IMPORT_SOURCE: ProjectVer=none, Profile=none, Schema=0"
        if source is None
        else (
            f"CONFIG_IMPORT_SOURCE: ProjectVer={source.project_version}, "
            f"Profile={source.profile_id}, Schema={source.nvs_schema}"
        )
    )
    return [
        f"CONFIG_IMPORT_STATUS: Phase={phase}, Received={received}/24, Result=OK",
        source_line,
        f"CONFIG_IMPORT_TARGET: ProjectVer={exported.target.project_version}, "
        f"Profile={exported.target.profile_id}, Schema={exported.target.nvs_schema}",
        f"CONFIG_IMPORT_BACKUP: BackupSHA={backup_sha256 or 'none'}",
        f"CONFIG_IMPORT_TRANSACTION: TransactionSHA={transaction_sha256 or 'none'}",
        "CONFIG_IMPORT_PENDING: PendingTransactionSHA="
        f"{pending_transaction_sha256 or 'none'}, "
        f"RecoveryRequired={'Yes' if pending_transaction_sha256 else 'No'}",
    ]


def config_import_begin_lines(exported):
    return [
        "CONFIG_IMPORT_BEGIN: Phase=COLLECTING, ConfigSchema=1, Proto=1.1, Items=24",
        f"CONFIG_IMPORT_SOURCE: ProjectVer={exported.source.project_version}, "
        f"Profile={exported.source.profile_id}, Schema={exported.source.nvs_schema}",
        f"CONFIG_IMPORT_TARGET: ProjectVer={exported.target.project_version}, "
        f"Profile={exported.target.profile_id}, Schema={exported.target.nvs_schema}",
        f"CONFIG_IMPORT_BACKUP: BackupSHA={exported.backup_sha256}",
    ]


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, duration):
        self.value += max(float(duration), 0.0001)


class FakeSerial:
    def __init__(self, handler, all_writes):
        self.handler = handler
        self.all_writes = all_writes
        self.pending = deque()
        self.is_open = True
        self.last_write = None

    def write(self, data):
        if not self.is_open:
            raise OSError("closed")
        line = data.decode("ascii").rstrip("\n")
        self.last_write = line
        self.all_writes.append(line)
        responses = self.handler(line)
        if isinstance(responses, BaseException):
            raise responses
        if responses is None:
            responses = []
        if isinstance(responses, str):
            responses = [responses]
        for response in responses:
            self.pending.append((response + "\n").encode("ascii"))
        return len(data)

    def flush(self):
        return None

    def readline(self):
        if not self.is_open:
            raise OSError("closed")
        return self.pending.popleft() if self.pending else b""

    def reset_input_buffer(self):
        self.pending.clear()

    def reset_output_buffer(self):
        return None

    def close(self):
        self.is_open = False


class SequenceFactory:
    def __init__(self, items):
        self.items = deque(items)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if not self.items:
            raise OSError("unavailable")
        item = self.items.popleft()
        if isinstance(item, BaseException):
            raise item
        return item


class FakeHttpResponse:
    def __init__(self, data, url, *, content_length=None):
        self.buffer = io.BytesIO(data)
        self.url = url
        self.headers = {
            "Content-Length": str(len(data) if content_length is None else content_length)
        }

    def read(self, size=-1):
        return self.buffer.read(size)

    def geturl(self):
        return self.url

    def close(self):
        self.buffer.close()


class FakeOpener:
    def __init__(self, routes, events=None):
        self.routes = routes
        self.calls = []
        self.events = events

    def __call__(self, request, *, timeout):
        url = request.full_url
        self.calls.append((url, timeout))
        if self.events is not None:
            self.events.append(("open", url))
        route = self.routes[url]
        if isinstance(route, BaseException):
            raise route
        if callable(route):
            return route(url)
        return FakeHttpResponse(route, url)


class FakeRedirectHttpsHandler(urllib.request.BaseHandler):
    handler_order = 100

    def __init__(self, location):
        self.location = location
        self.calls = []

    def https_open(self, request):
        self.calls.append(request.full_url)
        headers = Message()
        headers["Location"] = self.location
        response = urllib.response.addinfourl(
            io.BytesIO(b""),
            headers,
            request.full_url,
            code=302,
        )
        response.msg = "Found"
        return response


class PartialWriteSerial(FakeSerial):
    def write(self, data):
        return max(0, len(data) - 1)


class EndFlushFailureSerial(FakeSerial):
    def flush(self):
        if self.last_write == "fw end":
            raise OSError("flush failed after full write")
        return None


class BeginWriteFailureSerial(FakeSerial):
    def write(self, data):
        line = data.decode("ascii").rstrip("\n")
        if not line.startswith("fw begin "):
            return super().write(data)
        self.last_write = line
        self.all_writes.append(line)
        raise OSError("begin write failed with delivery unknown")


class EndWriteFailureSerial(FakeSerial):
    def __init__(self, handler, all_writes, *, mode):
        super().__init__(handler, all_writes)
        self.mode = mode

    def write(self, data):
        line = data.decode("ascii").rstrip("\n")
        if line != "fw end":
            return super().write(data)
        self.last_write = line
        self.all_writes.append(line)
        if self.mode == "partial":
            return max(0, len(data) - 1)
        raise OSError("write failed with delivery unknown")


class FirmwareScenario:
    def __init__(
        self,
        release,
        *,
        current_version="S1-OLD",
        pre_profile=None,
        post_version=None,
        post_profile=None,
        battery_line="b 12.1",
        post_battery_line=None,
        post_battery_lines=None,
        error_on=None,
        malformed_on=None,
        timeout_mode=None,
        end_ack=True,
        interrupt_on=None,
        legacy_version=False,
        profile_silent=False,
        migration_cleanup="Done",
        migration_error=None,
        post_configuration=None,
        migration_state="UNCLAIMED",
        migration_hash="none",
        post_migration_hash=None,
        final_configuration=None,
        post_level_calibration_states=None,
        post_static_states=None,
    ):
        self.release = release
        self.current_version = current_version
        self.pre_profile = pre_profile or {
            "id": release.target.profile_id,
            "schema": release.target.nvs_schema,
            "state": "READY",
            "motion": "Yes",
            "writes": "Yes",
            "protocol": release.target.protocol,
        }
        self.post_version = post_version or release.target.project_version
        self.post_profile = post_profile or dict(self.pre_profile)
        self.battery_line = battery_line
        self.post_battery_line = post_battery_line or battery_line
        self.post_battery_lines = deque(post_battery_lines or [])
        self.error_on = error_on
        self.malformed_on = malformed_on
        self.timeout_mode = timeout_mode
        self.end_ack = end_ack
        self.interrupt_on = interrupt_on
        self.legacy_version = legacy_version
        self.profile_silent = profile_silent
        self.migration_cleanup = migration_cleanup
        self.migration_error = migration_error
        self.post_configuration = post_configuration
        self.migration_state = migration_state
        self.migration_hash = migration_hash
        self.post_migration_hash = post_migration_hash or migration_hash
        self.final_configuration = final_configuration
        self.post_level_calibration_states = deque(post_level_calibration_states or [True])
        self.post_static_states = deque(post_static_states or [True])
        self.all_writes = []
        self.written = 0
        self.next_seq = 0
        self.data_attempts = {}
        self.image_size = release.app_size
        self.ota_active = False
        self.configuration = {
            "sn get": "SN: A1B2C3D4E5F6",
            "pid get": "PID: 1.00 2.00 3.00",
            "mc get": "MC: 0.000000 0.000000 0.000000 1.000000 0.000000 0.000000 0.000000 1.000000 0.000000 0.000000 0.000000 1.000000",
            "battery get": "BATTERY: Voltage=12.10V, Cal=User, Scale=1.0000",
            "odom scale get": "ODOM_SCALE: 1.0000 range=0.50..1.50",
            "trim get": "TRIM: 0.00deg center_pwm=1500us range=-5.0..5.0deg",
            "speed deadband get": "SPEED_DEADBAND: 20us range=0..300us",
            "level get": "LEVEL: offset=[0.0000 0.0000 0.0000]g",
        }

    @staticmethod
    def profile_line(profile):
        return (
            f"PROFILE: ID={profile['id']}, Schema={profile['schema']}, State={profile['state']}, "
            f"Motion={profile['motion']}, Writes={profile['writes']}"
        )

    @staticmethod
    def noise():
        return [
            "s 0 0 0 0 0 0 0 0 0 0 1 0 0 0 0 0 0",
            "m 0.1 0.2 0.3",
            "r 0 0 0 0 0 0 0 0 0 0",
        ]

    def pre_handler(self, line):
        if self.interrupt_on and line.startswith(self.interrupt_on):
            return KeyboardInterrupt()
        if line in {"v 0.00 0.00", "stream off"}:
            return []
        if line == "fw version":
            protocol = self.pre_profile.get("protocol", self.release.target.protocol)
            if self.legacy_version:
                return self.noise() + [
                    "FW_VERSION: Product=Neoracer V1, Firmware=osrcore, "
                    "Hardware=OSCORE_NEO_ESP32S3_RevA, "
                    "ProjectVer=NEORACER_V1.1-20260709-g8b28746, "
                    "Release=20260709, Git=8b28746, Dirty=No, Build=2026-07-09 17:5"
                ]
            return self.noise() + [f"FW_VERSION: ProjectVer={self.current_version}, Proto={protocol}"]
        if line == "profile get":
            if self.profile_silent:
                return []
            return self.noise() + [self.profile_line(self.pre_profile)]
        if line == "b":
            return self.noise() + [self.battery_line]
        if line in self.configuration:
            return self.noise() + [self.configuration[line]]
        if line == "profile migrate status":
            return f"MIGRATION: Enabled=Yes, State={self.migration_state}, Hash={self.migration_hash}"
        if line.startswith("profile migrate validate "):
            if self.migration_error == "validate":
                return "ERROR migrate legacy_parameters_invalid"
            return f"OK migrate validate hash={line.rsplit(' ', 1)[1][:12]}"
        if line.startswith("profile migrate apply "):
            if self.migration_error == "apply":
                return "ERROR migrate pending_commit_failed"
            return (
                "OK migrate apply state=READY reboot_required=Yes "
                f"cleanup={self.migration_cleanup} hash={line.rsplit(' ', 1)[1][:12]}"
            )
        if line == "reset":
            return "INFO: rebooting..."
        if line.startswith("fw begin "):
            if self.error_on == "begin":
                return "ERROR fw low_voltage"
            if self.malformed_on == "begin":
                return "OK fw begin broken"
            parts = line.split()
            self.image_size = int(parts[2])
            if self.malformed_on == "begin-size":
                return f"OK fw begin part=ota_1 size={self.image_size + 1}"
            self.ota_active = True
            return f"OK fw begin part=ota_1 size={self.image_size}"
        if line.startswith("fw data "):
            if self.error_on == "data":
                return "ERROR fw write_failed"
            if self.malformed_on == "data":
                return "OK fw data broken"
            _, _, seq_text, hex_data = line.split()
            seq = int(seq_text)
            chunk = bytes.fromhex(hex_data)
            attempts = self.data_attempts.get(seq, 0) + 1
            self.data_attempts[seq] = attempts
            if seq == 0 and self.timeout_mode == "stuck":
                return []
            if seq == 0 and attempts == 1 and self.timeout_mode:
                if self.timeout_mode == "committed":
                    self.written += len(chunk)
                    self.next_seq += 1
                return []
            if seq != self.next_seq:
                return "ERROR fw bad_seq"
            self.written += len(chunk)
            self.next_seq += 1
            return f"OK fw data {seq} {self.written}"
        if line == "fw status":
            if self.error_on == "status" and self.ota_active:
                return "ERROR fw status_failed"
            if self.malformed_on == "status" and self.ota_active:
                return "FW: active=Maybe"
            if self.timeout_mode == "inconsistent" and self.ota_active:
                return (
                    f"FW: active=Yes written={self.written + 1} size={self.image_size} "
                    "next_seq=9 running=ota_0 next=ota_1"
                )
            return (
                f"FW: active={'Yes' if self.ota_active else 'No'} written={self.written if self.ota_active else 0} size={self.image_size if self.ota_active else 0} "
                f"next_seq={self.next_seq} running=ota_0 next=ota_1"
            )
        if line == "fw end":
            if self.error_on == "end":
                return "ERROR fw verify_failed"
            if self.malformed_on == "end":
                return "OK fw done"
            return "OK fw reboot" if self.end_ack else []
        if line == "fw abort":
            return "OK fw abort"
        raise AssertionError(f"unexpected command: {line}")

    def post_handler(self, line):
        if line in {"v 0.00 0.00", "stream off"}:
            return []
        if line == "fw version":
            protocol = self.post_profile.get("protocol", self.release.target.protocol)
            return self.noise() + [f"FW_VERSION: ProjectVer={self.post_version}, Proto={protocol}"]
        if line == "profile get":
            return self.noise() + [self.profile_line(self.post_profile)]
        if line == "b":
            battery = self.post_battery_lines.popleft() if self.post_battery_lines else self.post_battery_line
            return self.noise() + [battery]
        if line == "status":
            ready = (
                self.post_level_calibration_states.popleft()
                if len(self.post_level_calibration_states) > 1
                else self.post_level_calibration_states[0]
            )
            stationary = (
                self.post_static_states.popleft()
                if len(self.post_static_states) > 1
                else self.post_static_states[0]
            )
            return [
                "Status: Speed=0.000m/s, Target=0.000m/s, Voltage=12.1V, "
                f"Control=Serial, SpeedMode=30%, Static={'Yes' if stationary else 'No'}",
                "IMU: BiasReady=Yes, LevelCal="
                f"{'Yes' if ready else 'No'}, GyroBias=0.0000,0.0000,0.0000, "
                "LevelOffset=0.0000,0.0000,0.0000",
            ]
        if line == "fw status":
            return "FW: active=No written=0 size=0 next_seq=0 running=ota_1 next=ota_0"
        configuration = self.post_configuration or self.configuration
        if line in configuration:
            return self.noise() + [configuration[line]]
        if line == "profile migrate status":
            return (
                f"MIGRATION: Enabled=Yes, State={self.migration_state}, "
                f"Hash={self.post_migration_hash}"
            )
        if line.startswith("profile migrate validate "):
            if self.migration_error == "validate":
                return "ERROR migrate legacy_parameters_invalid"
            return f"OK migrate validate hash={line.rsplit(' ', 1)[1][:12]}"
        if line.startswith("profile migrate apply "):
            if self.migration_error == "apply":
                return "ERROR migrate pending_commit_failed"
            return (
                "OK migrate apply state=READY reboot_required=Yes "
                f"cleanup={self.migration_cleanup} hash={line.rsplit(' ', 1)[1][:12]}"
            )
        if line == "reset":
            return "INFO: rebooting..."
        raise AssertionError(f"unexpected post-reboot command: {line}")

    def final_handler(self, line):
        original = self.post_profile
        original_configuration = self.post_configuration
        self.post_profile = {
            "id": self.release.target.profile_id,
            "schema": self.release.target.nvs_schema,
            "state": "READY",
            "motion": "Yes",
            "writes": "Yes",
            "protocol": self.release.target.protocol,
        }
        if self.final_configuration is not None:
            self.post_configuration = self.final_configuration
        try:
            return self.post_handler(line)
        finally:
            self.post_profile = original
            self.post_configuration = original_configuration

    def serials(self):
        return FakeSerial(self.pre_handler, self.all_writes), FakeSerial(self.post_handler, self.all_writes)


class PackageValidationTest(unittest.TestCase):
    def test_updater_and_tests_parse_with_python_310_grammar(self):
        for path in (TOOL_PATH, Path(__file__)):
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 10))

    def test_valid_zip_and_inspect_are_offline_and_path_is_not_printed(self):
        with tempfile.TemporaryDirectory(prefix="PRIVATE_PACKAGE_MARKER_") as temp_dir:
            package = write_zip(Path(temp_dir) / "release.zip")
            original_factory = ota.default_serial_factory
            ota.default_serial_factory = lambda **_kwargs: self.fail("inspect opened serial")
            output = io.StringIO()
            try:
                with contextlib.redirect_stdout(output):
                    self.assertEqual(ota.main(["inspect", "--package", str(package)]), 0)
            finally:
                ota.default_serial_factory = original_factory
            data = json.loads(output.getvalue())
            self.assertEqual(data["app_sha256"], ota._sha256(DEFAULT_APP))
            self.assertNotIn("PRIVATE_PACKAGE_MARKER", output.getvalue())

    def test_rejects_missing_manifest_and_non_zip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            missing_manifest = temp / "missing.zip"
            with zipfile.ZipFile(missing_manifest, "w") as archive:
                archive.writestr("images/osrcore.bin", DEFAULT_APP)
            with self.assertRaisesRegex(ota.PackageValidationError, "manifest.json"):
                ota.load_release_package(missing_manifest)

            not_zip = temp / "not.zip"
            not_zip.write_bytes(b"not a zip")
            with self.assertRaisesRegex(ota.PackageValidationError, "valid ZIP"):
                ota.load_release_package(not_zip)

    def test_rejects_path_traversal_absolute_and_backslash_members(self):
        invalid_names = ("../escape", "/absolute", "C:/absolute", "folder\\..\\escape", "a//b")
        with tempfile.TemporaryDirectory() as temp_dir:
            for index, name in enumerate(invalid_names):
                with self.subTest(name=name):
                    package = Path(temp_dir) / f"bad-{index}.zip"
                    manifest = base_manifest()
                    members = [
                        ("manifest.json", json.dumps(manifest)),
                        ("images/osrcore.bin", DEFAULT_APP),
                        (name, b"x"),
                    ]
                    write_zip(package, members=members)
                    with self.assertRaises(ota.PackageValidationError):
                        ota.load_release_package(package)

    def test_rejects_duplicate_and_symlink_members(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            duplicate = Path(temp_dir) / "duplicate.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                write_zip(
                    duplicate,
                    members=[
                        ("manifest.json", json.dumps(base_manifest())),
                        ("images/osrcore.bin", DEFAULT_APP),
                        ("images/osrcore.bin", DEFAULT_APP),
                    ],
                )
            with self.assertRaisesRegex(ota.PackageValidationError, "duplicate"):
                ota.load_release_package(duplicate)

            symlink = Path(temp_dir) / "symlink.zip"
            with zipfile.ZipFile(symlink, "w") as archive:
                archive.writestr("manifest.json", json.dumps(base_manifest()))
                info = zipfile.ZipInfo("images/osrcore.bin")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, "target")
            with self.assertRaisesRegex(ota.PackageValidationError, "symbolic"):
                ota.load_release_package(symlink)

    def test_rejects_bad_manifest_root_schema_and_field_types(self):
        cases = [
            [],
            {**base_manifest(), "schema": True},
            {**base_manifest(), "schema": 2},
            {**base_manifest(), "profile": []},
            {**base_manifest(), "flash": []},
            {**base_manifest(), "sha256": []},
        ]
        for section, field, value in (
            ("profile", "id", None),
            ("profile", "hardware", 1),
            ("profile", "nvs_schema", True),
            ("profile", "nvs_schema", -1),
            ("profile", "project_version", ""),
            ("profile", "protocol", None),
            ("flash", "package_app", None),
            ("sha256", "app", 123),
        ):
            manifest = base_manifest()
            if value is None:
                del manifest[section][field]
            else:
                manifest[section][field] = value
            cases.append(manifest)
        with tempfile.TemporaryDirectory() as temp_dir:
            for index, manifest in enumerate(cases):
                with self.subTest(index=index):
                    package = write_zip(Path(temp_dir) / f"manifest-{index}.zip", manifest=manifest)
                    with self.assertRaises(ota.PackageValidationError):
                        ota.load_release_package(package)

            invalid_json = Path(temp_dir) / "invalid-json.zip"
            write_zip(
                invalid_json,
                members=[("manifest.json", b"{"), ("images/osrcore.bin", DEFAULT_APP)],
            )
            with self.assertRaisesRegex(ota.PackageValidationError, "UTF-8 JSON"):
                ota.load_release_package(invalid_json)

    def test_rejects_missing_app_empty_app_bad_sha_and_does_not_scan_renames(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            missing = temp / "missing-app.zip"
            write_zip(
                missing,
                members=[
                    ("manifest.json", json.dumps(base_manifest())),
                    ("images/renamed.bin", DEFAULT_APP),
                ],
            )
            with self.assertRaisesRegex(ota.PackageValidationError, "missing"):
                ota.load_release_package(missing)

            empty = write_zip(temp / "empty.zip", app=b"")
            with self.assertRaisesRegex(ota.PackageValidationError, "empty"):
                ota.load_release_package(empty)

            bad_sha_manifest = base_manifest()
            bad_sha_manifest["sha256"]["app"] = "0" * 64
            bad_sha = write_zip(temp / "bad-sha.zip", manifest=bad_sha_manifest)
            with self.assertRaisesRegex(ota.PackageValidationError, "SHA256"):
                ota.load_release_package(bad_sha)

    def test_rejects_full_merged_and_unsupported_protocol(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            for name in (
                "images/full.bin",
                "images/fullimage.bin",
                "images/merged-flash.bin",
                "fullflash/app.bin",
            ):
                with self.subTest(name=name):
                    package = write_zip(temp / (name.replace("/", "-") + ".zip"), app_member=name)
                    with self.assertRaisesRegex(ota.PackageValidationError, "full or merged"):
                        ota.load_release_package(package)

            manifest = base_manifest()
            manifest["profile"]["protocol"] = "2.0"
            package = write_zip(temp / "protocol.zip", manifest=manifest)
            with self.assertRaisesRegex(ota.PackageValidationError, "protocol"):
                ota.load_release_package(package)

    def test_rejects_manifest_app_member_count_and_total_uncompressed_limits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)

            manifest = base_manifest()
            manifest["padding"] = "x" * 1024
            package = write_zip(
                temp / "large-manifest.zip",
                manifest=manifest,
                compression=zipfile.ZIP_STORED,
            )
            with mock.patch.object(ota, "MAX_MANIFEST_BYTES", 512):
                with self.assertRaisesRegex(ota.PackageValidationError, "manifest.json exceeds"):
                    ota.load_release_package(package)

            package = write_zip(
                temp / "large-app.zip",
                app=b"a" * 64,
                compression=zipfile.ZIP_STORED,
            )
            with mock.patch.object(ota, "MAX_APP_BYTES", 32):
                with self.assertRaisesRegex(ota.PackageValidationError, "App file exceeds"):
                    ota.load_release_package(package)

            package = write_zip(
                temp / "too-many-members.zip",
                members=[
                    ("manifest.json", json.dumps(base_manifest())),
                    ("images/osrcore.bin", DEFAULT_APP),
                    ("extra/one.txt", b"1"),
                    ("extra/two.txt", b"2"),
                    ("extra/three.txt", b"3"),
                ],
                compression=zipfile.ZIP_STORED,
            )
            with mock.patch.object(ota, "MAX_ZIP_MEMBERS", 4):
                with self.assertRaisesRegex(ota.PackageValidationError, "more than 4 members"):
                    ota.load_release_package(package)

            large_app = b"a" * 3000
            package = write_zip(
                temp / "large-total.zip",
                members=[
                    ("manifest.json", json.dumps(base_manifest(large_app))),
                    ("images/osrcore.bin", large_app),
                    ("extra/data.bin", b"b" * 3000),
                ],
                compression=zipfile.ZIP_STORED,
            )
            with mock.patch.object(ota, "MAX_TOTAL_UNCOMPRESSED_BYTES", 5000):
                with self.assertRaisesRegex(ota.PackageValidationError, "uncompressed limit"):
                    ota.load_release_package(package)

    def test_rejects_high_compression_ratio_before_app_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package = write_zip(
                Path(temp_dir) / "compression-bomb.zip",
                app=b"\x00" * (512 * 1024),
            )
            with self.assertRaisesRegex(ota.PackageValidationError, "compression ratio"):
                ota.load_release_package(package)


@unittest.skip("retired T00x package catalog; B01/B02 resources are covered by test_firmware_client")
class RepositoryCatalogAssetTest(unittest.TestCase):
    def test_checked_in_t002_catalog_and_package_are_exactly_consistent(self):
        package_path = REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t002.zip"
        package_data = package_path.read_bytes()
        self.assertEqual(len(package_data), 229681)
        self.assertEqual(
            ota._sha256(package_data),
            "e4cd08eab992dbe2ecebfe09f4743b0b486ad07952452a8f754d0a3fc910ca23",
        )
        with zipfile.ZipFile(package_path, "r") as archive:
            self.assertEqual(
                archive.namelist(),
                ["manifest.json", "images/application.bin"],
            )

        release = ota.load_release_package(package_path)
        self.assertEqual(
            release.app_sha256,
            "ee80786a4563e19f62d44d315018aa6e9f3b19614f07670347c6a81083ab598d",
        )

    def test_checked_in_t004_manifest_and_package_are_retained_exactly(self):
        package_path = REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        package_data = package_path.read_bytes()
        self.assertEqual(len(package_data), 246201)
        self.assertEqual(ota._sha256(package_data), T004_PACKAGE_SHA256)

        with zipfile.ZipFile(package_path, "r") as archive:
            self.assertEqual(archive.namelist(), ["manifest.json", "images/application.bin"])
            manifest_raw = archive.read("manifest.json")
            manifest = json.loads(manifest_raw)
            self.assertEqual(len(manifest_raw), 3812)
            self.assertEqual(
                ota._sha256(manifest_raw),
                "de77c476cf544d76158046921410eb76aa22b1c9cc7f36d6bca68f35b17411c5",
            )
            self.assertEqual(
                manifest["profile"],
                {
                    "firmware": "osrcore",
                    "hardware": "OSCORE_ESP32S3_RevA",
                    "id": "red",
                    "manufacturer": "OSRBOT",
                    "nvs_schema": 1,
                    "product": "OSRACER ARC-E01",
                    "project_version": T004_PROJECT_VERSION,
                    "protocol": "1.1",
                    "usb_serial": "OSRCOREV01",
                },
            )
            self.assertEqual(manifest["flash"], {"package_app": "images/application.bin", "type": "app_ota"})
            self.assertEqual(manifest["sha256"]["app"], T004_APP_SHA256)
            self.assertEqual(manifest["size"]["app"], 428752)
            self.assertEqual(manifest["build"]["source_tree_sha256"], T004_SOURCE_TREE_SHA256)
            self.assertEqual(manifest["bootstrap"]["source_project_version"], T003_PROJECT_VERSION)
            self.assertEqual(manifest["bootstrap"]["first_boot_state"], "BACKUP_REQUIRED")
            self.assertFalse(manifest["bootstrap"]["writes_allowed"])
            self.assertEqual(
                manifest["config_transfer"],
                {
                    "backup_hash_domain": "OSRVCFG1",
                    "item_count": 24,
                    "item_states": ["SET", "UNSET", "ERROR"],
                    "restore_journal": "osr_cfg/state+snap_sha",
                    "schema": 1,
                    "system_metadata_importable": False,
                    "transaction_hash_domain": "OSRRESTORE1",
                },
            )

        release = ota.load_release_package(package_path)
        self.assertEqual(release.manifest_sha256, ota._sha256(manifest_raw))
        self.assertEqual(release.app_size, 428752)
        self.assertEqual(release.app_sha256, T004_APP_SHA256)
        self.assertEqual(release.source_tree_sha256, T004_SOURCE_TREE_SHA256)
        self.assertEqual(release.bootstrap_source_project_version, T003_PROJECT_VERSION)
        self.assertEqual(release.config_item_count, 24)

    def test_checked_in_t005_manifest_package_and_catalog_are_exact(self):
        catalog_path = REPO_ROOT / "firmware" / "catalog.json"
        package_path = REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t005.zip"
        package_data = package_path.read_bytes()
        self.assertEqual(len(package_data), 246672)
        self.assertEqual(ota._sha256(package_data), T005_PACKAGE_SHA256)

        with zipfile.ZipFile(package_path, "r") as archive:
            self.assertEqual(archive.namelist(), ["manifest.json", "images/application.bin"])
            manifest_raw = archive.read("manifest.json")
            manifest = json.loads(manifest_raw)
            self.assertEqual(len(manifest_raw), 3823)
            self.assertEqual(
                ota._sha256(manifest_raw),
                "cbf7b1caad7490aaa66fc59899e8587c235f74c49cf69fe6bb4ba85dc4e30234",
            )
            self.assertEqual(manifest["profile"]["project_version"], T005_PROJECT_VERSION)
            self.assertEqual(manifest["profile"]["id"], "red")
            self.assertEqual(manifest["profile"]["hardware"], "OSCORE_ESP32S3_RevA")
            self.assertEqual(manifest["profile"]["nvs_schema"], 1)
            self.assertEqual(manifest["profile"]["protocol"], "1.1")
            self.assertEqual(manifest["flash"]["package_app"], "images/application.bin")
            self.assertEqual(manifest["flash"]["type"], "app_ota")
            self.assertEqual(manifest["sha256"]["app"], T005_APP_SHA256)
            self.assertEqual(manifest["size"]["app"], 429424)
            self.assertEqual(manifest["build"]["source_tree_sha256"], T005_SOURCE_TREE_SHA256)
            self.assertEqual(
                manifest["bootstrap"]["source_project_version"],
                T004_PROJECT_VERSION,
            )
            self.assertEqual(manifest["bootstrap"]["first_boot_state"], "BACKUP_REQUIRED")
            self.assertFalse(manifest["bootstrap"]["writes_allowed"])

        catalog = ota.parse_catalog(catalog_path.read_bytes())
        self.assertEqual(catalog.channels["stable"], ())
        self.assertEqual(len(catalog.channels["test"]), 1)
        candidate = catalog.channels["test"][0]
        self.assertEqual(candidate.candidate_id, "c03-t005")
        self.assertEqual(candidate.asset, "packages/osr-fw-c03-t005.zip")
        self.assertEqual(candidate.package_size, len(package_data))
        self.assertEqual(candidate.package_sha256, T005_PACKAGE_SHA256)
        self.assertEqual(candidate.app_size, 429424)
        self.assertEqual(candidate.app_sha256, T005_APP_SHA256)
        self.assertEqual(candidate.source_tree_sha256, T005_SOURCE_TREE_SHA256)
        self.assertEqual(candidate.bootstrap_source_project_version, T004_PROJECT_VERSION)
        self.assertEqual(candidate.target.project_version, T005_PROJECT_VERSION)
        self.assertFalse(candidate.release_ready)
        self.assertTrue(candidate.source_dirty)
        self.assertEqual(candidate.signature, "none")

        release = ota.load_release_package(package_path)
        ota._validate_catalog_manifest(release, candidate)
        self.assertTrue(ota._transport_recovery_package_contract(release))
        self.assertEqual(release.manifest_sha256, ota._sha256(manifest_raw))
        self.assertEqual(release.app_size, 429424)
        self.assertEqual(release.config_item_count, 24)


class ManagedConfigContractTest(unittest.TestCase):
    def test_all_unset_osrvcfg1_and_osrrestore1_known_vectors(self):
        items = unset_vehicle_config_items()
        backup_sha256 = ota.calculate_vehicle_config_sha256(
            T003_PROJECT_VERSION,
            "red",
            1,
            items,
        )
        self.assertEqual(
            backup_sha256,
            "b948706d112f1a78435f21e4105561b98bdb878e9424f51a859b6468b33fadff",
        )
        source = ota.TargetProfile("red", "", 1, T003_PROJECT_VERSION, "1.1")
        target = ota.TargetProfile("red", "", 1, "OSRF-C03-T004-s0123456789ab", "1.1")
        self.assertEqual(
            ota.calculate_vehicle_restore_sha256(backup_sha256, source, target),
            "94b315e51a28d0943434755d2cd49f83645a92b60440cd57e7c80372fbab4bc6",
        )
        actual_target = ota.TargetProfile("red", "", 1, T004_PROJECT_VERSION, "1.1")
        self.assertEqual(
            ota.calculate_vehicle_restore_sha256(backup_sha256, source, actual_target),
            "694458878a52d2085b10c3b415ad2048ce5f18e17c6d188e2d73213eb8e25260",
        )

    def test_semantic_compare_allows_only_healthy_level_calibration_refresh(self):
        original = ready_vehicle_config_items(
            offsets=("00000000", "00000000", "00000000")
        )
        refreshed = ready_vehicle_config_items(
            original,
            offsets=("CDCCCC3D", "CDCCCCBD", "CDCC4C3E"),
        )
        comparison = ota.compare_vehicle_config_semantics(original, refreshed)
        self.assertTrue(comparison.matches)
        self.assertEqual(comparison.non_level_mismatches, ())
        self.assertEqual(comparison.level_init_status, "unchanged")
        self.assertEqual(comparison.level_offset_status, "refreshed")
        self.assertEqual(
            comparison.changed_level_fields,
            ota.LEVEL_CALIBRATION_OFFSET_FIELDS,
        )

        initialized = ota.compare_vehicle_config_semantics(
            unset_vehicle_config_items(),
            ready_vehicle_config_items(),
        )
        self.assertTrue(initialized.matches)
        self.assertEqual(initialized.level_init_status, "refreshed")
        self.assertEqual(initialized.level_offset_status, "refreshed")

        changed_non_level = list(refreshed)
        changed_non_level[3] = ota.VehicleConfigItem(
            "pid_params.init", "SET", "U8", "1"
        )
        mismatch = ota.compare_vehicle_config_semantics(original, changed_non_level)
        self.assertFalse(mismatch.matches)
        self.assertEqual(mismatch.non_level_mismatches, ("pid_params.init",))

    def test_semantic_compare_rejects_unhealthy_ready_level_values(self):
        original = ready_vehicle_config_items()
        cases = {
            "nan": ("0000C07F", "level_cal.ox"),
            "infinity": ("0000807F", "level_cal.ox"),
            "out_of_range": ("00004040", "level_cal.ox"),
        }
        for label, (value, field) in cases.items():
            with self.subTest(label=label):
                current = list(original)
                current[12] = ota.VehicleConfigItem(field, "SET", "BLOB", value)
                comparison = ota.compare_vehicle_config_semantics(original, current)
                self.assertFalse(comparison.matches)
                self.assertEqual(comparison.invalid_level_fields, (field,))

        invalid_init = list(original)
        invalid_init[15] = ota.VehicleConfigItem(
            ota.LEVEL_CALIBRATION_INIT_FIELD,
            "SET",
            "U8",
            "0",
        )
        comparison = ota.compare_vehicle_config_semantics(original, invalid_init)
        self.assertFalse(comparison.matches)
        self.assertEqual(
            comparison.invalid_level_fields,
            (ota.LEVEL_CALIBRATION_INIT_FIELD,),
        )

    def test_exact_29_line_export_accepts_mixed_set_and_unset_items(self):
        items = list(unset_vehicle_config_items())
        items[0] = ota.VehicleConfigItem("pid_params.kp", "SET", "BLOB", "00010203")
        items[3] = ota.VehicleConfigItem("pid_params.init", "SET", "U8", "255")
        items[4] = ota.VehicleConfigItem("pid_params.profile", "SET", "U32", "4294967295")
        items[18] = ota.VehicleConfigItem("chassis_cal.steer_trim", "SET", "I32", "-2147483648")
        lines = vehicle_config_export_lines(items=items)
        self.assertEqual(len(lines), 29)

        exported = ota.parse_vehicle_config_export_lines(lines)
        self.assertEqual(exported.source.project_version, T003_PROJECT_VERSION)
        self.assertEqual(exported.target.project_version, T004_PROJECT_VERSION)
        self.assertEqual(exported.items, tuple(items))
        self.assertEqual(
            exported.backup_sha256,
            ota.calculate_vehicle_config_sha256(T003_PROJECT_VERSION, "red", 1, items),
        )

    def test_uppercase_blob_export_matches_independent_osrvcfg1_vector(self):
        items = (
            ota.VehicleConfigItem("pid_params.kp", "SET", "BLOB", "0080DF43"),
            ota.VehicleConfigItem("pid_params.ki", "SET", "BLOB", "00A09643"),
            ota.VehicleConfigItem("pid_params.kd", "SET", "BLOB", "00609643"),
            ota.VehicleConfigItem("pid_params.init", "SET", "U8", "1"),
            ota.VehicleConfigItem("pid_params.profile", "SET", "U32", "2"),
            ota.VehicleConfigItem(
                "mag_calib.hi",
                "SET",
                "BLOB",
                "0000803F0000004000004040",
            ),
            ota.VehicleConfigItem(
                "mag_calib.si",
                "SET",
                "BLOB",
                "0000803F0000000000000000000000000000803F0000000000000000000000000000803F",
            ),
            ota.VehicleConfigItem("mag_calib.init", "SET", "U8", "1"),
            ota.VehicleConfigItem("cf_params.alpha_s", "SET", "BLOB", "CDCCCC3D"),
            ota.VehicleConfigItem("cf_params.alpha_m", "SET", "BLOB", "0000003F"),
            ota.VehicleConfigItem("cf_params.spd_thr", "SET", "BLOB", "0000803F"),
            ota.VehicleConfigItem("cf_params.init", "SET", "U8", "1"),
            ota.VehicleConfigItem("level_cal.ox", "SET", "BLOB", "0AD7233C"),
            ota.VehicleConfigItem("level_cal.oy", "SET", "BLOB", "0AD7A33C"),
            ota.VehicleConfigItem("level_cal.oz", "SET", "BLOB", "0AD7F33C"),
            ota.VehicleConfigItem("level_cal.init", "SET", "U8", "1"),
            ota.VehicleConfigItem(
                "chassis_cal.odom_scale",
                "SET",
                "BLOB",
                "0000803F",
            ),
            ota.VehicleConfigItem(
                "chassis_cal.steer_trim_deg",
                "SET",
                "BLOB",
                "CDCC4C3E",
            ),
            ota.VehicleConfigItem("chassis_cal.steer_trim", "SET", "I32", "1507"),
            ota.VehicleConfigItem("chassis_cal.init", "SET", "U8", "1"),
            ota.VehicleConfigItem("speed_cal.deadband_us", "SET", "I32", "90"),
            ota.VehicleConfigItem("speed_cal.init", "SET", "U8", "1"),
            ota.VehicleConfigItem("battery_cal.scale", "SET", "BLOB", "0000803F"),
            ota.VehicleConfigItem("battery_cal.init", "SET", "U8", "1"),
        )
        expected_hash = "2ab489fb0d72c768cb34120b31cde91b56f80efed5aa7a3e31e9136fd65bf5bf"
        lines = [
            "CONFIG_EXPORT_BEGIN: ConfigSchema=1, Proto=1.1, Items=24",
            f"CONFIG_EXPORT_SOURCE: ProjectVer={T004_PROJECT_VERSION}, Profile=red, Schema=1",
            f"CONFIG_EXPORT_TARGET: ProjectVer={T005_PROJECT_VERSION}, Profile=red, Schema=1",
            f"CONFIG_EXPORT_HASH: BackupSHA={expected_hash}",
            *(
                f"CONFIG_ITEM: Name={item.name}, State={item.state}, "
                f"Type={item.value_type}, Value={item.value}"
                for item in items
            ),
            f"CONFIG_EXPORT_END: Result=OK, Items=24, BackupSHA={expected_hash}, Reason=ok",
        ]

        exported = ota.parse_vehicle_config_export_lines(lines)
        self.assertEqual(exported.items, items)
        self.assertEqual(exported.backup_sha256, expected_hash)
        lowercase_items = tuple(
            replace(item, value=item.value.lower()) if item.value_type == "BLOB" else item
            for item in items
        )
        self.assertEqual(
            ota.calculate_vehicle_config_sha256(
                T004_PROJECT_VERSION,
                "red",
                1,
                lowercase_items,
            ),
            expected_hash,
        )

    def test_blob_hex_stays_strict_for_nonhex_odd_length_and_wrong_size(self):
        cases = (
            ("nonhex", "0080DG43", "invalid typed value"),
            ("odd", "ABC", "invalid typed value"),
            ("size", "ABCD", "invalid value size"),
        )
        for label, value, message in cases:
            with self.subTest(label=label):
                item = ota.VehicleConfigItem("pid_params.kp", "SET", "BLOB", value)
                with self.assertRaisesRegex(ota.ProtocolError, message):
                    ota._config_value_bytes(item, 4)

    def test_export_parser_rejects_non_exact_order_duplicate_error_incomplete_and_bad_hash(self):
        valid = vehicle_config_export_lines()

        out_of_order = list(valid)
        out_of_order[4], out_of_order[5] = out_of_order[5], out_of_order[4]
        duplicate = list(valid)
        duplicate.insert(5, duplicate[4])
        telemetry = ["s 0 0 0"] + list(valid)
        missing = list(valid[:-2]) + [valid[-1]]
        bad_hash = list(valid)
        bad_hash[3] = f"CONFIG_EXPORT_HASH: BackupSHA={'0' * 64}"
        bad_hash[-1] = (
            f"CONFIG_EXPORT_END: Result=OK, Items=24, BackupSHA={'0' * 64}, Reason=ok"
        )

        for label, lines in (
            ("out_of_order", out_of_order),
            ("duplicate", duplicate),
            ("telemetry", telemetry),
            ("missing", missing),
            ("bad_hash", bad_hash),
        ):
            with self.subTest(label=label):
                with self.assertRaises(ota.ProtocolError):
                    ota.parse_vehicle_config_export_lines(lines)

        error = list(valid)
        error[3] = "CONFIG_EXPORT_HASH: BackupSHA=none"
        error[4] = (
            "CONFIG_ITEM: Name=pid_params.kp, State=ERROR, Type=BLOB, "
            "Value=-, Code=NVS_READ_FAILED"
        )
        error[-1] = (
            "CONFIG_EXPORT_END: Result=ERROR, Items=24, "
            "BackupSHA=none, Reason=item_error"
        )
        incomplete = list(valid)
        incomplete[-1] = (
            f"CONFIG_EXPORT_END: Result=INCOMPLETE, Items=24, "
            f"BackupSHA={valid[3].rsplit('=', 1)[1]}, Reason=namespace_not_initialized"
        )
        for label, lines in (("error", error), ("incomplete", incomplete)):
            with self.subTest(label=label):
                with self.assertRaises(ota.DevicePreflightError):
                    ota.parse_vehicle_config_export_lines(lines)

    def test_receive_export_ignores_interleaved_telemetry_but_keeps_protocol_strict(self):
        protocol_lines = vehicle_config_export_lines()
        noisy_lines = []
        for index, line in enumerate(protocol_lines):
            noisy_lines.extend((f"s {index} 0 0", "m 0.1 0.2 0.3", line))
        writes = []
        serial = FakeSerial(
            lambda command: noisy_lines
            if command == "config export"
            else AssertionError(f"unexpected command: {command}"),
            writes,
        )
        clock = FakeClock()
        transport = ota.SerialTransport(
            serial,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        config = ota.UpdateConfig(response_timeout=0.02)

        exported = ota._receive_vehicle_config_export(transport, config)
        self.assertEqual(writes, ["config export"])
        self.assertEqual(exported.items, unset_vehicle_config_items())

    def test_backup_is_atomically_replaced_private_and_read_back(self):
        exported = ota.parse_vehicle_config_export_lines(vehicle_config_export_lines())
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir) / "vehicle-backups"
            with mock.patch.object(ota.os, "replace", wraps=ota.os.replace) as replace_mock:
                with mock.patch.object(
                    ota,
                    "_load_vehicle_config_backup",
                    wraps=ota._load_vehicle_config_backup,
                ) as load_mock:
                    path, file_sha256 = ota._write_vehicle_config_backup(
                        exported,
                        None,
                        directory,
                    )
            replace_mock.assert_called_once()
            load_mock.assert_called_once_with(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(file_sha256, ota._sha256(path.read_bytes()))
            self.assertEqual(tuple(directory.glob(".vehicle-config-*")), ())
            loaded, loaded_file_sha256 = ota._load_vehicle_config_backup(path)
            self.assertEqual(loaded, exported)
            self.assertEqual(loaded_file_sha256, file_sha256)

    def test_backup_report_is_format_evidence_not_device_compatibility(self):
        exported = ota.parse_vehicle_config_export_lines(vehicle_config_export_lines())
        with tempfile.TemporaryDirectory() as temp_dir:
            path, _file_sha = ota._write_vehicle_config_backup(
                exported,
                None,
                Path(temp_dir) / "vehicle-backups",
            )
            output = []
            serial_factory = SequenceFactory(
                [AssertionError("offline backup report must not open serial")]
            )
            result = ota.main(
                ["report", "--backup", str(path)],
                serial_factory=serial_factory,
                output_func=output.append,
            )
        self.assertEqual(result, 0)
        report = json.loads(output[0])
        self.assertTrue(report["format_valid"])
        self.assertEqual(report["device_compatibility"], "not_checked")
        self.assertNotIn("restorable", report)
        self.assertIn("explicit RESTORE", report["restore_policy"])
        self.assertEqual(serial_factory.calls, [])

    def test_validate_accepts_only_the_exact_persistent_transaction_on_power_loss_resume(self):
        exported = ota.parse_vehicle_config_export_lines(vehicle_config_export_lines())
        transaction_sha = ota.calculate_vehicle_restore_sha256(
            exported.backup_sha256,
            exported.source,
            exported.target,
        )
        status_lines = [
            "CONFIG_IMPORT_STATUS: Phase=VALIDATED, Received=24/24, Result=OK",
            f"CONFIG_IMPORT_SOURCE: ProjectVer={exported.source.project_version}, "
            f"Profile={exported.source.profile_id}, Schema={exported.source.nvs_schema}",
            f"CONFIG_IMPORT_TARGET: ProjectVer={exported.target.project_version}, "
            f"Profile={exported.target.profile_id}, Schema={exported.target.nvs_schema}",
            f"CONFIG_IMPORT_BACKUP: BackupSHA={exported.backup_sha256}",
            f"CONFIG_IMPORT_TRANSACTION: TransactionSHA={transaction_sha}",
            f"CONFIG_IMPORT_PENDING: PendingTransactionSHA={transaction_sha}, "
            "RecoveryRequired=Yes",
        ]

        def handler(command):
            if command == "config import validate":
                return (
                    "OK config import validate items=24 "
                    f"BackupSHA={exported.backup_sha256[:12]} "
                    f"TransactionSHA={transaction_sha[:12]}"
                )
            if command == "config import status":
                return status_lines
            return AssertionError(f"unexpected command: {command}")

        config = ota.UpdateConfig(response_timeout=0.02)
        for persistent_resume, succeeds in ((True, True), (False, False)):
            with self.subTest(persistent_resume=persistent_resume):
                clock = FakeClock()
                serial = FakeSerial(handler, [])
                transport = ota.SerialTransport(
                    serial,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                )
                if succeeds:
                    status = ota._validate_config_import(
                        transport,
                        config,
                        exported,
                        transaction_sha,
                        persistent_resume=True,
                    )
                    self.assertEqual(status.pending_transaction_sha256, transaction_sha)
                    self.assertTrue(status.recovery_required)
                else:
                    with self.assertRaisesRegex(ota.ProtocolError, "full config import"):
                        ota._validate_config_import(
                            transport,
                            config,
                            exported,
                            transaction_sha,
                            persistent_resume=False,
                        )

    def test_partial_collecting_session_is_aborted_and_rechecked_before_restart(self):
        exported = ota.parse_vehicle_config_export_lines(vehicle_config_export_lines())
        writes = []

        def handler(command):
            if command == "config import abort":
                return "OK config import abort RecoveryRequired=No reboot_required=No"
            if command == "config import status":
                return [
                    "CONFIG_IMPORT_STATUS: Phase=EMPTY, Received=0/24, Result=OK",
                    "CONFIG_IMPORT_SOURCE: ProjectVer=none, Profile=none, Schema=0",
                    f"CONFIG_IMPORT_TARGET: ProjectVer={exported.target.project_version}, "
                    f"Profile={exported.target.profile_id}, Schema={exported.target.nvs_schema}",
                    "CONFIG_IMPORT_BACKUP: BackupSHA=none",
                    "CONFIG_IMPORT_TRANSACTION: TransactionSHA=none",
                    "CONFIG_IMPORT_PENDING: PendingTransactionSHA=none, RecoveryRequired=No",
                ]
            return AssertionError(f"unexpected command: {command}")

        clock = FakeClock()
        status = ota._abort_collecting_config_import(
            ota.SerialTransport(
                FakeSerial(handler, writes),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            ),
            ota.UpdateConfig(response_timeout=0.02),
            persistent_transaction_sha256=None,
        )
        self.assertEqual(writes, ["config import abort", "config import status"])
        self.assertEqual(status.phase, "EMPTY")
        self.assertIsNone(status.pending_transaction_sha256)
        self.assertFalse(status.recovery_required)

    def test_config_import_status_rejects_fields_inconsistent_with_phase(self):
        exported = ota.parse_vehicle_config_export_lines(vehicle_config_export_lines())
        transaction_sha = ota.calculate_vehicle_restore_sha256(
            exported.backup_sha256,
            exported.source,
            exported.target,
        )
        cases = {
            "empty_received": config_import_status_lines(
                exported,
                phase="EMPTY",
                received=1,
            ),
            "collecting_unbound": config_import_status_lines(
                exported,
                phase="COLLECTING",
                received=0,
            ),
            "validated_partial": config_import_status_lines(
                exported,
                phase="VALIDATED",
                received=23,
                source=exported.source,
                backup_sha256=exported.backup_sha256,
                transaction_sha256=transaction_sha,
            ),
            "applied_without_journal": config_import_status_lines(
                exported,
                phase="APPLIED",
                received=24,
                source=exported.source,
                backup_sha256=exported.backup_sha256,
                transaction_sha256=transaction_sha,
            ),
            "readback_with_pending": config_import_status_lines(
                exported,
                phase="READBACK_OK",
                received=24,
                source=exported.source,
                backup_sha256=exported.backup_sha256,
                transaction_sha256=transaction_sha,
                pending_transaction_sha256=transaction_sha,
            ),
        }
        for label, lines in cases.items():
            with self.subTest(label=label):
                writes = []

                def handler(command, lines=lines):
                    if command == "config import status":
                        return lines
                    return AssertionError(f"unexpected phase-status command: {command}")

                clock = FakeClock()
                with self.assertRaisesRegex(ota.ProtocolError, "inconsistent with its phase"):
                    ota._query_config_import_status(
                        ota.SerialTransport(
                            FakeSerial(handler, writes),
                            monotonic=clock.monotonic,
                            sleep=clock.sleep,
                        ),
                        ota.UpdateConfig(response_timeout=0.02),
                    )

    def test_console_renderer_plain_output_is_english_progressive_and_has_final_cards(self):
        lines = []
        clock = FakeClock()
        renderer = ota.ConsoleRenderer(
            lines.append,
            tty=False,
            no_color=False,
            monotonic=clock.monotonic,
        )
        renderer.header("OSRacer Managed Firmware Update")
        renderer.phase(1, 3, "Inspect device", "exclusive serial")
        renderer.progress(5, 100)
        renderer.progress(7, 100)
        clock.value = 1.5
        renderer.progress(10, 100)
        renderer.progress(100, 100)
        renderer.result(True, [("Firmware", "READY")])
        renderer.result(False, [("Reason", "device validation failed")])

        output = "\n".join(lines)
        self.assertIn("=== OSRacer Managed Firmware Update ===", output)
        self.assertIn("[1/3] Inspect device", output)
        self.assertIn("  5%", output)
        self.assertNotIn("  7%", output)
        self.assertIn(" 10%", output)
        self.assertIn("100%", output)
        self.assertIn("=== RESULT: SUCCESS ===", output)
        self.assertIn("=== RESULT: ACTION REQUIRED ===", output)
        self.assertNotIn("\x1b[", output)
        self.assertFalse(any("\u4e00" <= character <= "\u9fff" for character in output))

    def test_console_renderer_honors_no_color_even_for_tty(self):
        no_color_lines = []
        with mock.patch.dict(ota.os.environ, {"NO_COLOR": "1"}):
            renderer = ota.ConsoleRenderer(no_color_lines.append, tty=True, no_color=None)
            renderer.header("Firmware Update")
            renderer.phase(1, 1, "Complete")
            renderer.result(True, [("Status", "READY")])
        self.assertNotIn("\x1b[", "\n".join(no_color_lines))

        color_lines = []
        renderer = ota.ConsoleRenderer(color_lines.append, tty=True, no_color=False)
        renderer.phase(1, 1, "Complete")
        renderer.result(True, [("Status", "READY")])
        self.assertIn("\x1b[", "\n".join(color_lines))


@unittest.skip("retired T004/T005 migration wrapper; the standalone client has new operation tests")
class ManagedUpdateStateMachineTest(unittest.TestCase):
    @staticmethod
    def identity_lines(
        state,
        *,
        project_version=T004_PROJECT_VERSION,
        protocol="1.1",
        profile_id="red",
        schema=1,
        motion=False,
        writes=False,
    ):
        return {
            "fw version": f"FW_VERSION: ProjectVer={project_version}, Proto={protocol}",
            "profile get": (
                f"PROFILE: ID={profile_id}, Schema={schema}, State={state}, "
                f"Motion={'Yes' if motion else 'No'}, Writes={'Yes' if writes else 'No'}"
            ),
            "fw status": "FW: active=No written=0 size=0 next_seq=0 running=ota_1 next=ota_0",
            "status": [
                "Status: Speed=0.000m/s, Target=0.000m/s, Voltage=12.1V, "
                "Control=Serial, SpeedMode=30%, Static=Yes",
                "IMU: BiasReady=Yes, LevelCal=Yes, GyroBias=0.0000,0.0000,0.0000, "
                "LevelOffset=0.0000,0.0000,0.0000",
            ],
        }

    def config(self, directory):
        return ota.UpdateConfig(
            response_timeout=0.02,
            reconnect_timeout=0.08,
            reconnect_interval=0.005,
            log_dir=directory / "logs",
        )

    @staticmethod
    def write_package_backup(directory, release):
        exported = ota.parse_vehicle_config_export_lines(vehicle_config_export_lines())
        path, _file_sha = ota._write_vehicle_config_backup(
            exported,
            release,
            directory / "backups",
        )
        return exported, path

    def run_restore_scenario(
        self,
        directory,
        *,
        initial_phase,
        initial_received,
        pending,
        initial_transaction_sha256=None,
    ):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        exported, backup_path = self.write_package_backup(directory, release)
        transaction_sha = ota.calculate_vehicle_restore_sha256(
            exported.backup_sha256,
            exported.source,
            exported.target,
        )
        if initial_transaction_sha256 == "exact":
            initial_transaction_sha256 = transaction_sha
        pending_sha = transaction_sha if pending else None
        state = {
            "phase": initial_phase,
            "received": initial_received,
            "source": exported.source if initial_phase != "EMPTY" else None,
            "backup": exported.backup_sha256 if initial_phase != "EMPTY" else None,
            "transaction": initial_transaction_sha256,
            "pending": pending_sha,
        }
        all_writes = []
        ready_identity = self.identity_lines("READY", motion=True, writes=True)

        def current_status():
            return config_import_status_lines(
                exported,
                phase=state["phase"],
                received=state["received"],
                source=state["source"],
                backup_sha256=state["backup"],
                transaction_sha256=state["transaction"],
                pending_transaction_sha256=state["pending"],
            )

        def handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in ready_identity:
                return ready_identity[command]
            if command == "config import status":
                return current_status()
            if command == "config import abort":
                state.update(
                    phase="EMPTY",
                    received=0,
                    source=None,
                    backup=None,
                    transaction=None,
                )
                required = "Yes" if state["pending"] else "No"
                return (
                    f"OK config import abort RecoveryRequired={required} "
                    f"reboot_required={required}"
                )
            if command.startswith("config import begin "):
                state.update(
                    phase="COLLECTING",
                    received=0,
                    source=exported.source,
                    backup=exported.backup_sha256,
                    transaction=None,
                )
                return config_import_begin_lines(exported)
            if command.startswith("config import item "):
                name = command.split()[3]
                expected = exported.items[state["received"]].name
                if name != expected:
                    return AssertionError(f"unexpected item {name}, expected {expected}")
                state["received"] += 1
                return (
                    f"OK config import item name={name} "
                    f"received={state['received']}/24"
                )
            if command == "config import validate":
                state.update(phase="VALIDATED", transaction=transaction_sha)
                return (
                    "OK config import validate items=24 "
                    f"BackupSHA={exported.backup_sha256[:12]} "
                    f"TransactionSHA={transaction_sha[:12]}"
                )
            if command == "config import apply":
                state.update(phase="APPLIED", pending=transaction_sha)
                return "OK config import apply readback_required=Yes reboot_required=Yes"
            if command == "config import readback":
                state.update(phase="READBACK_OK", pending=None)
                return (
                    "OK config import readback result=MATCH "
                    f"TransactionSHA={transaction_sha[:12]} reboot_required=Yes"
                )
            if command == "reset":
                return "INFO: rebooting..."
            return AssertionError(f"unexpected restore command: {command}")

        ready_export = vehicle_config_export_lines(
            source_project=T004_PROJECT_VERSION,
            target_project=T004_PROJECT_VERSION,
            items=ready_vehicle_config_items(exported.items),
        )

        def post_handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in ready_identity:
                return ready_identity[command]
            if command == "config export":
                return ready_export
            return AssertionError(f"unexpected post-restore command: {command}")

        output = []
        clock = FakeClock()
        result = ota.run_config_restore(
            backup_path,
            self.config(directory),
            serial_factory=SequenceFactory(
                [FakeSerial(handler, all_writes), FakeSerial(post_handler, all_writes)]
            ),
            input_func=lambda _prompt: "RESTORE",
            output_func=output.append,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        return result, all_writes, transaction_sha, output

    def test_installed_t004_backup_required_resume_never_sends_app_data(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        initial_export = vehicle_config_export_lines()
        ready_export = vehicle_config_export_lines(
            source_project=T004_PROJECT_VERSION,
            target_project=T004_PROJECT_VERSION,
            items=ready_vehicle_config_items(),
        )
        backup_sha256 = initial_export[3].rsplit("=", 1)[1]
        all_writes = []
        initial_identity = self.identity_lines("BACKUP_REQUIRED")
        ready_identity = self.identity_lines("READY", motion=True, writes=True)

        def initial_handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in initial_identity:
                return initial_identity[command]
            if command == "config export":
                return initial_export
            if command.startswith("config backup confirm "):
                return (
                    f"OK config backup confirmed BackupSHA={backup_sha256[:12]} "
                    "state=READY reboot_required=Yes"
                )
            if command == "reset":
                return "INFO: rebooting..."
            return AssertionError(f"unexpected initial command: {command}")

        def ready_handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in ready_identity:
                return ready_identity[command]
            if command == "config export":
                return ready_export
            return AssertionError(f"unexpected READY command: {command}")

        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            result = ota.run_managed_update(
                release,
                self.config(temp),
                backup_dir=temp / "backups",
                serial_factory=SequenceFactory(
                    [
                        FakeSerial(initial_handler, all_writes),
                        FakeSerial(ready_handler, all_writes),
                    ]
                ),
                input_func=lambda _prompt: "UPDATE",
                output_func=lambda _line: None,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
                resume_only=True,
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.pre_snapshot.profile.state, "BACKUP_REQUIRED")
        self.assertEqual(result.post_snapshot.profile.state, "READY")
        self.assertEqual(all_writes.count("config export"), 2)
        self.assertTrue(any(line.startswith("config backup confirm ") for line in all_writes))
        self.assertFalse(any(line.startswith("fw begin ") for line in all_writes))
        self.assertFalse(any(line.startswith("fw data ") for line in all_writes))
        self.assertNotIn("fw end", all_writes)

    def test_installed_t004_app_validation_failed_is_fail_closed_without_app_data(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        identity = self.identity_lines("APP_VALIDATION_FAILED")
        all_writes = []

        def handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in identity:
                return identity[command]
            return AssertionError(f"unexpected command: {command}")

        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            with self.assertRaises(ota.PostInstallError) as raised:
                ota.run_managed_update(
                    release,
                    self.config(temp),
                    serial_factory=SequenceFactory([FakeSerial(handler, all_writes)]),
                    input_func=lambda _prompt: self.fail("validation failure prompted for update"),
                    output_func=lambda _line: None,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                )

        self.assertEqual(raised.exception.stage, "app_validation")
        self.assertTrue(raised.exception.no_app_reflash)
        self.assertFalse(any(line.startswith("fw begin ") for line in all_writes))
        self.assertFalse(any(line.startswith("fw data ") for line in all_writes))
        self.assertNotIn("fw end", all_writes)
        self.assertNotIn("config export", all_writes)
        self.assertFalse(any(line.startswith("config backup confirm ") for line in all_writes))

    def test_fresh_t003_to_t004_managed_update_completes_app_backup_and_final_verify(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        all_writes = []
        pre_identity = self.identity_lines(
            "READY",
            project_version=T003_PROJECT_VERSION,
            motion=True,
            writes=True,
        )
        backup_identity = self.identity_lines("BACKUP_REQUIRED")
        ready_identity = self.identity_lines("READY", motion=True, writes=True)
        initial_export = vehicle_config_export_lines()
        ready_export = vehicle_config_export_lines(
            source_project=T004_PROJECT_VERSION,
            target_project=T004_PROJECT_VERSION,
            items=ready_vehicle_config_items(),
        )
        backup_sha = initial_export[3].rsplit("=", 1)[1]
        ota_state = {"written": 0, "next_seq": 0}

        def pre_handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in pre_identity:
                return pre_identity[command]
            if command.startswith("fw begin "):
                size = int(command.split()[2])
                self.assertEqual(size, release.app_size)
                return f"OK fw begin part=ota_1 size={size}"
            if command.startswith("fw data "):
                _fw, _data, seq_text, payload = command.split()
                seq = int(seq_text)
                self.assertEqual(seq, ota_state["next_seq"])
                ota_state["written"] += len(bytes.fromhex(payload))
                ota_state["next_seq"] += 1
                return f"OK fw data {seq} {ota_state['written']}"
            if command == "fw end":
                return "OK fw reboot"
            return AssertionError(f"unexpected pre-update command: {command}")

        def backup_handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in backup_identity:
                return backup_identity[command]
            if command == "config export":
                return initial_export
            if command.startswith("config backup confirm "):
                return (
                    f"OK config backup confirmed BackupSHA={backup_sha[:12]} "
                    "state=READY reboot_required=Yes"
                )
            if command == "reset":
                return "INFO: rebooting..."
            return AssertionError(f"unexpected backup command: {command}")

        def ready_handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in ready_identity:
                return ready_identity[command]
            if command == "config export":
                return ready_export
            return AssertionError(f"unexpected final command: {command}")

        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            result = ota.run_managed_update(
                release,
                self.config(temp),
                backup_dir=temp / "backups",
                serial_factory=SequenceFactory(
                    [
                        FakeSerial(pre_handler, all_writes),
                        FakeSerial(backup_handler, all_writes),
                        FakeSerial(ready_handler, all_writes),
                    ]
                ),
                input_func=lambda _prompt: "UPDATE",
                output_func=lambda _line: None,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(ota_state["written"], release.app_size)
        self.assertTrue(any(line.startswith("fw begin ") for line in all_writes))
        self.assertEqual(all_writes.count("fw end"), 1)
        self.assertEqual(all_writes.count("config export"), 2)
        self.assertTrue(any(line.startswith("config backup confirm ") for line in all_writes))

    def test_exact_t004_to_t005_transport_recovery_completes_without_pre_export(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t005.zip"
        )
        all_writes = []
        prompts = []
        output = []
        source_identity = self.identity_lines(
            "BACKUP_REQUIRED",
            project_version=T004_PROJECT_VERSION,
        )
        backup_identity = self.identity_lines(
            "BACKUP_REQUIRED",
            project_version=T005_PROJECT_VERSION,
        )
        ready_identity = self.identity_lines(
            "READY",
            project_version=T005_PROJECT_VERSION,
            motion=True,
            writes=True,
        )
        saved_items = ready_vehicle_config_items(
            offsets=("00000000", "00000000", "00000000")
        )
        refreshed_items = ready_vehicle_config_items(
            saved_items,
            offsets=("CDCCCC3D", "CDCCCCBD", "CDCC4C3E"),
        )
        post_app_export = vehicle_config_export_lines(
            source_project=T004_PROJECT_VERSION,
            target_project=T005_PROJECT_VERSION,
            items=saved_items,
        )
        ready_export = vehicle_config_export_lines(
            source_project=T005_PROJECT_VERSION,
            target_project=T005_PROJECT_VERSION,
            items=refreshed_items,
        )
        backup_sha = post_app_export[3].rsplit("=", 1)[1]
        ota_state = {"written": 0, "next_seq": 0}

        def source_handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in source_identity:
                return source_identity[command]
            if command.startswith("fw begin "):
                size = int(command.split()[2])
                self.assertEqual(size, release.app_size)
                return f"OK fw begin part=ota_1 size={size}"
            if command.startswith("fw data "):
                _fw, _data, seq_text, payload = command.split()
                seq = int(seq_text)
                self.assertEqual(seq, ota_state["next_seq"])
                ota_state["written"] += len(bytes.fromhex(payload))
                ota_state["next_seq"] += 1
                return f"OK fw data {seq} {ota_state['written']}"
            if command == "fw end":
                return "OK fw reboot"
            return AssertionError(f"unexpected transport-recovery command: {command}")

        def backup_handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in backup_identity:
                return backup_identity[command]
            if command == "config export":
                return post_app_export
            if command.startswith("config backup confirm "):
                return (
                    f"OK config backup confirmed BackupSHA={backup_sha[:12]} "
                    "state=READY reboot_required=Yes"
                )
            if command == "reset":
                return "INFO: rebooting..."
            return AssertionError(f"unexpected T005 backup command: {command}")

        def ready_handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in ready_identity:
                return ready_identity[command]
            if command == "config export":
                return ready_export
            return AssertionError(f"unexpected T005 READY command: {command}")

        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            result = ota.run_managed_update(
                release,
                self.config(temp),
                backup_dir=temp / "backups",
                serial_factory=SequenceFactory(
                    [
                        FakeSerial(source_handler, all_writes),
                        FakeSerial(backup_handler, all_writes),
                        FakeSerial(ready_handler, all_writes),
                    ]
                ),
                input_func=lambda prompt: prompts.append(prompt) or "UPDATE",
                output_func=output.append,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            records = [json.loads(line) for line in result.audit_path.read_text().splitlines()]

        self.assertEqual(result.status, "success")
        self.assertEqual(result.pre_snapshot.version.project_version, T004_PROJECT_VERSION)
        self.assertEqual(result.post_snapshot.version.project_version, T005_PROJECT_VERSION)
        self.assertEqual(ota_state["written"], release.app_size)
        self.assertEqual(all_writes.count("fw end"), 1)
        self.assertEqual(all_writes.count("config export"), 2)
        self.assertLess(all_writes.index("fw end"), all_writes.index("config export"))
        self.assertIn("no logical backup exists before this update", prompts[0])
        rendered = "\n".join(output)
        self.assertIn("T004 configuration export is unreliable", rendered)
        self.assertIn("no backup will be fabricated", rendered)
        self.assertIn("physically preserved by the App-only OTA", rendered)
        self.assertIn("Non-level config MATCH (20 items)", rendered)
        self.assertIn("Level calibration init UNCHANGED", rendered)
        self.assertIn("Level calibration offsets REFRESHED", rendered)
        self.assertIn("(retained)", rendered)
        comparison = next(
            record for record in records if record["step"] == "config_semantic_compare"
        )
        self.assertEqual(comparison["non_level_item_count"], 20)
        self.assertTrue(comparison["non_level_match"])
        self.assertEqual(comparison["level_init_status"], "unchanged")
        self.assertEqual(comparison["level_offset_status"], "refreshed")
        self.assertEqual(
            comparison["changed_level_fields"],
            list(ota.LEVEL_CALIBRATION_OFFSET_FIELDS),
        )
        self.assertNotIn("level_offset_values", comparison)

    def test_t005_transport_recovery_rejects_wrong_identity_before_fw_begin(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t005.zip"
        )
        cases = {
            "state": self.identity_lines(
                "READY",
                project_version=T004_PROJECT_VERSION,
                motion=True,
                writes=True,
            ),
            "version": self.identity_lines(
                "BACKUP_REQUIRED",
                project_version="OSRF-C03-T004-wrong",
            ),
            "profile": self.identity_lines(
                "BACKUP_REQUIRED",
                project_version=T004_PROJECT_VERSION,
                profile_id="other",
            ),
            "schema": self.identity_lines(
                "BACKUP_REQUIRED",
                project_version=T004_PROJECT_VERSION,
                schema=2,
            ),
            "protocol": self.identity_lines(
                "BACKUP_REQUIRED",
                project_version=T004_PROJECT_VERSION,
                protocol="1.0",
            ),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            for label, identity in cases.items():
                with self.subTest(label=label):
                    writes = []

                    def handler(command):
                        if command in {"v 0.00 0.00", "stream off"}:
                            return []
                        if command in identity:
                            return identity[command]
                        return AssertionError(f"unexpected T005 preflight command: {command}")

                    clock = FakeClock()
                    with self.assertRaisesRegex(
                        ota.DevicePreflightError,
                        "T005 transport recovery requires",
                    ):
                        ota.run_managed_update(
                            release,
                            replace(self.config(temp), log_dir=temp / f"logs-t005-{label}"),
                            serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                            input_func=lambda _prompt: self.fail("invalid recovery prompted"),
                            output_func=lambda _line: None,
                            monotonic=clock.monotonic,
                            sleep=clock.sleep,
                        )
                    self.assertFalse(any(line.startswith("fw begin ") for line in writes))
                    self.assertFalse(any(line.startswith("fw data ") for line in writes))
                    self.assertNotIn("fw end", writes)
                    self.assertNotIn("config export", writes)

    def test_t005_transport_recovery_rejects_non_exact_package_before_serial(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t005.zip"
        )
        cases = {
            "package": replace(release, package_sha256="0" * 64),
            "manifest": replace(release, manifest_sha256="0" * 64),
            "app": replace(release, app_sha256="0" * 64),
            "tree": replace(release, source_tree_sha256="0" * 64),
            "bootstrap": replace(release, bootstrap_source_project_version="wrong"),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            for label, invalid_release in cases.items():
                with self.subTest(label=label):
                    factory = SequenceFactory(
                        [AssertionError("non-exact T005 package must fail before serial")]
                    )
                    with self.assertRaisesRegex(
                        ota.PackageValidationError,
                        "approved exact package asset|ProjectVer is not bound",
                    ):
                        ota.run_managed_update(
                            invalid_release,
                            replace(self.config(temp), log_dir=temp / f"logs-package-{label}"),
                            serial_factory=factory,
                            input_func=lambda _prompt: self.fail("invalid package prompted"),
                            output_func=lambda _line: None,
                        )
                    self.assertEqual(factory.calls, [])

    def test_t005_begin_rejection_is_safe_to_rerun_update(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t005.zip"
        )
        identity = self.identity_lines(
            "BACKUP_REQUIRED",
            project_version=T004_PROJECT_VERSION,
        )
        writes = []
        output = []

        def handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in identity:
                return identity[command]
            if command.startswith("fw begin "):
                return "ERROR fw low_voltage"
            return AssertionError(f"unexpected begin-rejection command: {command}")

        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            with self.assertRaises(ota.DeviceRejectedError) as raised:
                ota.run_managed_update(
                    release,
                    self.config(temp),
                    serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                    input_func=lambda _prompt: "UPDATE",
                    output_func=output.append,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                )
            records = [
                json.loads(line)
                for line in raised.exception.audit_path.read_text().splitlines()
            ]

        self.assertEqual(raised.exception.stage, "fw begin")
        self.assertEqual(raised.exception.device_reason, "low_voltage")
        self.assertFalse(raised.exception.no_app_reflash)
        self.assertFalse(records[-1]["no_app_reflash"])
        self.assertFalse(records[-1]["action_required"])
        self.assertFalse(records[-1]["begin_delivery_unknown"])
        self.assertEqual(records[-1]["app_data_committed_bytes"], 0)
        self.assertTrue(any(line.startswith("fw begin ") for line in writes))
        self.assertFalse(any(line.startswith("fw data ") for line in writes))
        self.assertNotIn("fw end", writes)
        self.assertNotIn("fw abort", writes)
        rendered = "\n".join(output)
        self.assertIn("not started; zero App data committed", rendered)
        self.assertIn("NVS", rendered)
        self.assertIn("unchanged by this command", rendered)
        self.assertIn("fix the device condition, then rerun update", rendered)
        self.assertIn("resume does not apply to T004", rendered)

    def test_t005_first_data_rejection_with_confirmed_abort_is_safe_to_rerun(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t005.zip"
        )
        identity = self.identity_lines(
            "BACKUP_REQUIRED",
            project_version=T004_PROJECT_VERSION,
        )
        writes = []
        output = []

        def handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in identity:
                return identity[command]
            if command.startswith("fw begin "):
                return f"OK fw begin part=ota_1 size={release.app_size}"
            if command.startswith("fw data 0 "):
                return "ERROR fw write_blocked"
            if command == "fw abort":
                return "OK fw abort"
            return AssertionError(f"unexpected data-rejection command: {command}")

        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            with self.assertRaises(ota.DeviceRejectedError) as raised:
                ota.run_managed_update(
                    release,
                    self.config(temp),
                    serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                    input_func=lambda _prompt: "UPDATE",
                    output_func=output.append,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                )
            records = [
                json.loads(line)
                for line in raised.exception.audit_path.read_text().splitlines()
            ]

        self.assertEqual(raised.exception.stage, "fw data 0")
        self.assertEqual(raised.exception.device_reason, "write_blocked")
        self.assertFalse(raised.exception.no_app_reflash)
        self.assertFalse(records[-1]["no_app_reflash"])
        self.assertFalse(records[-1]["action_required"])
        self.assertTrue(records[-1]["abort_acknowledged"])
        self.assertFalse(records[-1]["data_delivery_unknown"])
        self.assertEqual(records[-1]["app_data_committed_bytes"], 0)
        self.assertEqual(sum(line.startswith("fw data ") for line in writes), 1)
        self.assertIn("fw abort", writes)
        self.assertNotIn("fw end", writes)
        self.assertNotIn("config export", writes)
        rendered = "\n".join(output)
        self.assertIn("not started; zero App data committed", rendered)
        self.assertIn("unchanged by this command", rendered)
        self.assertIn("fix the device condition, then rerun update", rendered)

    def test_t005_begin_delivery_uncertainty_still_forbids_reflash(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t005.zip"
        )
        identity = self.identity_lines(
            "BACKUP_REQUIRED",
            project_version=T004_PROJECT_VERSION,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            for label in ("write", "timeout", "malformed"):
                with self.subTest(label=label):
                    writes = []
                    output = []

                    def handler(command):
                        if command in {"v 0.00 0.00", "stream off"}:
                            return []
                        if command in identity:
                            return identity[command]
                        if command.startswith("fw begin "):
                            return [] if label == "timeout" else "OK fw begin malformed"
                        return AssertionError(f"unexpected begin-uncertainty command: {command}")

                    serial = (
                        BeginWriteFailureSerial(handler, writes)
                        if label == "write"
                        else FakeSerial(handler, writes)
                    )
                    clock = FakeClock()
                    with self.assertRaises(ota.FirmwareUpdateError) as raised:
                        ota.run_managed_update(
                            release,
                            replace(
                                self.config(temp),
                                log_dir=temp / f"logs-begin-uncertain-{label}",
                            ),
                            serial_factory=SequenceFactory([serial]),
                            input_func=lambda _prompt: "UPDATE",
                            output_func=output.append,
                            monotonic=clock.monotonic,
                            sleep=clock.sleep,
                        )
                    records = [
                        json.loads(line)
                        for line in raised.exception.audit_path.read_text().splitlines()
                    ]
                    self.assertTrue(raised.exception.no_app_reflash)
                    self.assertTrue(records[-1]["no_app_reflash"])
                    self.assertTrue(records[-1]["action_required"])
                    self.assertTrue(records[-1]["begin_delivery_unknown"])
                    self.assertEqual(records[-1]["app_data_committed_bytes"], 0)
                    self.assertFalse(any(line.startswith("fw data ") for line in writes))
                    self.assertNotIn("fw end", writes)
                    self.assertIn("do not reflash", "\n".join(output))

    def test_t005_keyboard_interrupt_records_abort_success_and_failure(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t005.zip"
        )
        identity = self.identity_lines(
            "BACKUP_REQUIRED",
            project_version=T004_PROJECT_VERSION,
        )

        class InterruptBeforeFirstDataRenderer(ota.ConsoleRenderer):
            def progress(self, _written, _total):
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            for abort_ok in (True, False):
                with self.subTest(abort_ok=abort_ok):
                    writes = []
                    output = []

                    def handler(command):
                        if command in {"v 0.00 0.00", "stream off"}:
                            return []
                        if command in identity:
                            return identity[command]
                        if command.startswith("fw begin "):
                            return f"OK fw begin part=ota_1 size={release.app_size}"
                        if command == "fw abort":
                            return "OK fw abort" if abort_ok else []
                        return AssertionError(f"unexpected interrupt command: {command}")

                    clock = FakeClock()
                    renderer = InterruptBeforeFirstDataRenderer(
                        output.append,
                        monotonic=clock.monotonic,
                    )
                    with self.assertRaises(ota.UpdateInterruptedError) as raised:
                        ota.run_managed_update(
                            release,
                            replace(
                                self.config(temp),
                                log_dir=temp / f"logs-interrupt-{abort_ok}",
                            ),
                            serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                            input_func=lambda _prompt: "UPDATE",
                            output_func=output.append,
                            renderer=renderer,
                            monotonic=clock.monotonic,
                            sleep=clock.sleep,
                        )
                    records = [
                        json.loads(line)
                        for line in raised.exception.audit_path.read_text().splitlines()
                    ]
                    self.assertEqual(records[-1]["abort_acknowledged"], abort_ok)
                    self.assertEqual(records[-1]["no_app_reflash"], not abort_ok)
                    self.assertEqual(records[-1]["action_required"], not abort_ok)
                    self.assertEqual(raised.exception.no_app_reflash, not abort_ok)
                    self.assertEqual(records[-1]["app_data_committed_bytes"], 0)
                    self.assertFalse(records[-1]["data_delivery_unknown"])
                    self.assertIn("fw abort", writes)
                    self.assertFalse(any(line.startswith("fw data ") for line in writes))
                    if abort_ok:
                        self.assertIn("rerun update when ready", "\n".join(output))
                    else:
                        self.assertIn("do not reflash", "\n".join(output))

    def test_t005_data_timeout_status_error_remains_delivery_unknown_after_abort(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t005.zip"
        )
        identity = self.identity_lines(
            "BACKUP_REQUIRED",
            project_version=T004_PROJECT_VERSION,
        )
        writes = []
        output = []

        def handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command == "fw status" and any(
                line.startswith("fw data ") for line in writes
            ):
                return "ERROR fw status_failed"
            if command in identity:
                return identity[command]
            if command.startswith("fw begin "):
                return f"OK fw begin part=ota_1 size={release.app_size}"
            if command.startswith("fw data 0 "):
                return []
            if command == "fw abort":
                return "OK fw abort"
            return AssertionError(f"unexpected status-error command: {command}")

        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            with self.assertRaises(ota.DeviceRejectedError) as raised:
                ota.run_managed_update(
                    release,
                    self.config(temp),
                    serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                    input_func=lambda _prompt: "UPDATE",
                    output_func=output.append,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                )
            records = [
                json.loads(line)
                for line in raised.exception.audit_path.read_text().splitlines()
            ]

        self.assertEqual(raised.exception.stage, "fw status")
        self.assertTrue(raised.exception.no_app_reflash)
        self.assertTrue(records[-1]["no_app_reflash"])
        self.assertTrue(records[-1]["action_required"])
        self.assertTrue(records[-1]["abort_acknowledged"])
        self.assertTrue(records[-1]["data_delivery_unknown"])
        self.assertEqual(records[-1]["app_data_committed_bytes"], 0)
        self.assertIn("fw abort", writes)
        self.assertNotIn("fw end", writes)
        self.assertIn("do not reflash", "\n".join(output))

    def test_installed_t005_backup_required_resume_never_reflashes(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t005.zip"
        )
        initial_export = vehicle_config_export_lines(
            source_project=T004_PROJECT_VERSION,
            target_project=T005_PROJECT_VERSION,
        )
        ready_export = vehicle_config_export_lines(
            source_project=T005_PROJECT_VERSION,
            target_project=T005_PROJECT_VERSION,
            items=ready_vehicle_config_items(),
        )
        backup_sha = initial_export[3].rsplit("=", 1)[1]
        all_writes = []
        initial_identity = self.identity_lines(
            "BACKUP_REQUIRED",
            project_version=T005_PROJECT_VERSION,
        )
        ready_identity = self.identity_lines(
            "READY",
            project_version=T005_PROJECT_VERSION,
            motion=True,
            writes=True,
        )

        def initial_handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in initial_identity:
                return initial_identity[command]
            if command == "config export":
                return initial_export
            if command.startswith("config backup confirm "):
                return (
                    f"OK config backup confirmed BackupSHA={backup_sha[:12]} "
                    "state=READY reboot_required=Yes"
                )
            if command == "reset":
                return "INFO: rebooting..."
            return AssertionError(f"unexpected installed T005 command: {command}")

        def ready_handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in ready_identity:
                return ready_identity[command]
            if command == "config export":
                return ready_export
            return AssertionError(f"unexpected installed T005 READY command: {command}")

        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            result = ota.run_managed_update(
                release,
                self.config(temp),
                backup_dir=temp / "backups",
                serial_factory=SequenceFactory(
                    [
                        FakeSerial(initial_handler, all_writes),
                        FakeSerial(ready_handler, all_writes),
                    ]
                ),
                input_func=lambda _prompt: "UPDATE",
                output_func=lambda _line: None,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
                resume_only=True,
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.pre_snapshot.version.project_version, T005_PROJECT_VERSION)
        self.assertEqual(result.post_snapshot.profile.state, "READY")
        self.assertEqual(all_writes.count("config export"), 2)
        self.assertFalse(any(line.startswith("fw begin ") for line in all_writes))
        self.assertFalse(any(line.startswith("fw data ") for line in all_writes))
        self.assertNotIn("fw end", all_writes)

    def test_t005_transport_recovery_fw_end_uncertainty_forbids_reflash(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t005.zip"
        )
        identity = self.identity_lines(
            "BACKUP_REQUIRED",
            project_version=T004_PROJECT_VERSION,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            for label in ("write", "flush"):
                with self.subTest(label=label):
                    writes = []
                    output = []
                    state = {"written": 0, "seq": 0}

                    def handler(command):
                        if command in {"v 0.00 0.00", "stream off"}:
                            return []
                        if command in identity:
                            return identity[command]
                        if command.startswith("fw begin "):
                            return f"OK fw begin part=ota_1 size={release.app_size}"
                        if command.startswith("fw data "):
                            _fw, _data, seq_text, payload = command.split()
                            seq = int(seq_text)
                            self.assertEqual(seq, state["seq"])
                            state["written"] += len(bytes.fromhex(payload))
                            state["seq"] += 1
                            return f"OK fw data {seq} {state['written']}"
                        if command == "fw end":
                            return "OK fw reboot"
                        return AssertionError(f"unexpected T005 uncertainty command: {command}")

                    serial = (
                        EndWriteFailureSerial(handler, writes, mode="error")
                        if label == "write"
                        else EndFlushFailureSerial(handler, writes)
                    )
                    clock = FakeClock()
                    with self.assertRaises(ota.SerialCommunicationError) as raised:
                        ota.run_managed_update(
                            release,
                            replace(
                                self.config(temp),
                                chunk_size=384,
                                log_dir=temp / f"logs-t005-end-{label}",
                            ),
                            serial_factory=SequenceFactory([serial]),
                            input_func=lambda _prompt: "UPDATE",
                            output_func=output.append,
                            monotonic=clock.monotonic,
                            sleep=clock.sleep,
                        )
                    self.assertEqual(state["written"], release.app_size)
                    self.assertTrue(raised.exception.no_app_reflash)
                    self.assertIn("fw end", writes)
                    self.assertNotIn("fw abort", writes)
                    self.assertIn("delivery unknown; do not reflash", "\n".join(output))

    def test_t005_post_app_export_timeout_is_action_required_without_fake_backup(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t005.zip"
        )
        source_identity = self.identity_lines(
            "BACKUP_REQUIRED",
            project_version=T004_PROJECT_VERSION,
        )
        target_identity = self.identity_lines(
            "BACKUP_REQUIRED",
            project_version=T005_PROJECT_VERSION,
        )
        writes = []
        output = []
        state = {"written": 0, "seq": 0}

        def source_handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in source_identity:
                return source_identity[command]
            if command.startswith("fw begin "):
                return f"OK fw begin part=ota_1 size={release.app_size}"
            if command.startswith("fw data "):
                _fw, _data, seq_text, payload = command.split()
                seq = int(seq_text)
                self.assertEqual(seq, state["seq"])
                state["written"] += len(bytes.fromhex(payload))
                state["seq"] += 1
                return f"OK fw data {seq} {state['written']}"
            if command == "fw end":
                return "OK fw reboot"
            return AssertionError(f"unexpected T005 source command: {command}")

        def target_handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in target_identity:
                return target_identity[command]
            if command == "config export":
                return []
            return AssertionError(f"unexpected T005 timeout command: {command}")

        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            backup_dir = temp / "backups"
            with self.assertRaises(ota.ResponseTimeoutError) as raised:
                ota.run_managed_update(
                    release,
                    replace(self.config(temp), chunk_size=384),
                    backup_dir=backup_dir,
                    serial_factory=SequenceFactory(
                        [FakeSerial(source_handler, writes), FakeSerial(target_handler, writes)]
                    ),
                    input_func=lambda _prompt: "UPDATE",
                    output_func=output.append,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                )
            self.assertFalse(backup_dir.exists())
            records = [
                json.loads(line)
                for line in raised.exception.audit_path.read_text().splitlines()
            ]
        self.assertTrue(raised.exception.no_app_reflash)
        self.assertTrue(records[-1]["app_write_completed"])
        self.assertTrue(records[-1]["action_required"])
        self.assertEqual(writes.count("config export"), 1)
        self.assertIn("Backup file", "\n".join(output))
        self.assertIn("not created", "\n".join(output))

    def test_wrong_bootstrap_identity_or_package_source_is_rejected_before_fw_begin(self):
        base_release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        cases = {
            "project": (
                base_release,
                self.identity_lines(
                    "READY",
                    project_version="OSRF-C03-T003-wrongsource",
                    motion=True,
                    writes=True,
                ),
            ),
            "profile": (
                base_release,
                self.identity_lines(
                    "READY",
                    project_version=T003_PROJECT_VERSION,
                    profile_id="other",
                    motion=True,
                    writes=True,
                ),
            ),
            "schema": (
                base_release,
                self.identity_lines(
                    "READY",
                    project_version=T003_PROJECT_VERSION,
                    schema=2,
                    motion=True,
                    writes=True,
                ),
            ),
            "protocol": (
                base_release,
                self.identity_lines(
                    "READY",
                    project_version=T003_PROJECT_VERSION,
                    protocol="1.0",
                    motion=True,
                    writes=True,
                ),
            ),
            "package_bootstrap": (
                replace(
                    base_release,
                    bootstrap_source_project_version="OSRF-C03-T003-otherpackage",
                ),
                self.identity_lines(
                    "READY",
                    project_version=T003_PROJECT_VERSION,
                    motion=True,
                    writes=True,
                ),
            ),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            for label, (release, identity) in cases.items():
                with self.subTest(label=label):
                    writes = []

                    def handler(command):
                        if command in {"v 0.00 0.00", "stream off"}:
                            return []
                        if command in identity:
                            return identity[command]
                        return AssertionError(f"unexpected preflight command: {command}")

                    clock = FakeClock()
                    with self.assertRaises(ota.DevicePreflightError):
                        ota.run_managed_update(
                            release,
                            replace(self.config(temp), log_dir=temp / f"logs-{label}"),
                            serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                            input_func=lambda _prompt: self.fail("invalid bootstrap prompted"),
                            output_func=lambda _line: None,
                            monotonic=clock.monotonic,
                            sleep=clock.sleep,
                        )
                    self.assertFalse(any(line.startswith("fw begin ") for line in writes))
                    self.assertFalse(any(line.startswith("fw data ") for line in writes))

    def test_explicit_backup_is_never_ignored_for_unsupported_managed_states(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        cases = {
            "fresh": self.identity_lines(
                "READY",
                project_version=T003_PROJECT_VERSION,
                motion=True,
                writes=True,
            ),
            "backup_required": self.identity_lines("BACKUP_REQUIRED"),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            for label, identity in cases.items():
                with self.subTest(label=label):
                    writes = []

                    def handler(command):
                        if command in {"v 0.00 0.00", "stream off"}:
                            return []
                        if command in identity:
                            return identity[command]
                        return AssertionError(f"unexpected backup-option command: {command}")

                    clock = FakeClock()
                    with self.assertRaisesRegex(
                        ota.DevicePreflightError,
                        "--backup is accepted only",
                    ):
                        ota.run_managed_update(
                            release,
                            replace(self.config(temp), log_dir=temp / f"logs-backup-{label}"),
                            resume_backup_path=temp / "must-not-be-ignored.json",
                            serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                            input_func=lambda _prompt: self.fail("unsupported backup prompted"),
                            output_func=lambda _line: None,
                            monotonic=clock.monotonic,
                            sleep=clock.sleep,
                            resume_only=label == "backup_required",
                        )
                    self.assertFalse(
                        any(
                            line.startswith(("fw begin ", "fw data "))
                            or line in {"fw end", "fw abort"}
                            for line in writes
                        )
                    )
                    self.assertNotIn("config export", writes)
                    self.assertFalse(any(line.startswith("config backup") for line in writes))

    def test_legacy_app_ota_rejects_backup_options_before_serial_open(self):
        package = REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t002.zip"
        parser = ota.build_parser()
        resume_help = parser._subparsers._group_actions[0].choices["resume"].format_help()
        self.assertIn("READY/Yes/Yes read-only verification", resume_help)

        with tempfile.TemporaryDirectory() as temp_dir:
            for option in ("--backup", "--backup-dir"):
                with self.subTest(option=option):
                    serial_factory = SequenceFactory(
                        [AssertionError("legacy backup option must fail before serial")]
                    )
                    stderr = io.StringIO()
                    with contextlib.redirect_stderr(stderr):
                        result = ota.main(
                            [
                                "app-ota",
                                "--package",
                                str(package),
                                option,
                                str(Path(temp_dir) / "unused"),
                            ],
                            serial_factory=serial_factory,
                        )
                    self.assertNotEqual(result, 0)
                    self.assertIn("legacy app-ota does not use", stderr.getvalue())
                    self.assertEqual(serial_factory.calls, [])

    def test_managed_fw_end_write_or_flush_uncertainty_forbids_reflash(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        identity = self.identity_lines(
            "READY",
            project_version=T003_PROJECT_VERSION,
            motion=True,
            writes=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            for label in ("write", "flush"):
                with self.subTest(label=label):
                    writes = []
                    output = []
                    state = {"written": 0, "seq": 0}

                    def handler(command):
                        if command in {"v 0.00 0.00", "stream off"}:
                            return []
                        if command in identity:
                            return identity[command]
                        if command.startswith("fw begin "):
                            return f"OK fw begin part=ota_1 size={release.app_size}"
                        if command.startswith("fw data "):
                            _fw, _data, seq_text, payload = command.split()
                            seq = int(seq_text)
                            self.assertEqual(seq, state["seq"])
                            state["written"] += len(bytes.fromhex(payload))
                            state["seq"] += 1
                            return f"OK fw data {seq} {state['written']}"
                        if command == "fw end":
                            return "OK fw reboot"
                        return AssertionError(f"unexpected uncertainty command: {command}")

                    if label == "write":
                        serial = EndWriteFailureSerial(handler, writes, mode="error")
                    else:
                        serial = EndFlushFailureSerial(handler, writes)
                    clock = FakeClock()
                    with self.assertRaises(ota.SerialCommunicationError) as raised:
                        ota.run_managed_update(
                            release,
                            replace(self.config(temp), log_dir=temp / f"logs-end-{label}"),
                            serial_factory=SequenceFactory([serial]),
                            input_func=lambda _prompt: "UPDATE",
                            output_func=output.append,
                            monotonic=clock.monotonic,
                            sleep=clock.sleep,
                        )
                    self.assertEqual(state["written"], release.app_size)
                    self.assertTrue(raised.exception.no_app_reflash)
                    self.assertIn("fw end", writes)
                    self.assertNotIn("fw abort", writes)
                    self.assertIn("delivery unknown; do not reflash", "\n".join(output))
                    records = [
                        json.loads(line)
                        for line in raised.exception.audit_path.read_text().splitlines()
                    ]
                    self.assertTrue(records[-1]["app_delivery_unknown"])
                    self.assertTrue(records[-1]["action_required"])

    def test_ready_resume_with_exact_backup_is_read_only_verification(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        identity = self.identity_lines("READY", motion=True, writes=True)
        all_writes = []
        output = []
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            exported, backup_path = self.write_package_backup(temp, release)
            ready_export = vehicle_config_export_lines(
                source_project=T004_PROJECT_VERSION,
                target_project=T004_PROJECT_VERSION,
                items=ready_vehicle_config_items(exported.items),
            )

            def handler(command):
                if command in {"v 0.00 0.00", "stream off"}:
                    return []
                if command in identity:
                    return identity[command]
                if command == "config export":
                    return ready_export
                return AssertionError(f"unexpected verification command: {command}")

            result = ota.run_managed_update(
                release,
                self.config(temp),
                resume_backup_path=backup_path,
                serial_factory=SequenceFactory([FakeSerial(handler, all_writes)]),
                input_func=lambda _prompt: self.fail("read-only verification prompted"),
                output_func=output.append,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
                resume_only=True,
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(all_writes.count("config export"), 1)
        rendered = "\n".join(output)
        self.assertIn("Non-level config MATCH (20 items)", rendered)
        self.assertIn("Level calibration init REFRESHED", rendered)
        self.assertIn("Level calibration offsets REFRESHED", rendered)
        self.assertIn("(retained)", rendered)
        forbidden = ("fw begin", "fw data", "fw end", "config import", "config backup", "reset")
        self.assertFalse(any(line.startswith(forbidden) for line in all_writes))

    def test_ready_without_backup_update_skips_but_resume_fails_closed(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        identity = self.identity_lines("READY", motion=True, writes=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            for command, resume_only in (("update", False), ("resume", True)):
                with self.subTest(command=command):
                    writes = []
                    output = []

                    def handler(line):
                        if line in {"v 0.00 0.00", "stream off"}:
                            return []
                        if line in identity:
                            return identity[line]
                        return AssertionError(f"unexpected READY command: {line}")

                    clock = FakeClock()
                    call = lambda: ota.run_managed_update(
                        release,
                        replace(self.config(temp), log_dir=temp / f"logs-ready-{command}"),
                        serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                        input_func=lambda _prompt: self.fail("READY without backup prompted"),
                        output_func=output.append,
                        monotonic=clock.monotonic,
                        sleep=clock.sleep,
                        resume_only=resume_only,
                    )
                    if resume_only:
                        with self.assertRaisesRegex(
                            ota.DevicePreflightError,
                            "READY state with the exact --backup",
                        ):
                            call()
                    else:
                        self.assertEqual(call().status, "skipped")
                        rendered = "\n".join(output)
                        self.assertIn("NVS writes      none by this command", rendered)
                        self.assertIn("NOT VERIFIED; exact --backup not provided", rendered)
                        self.assertIn("not evaluated", rendered)
                    self.assertNotIn("config export", writes)
                    self.assertFalse(any(line.startswith("fw begin ") for line in writes))

    def test_config_verify_match_and_mismatch_have_explicit_results(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        identity = self.identity_lines("READY", motion=True, writes=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            exported, backup_path = self.write_package_backup(temp, release)
            changed = list(exported.items)
            changed[3] = ota.VehicleConfigItem(
                changed[3].name,
                "SET",
                changed[3].value_type,
                "255",
            )
            cases = (
                ("match", exported.items, True),
                ("mismatch", tuple(changed), False),
            )
            for label, items, succeeds in cases:
                with self.subTest(label=label):
                    output = []
                    writes = []
                    current_export = vehicle_config_export_lines(
                        source_project=T004_PROJECT_VERSION,
                        target_project=T004_PROJECT_VERSION,
                        items=ready_vehicle_config_items(items),
                    )

                    def handler(command):
                        if command in {"v 0.00 0.00", "stream off"}:
                            return []
                        if command in identity:
                            return identity[command]
                        if command == "config export":
                            return current_export
                        return AssertionError(f"unexpected verify command: {command}")

                    clock = FakeClock()
                    call = lambda: ota.run_config_verify(
                        backup_path,
                        replace(self.config(temp), log_dir=temp / f"logs-{label}"),
                        serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                        output_func=output.append,
                        monotonic=clock.monotonic,
                        sleep=clock.sleep,
                    )
                    if succeeds:
                        self.assertTrue(call())
                        rendered = "\n".join(output)
                        self.assertIn("Non-level config MATCH (20 items)", rendered)
                        self.assertIn("Level calibration init REFRESHED", rendered)
                        self.assertIn("Level calibration offsets REFRESHED", rendered)
                    else:
                        with self.assertRaises(ota.PostInstallError):
                            call()
                        rendered = "\n".join(output)
                        self.assertIn("RESULT: ACTION REQUIRED", rendered)
                        self.assertIn("Non-level config MISMATCH", rendered)
                        self.assertIn("operator decision required", rendered)
                        self.assertIn("only then run config restore", rendered)
                    self.assertFalse(any(line.startswith("config import") for line in writes))

    def test_config_verify_rejects_wrong_ready_export_identity_before_item_result(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        identity = self.identity_lines("READY", motion=True, writes=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            _exported, backup_path = self.write_package_backup(temp, release)
            wrong_export = vehicle_config_export_lines(
                source_project=T003_PROJECT_VERSION,
                target_project=T004_PROJECT_VERSION,
                items=ready_vehicle_config_items(),
            )
            writes = []

            def handler(command):
                if command in {"v 0.00 0.00", "stream off"}:
                    return []
                if command in identity:
                    return identity[command]
                if command == "config export":
                    return wrong_export
                return AssertionError(f"unexpected identity command: {command}")

            clock = FakeClock()
            with self.assertRaisesRegex(ota.ProtocolError, "READY configuration export source"):
                ota.run_config_verify(
                    backup_path,
                    self.config(temp),
                    serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                    output_func=lambda _line: None,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                )
        self.assertFalse(any(line.startswith("config import") for line in writes))

    def test_config_verify_waits_for_level_calibration_before_export(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        identity = self.identity_lines("READY", motion=True, writes=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            _exported, backup_path = self.write_package_backup(temp, release)
            writes = []

            def handler(command):
                if command in {"v 0.00 0.00", "stream off"}:
                    return []
                if command == "status":
                    return [
                        "Status: Speed=0.000m/s, Target=0.000m/s, Voltage=12.1V, "
                        "Control=Serial, SpeedMode=30%, Static=Yes",
                        "IMU: BiasReady=Yes, LevelCal=No, "
                        "GyroBias=0.0000,0.0000,0.0000, "
                        "LevelOffset=0.0000,0.0000,0.0000",
                    ]
                if command in identity:
                    return identity[command]
                return AssertionError(f"unexpected calibration command: {command}")

            clock = FakeClock()
            with self.assertRaises(ota.PostInstallError) as raised:
                ota.run_config_verify(
                    backup_path,
                    replace(
                        self.config(temp),
                        level_calibration_timeout=0.21,
                        level_calibration_interval=0.2,
                    ),
                    serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                    output_func=lambda _line: None,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                )
        self.assertEqual(raised.exception.stage, "level_calibration")
        self.assertNotIn("config export", writes)

    def test_config_backup_rejects_export_target_not_matching_running_device(self):
        identity = self.identity_lines("READY", motion=True, writes=True)
        wrong_export = vehicle_config_export_lines(
            source_project=T003_PROJECT_VERSION,
            target_project=T003_PROJECT_VERSION,
        )
        writes = []
        output = []

        def handler(command):
            if command in {"v 0.00 0.00", "stream off"}:
                return []
            if command in identity:
                return identity[command]
            if command == "config export":
                return wrong_export
            return AssertionError(f"unexpected backup command: {command}")

        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            with self.assertRaisesRegex(ota.ProtocolError, "running device identity"):
                ota.run_config_backup(
                    self.config(temp),
                    backup_dir=temp / "backups",
                    serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                    output_func=output.append,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                )
        self.assertIn("RESULT: ACTION REQUIRED", "\n".join(output))
        self.assertFalse(any(line.startswith("config import") for line in writes))

    def test_restore_normal_full_transaction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result, writes, transaction_sha, output = self.run_restore_scenario(
                Path(temp_dir),
                initial_phase="EMPTY",
                initial_received=0,
                pending=False,
            )
        self.assertTrue(result)
        self.assertEqual(sum(line.startswith("config import item ") for line in writes), 24)
        self.assertIn("config import apply", writes)
        self.assertIn("config import readback", writes)
        self.assertNotEqual(transaction_sha, "0" * 64)
        rendered = "\n".join(output)
        self.assertIn("Non-level config MATCH (20 items)", rendered)
        self.assertIn("Level calibration init REFRESHED", rendered)
        self.assertIn("Level calibration offsets REFRESHED", rendered)
        self.assertIn("(retained)", rendered)

    def test_restore_resumes_empty_with_exact_persistent_transaction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result, writes, _transaction_sha, _output = self.run_restore_scenario(
                Path(temp_dir),
                initial_phase="EMPTY",
                initial_received=0,
                pending=True,
            )
        self.assertTrue(result)
        self.assertNotIn("config import abort", writes)
        self.assertEqual(sum(line.startswith("config import item ") for line in writes), 24)
        self.assertIn("config import apply", writes)

    def test_restore_aborts_partial_collecting_then_restarts_from_item_one(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result, writes, _transaction_sha, _output = self.run_restore_scenario(
                Path(temp_dir),
                initial_phase="COLLECTING",
                initial_received=7,
                pending=True,
            )
        self.assertTrue(result)
        abort_index = writes.index("config import abort")
        begin_index = next(
            index for index, line in enumerate(writes) if line.startswith("config import begin ")
        )
        first_item_index = next(
            index for index, line in enumerate(writes) if line.startswith("config import item ")
        )
        self.assertLess(abort_index, begin_index)
        self.assertLess(begin_index, first_item_index)
        self.assertEqual(sum(line.startswith("config import item ") for line in writes), 24)

    def test_restore_resumes_exact_readback_ok_after_ack_loss_without_reapplying(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result, writes, _transaction_sha, _output = self.run_restore_scenario(
                Path(temp_dir),
                initial_phase="READBACK_OK",
                initial_received=24,
                pending=False,
                initial_transaction_sha256="exact",
            )
        self.assertTrue(result)
        self.assertNotIn("config import apply", writes)
        self.assertNotIn("config import readback", writes)
        self.assertFalse(any(line.startswith("config import item ") for line in writes))
        self.assertIn("reset", writes)

    def test_readback_ok_reconnect_failure_reports_configuration_already_applied(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        identity = self.identity_lines("READY", motion=True, writes=True)
        writes = []
        output = []
        prompts = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            exported, backup_path = self.write_package_backup(temp, release)
            transaction_sha = ota.calculate_vehicle_restore_sha256(
                exported.backup_sha256,
                exported.source,
                exported.target,
            )

            def handler(command):
                if command in {"v 0.00 0.00", "stream off"}:
                    return []
                if command in identity:
                    return identity[command]
                if command == "config import status":
                    return config_import_status_lines(
                        exported,
                        phase="READBACK_OK",
                        received=24,
                        source=exported.source,
                        backup_sha256=exported.backup_sha256,
                        transaction_sha256=transaction_sha,
                    )
                if command == "reset":
                    return "INFO: rebooting..."
                return AssertionError(f"unexpected readback reconnect command: {command}")

            clock = FakeClock()
            with self.assertRaises(ota.ReconnectTimeoutError) as raised:
                ota.run_config_restore(
                    backup_path,
                    self.config(temp),
                    serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                    input_func=lambda prompt: prompts.append(prompt) or "RESTORE",
                    output_func=output.append,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                )
            records = [
                json.loads(line)
                for line in raised.exception.audit_path.read_text().splitlines()
            ]
        self.assertTrue(records[-1]["restore_apply_started"])
        self.assertTrue(records[-1]["restore_apply_may_have_occurred"])
        self.assertTrue(records[-1]["device_reports_readback_ok"])
        self.assertTrue(records[-1]["selected_transaction_readback_verified"])
        self.assertTrue(records[-1]["restore_readback_completed"])
        self.assertIn(
            "selected transaction already applied and read back; resume reboot/final verification",
            "\n".join(output),
        )
        self.assertIn("no configuration items will be sent", prompts[0])
        self.assertNotIn("Restore 24 vehicle configuration items", prompts[0])
        self.assertNotIn("config import apply", writes)

    def test_restore_rejects_readback_ok_with_different_full_transaction(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        identity = self.identity_lines("READY", motion=True, writes=True)
        writes = []
        output = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            exported, backup_path = self.write_package_backup(temp, release)
            wrong_transaction = "f" * 64
            expected_transaction = ota.calculate_vehicle_restore_sha256(
                exported.backup_sha256,
                exported.source,
                exported.target,
            )
            self.assertNotEqual(wrong_transaction, expected_transaction)

            def handler(command):
                if command in {"v 0.00 0.00", "stream off"}:
                    return []
                if command in identity:
                    return identity[command]
                if command == "config import status":
                    return config_import_status_lines(
                        exported,
                        phase="READBACK_OK",
                        received=24,
                        source=exported.source,
                        backup_sha256=exported.backup_sha256,
                        transaction_sha256=wrong_transaction,
                    )
                return AssertionError(f"unexpected readback mismatch command: {command}")

            clock = FakeClock()
            with self.assertRaises(ota.PostInstallError) as raised:
                ota.run_config_restore(
                    backup_path,
                    self.config(temp),
                    serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                    input_func=lambda _prompt: self.fail("wrong readback transaction prompted"),
                    output_func=output.append,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                )
            records = [
                json.loads(line)
                for line in raised.exception.audit_path.read_text().splitlines()
            ]
        self.assertEqual(raised.exception.stage, "config_import_status")
        self.assertTrue(records[-1]["device_reports_readback_ok"])
        self.assertFalse(records[-1]["selected_transaction_readback_verified"])
        self.assertFalse(records[-1]["restore_readback_completed"])
        self.assertTrue(records[-1]["restore_binding_conflict"])
        rendered = "\n".join(output)
        self.assertIn("different or invalid restore binding", rendered)
        self.assertIn("do not continue with the selected backup", rendered)
        self.assertNotIn("resume reboot/final verification", rendered)
        self.assertNotIn("reset", writes)
        self.assertNotIn("config import apply", writes)

    def test_restore_rejects_readback_ok_with_missing_full_transaction_binding(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        identity = self.identity_lines("READY", motion=True, writes=True)
        writes = []
        output = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            exported, backup_path = self.write_package_backup(temp, release)

            def handler(command):
                if command in {"v 0.00 0.00", "stream off"}:
                    return []
                if command in identity:
                    return identity[command]
                if command == "config import status":
                    return config_import_status_lines(
                        exported,
                        phase="READBACK_OK",
                        received=24,
                        source=exported.source,
                        backup_sha256=exported.backup_sha256,
                        transaction_sha256=None,
                    )
                return AssertionError(f"unexpected missing-binding command: {command}")

            clock = FakeClock()
            with self.assertRaisesRegex(ota.ProtocolError, "inconsistent with its phase") as raised:
                ota.run_config_restore(
                    backup_path,
                    self.config(temp),
                    serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                    input_func=lambda _prompt: self.fail("missing transaction prompted"),
                    output_func=output.append,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                )
            records = [
                json.loads(line)
                for line in raised.exception.audit_path.read_text().splitlines()
            ]
        self.assertTrue(records[-1]["restore_apply_may_have_occurred"])
        self.assertTrue(records[-1]["device_reports_readback_ok"])
        self.assertFalse(records[-1]["selected_transaction_readback_verified"])
        self.assertTrue(records[-1]["restore_binding_conflict"])
        rendered = "\n".join(output)
        self.assertIn("different or invalid restore binding", rendered)
        self.assertNotIn("resume reboot/final verification", rendered)
        self.assertNotIn("reset", writes)

    def test_restore_rejects_different_full_pending_transaction_without_writes(self):
        release = ota.load_release_package(
            REPO_ROOT / "firmware" / "packages" / "osr-fw-c03-t004.zip"
        )
        identity = self.identity_lines("CONFIG_RESTORE_INCOMPLETE")
        writes = []
        output = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            exported, backup_path = self.write_package_backup(temp, release)
            wrong_transaction = "f" * 64
            expected_transaction = ota.calculate_vehicle_restore_sha256(
                exported.backup_sha256,
                exported.source,
                exported.target,
            )
            self.assertNotEqual(wrong_transaction, expected_transaction)

            def handler(command):
                if command in {"v 0.00 0.00", "stream off"}:
                    return []
                if command in identity:
                    return identity[command]
                if command == "config import status":
                    return config_import_status_lines(
                        exported,
                        phase="EMPTY",
                        received=0,
                        pending_transaction_sha256=wrong_transaction,
                    )
                return AssertionError(f"unexpected mismatch command: {command}")

            clock = FakeClock()
            with self.assertRaises(ota.PostInstallError) as raised:
                ota.run_config_restore(
                    backup_path,
                    self.config(temp),
                    serial_factory=SequenceFactory([FakeSerial(handler, writes)]),
                    input_func=lambda _prompt: self.fail("mismatched transaction prompted"),
                    output_func=output.append,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                )
            records = [
                json.loads(line)
                for line in raised.exception.audit_path.read_text().splitlines()
            ]
        self.assertEqual(raised.exception.stage, "config_import_status")
        self.assertTrue(records[-1]["restore_apply_may_have_occurred"])
        self.assertTrue(records[-1]["restore_binding_conflict"])
        self.assertIn("different or invalid restore binding", "\n".join(output))
        self.assertFalse(any(line.startswith("config import begin") for line in writes))
        self.assertFalse(any(line.startswith("config import item") for line in writes))


class AppOtaTest(unittest.TestCase):
    def setUp(self):
        self.temp_context = tempfile.TemporaryDirectory(prefix="osracer-ota-test-")
        self.temp = Path(self.temp_context.name)
        self.package_path = write_zip(self.temp / "release.zip", app=DEFAULT_APP)
        self.release = ota.load_release_package(self.package_path)

    def tearDown(self):
        self.temp_context.cleanup()

    def config(self, **overrides):
        values = {
            "response_timeout": 0.02,
            "reconnect_timeout": 0.08,
            "reconnect_interval": 0.005,
            "log_dir": self.temp / "logs",
            "snapshot_dir": self.temp / "snapshots",
            "chunk_size": 8,
        }
        values.update(overrides)
        return ota.UpdateConfig(**values)

    def run_scenario(self, scenario, *, config=None, factory_items=None, input_func=lambda _prompt: "UPDATE"):
        pre_serial, post_serial = scenario.serials()
        factory = SequenceFactory(factory_items or [pre_serial, post_serial])
        clock = FakeClock()
        result = ota.run_app_ota(
            self.release,
            config or self.config(),
            serial_factory=factory,
            input_func=input_func,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        return result, scenario, factory

    def audit_records(self, path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def bind_transaction(self, snapshot, snapshot_sha256, snapshot_dir):
        configuration = snapshot.configuration or {}
        transaction = ota.MigrationTransaction(
            target=self.release.target,
            manifest_sha256=self.release.manifest_sha256,
            app_sha256=self.release.app_sha256,
            app_bytes=self.release.app_size,
            device_serial_sha256=ota._sha256(configuration["serial_number"].encode("ascii")),
            source_snapshot_sha256=snapshot_sha256,
            source_project_version=snapshot.version.project_version,
        )
        return ota._write_transaction(transaction, snapshot_dir.parent / "transactions")

    def corrective_evidence(self, label="corrective"):
        source_app = b"synthetic-t002-source-app"
        target_app = b"synthetic-t003-corrective-app"
        source_manifest = base_manifest(source_app)
        source_manifest["profile"] = {
            "id": "red",
            "hardware": "OSCORE_ESP32S3_RevA",
            "nvs_schema": 1,
            "project_version": "OSRF-C03-T002-g9ebacb3",
            "protocol": "1.1",
        }
        target_manifest = base_manifest(target_app)
        target_manifest["profile"] = {
            **source_manifest["profile"],
            "project_version": "OSRF-C03-T003-g9ebacb3",
        }
        source_release = ota.load_release_package(
            write_zip(self.temp / f"{label}-t002.zip", manifest=source_manifest, app=source_app)
        )
        target_release = ota.load_release_package(
            write_zip(self.temp / f"{label}-t003.zip", manifest=target_manifest, app=target_app)
        )
        snapshot_dir = self.temp / f"{label}-snapshots"
        source = FirmwareScenario(source_release, current_version="OSRF-LEGACY-g1234567")
        source.configuration["trim get"] = (
            "TRIM: 0.40deg center_pwm=1507us range=-5.0..5.0deg"
        )
        source.configuration["level get"] = "LEVEL: offset=[0.0337 0.1043 0.0265]g"
        source_serial, _unused = source.serials()
        clock = FakeClock()
        source_audit = ota.AuditLogger(self.temp / f"{label}-source-query-audit")
        try:
            source_snapshot = ota._query_snapshot(
                ota.SerialTransport(source_serial, monotonic=clock.monotonic, sleep=clock.sleep),
                self.config(),
                source_audit,
                phase="pre",
            )
            source_hash, _path = ota._write_snapshot(source_snapshot, snapshot_dir)
        finally:
            source_audit.close()
        device_serial_sha256 = ota._sha256(b"A1B2C3D4E5F6")
        transaction = ota.MigrationTransaction(
            target=source_release.target,
            manifest_sha256=source_release.manifest_sha256,
            app_sha256=source_release.app_sha256,
            app_bytes=source_release.app_size,
            device_serial_sha256=device_serial_sha256,
            source_snapshot_sha256=source_hash,
            source_project_version=source_snapshot.version.project_version,
        )
        transaction_path = ota._write_transaction(
            transaction,
            snapshot_dir.parent / f"{label}-transactions",
        )
        records = [
            {
                "step": "session",
                "status": "started",
                "manifest_sha256": source_release.manifest_sha256,
                "app_sha256": source_release.app_sha256,
                "app_bytes": source_release.app_size,
                "target_profile_id": source_release.target.profile_id,
                "target_hardware": source_release.target.hardware,
                "target_nvs_schema": source_release.target.nvs_schema,
                "target_project_version": source_release.target.project_version,
                "target_protocol": source_release.target.protocol,
            },
            {
                "step": "device_snapshot",
                "status": "ok",
                "phase": "pre",
                "pre_project_version": source_snapshot.version.project_version,
            },
            {
                "step": "configuration_snapshot",
                "status": "created",
                "snapshot_sha256": source_hash,
                "device_serial_sha256": device_serial_sha256,
                "fields": list(ota.CONFIGURATION_FIELDS),
            },
            {"step": "begin", "status": "ok"},
            {
                "step": "data",
                "status": "committed",
                "seq": 0,
                "cumulative_written": source_release.app_size,
            },
            {"step": "end", "status": "acknowledged"},
            {"step": "result", "status": "failed"},
        ]
        prior_audit = self.temp / f"{label}-original-t002-audit.jsonl"
        prior_audit.write_text(
            "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
            encoding="utf-8",
        )
        prior_audit.chmod(0o600)
        return {
            "source_release": source_release,
            "target_release": target_release,
            "source": source,
            "source_hash": source_hash,
            "snapshot_dir": snapshot_dir,
            "transaction_dir": transaction_path.parent,
            "transaction_path": transaction_path,
            "prior_audit": prior_audit,
        }

    def run_corrective(self, evidence, scenario, *, config_overrides=None):
        pre_serial, post_serial = scenario.serials()
        final_serial = FakeSerial(scenario.final_handler, scenario.all_writes)
        values = {
            "snapshot_dir": evidence["snapshot_dir"],
            "transaction_dir": evidence["transaction_dir"],
            "resume_audit": evidence["prior_audit"],
            "corrective_recovery": True,
            "log_dir": self.temp / f"logs-{evidence['snapshot_dir'].name}",
        }
        values.update(config_overrides or {})
        clock = FakeClock()
        return ota.run_app_ota(
            evidence["target_release"],
            self.config(**values),
            serial_factory=SequenceFactory([pre_serial, post_serial, final_serial]),
            input_func=lambda _prompt: "UPDATE",
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    def corrective_scenario(self, evidence, **overrides):
        target = evidence["target_release"].target
        incomplete = {
            "id": target.profile_id,
            "schema": target.nvs_schema,
            "state": "MIGRATION_INCOMPLETE",
            "motion": "No",
            "writes": "No",
            "protocol": target.protocol,
        }
        values = {
            "current_version": evidence["source_release"].target.project_version,
            "pre_profile": incomplete,
            "post_profile": incomplete,
            "migration_state": "MIGRATION_INCOMPLETE",
            "migration_hash": "none",
            "post_migration_hash": evidence["source_hash"][:12],
            "final_configuration": evidence["source"].configuration,
        }
        values.update(overrides)
        return FirmwareScenario(evidence["target_release"], **values)

    def test_strict_configuration_and_version_parsers_reject_bad_values(self):
        legacy = ota.parse_firmware_version(
            "FW_VERSION: Product=Neoracer V1, Firmware=osrcore, "
            "Hardware=OSCORE_NEO_ESP32S3_RevA, ProjectVer=NEORACER_V1.1-20260709-g8b28746, "
            "Release=20260709, Git=8b28746, Dirty=No, Build=2026-07-09 17:5"
        )
        self.assertEqual(legacy.format, "legacy_long")
        self.assertIsNone(legacy.protocol)
        invalid_calls = (
            (ota.parse_firmware_version, "FW_VERSION: ProjectVer=A, ProjectVer=B, Proto=1.1"),
            (ota.parse_serial_number, "SN: a1b2c3d4e5f6"),
            (ota.parse_pid, "PID: nan 1 2"),
            (ota.parse_mag_calibration, "MC: " + "0 " * 11 + "inf"),
            (ota.parse_battery_calibration, "BATTERY: Voltage=12.0V, Cal=User, Scale=2.0"),
            (ota.parse_odom_scale, "ODOM_SCALE: 2.0 range=0.5..1.5"),
            (ota.parse_steering_trim, "TRIM: 8.0deg center_pwm=1500us range=-5.0..5.0deg"),
            (ota.parse_speed_deadband, "SPEED_DEADBAND: 301us range=0..300us"),
            (ota.parse_level_offset, "LEVEL: offset=[0 0 nan]g"),
        )
        for parser, value in invalid_calls:
            with self.subTest(parser=parser.__name__):
                with self.assertRaises(ota.ProtocolError):
                    parser(value)

    def test_generic_device_error_preserves_wait_stage_without_payload(self):
        serial = FakeSerial(lambda _line: "ERROR: profile guard rejected", [])
        clock = FakeClock()
        transport = ota.SerialTransport(serial, monotonic=clock.monotonic, sleep=clock.sleep)
        transport.send_line("profile get")
        with self.assertRaises(ota.DeviceRejectedError) as raised:
            transport.wait_for(
                label="profile",
                prefixes=("PROFILE:",),
                parser=ota.parse_profile_status,
                timeout=0.02,
            )
        self.assertEqual(raised.exception.stage, "profile")
        self.assertEqual(raised.exception.device_reason, "profile guard rejected")

    def test_successful_ota_filters_noise_uses_exclusive_port_and_audits_without_payload(self):
        scenario = FirmwareScenario(self.release)
        result, scenario, factory = self.run_scenario(scenario)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.post_snapshot.version.project_version, self.release.target.project_version)
        self.assertTrue(factory.calls[0]["exclusive"])
        self.assertEqual(factory.calls[0]["baudrate"], 460800)
        self.assertIn("v 0.00 0.00", scenario.all_writes)
        self.assertIn("stream off", scenario.all_writes)
        self.assertIn("profile get", scenario.all_writes)
        self.assertIn("fw end", scenario.all_writes)

        log_text = result.audit_path.read_text(encoding="utf-8")
        self.assertNotIn(str(self.package_path), log_text)
        self.assertNotIn(DEFAULT_APP.hex(), log_text)
        records = self.audit_records(result.audit_path)
        self.assertEqual(records[-1]["status"], "success")
        self.assertEqual(records[-1]["exit_code"], 0)
        session = records[0]
        self.assertEqual(session["port"], "/dev/osrbot_base")
        self.assertEqual(session["manifest_sha256"], self.release.manifest_sha256)
        self.assertEqual(session["app_sha256"], self.release.app_sha256)

    def test_post_battery_retries_not_ready_and_audits_each_verification_stage(self):
        scenario = FirmwareScenario(
            self.release,
            post_battery_lines=["b 0.00", "b 0.00", "b 11.72"],
        )
        result, _scenario, _factory = self.run_scenario(
            scenario,
            config=self.config(
                reconnect_timeout=1.5,
                post_battery_timeout=1.0,
                post_battery_interval=0.2,
            ),
        )
        self.assertEqual(result.status, "success")
        records = self.audit_records(result.audit_path)
        waiting = [record for record in records if record["step"] == "post_battery" and record["status"] == "waiting"]
        self.assertEqual([record["voltage"] for record in waiting], [0.0, 0.0])
        self.assertTrue(any(record["step"] == "post_battery" and record["status"] == "ok" for record in records))
        for step in ("snapshot_version", "snapshot_profile", "snapshot_fw_status"):
            self.assertTrue(any(record["step"] == step and record.get("phase") == "post" for record in records))

    def test_persistent_post_battery_not_ready_is_recovery_required_not_not_written(self):
        for label, line in (("zero", "b 0.00"), ("malformed", "b invalid")):
            with self.subTest(label=label):
                scenario = FirmwareScenario(self.release, post_battery_line=line)
                with self.assertRaises(ota.PostInstallError) as raised:
                    self.run_scenario(
                        scenario,
                        config=self.config(
                            log_dir=self.temp / f"logs-post-battery-{label}",
                            snapshot_dir=self.temp / f"snapshots-post-battery-{label}",
                            reconnect_timeout=0.8,
                            post_battery_timeout=0.45,
                            post_battery_interval=0.2,
                        ),
                    )
                self.assertEqual(raised.exception.outcome, "post_verification_pending")
                self.assertEqual(raised.exception.stage, "post_battery_telemetry")
                self.assertIn("do not reflash", str(raised.exception))
                self.assertIn("fw end", scenario.all_writes)
                self.assertNotIn("fw abort", scenario.all_writes)
                terminal = self.audit_records(raised.exception.audit_path)[-1]
                self.assertEqual(terminal["outcome"], "post_verification_pending")
                self.assertEqual(terminal["failure_stage"], "post_battery_telemetry")
                self.assertTrue(terminal["recovery_required"])

    def test_pre_battery_zero_blocks_before_begin(self):
        scenario = FirmwareScenario(self.release, battery_line="b 0.00")
        with self.assertRaises(ota.DevicePreflightError) as raised:
            self.run_scenario(
                scenario,
                config=self.config(log_dir=self.temp / "logs-pre-zero"),
            )
        self.assertFalse(any(command.startswith("fw begin") for command in scenario.all_writes))
        terminal = self.audit_records(raised.exception.audit_path)[-1]
        self.assertEqual(terminal["outcome"], "not_written")
        self.assertFalse(terminal["recovery_required"])

    def test_target_version_is_skipped_unless_reinstall_is_explicit(self):
        scenario = FirmwareScenario(self.release, current_version=self.release.target.project_version)
        pre_serial, _post_serial = scenario.serials()
        factory = SequenceFactory([pre_serial])
        clock = FakeClock()
        result = ota.run_app_ota(
            self.release,
            self.config(),
            serial_factory=factory,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        self.assertEqual(result.status, "skipped")
        self.assertFalse(any(line.startswith("fw begin") for line in scenario.all_writes))

        reinstall_scenario = FirmwareScenario(self.release, current_version=self.release.target.project_version)
        result, reinstall_scenario, _factory = self.run_scenario(
            reinstall_scenario,
            config=self.config(reinstall=True),
        )
        self.assertEqual(result.status, "success")
        self.assertTrue(any(line.startswith("fw begin") for line in reinstall_scenario.all_writes))
        records = self.audit_records(result.audit_path)
        self.assertTrue(records[0]["reinstall"])

    def test_interactive_confirmation_is_required_before_begin(self):
        scenario = FirmwareScenario(self.release)
        pre_serial, _post_serial = scenario.serials()
        factory = SequenceFactory([pre_serial])
        clock = FakeClock()
        with self.assertRaises(ota.UserCancelledError) as raised:
            ota.run_app_ota(
                self.release,
                self.config(),
                serial_factory=factory,
                input_func=lambda _prompt: "no",
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
        self.assertFalse(any(line.startswith("fw begin") for line in scenario.all_writes))
        records = self.audit_records(raised.exception.audit_path)
        self.assertEqual(records[-1]["exit_code"], 2)

    def test_occupied_port_fails_without_killing_processes(self):
        factory = SequenceFactory([OSError("busy")])
        clock = FakeClock()
        with self.assertRaisesRegex(ota.SerialUnavailableError, "stop chassis") as raised:
            ota.run_app_ota(
                self.release,
                self.config(),
                serial_factory=factory,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
        records = self.audit_records(raised.exception.audit_path)
        self.assertEqual(records[-1]["exit_code"], 3)

    def test_known_serial_holder_is_rejected_before_open(self):
        factory = SequenceFactory([AssertionError("serial factory must not run")])
        with self.assertRaisesRegex(ota.SerialUnavailableError, "already open"):
            ota._open_exclusive(
                self.config(),
                factory,
                in_use_check=lambda _port: True,
            )
        self.assertEqual(factory.calls, [])

    def test_partial_serial_write_fails_closed(self):
        serial = PartialWriteSerial(lambda _line: [], [])
        clock = FakeClock()
        with self.assertRaises(ota.SerialCommunicationError):
            ota.run_app_ota(
                self.release,
                self.config(log_dir=self.temp / "logs-partial-write"),
                serial_factory=SequenceFactory([serial]),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )

    def test_source_profile_and_protocol_differences_are_warned_but_do_not_block(self):
        cases = {
            "profile": {"id": "OTHER_PROFILE"},
            "protocol": {"protocol": "2.0"},
        }
        for label, override in cases.items():
            with self.subTest(label=label):
                profile = {
                    "id": self.release.target.profile_id,
                    "schema": self.release.target.nvs_schema,
                    "state": "READY",
                    "motion": "Yes",
                    "writes": "Yes",
                    "protocol": self.release.target.protocol,
                }
                profile.update(override)
                scenario = FirmwareScenario(self.release, pre_profile=profile)
                scenario.post_profile = {
                    "id": self.release.target.profile_id,
                    "schema": self.release.target.nvs_schema,
                    "state": "READY",
                    "motion": "Yes",
                    "writes": "Yes",
                    "protocol": self.release.target.protocol,
                }
                result, scenario, _factory = self.run_scenario(
                    scenario,
                    config=self.config(log_dir=self.temp / f"logs-{label}"),
                )
                self.assertEqual(result.status, "success")
                self.assertTrue(any(line.startswith("fw begin") for line in scenario.all_writes))
                preflight = next(record for record in self.audit_records(result.audit_path) if record["step"] == "preflight")
                self.assertTrue(preflight["warnings"])

    def test_legacy_long_version_and_silent_source_profile_are_snapshotted(self):
        scenario = FirmwareScenario(self.release, legacy_version=True, profile_silent=True)
        scenario.post_profile = {
            "id": self.release.target.profile_id,
            "schema": self.release.target.nvs_schema,
            "state": "READY",
            "motion": "Yes",
            "writes": "Yes",
            "protocol": self.release.target.protocol,
        }
        result, _scenario, _factory = self.run_scenario(scenario)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.pre_snapshot.version.format, "legacy_long")
        self.assertIsNone(result.pre_snapshot.version.protocol)
        self.assertIsNone(result.pre_snapshot.profile)
        self.assertIn("source_profile", result.pre_snapshot.unavailable_fields)
        snapshots = list((self.temp / "snapshots").glob("*.json"))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(stat.S_IMODE(snapshots[0].stat().st_mode), 0o600)
        raw = snapshots[0].read_bytes()
        self.assertEqual(ota._sha256(raw), result.pre_snapshot.snapshot_sha256)
        document = json.loads(raw)
        self.assertEqual(document["device_serial"], "A1B2C3D4E5F6")
        self.assertIn("complementary_filter", document["unavailable_fields"])
        audit_text = result.audit_path.read_text(encoding="utf-8")
        self.assertNotIn(str(snapshots[0]), audit_text)
        self.assertNotIn("A1B2C3D4E5F6", audit_text)
        records = self.audit_records(result.audit_path)
        snapshot_record = next(record for record in records if record["step"] == "configuration_snapshot")
        self.assertEqual(snapshot_record["device_serial_sha256"], ota._sha256(b"A1B2C3D4E5F6"))

    def test_unclaimed_target_runs_hash_bound_migration_reset_and_compare(self):
        for cleanup in ("Done", "Deferred"):
            with self.subTest(cleanup=cleanup):
                unclaimed = {
                    "id": self.release.target.profile_id,
                    "schema": self.release.target.nvs_schema,
                    "state": "UNCLAIMED",
                    "motion": "No",
                    "writes": "No",
                    "protocol": self.release.target.protocol,
                }
                scenario = FirmwareScenario(self.release, post_profile=unclaimed, migration_cleanup=cleanup)
                pre_serial, post_serial = scenario.serials()
                final_serial = FakeSerial(scenario.final_handler, scenario.all_writes)
                result, scenario, _factory = self.run_scenario(
                    scenario,
                    config=self.config(
                        log_dir=self.temp / f"logs-migrate-{cleanup}",
                        snapshot_dir=self.temp / f"snapshots-migrate-{cleanup}",
                    ),
                    factory_items=[pre_serial, post_serial, final_serial],
                )
                self.assertEqual(result.status, "success")
                digest = result.pre_snapshot.snapshot_sha256
                self.assertIn(f"profile migrate validate {digest}", scenario.all_writes)
                self.assertIn(f"profile migrate apply {digest}", scenario.all_writes)
                self.assertIn("reset", scenario.all_writes)
                records = self.audit_records(result.audit_path)
                self.assertTrue(any(record["step"] == "migration_applied" and record["cleanup"] == cleanup for record in records))
                self.assertTrue(any(record["step"] == "reboot_verified" for record in records))
                self.assertTrue(any(record["step"] == "config_compared" for record in records))

    def test_migration_rejection_is_retryable_and_never_aborts_installed_app(self):
        unclaimed = {
            "id": self.release.target.profile_id,
            "schema": self.release.target.nvs_schema,
            "state": "UNCLAIMED",
            "motion": "No",
            "writes": "No",
            "protocol": self.release.target.protocol,
        }
        scenario = FirmwareScenario(self.release, post_profile=unclaimed, migration_error="validate")
        with self.assertRaises(ota.DeviceRejectedError) as raised:
            self.run_scenario(scenario)
        self.assertNotIn("fw abort", scenario.all_writes)
        records = self.audit_records(raised.exception.audit_path)
        self.assertEqual(records[-1]["outcome"], "migration_pending")
        self.assertEqual(records[-1]["failure_stage"], "migration_pending")

    def test_already_installed_unclaimed_target_resumes_migration_without_reflash(self):
        unclaimed = {
            "id": self.release.target.profile_id,
            "schema": self.release.target.nvs_schema,
            "state": "UNCLAIMED",
            "motion": "No",
            "writes": "No",
            "protocol": self.release.target.protocol,
        }
        snapshot_dir = self.temp / "snapshots-installed-unclaimed"
        source = FirmwareScenario(self.release)
        source_serial, _unused = source.serials()
        clock = FakeClock()
        source_audit = ota.AuditLogger(self.temp / "source-audit-installed-unclaimed")
        try:
            source_snapshot = ota._query_snapshot(
                ota.SerialTransport(source_serial, monotonic=clock.monotonic, sleep=clock.sleep),
                self.config(),
                source_audit,
                phase="pre",
            )
            source_hash, _path = ota._write_snapshot(source_snapshot, snapshot_dir)
            self.bind_transaction(source_snapshot, source_hash, snapshot_dir)
        finally:
            source_audit.close()

        scenario = FirmwareScenario(
            self.release,
            current_version=self.release.target.project_version,
            pre_profile=unclaimed,
        )
        pre_serial, _post_serial = scenario.serials()
        final_serial = FakeSerial(scenario.final_handler, scenario.all_writes)
        result, scenario, _factory = self.run_scenario(
            scenario,
            config=self.config(snapshot_dir=snapshot_dir),
            factory_items=[pre_serial, final_serial],
        )
        self.assertEqual(result.status, "success")
        self.assertFalse(any(command.startswith("fw begin") for command in scenario.all_writes))
        self.assertTrue(any(command.startswith("profile migrate apply") for command in scenario.all_writes))

        blocked = FirmwareScenario(
            self.release,
            current_version=self.release.target.project_version,
            pre_profile=unclaimed,
            migration_hash="b" * 12,
        )
        with self.assertRaises(ota.PostInstallError) as raised:
            self.run_scenario(
                blocked,
                config=self.config(
                    snapshot_dir=snapshot_dir,
                    log_dir=self.temp / "logs-unclaimed-nonempty-hash",
                ),
            )
        self.assertEqual(raised.exception.stage, "migration_status")
        self.assertFalse(
            any(
                command.startswith(("fw begin", "profile migrate validate", "profile migrate apply"))
                or command == "reset"
                for command in blocked.all_writes
            )
        )

    def test_already_installed_fail_closed_target_with_wrong_protocol_refuses_migration_entry(self):
        wrong_protocol = {
            "id": self.release.target.profile_id,
            "schema": self.release.target.nvs_schema,
            "state": "UNCLAIMED",
            "motion": "No",
            "writes": "No",
            "protocol": "9.9",
        }
        snapshot_dir = self.temp / "snapshots-wrong-migration-protocol"
        scenario = FirmwareScenario(
            self.release,
            current_version=self.release.target.project_version,
            pre_profile=wrong_protocol,
        )
        with self.assertRaises(ota.PostInstallError) as raised:
            self.run_scenario(
                scenario,
                config=self.config(
                    snapshot_dir=snapshot_dir,
                    log_dir=self.temp / "logs-wrong-migration-protocol",
                ),
            )
        self.assertEqual(raised.exception.outcome, "migration_pending")
        self.assertEqual(raised.exception.stage, "migration_protocol")
        self.assertTrue(raised.exception.no_app_reflash)
        self.assertFalse(snapshot_dir.exists())
        self.assertFalse((snapshot_dir.parent / "transactions").exists())
        self.assertFalse(
            any(
                command.startswith(
                    (
                        "fw begin",
                        "profile migrate status",
                        "profile migrate validate",
                        "profile migrate apply",
                    )
                )
                or command == "reset"
                for command in scenario.all_writes
            )
        )

    def test_t002_to_t003_corrective_recovery_inherits_original_snapshot_and_transaction(self):
        evidence = self.corrective_evidence("corrective-success")
        original_snapshot_files = sorted(evidence["snapshot_dir"].glob("*.json"))
        scenario = self.corrective_scenario(evidence)

        result = self.run_corrective(evidence, scenario)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.operation, "corrective_recovery")
        self.assertEqual(result.pre_snapshot.snapshot_sha256, evidence["source_hash"])
        self.assertEqual(sorted(evidence["snapshot_dir"].glob("*.json")), original_snapshot_files)
        self.assertIn(
            f"profile migrate validate {evidence['source_hash']}",
            scenario.all_writes,
        )
        self.assertEqual(
            scenario.all_writes.count(f"profile migrate validate {evidence['source_hash']}"),
            2,
        )
        self.assertIn(f"profile migrate apply {evidence['source_hash']}", scenario.all_writes)
        self.assertTrue(any(command.startswith("fw begin") for command in scenario.all_writes))
        transactions = sorted(evidence["transaction_dir"].glob("migration-*.json"))
        self.assertEqual(len(transactions), 2)
        parsed = [ota._parse_transaction(path.read_bytes()) for path in transactions]
        corrective = next(
            transaction
            for transaction in parsed
            if transaction.target == evidence["target_release"].target
        )
        self.assertEqual(corrective.source_snapshot_sha256, evidence["source_hash"])
        records = self.audit_records(result.audit_path)
        self.assertTrue(
            any(
                record["step"] == "corrective_journal"
                and record["status"] == "validated"
                and record["metadata_hash_state"] == "none"
                for record in records
            )
        )
        self.assertTrue(
            any(
                record["step"] == "configuration_snapshot"
                and record["status"] == "inherited"
                and record["snapshot_sha256"] == evidence["source_hash"]
                for record in records
            )
        )
        comparison = next(record for record in records if record["step"] == "config_compared")
        self.assertEqual(comparison["mismatch_fields"], [])

    def test_corrective_recovery_preflight_failures_send_no_app_data(self):
        cases = (
            "wrong_version",
            "wrong_state",
            "wrong_hash",
            "validate_rejected",
            "audit_conflict",
        )
        for name in cases:
            with self.subTest(name=name):
                evidence = self.corrective_evidence(f"corrective-{name}")
                overrides = {}
                if name == "wrong_version":
                    overrides["current_version"] = "OSRF-C03-T001-g9ebacb3"
                elif name == "wrong_state":
                    target = evidence["target_release"].target
                    overrides["pre_profile"] = {
                        "id": target.profile_id,
                        "schema": target.nvs_schema,
                        "state": "UNCLAIMED",
                        "motion": "No",
                        "writes": "No",
                        "protocol": target.protocol,
                    }
                elif name == "wrong_hash":
                    overrides["migration_hash"] = "a" * 12
                elif name == "validate_rejected":
                    overrides["migration_error"] = "validate"
                else:
                    records = self.audit_records(evidence["prior_audit"])
                    records[0]["app_sha256"] = "b" * 64
                    evidence["prior_audit"].write_text(
                        "".join(json.dumps(record) + "\n" for record in records),
                        encoding="utf-8",
                    )
                scenario = self.corrective_scenario(evidence, **overrides)
                with self.assertRaises(ota.FirmwareUpdateError):
                    self.run_corrective(evidence, scenario)
                self.assertFalse(any(command.startswith("fw begin") for command in scenario.all_writes))
                self.assertFalse(any(command.startswith("fw data") for command in scenario.all_writes))
                self.assertFalse(any(command.startswith("profile migrate apply") for command in scenario.all_writes))

    def test_corrective_recovery_rejects_wrong_package_and_missing_original_evidence(self):
        evidence = self.corrective_evidence("corrective-package-evidence")
        bad_manifest = base_manifest(b"wrong-version-app")
        bad_manifest["profile"] = {
            "id": "red",
            "hardware": "OSCORE_ESP32S3_RevA",
            "nvs_schema": 1,
            "project_version": "OSRF-C03-T004-g9ebacb3",
            "protocol": "1.1",
        }
        evidence["target_release"] = ota.load_release_package(
            write_zip(
                self.temp / "wrong-corrective-version.zip",
                manifest=bad_manifest,
                app=b"wrong-version-app",
            )
        )
        wrong_package = self.corrective_scenario(evidence)
        with self.assertRaisesRegex(ota.PackageValidationError, "T003"):
            self.run_corrective(evidence, wrong_package)
        self.assertFalse(any(command.startswith("fw begin") for command in wrong_package.all_writes))

        missing = self.corrective_evidence("corrective-missing-transaction")
        missing["transaction_path"].unlink()
        missing_transaction = self.corrective_scenario(missing)
        with self.assertRaisesRegex(ota.PostInstallError, "exactly one"):
            self.run_corrective(missing, missing_transaction)
        self.assertFalse(
            any(command.startswith("fw begin") for command in missing_transaction.all_writes)
        )

        missing_snapshot = self.corrective_evidence("corrective-missing-snapshot")
        for path in missing_snapshot["snapshot_dir"].glob("*.json"):
            path.unlink()
        no_snapshot = self.corrective_scenario(missing_snapshot)
        with self.assertRaisesRegex(ota.PostInstallError, "snapshot is missing"):
            self.run_corrective(missing_snapshot, no_snapshot)
        self.assertFalse(any(command.startswith("fw begin") for command in no_snapshot.all_writes))

    def test_corrective_recovery_requires_t003_post_hash_prefix_before_apply(self):
        evidence = self.corrective_evidence("corrective-post-hash")
        scenario = self.corrective_scenario(evidence, post_migration_hash="c" * 12)
        with self.assertRaises(ota.PostInstallError) as raised:
            self.run_corrective(evidence, scenario)
        self.assertEqual(raised.exception.stage, "migration_snapshot_recovery")
        self.assertTrue(any(command.startswith("fw begin") for command in scenario.all_writes))
        self.assertFalse(any(command.startswith("profile migrate apply") for command in scenario.all_writes))

    def test_corrective_recovery_requires_t003_to_remain_fail_closed_before_migration(self):
        evidence = self.corrective_evidence("corrective-post-state")
        target = evidence["target_release"].target
        ready = {
            "id": target.profile_id,
            "schema": target.nvs_schema,
            "state": "READY",
            "motion": "Yes",
            "writes": "Yes",
            "protocol": target.protocol,
        }
        scenario = self.corrective_scenario(evidence, post_profile=ready)
        with self.assertRaises(ota.PostInstallError) as raised:
            self.run_corrective(evidence, scenario)
        self.assertEqual(raised.exception.stage, "corrective_post_state")
        self.assertTrue(any(command.startswith("fw begin") for command in scenario.all_writes))
        self.assertFalse(any(command.startswith("profile migrate apply") for command in scenario.all_writes))

    def test_corrective_post_install_failure_resumes_t003_migration_without_reflash(self):
        evidence = self.corrective_evidence("corrective-post-retry")
        first = self.corrective_scenario(evidence, migration_error="apply")
        with self.assertRaises(ota.DeviceRejectedError):
            self.run_corrective(evidence, first)
        self.assertTrue(any(command.startswith("fw begin") for command in first.all_writes))

        retry = self.corrective_scenario(
            evidence,
            current_version=evidence["target_release"].target.project_version,
            migration_hash=evidence["source_hash"][:12],
            post_migration_hash=evidence["source_hash"][:12],
        )
        pre_serial, _unused = retry.serials()
        final_serial = FakeSerial(retry.final_handler, retry.all_writes)
        clock = FakeClock()
        result = ota.run_app_ota(
            evidence["target_release"],
            self.config(
                snapshot_dir=evidence["snapshot_dir"],
                transaction_dir=evidence["transaction_dir"],
                log_dir=self.temp / "logs-corrective-post-retry",
            ),
            serial_factory=SequenceFactory([pre_serial, final_serial]),
            input_func=lambda _prompt: "UPDATE",
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        self.assertEqual(result.operation, "migration_recovery")
        self.assertFalse(any(command.startswith("fw begin") for command in retry.all_writes))
        self.assertIn(f"profile migrate apply {evidence['source_hash']}", retry.all_writes)

    def test_explicit_large_legacy_audit_import_resumes_without_new_snapshot_or_reflash(self):
        snapshot_dir = self.temp / "snapshots-legacy-import"
        source = FirmwareScenario(self.release)
        source.configuration["trim get"] = (
            "TRIM: 0.40deg center_pwm=1507us range=-5.0..5.0deg"
        )
        source.configuration["level get"] = (
            "LEVEL: offset=[0.0337 0.1043 0.0265]g"
        )
        source_serial, _unused = source.serials()
        clock = FakeClock()
        source_audit = ota.AuditLogger(self.temp / "legacy-source-audit")
        try:
            source_snapshot = ota._query_snapshot(
                ota.SerialTransport(source_serial, monotonic=clock.monotonic, sleep=clock.sleep),
                self.config(),
                source_audit,
                phase="pre",
            )
            source_hash, _path = ota._write_snapshot(source_snapshot, snapshot_dir)
        finally:
            source_audit.close()

        serial_hash = ota._sha256(b"A1B2C3D4E5F6")
        required = [
            {
                "step": "session",
                "status": "started",
                "manifest_sha256": self.release.manifest_sha256,
                "app_sha256": self.release.app_sha256,
                "app_bytes": self.release.app_size,
                "target_profile_id": self.release.target.profile_id,
                "target_hardware": self.release.target.hardware,
                "target_nvs_schema": self.release.target.nvs_schema,
                "target_project_version": self.release.target.project_version,
                "target_protocol": self.release.target.protocol,
            },
            {
                "step": "device_snapshot",
                "status": "ok",
                "phase": "pre",
                "pre_project_version": source_snapshot.version.project_version,
            },
            {
                "step": "configuration_snapshot",
                "status": "created",
                "snapshot_sha256": source_hash,
                "device_serial_sha256": serial_hash,
                "fields": sorted((source_snapshot.configuration or {}).keys()),
            },
            {"step": "begin", "status": "ok"},
            {
                "step": "data",
                "status": "committed",
                "seq": 0,
                "cumulative_written": self.release.app_size,
            },
            {"step": "end", "status": "acknowledged"},
            {
                "step": "result",
                "status": "failed",
                "outcome": "app_installed_not_ready",
                "recovery_required": False,
            },
        ]
        filler = {"step": "noise", "status": "ignored", "padding": "x" * 100}
        records = required[:3] + [filler] * (6275 - len(required)) + required[3:]
        prior_audit = self.temp / "legacy-real-scale.jsonl"
        prior_audit.write_text(
            "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
            encoding="utf-8",
        )
        prior_audit.chmod(0o600)
        self.assertEqual(len(prior_audit.read_text(encoding="utf-8").splitlines()), 6275)
        self.assertGreater(prior_audit.stat().st_size, 707115)

        unclaimed = {
            "id": self.release.target.profile_id,
            "schema": self.release.target.nvs_schema,
            "state": "UNCLAIMED",
            "motion": "No",
            "writes": "No",
            "protocol": self.release.target.protocol,
        }
        post_configuration = dict(source.configuration)
        post_configuration["level get"] = (
            "LEVEL: offset=[0.0416 0.0822 0.0200]g"
        )
        scenario = FirmwareScenario(
            self.release,
            current_version=self.release.target.project_version,
            pre_profile=unclaimed,
            post_configuration=post_configuration,
        )
        scenario.configuration["trim get"] = (
            "TRIM: 0.00deg center_pwm=1500us range=-5.0..5.0deg"
        )
        scenario.configuration["level get"] = (
            "LEVEL: offset=[0.0416 0.0822 0.0200]g"
        )
        pre_serial, _unused = scenario.serials()
        final_serial = FakeSerial(scenario.final_handler, scenario.all_writes)
        prompts = []
        result, _scenario, _factory = self.run_scenario(
            scenario,
            config=self.config(snapshot_dir=snapshot_dir, resume_audit=prior_audit),
            factory_items=[pre_serial, final_serial],
            input_func=lambda prompt: prompts.append(prompt) or "UPDATE",
        )
        self.assertEqual(result.operation, "migration_recovery")
        self.assertEqual(len(list(snapshot_dir.glob("*.json"))), 1)
        self.assertFalse(any(command.startswith("fw begin") for command in scenario.all_writes))
        self.assertIn(f"source snapshot {source_hash[:12]}", prompts[0])
        self.assertIn("no App data will be sent", prompts[0])
        transactions = list((snapshot_dir.parent / "transactions").glob("migration-*.json"))
        self.assertEqual(len(transactions), 1)
        self.assertEqual(stat.S_IMODE(transactions[0].stat().st_mode), 0o600)
        transaction = ota._parse_transaction(transactions[0].read_bytes())
        self.assertEqual(transaction.source_snapshot_sha256, source_hash)
        result_records = self.audit_records(result.audit_path)
        deferred = next(
            record for record in result_records
            if record["step"] == "configuration_comparison"
        )
        self.assertEqual(deferred["status"], "deferred")
        comparison = next(
            record for record in result_records if record["step"] == "config_compared"
        )
        self.assertEqual(comparison["status"], "ok_with_warnings")
        self.assertTrue(comparison["level_offset_changed"])
        self.assertEqual(comparison["mismatch_fields"], [])
        self.assertTrue(
            any(record["step"] == "level_calibration" and record["status"] == "ok"
                for record in result_records)
        )

        mismatch = FirmwareScenario(
            self.release,
            current_version=self.release.target.project_version,
            pre_profile=unclaimed,
            post_configuration=dict(scenario.configuration),
        )
        mismatch.configuration["level get"] = scenario.configuration["level get"]
        mismatch.configuration["trim get"] = scenario.configuration["trim get"]
        mismatch_pre, _unused = mismatch.serials()
        mismatch_final = FakeSerial(mismatch.final_handler, mismatch.all_writes)
        with self.assertRaises(ota.PostInstallError) as raised:
            self.run_scenario(
                mismatch,
                config=self.config(
                    snapshot_dir=snapshot_dir,
                    log_dir=self.temp / "logs-recovered-trim-mismatch",
                ),
                factory_items=[mismatch_pre, mismatch_final],
            )
        self.assertEqual(raised.exception.outcome, "ready_config_mismatch")
        mismatch_record = next(
            record for record in self.audit_records(raised.exception.audit_path)
            if record["step"] == "config_compared"
        )
        self.assertEqual(mismatch_record["mismatch_fields"], ["steering_trim.degrees"])
        self.assertGreaterEqual(mismatch.all_writes.count("v 0.00 0.00"), 2)
        self.assertFalse(any(command.startswith("trim set") for command in mismatch.all_writes))

        pending = FirmwareScenario(
            self.release,
            current_version=self.release.target.project_version,
            pre_profile=unclaimed,
            post_configuration=post_configuration,
            post_level_calibration_states=[False],
        )
        pending.configuration["trim get"] = scenario.configuration["trim get"]
        pending.configuration["level get"] = scenario.configuration["level get"]
        pending_pre, _unused = pending.serials()
        pending_final = FakeSerial(pending.final_handler, pending.all_writes)
        with self.assertRaises(ota.PostInstallError) as raised:
            self.run_scenario(
                pending,
                config=self.config(
                    snapshot_dir=snapshot_dir,
                    log_dir=self.temp / "logs-level-calibration-pending",
                    reconnect_timeout=0.8,
                    level_calibration_timeout=0.45,
                    level_calibration_interval=0.2,
                ),
                factory_items=[pending_pre, pending_final],
            )
        self.assertEqual(raised.exception.outcome, "post_verification_pending")
        self.assertEqual(raised.exception.stage, "level_calibration")
        self.assertIn("stationary on level ground", str(raised.exception))
        self.assertFalse(any(command.startswith(("trim set", "level cal")) for command in pending.all_writes))

    def test_ambiguous_transactions_fail_closed_before_snapshot_or_reflash(self):
        snapshot_dir = self.temp / "snapshots-ambiguous-transaction"
        source = FirmwareScenario(self.release)
        source_serial, _unused = source.serials()
        clock = FakeClock()
        source_audit = ota.AuditLogger(self.temp / "ambiguous-source-audit")
        try:
            source_snapshot = ota._query_snapshot(
                ota.SerialTransport(source_serial, monotonic=clock.monotonic, sleep=clock.sleep),
                self.config(),
                source_audit,
                phase="pre",
            )
            source_hash, _path = ota._write_snapshot(source_snapshot, snapshot_dir)
            transaction_path = self.bind_transaction(source_snapshot, source_hash, snapshot_dir)
        finally:
            source_audit.close()
        duplicate = transaction_path.parent / "migration-duplicate.json"
        duplicate.write_bytes(transaction_path.read_bytes())
        duplicate.chmod(0o600)

        unclaimed = {
            "id": self.release.target.profile_id,
            "schema": self.release.target.nvs_schema,
            "state": "UNCLAIMED",
            "motion": "No",
            "writes": "No",
            "protocol": self.release.target.protocol,
        }
        scenario = FirmwareScenario(
            self.release,
            current_version=self.release.target.project_version,
            pre_profile=unclaimed,
        )
        with self.assertRaises(ota.PostInstallError) as raised:
            self.run_scenario(scenario, config=self.config(snapshot_dir=snapshot_dir))
        self.assertEqual(raised.exception.stage, "migration_transaction")
        self.assertEqual(len(list(snapshot_dir.glob("*.json"))), 1)
        self.assertFalse(any(command.startswith("fw begin") for command in scenario.all_writes))

    def test_resume_audit_permissions_symlink_and_size_limits_fail_closed(self):
        serial_hash = ota._sha256(b"A1B2C3D4E5F6")
        current_audit = self.temp / "current.jsonl"
        current_audit.write_text("{}\n", encoding="utf-8")
        current_audit.chmod(0o600)

        world_readable = self.temp / "world-readable.jsonl"
        world_readable.write_text("{}\n", encoding="utf-8")
        world_readable.chmod(0o644)
        oversized = self.temp / "oversized.jsonl"
        oversized.write_bytes(b"x" * (ota.MAX_RESUME_AUDIT_BYTES + 1))
        oversized.chmod(0o600)
        symlink = self.temp / "audit-link.jsonl"
        symlink.symlink_to(world_readable)

        for path in (world_readable, oversized, symlink, current_audit):
            with self.subTest(path=path.name):
                self.assertIsNone(
                    ota._transaction_from_prior_audit(
                        path,
                        self.release,
                        serial_hash,
                        current_audit_path=current_audit,
                    )
                )

    def test_resume_audit_rejects_out_of_order_duplicate_and_nonmonotonic_evidence(self):
        serial_hash = ota._sha256(b"A1B2C3D4E5F6")
        session = {
            "step": "session",
            "status": "started",
            "manifest_sha256": self.release.manifest_sha256,
            "app_sha256": self.release.app_sha256,
            "app_bytes": self.release.app_size,
            "target_profile_id": self.release.target.profile_id,
            "target_hardware": self.release.target.hardware,
            "target_nvs_schema": self.release.target.nvs_schema,
            "target_project_version": self.release.target.project_version,
            "target_protocol": self.release.target.protocol,
        }
        pre = {
            "step": "device_snapshot",
            "status": "ok",
            "phase": "pre",
            "pre_project_version": "S1-OLD",
        }
        snapshot = {
            "step": "configuration_snapshot",
            "status": "created",
            "snapshot_sha256": "a" * 64,
            "device_serial_sha256": serial_hash,
            "fields": [
                "battery",
                "level_offset",
                "magnetometer",
                "odom_scale",
                "pid",
                "serial_number",
                "speed_deadband_us",
                "steering_trim",
            ],
        }
        prefix = [
            session,
            pre,
            snapshot,
            {"step": "preflight", "status": "ok"},
            {"step": "confirmation", "status": "ok"},
            {"step": "begin", "status": "sent"},
            {"step": "begin", "status": "ok"},
        ]
        data = [
            {"step": "data", "status": "committed", "seq": 0, "cumulative_written": 1},
            {
                "step": "data",
                "status": "committed",
                "seq": 1,
                "cumulative_written": self.release.app_size,
            },
        ]
        suffix = [
            {"step": "end", "status": "sent"},
            {"step": "end", "status": "acknowledged"},
            {"step": "result", "status": "failed"},
        ]

        cases = {
            "snapshot_before_pre": [session, snapshot, pre] + prefix[3:] + data + suffix,
            "duplicate_begin": prefix + [{"step": "begin", "status": "ok"}] + data + suffix,
            "seq_gap": prefix + [data[0], {**data[1], "seq": 2}] + suffix,
            "cumulative_rollback": prefix + [data[0], {**data[1], "cumulative_written": 1}] + suffix,
            "result_before_end": prefix + data + [suffix[-1]] + suffix[:2],
            "critical_after_result": prefix + data + suffix + [{"step": "end", "status": "acknowledged"}],
            "noncritical_after_result": prefix + data + suffix + [{"step": "noise", "status": "ignored"}],
        }
        current_audit = self.temp / "state-machine-current.jsonl"
        current_audit.write_text("{}\n", encoding="utf-8")
        current_audit.chmod(0o600)
        valid_path = self.temp / "state-machine-valid.jsonl"
        valid_path.write_text(
            "".join(json.dumps(record) + "\n" for record in prefix + data + suffix),
            encoding="utf-8",
        )
        valid_path.chmod(0o600)
        self.assertIsNotNone(
            ota._transaction_from_prior_audit(
                valid_path,
                self.release,
                serial_hash,
                current_audit_path=current_audit,
            )
        )
        for label, records in cases.items():
            with self.subTest(label=label):
                path = self.temp / f"state-machine-{label}.jsonl"
                path.write_text(
                    "".join(json.dumps(record) + "\n" for record in records),
                    encoding="utf-8",
                )
                path.chmod(0o600)
                self.assertIsNone(
                    ota._transaction_from_prior_audit(
                        path,
                        self.release,
                        serial_hash,
                        current_audit_path=current_audit,
                    )
                )

    def test_incomplete_migration_recovers_original_private_snapshot_hash(self):
        unclaimed = {
            "id": self.release.target.profile_id,
            "schema": self.release.target.nvs_schema,
            "state": "UNCLAIMED",
            "motion": "No",
            "writes": "No",
            "protocol": self.release.target.protocol,
        }
        snapshot_dir = self.temp / "snapshots-retry"
        first = FirmwareScenario(self.release, post_profile=unclaimed, migration_error="apply")
        with self.assertRaises(ota.DeviceRejectedError) as raised:
            self.run_scenario(first, config=self.config(snapshot_dir=snapshot_dir))
        records = self.audit_records(raised.exception.audit_path)
        original_hash = next(record["snapshot_sha256"] for record in records if record["step"] == "configuration_snapshot")
        original_files = sorted(snapshot_dir.glob("*.json"))
        self.assertEqual(len(original_files), 1)

        incomplete = {**unclaimed, "state": "MIGRATION_INCOMPLETE"}
        retry = FirmwareScenario(
            self.release,
            current_version=self.release.target.project_version,
            pre_profile=incomplete,
            migration_state="MIGRATION_INCOMPLETE",
            migration_hash=original_hash[:12],
        )
        pre_serial, _post_serial = retry.serials()
        final_serial = FakeSerial(retry.final_handler, retry.all_writes)
        result, retry, _factory = self.run_scenario(
            retry,
            config=self.config(snapshot_dir=snapshot_dir, log_dir=self.temp / "logs-retry"),
            factory_items=[pre_serial, final_serial],
        )
        self.assertEqual(result.status, "success")
        self.assertIn(f"profile migrate validate {original_hash}", retry.all_writes)
        self.assertFalse(any(command.startswith("fw begin") for command in retry.all_writes))
        self.assertEqual(sorted(snapshot_dir.glob("*.json")), original_files)
        retry_records = self.audit_records(result.audit_path)
        snapshot_record = next(record for record in retry_records if record["step"] == "configuration_snapshot")
        self.assertEqual(snapshot_record["status"], "recovered")
        self.assertEqual(snapshot_record["snapshot_sha256"], original_hash)

    def test_committed_migration_with_lost_apply_ack_recovers_by_verified_reset(self):
        snapshot_dir = self.temp / "snapshots-committed-retry"
        source = FirmwareScenario(self.release)
        pre_serial, _post_serial = source.serials()
        clock = FakeClock()
        audit = ota.AuditLogger(self.temp / "snapshot-source-audit")
        try:
            transport = ota.SerialTransport(pre_serial, monotonic=clock.monotonic, sleep=clock.sleep)
            snapshot = ota._query_snapshot(transport, self.config(), audit, phase="pre")
            original_hash, _path = ota._write_snapshot(snapshot, snapshot_dir)
            self.bind_transaction(snapshot, original_hash, snapshot_dir)
        finally:
            audit.close()
        original_files = sorted(snapshot_dir.glob("*.json"))
        self.assertEqual(len(original_files), 1)

        committed = {
            "id": self.release.target.profile_id,
            "schema": self.release.target.nvs_schema,
            "state": "READY",
            "motion": "No",
            "writes": "No",
            "protocol": self.release.target.protocol,
        }
        retry = FirmwareScenario(
            self.release,
            current_version=self.release.target.project_version,
            pre_profile=committed,
            migration_state="READY",
            migration_hash=original_hash[:12],
        )
        retry.configuration = dict(source.configuration)
        current_serial, _post_serial = retry.serials()
        final_serial = FakeSerial(retry.final_handler, retry.all_writes)
        result, retry, _factory = self.run_scenario(
            retry,
            config=self.config(snapshot_dir=snapshot_dir, log_dir=self.temp / "logs-committed-retry"),
            factory_items=[current_serial, final_serial],
        )
        self.assertEqual(result.status, "success")
        self.assertIn("reset", retry.all_writes)
        self.assertFalse(any(command.startswith("profile migrate apply") for command in retry.all_writes))
        self.assertFalse(any(command.startswith("fw begin") for command in retry.all_writes))
        self.assertEqual(sorted(snapshot_dir.glob("*.json")), original_files)

    def test_metadata_invalid_resume_recovers_before_creating_any_snapshot(self):
        snapshot_dir = self.temp / "snapshots-metadata-invalid"
        source = FirmwareScenario(self.release)
        pre_serial, _post_serial = source.serials()
        clock = FakeClock()
        audit = ota.AuditLogger(self.temp / "metadata-source-audit")
        try:
            snapshot = ota._query_snapshot(
                ota.SerialTransport(pre_serial, monotonic=clock.monotonic, sleep=clock.sleep),
                self.config(),
                audit,
                phase="pre",
            )
            original_hash, _path = ota._write_snapshot(snapshot, snapshot_dir)
            self.bind_transaction(snapshot, original_hash, snapshot_dir)
        finally:
            audit.close()
        original_files = sorted(snapshot_dir.glob("*.json"))

        metadata_invalid = {
            "id": self.release.target.profile_id,
            "schema": self.release.target.nvs_schema,
            "state": "METADATA_INVALID",
            "motion": "No",
            "writes": "No",
            "protocol": self.release.target.protocol,
        }
        retry = FirmwareScenario(
            self.release,
            current_version=self.release.target.project_version,
            pre_profile=metadata_invalid,
            migration_state="METADATA_INVALID",
            migration_hash=original_hash[:12],
        )
        current_serial, _post_serial = retry.serials()
        final_serial = FakeSerial(retry.final_handler, retry.all_writes)
        result, _scenario, _factory = self.run_scenario(
            retry,
            config=self.config(snapshot_dir=snapshot_dir, log_dir=self.temp / "logs-metadata-invalid"),
            factory_items=[current_serial, final_serial],
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(sorted(snapshot_dir.glob("*.json")), original_files)
        records = self.audit_records(result.audit_path)
        snapshot_record = next(record for record in records if record["step"] == "configuration_snapshot")
        self.assertEqual(snapshot_record["status"], "recovered")
        self.assertEqual(snapshot_record["snapshot_sha256"], original_hash)

    def test_incomplete_resume_without_original_fails_before_creating_snapshot(self):
        snapshot_dir = self.temp / "snapshots-missing-original"
        incomplete = {
            "id": self.release.target.profile_id,
            "schema": self.release.target.nvs_schema,
            "state": "MIGRATION_INCOMPLETE",
            "motion": "No",
            "writes": "No",
            "protocol": self.release.target.protocol,
        }
        scenario = FirmwareScenario(
            self.release,
            current_version=self.release.target.project_version,
            pre_profile=incomplete,
            migration_state="MIGRATION_INCOMPLETE",
            migration_hash="a" * 12,
        )
        with self.assertRaises(ota.PostInstallError) as raised:
            self.run_scenario(
                scenario,
                config=self.config(
                    snapshot_dir=snapshot_dir,
                    log_dir=self.temp / "logs-missing-original",
                ),
            )
        self.assertEqual(raised.exception.stage, "migration_transaction_recovery")
        self.assertEqual(list(snapshot_dir.glob("*.json")), [])
        self.assertFalse(any(command.startswith("fw begin") for command in scenario.all_writes))

    def test_config_mismatch_is_reported_without_writing_configuration(self):
        changed = dict(FirmwareScenario(self.release).configuration)
        changed["pid get"] = "PID: 1.10 2.00 3.00"
        scenario = FirmwareScenario(self.release, post_configuration=changed)
        with self.assertRaises(ota.PostInstallError) as raised:
            self.run_scenario(scenario)
        self.assertEqual(raised.exception.outcome, "ready_config_mismatch")
        self.assertFalse(any(command.startswith("pid set") for command in scenario.all_writes))
        records = self.audit_records(raised.exception.audit_path)
        comparison = next(record for record in records if record["step"] == "config_compared")
        self.assertEqual(comparison["mismatch_fields"], ["pid[0]"])

    def test_trim_center_pwm_is_derived_warning_not_independent_source_parameter(self):
        before = {
            "battery": {"scale": 1.0},
            "odom_scale": 1.0,
            "steering_trim": {"degrees": 0.4, "center_pwm_us": 1507},
        }
        after = {
            "battery": {"scale": 1.0},
            "odom_scale": 1.0,
            "steering_trim": {"degrees": 0.4, "center_pwm_us": 1508},
        }
        self.assertEqual(ota._configuration_mismatches(before, after), [])
        self.assertEqual(
            ota._derived_configuration_warnings(before, after),
            ["steering_trim.center_pwm_changed_derived_from_degrees"],
        )

    def test_level_calibration_requires_vehicle_static(self):
        def handler(line):
            if line != "status":
                raise AssertionError(f"unexpected command: {line}")
            return [
                "Status: Speed=0.000m/s, Target=0.000m/s, Voltage=12.1V, "
                "Control=Serial, SpeedMode=30%, Static=No",
                "IMU: BiasReady=Yes, LevelCal=Yes, GyroBias=0.0000,0.0000,0.0000, "
                "LevelOffset=0.0000,0.0000,0.0000",
            ]

        clock = FakeClock()
        audit = ota.AuditLogger(self.temp / "logs-level-not-static")
        try:
            with self.assertRaises(ota.PostInstallError) as raised:
                ota._wait_for_level_calibration(
                    ota.SerialTransport(
                        FakeSerial(handler, []),
                        monotonic=clock.monotonic,
                        sleep=clock.sleep,
                    ),
                    self.config(
                        level_calibration_timeout=0.45,
                        level_calibration_interval=0.2,
                    ),
                    audit,
                    reconnect_deadline=None,
                )
            self.assertEqual(raised.exception.stage, "level_calibration")
            self.assertTrue(
                any(
                    record.get("reason") == "vehicle_not_static"
                    for record in self.audit_records(audit.path)
                )
            )
        finally:
            audit.close()

    def test_level_calibration_status_generation_rejects_stale_and_interleaved_pairs(self):
        status = (
            "Status: Speed=0.000m/s, Target=0.000m/s, Voltage=12.1V, "
            "Control=Serial, SpeedMode=30%, Static=Yes"
        )
        imu = (
            "IMU: BiasReady=Yes, LevelCal=Yes, GyroBias=0.0000,0.0000,0.0000, "
            "LevelOffset=0.0000,0.0000,0.0000"
        )
        valid = [status, "s 0 0 0 0 0 0 0 0 0 0 1 0 0 0 0 0 0", imu]
        cases = {
            "stale": (None, None),
            "interleaved": ([status, "unexpected generation boundary", imu], "unknown_between_status_and_imu"),
            "duplicate_status": ([status, status, imu], "duplicate_status"),
            "imu_first": ([imu, status], "imu_before_status"),
        }
        for name, (invalid, expected_reason) in cases.items():
            with self.subTest(name=name):
                batches = deque(([invalid, valid] if invalid is not None else [valid]))
                writes = []

                def handler(line):
                    if line != "status":
                        raise AssertionError(f"unexpected command: {line}")
                    return batches.popleft()

                serial = FakeSerial(handler, writes)
                if name == "stale":
                    serial.pending.extend(
                        ((status + "\n").encode("ascii"), (imu + "\n").encode("ascii"))
                    )
                clock = FakeClock()
                audit = ota.AuditLogger(self.temp / f"logs-level-generation-{name}")
                try:
                    ota._wait_for_level_calibration(
                        ota.SerialTransport(
                            serial,
                            monotonic=clock.monotonic,
                            sleep=clock.sleep,
                        ),
                        self.config(
                            level_calibration_timeout=0.45,
                            level_calibration_interval=0.2,
                        ),
                        audit,
                        reconnect_deadline=None,
                    )
                    records = self.audit_records(audit.path)
                    self.assertTrue(
                        any(
                            record["step"] == "level_calibration"
                            and record["status"] == "ok"
                            for record in records
                        )
                    )
                    if expected_reason is None:
                        self.assertEqual(writes, ["status"])
                        self.assertTrue(
                            any(
                                record["step"] == "level_calibration_generation"
                                and record["status"] == "discarded_stale"
                                and record["lines"] == 2
                                for record in records
                            )
                        )
                    else:
                        self.assertEqual(writes, ["status", "status"])
                        self.assertTrue(
                            any(record.get("reason") == expected_reason for record in records)
                        )
                finally:
                    audit.close()

    def test_invalid_voltage_and_firmware_low_voltage_rejection_stop_update(self):
        invalid = FirmwareScenario(self.release, battery_line="b nan")
        pre_serial, _post_serial = invalid.serials()
        clock = FakeClock()
        with self.assertRaises(ota.DevicePreflightError):
            ota.run_app_ota(
                self.release,
                self.config(log_dir=self.temp / "logs-invalid-voltage"),
                serial_factory=SequenceFactory([pre_serial]),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
        self.assertFalse(any(line.startswith("fw begin") for line in invalid.all_writes))

        low_voltage = FirmwareScenario(self.release, error_on="begin")
        with self.assertRaisesRegex(ota.DeviceRejectedError, "low_voltage") as raised:
            self.run_scenario(
                low_voltage,
                config=self.config(log_dir=self.temp / "logs-low-voltage"),
            )
        self.assertNotIn("fw abort", low_voltage.all_writes)
        records = self.audit_records(raised.exception.audit_path)
        self.assertEqual(records[-1]["outcome"], "not_written")

    def test_begin_rejection_or_malformed_ack_never_aborts_without_active_session(self):
        cases = [
            ("begin-error", {"error_on": "begin"}),
            ("begin-malformed", {"malformed_on": "begin"}),
            ("begin-size-mismatch", {"malformed_on": "begin-size"}),
        ]
        for label, kwargs in cases:
            with self.subTest(label=label):
                scenario = FirmwareScenario(self.release, **kwargs)
                with self.assertRaises(ota.ProtocolError) as raised:
                    self.run_scenario(
                        scenario,
                        config=self.config(log_dir=self.temp / f"logs-{label}"),
                    )
                self.assertNotIn("fw abort", scenario.all_writes)
                records = self.audit_records(raised.exception.audit_path)
                self.assertEqual(records[-1]["outcome"], "not_written")

    def test_data_errors_abort_only_after_valid_begin_ack(self):
        cases = [
            ("data-error", {"error_on": "data"}),
            ("data-malformed", {"malformed_on": "data"}),
        ]
        for label, kwargs in cases:
            with self.subTest(label=label):
                scenario = FirmwareScenario(self.release, **kwargs)
                with self.assertRaises(ota.ProtocolError):
                    self.run_scenario(
                        scenario,
                        config=self.config(log_dir=self.temp / f"logs-{label}"),
                    )
                self.assertIn("fw abort", scenario.all_writes)

    def test_end_error_or_malformed_ack_never_aborts_and_requires_recovery(self):
        cases = [
            ("end-error", {"error_on": "end"}),
            ("end-malformed", {"malformed_on": "end"}),
        ]
        for label, kwargs in cases:
            with self.subTest(label=label):
                scenario = FirmwareScenario(self.release, **kwargs)
                with self.assertRaises(ota.ProtocolError) as raised:
                    self.run_scenario(
                        scenario,
                        config=self.config(log_dir=self.temp / f"logs-{label}"),
                    )
                self.assertNotIn("fw abort", scenario.all_writes)
                records = self.audit_records(raised.exception.audit_path)
                self.assertEqual(records[-1]["outcome"], "app_write_status_unknown")
                self.assertTrue(records[-1]["recovery_required"])

    def test_end_write_or_flush_uncertainty_never_aborts_and_requires_recovery(self):
        cases = (
            ("flush-after-full-write", EndFlushFailureSerial),
            (
                "partial-write",
                lambda handler, writes: EndWriteFailureSerial(handler, writes, mode="partial"),
            ),
            (
                "write-exception",
                lambda handler, writes: EndWriteFailureSerial(handler, writes, mode="exception"),
            ),
        )
        for label, serial_type in cases:
            with self.subTest(label=label):
                scenario = FirmwareScenario(self.release)
                serial = serial_type(scenario.pre_handler, scenario.all_writes)
                with self.assertRaises(ota.SerialCommunicationError) as raised:
                    self.run_scenario(
                        scenario,
                        config=self.config(log_dir=self.temp / f"logs-end-{label}"),
                        factory_items=[serial],
                    )
                self.assertIn("fw end", scenario.all_writes)
                self.assertNotIn("fw abort", scenario.all_writes)
                records = self.audit_records(raised.exception.audit_path)
                self.assertEqual(records[-1]["outcome"], "app_write_status_unknown")
                self.assertTrue(records[-1]["recovery_required"])

    def test_data_timeout_status_committed_continues_without_resend(self):
        scenario = FirmwareScenario(self.release, timeout_mode="committed")
        result, scenario, _factory = self.run_scenario(scenario)
        self.assertEqual(result.status, "success")
        first_chunk_commands = [line for line in scenario.all_writes if line.startswith("fw data 0 ")]
        self.assertEqual(len(first_chunk_commands), 1)
        self.assertIn("fw status", scenario.all_writes)

    def test_data_timeout_status_not_committed_resends_once(self):
        scenario = FirmwareScenario(self.release, timeout_mode="resend")
        result, scenario, _factory = self.run_scenario(scenario)
        self.assertEqual(result.status, "success")
        first_chunk_commands = [line for line in scenario.all_writes if line.startswith("fw data 0 ")]
        self.assertEqual(len(first_chunk_commands), 2)

    def test_data_timeout_inconsistent_or_stuck_status_aborts(self):
        for mode in ("inconsistent", "stuck"):
            with self.subTest(mode=mode):
                scenario = FirmwareScenario(self.release, timeout_mode=mode)
                with self.assertRaises(ota.ProtocolError):
                    self.run_scenario(
                        scenario,
                        config=self.config(log_dir=self.temp / f"logs-{mode}"),
                    )
                self.assertIn("fw abort", scenario.all_writes)
                if mode == "stuck":
                    first_chunk_commands = [line for line in scenario.all_writes if line.startswith("fw data 0 ")]
                    self.assertEqual(len(first_chunk_commands), 2)

    def test_fw_status_error_and_malformed_response_abort(self):
        for label, kwargs in (
            ("status-error", {"timeout_mode": "resend", "error_on": "status"}),
            ("status-malformed", {"timeout_mode": "resend", "malformed_on": "status"}),
        ):
            with self.subTest(label=label):
                scenario = FirmwareScenario(self.release, **kwargs)
                with self.assertRaises(ota.ProtocolError):
                    self.run_scenario(
                        scenario,
                        config=self.config(log_dir=self.temp / f"logs-{label}"),
                    )
                self.assertIn("fw abort", scenario.all_writes)

    def test_missing_end_ack_is_accepted_only_after_post_reboot_verification(self):
        scenario = FirmwareScenario(self.release, end_ack=False)
        result, _scenario, _factory = self.run_scenario(scenario)
        self.assertEqual(result.status, "success")
        records = self.audit_records(result.audit_path)
        self.assertTrue(
            any(record["step"] == "end" and record["status"] == "ack_lost_reconnect_required" for record in records)
        )
        self.assertNotIn("fw abort", scenario.all_writes)

    def test_end_timeout_without_reconnect_never_aborts_or_claims_not_written(self):
        scenario = FirmwareScenario(self.release, end_ack=False)
        pre_serial, _post_serial = scenario.serials()
        clock = FakeClock()
        with self.assertRaises(ota.ReconnectTimeoutError) as raised:
            ota.run_app_ota(
                self.release,
                self.config(log_dir=self.temp / "logs-end-timeout-no-reconnect"),
                serial_factory=SequenceFactory([pre_serial, OSError("missing")]),
                input_func=lambda _prompt: "UPDATE",
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
        self.assertNotIn("fw abort", scenario.all_writes)
        records = self.audit_records(raised.exception.audit_path)
        self.assertEqual(records[-1]["outcome"], "app_write_status_unknown")
        self.assertTrue(records[-1]["recovery_required"])

    def test_reconnect_timeout_fails_without_fullflash_fallback(self):
        scenario = FirmwareScenario(self.release)
        pre_serial, _post_serial = scenario.serials()
        factory = SequenceFactory([pre_serial, OSError("missing")])
        clock = FakeClock()
        with self.assertRaises(ota.ReconnectTimeoutError) as raised:
            ota.run_app_ota(
                self.release,
                self.config(log_dir=self.temp / "logs-reconnect"),
                serial_factory=factory,
                input_func=lambda _prompt: "UPDATE",
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
        self.assertNotIn("fw abort", scenario.all_writes)
        records = self.audit_records(raised.exception.audit_path)
        self.assertEqual(records[-1]["status"], "failed")

    def test_post_reboot_version_profile_state_and_nvs_schema_mismatches_fail(self):
        base_profile = {
            "id": self.release.target.profile_id,
            "schema": self.release.target.nvs_schema,
            "state": "READY",
            "motion": "Yes",
            "writes": "Yes",
            "protocol": self.release.target.protocol,
        }
        cases = {
            "version": {"post_version": "WRONG"},
            "profile": {"post_profile": {**base_profile, "id": "OTHER_PROFILE"}},
            "nvs": {"post_profile": {**base_profile, "schema": self.release.target.nvs_schema + 1}},
            "state": {"post_profile": {**base_profile, "state": "RECOVERY"}},
            "protocol": {"post_profile": {**base_profile, "protocol": "2.0"}},
            "voltage": {"post_battery_line": "b nan"},
        }
        for label, kwargs in cases.items():
            with self.subTest(label=label):
                scenario = FirmwareScenario(self.release, **kwargs)
                with self.assertRaises(ota.DevicePreflightError):
                    self.run_scenario(
                        scenario,
                        config=self.config(log_dir=self.temp / f"logs-post-{label}"),
                    )
                self.assertNotIn("fw abort", scenario.all_writes)

    def test_keyboard_interrupt_attempts_abort_and_records_exit_130(self):
        scenario = FirmwareScenario(self.release, interrupt_on="fw data")
        with self.assertRaises(ota.UpdateInterruptedError) as raised:
            self.run_scenario(
                scenario,
                config=self.config(log_dir=self.temp / "logs-interrupt"),
            )
        self.assertIn("fw abort", scenario.all_writes)
        records = self.audit_records(raised.exception.audit_path)
        self.assertEqual(records[-1]["exit_code"], 130)

    def test_chunk_size_boundaries_are_enforced(self):
        self.config(chunk_size=1).validate()
        self.config(chunk_size=384).validate()
        for value in (0, 385):
            with self.subTest(value=value):
                with self.assertRaises(ota.PackageValidationError):
                    self.config(chunk_size=value).validate()


class CatalogDownloadTest(unittest.TestCase):
    def setUp(self):
        self.temp_context = tempfile.TemporaryDirectory(prefix="osracer-catalog-test-")
        self.temp = Path(self.temp_context.name).resolve()
        self.manifest = base_manifest()
        self.package_path = write_zip(
            self.temp / "candidate.zip",
            manifest=self.manifest,
        )
        self.package_data = self.package_path.read_bytes()
        self.entry = candidate_entry(self.package_data, manifest=self.manifest)
        self.catalog_data = catalog_bytes(test=[self.entry])
        self.cache = self.temp / "cache"

    def tearDown(self):
        self.temp_context.cleanup()

    def opener(self, *, catalog_data=None, package_route=None, events=None):
        return FakeOpener(
            {
                CATALOG_URL: self.catalog_data if catalog_data is None else catalog_data,
                PACKAGE_URL: self.package_data if package_route is None else package_route,
            },
            events=events,
        )

    def acquire(self, *, opener=None, catalog_data=None, output_func=lambda _message: None, **kwargs):
        cache_directory = kwargs.pop("cache_directory", self.cache)
        return ota.acquire_catalog_release(
            catalog_url=CATALOG_URL,
            channel="test",
            candidate_id="c03-t001",
            opener=opener or self.opener(catalog_data=catalog_data),
            cache_directory=cache_directory,
            output_func=output_func,
            **kwargs,
        )

    def test_valid_catalog_download_is_warned_validated_and_atomically_cached(self):
        events = []
        opener = self.opener(events=events)
        release, source = self.acquire(
            opener=opener,
            output_func=lambda message: events.append(("warning", message)),
        )
        self.assertEqual(release.target, ota.TargetProfile(**{
            "profile_id": self.manifest["profile"]["id"],
            "hardware": self.manifest["profile"]["hardware"],
            "nvs_schema": self.manifest["profile"]["nvs_schema"],
            "project_version": self.manifest["profile"]["project_version"],
            "protocol": self.manifest["profile"]["protocol"],
        }))
        self.assertEqual(source.kind, "catalog")
        self.assertEqual(source.catalog_sha256, ota._sha256(self.catalog_data))
        self.assertEqual(
            [event[0] for event in events],
            ["open", "warning", "open"],
        )
        self.assertIn("TEST FIRMWARE", events[1][1])
        self.assertIn("source_dirty=true", events[1][1])
        self.assertIn("unsigned", events[1][1])
        self.assertIn("release_ready=false", events[1][1])

        cached_packages = list(self.cache.glob("*.zip"))
        self.assertEqual(len(cached_packages), 1)
        self.assertEqual(cached_packages[0].read_bytes(), self.package_data)
        self.assertEqual(list(self.cache.glob(".partial-*")), [])

        cache_opener = FakeOpener({CATALOG_URL: self.catalog_data})
        cached_release, _source = self.acquire(opener=cache_opener)
        self.assertEqual(cached_release.app_sha256, release.app_sha256)
        self.assertEqual([call[0] for call in cache_opener.calls], [CATALOG_URL])

    def test_catalog_cli_lists_without_downloading_or_opening_serial(self):
        opener = FakeOpener({CATALOG_URL: self.catalog_data})
        output = []
        serial_factory = SequenceFactory([AssertionError("serial must not open")])
        result = ota.main(
            ["catalog", "--channel", "test", "--catalog-url", CATALOG_URL],
            opener=opener,
            serial_factory=serial_factory,
            output_func=output.append,
        )
        self.assertEqual(result, 0)
        parsed = json.loads(output[0])
        self.assertEqual(parsed["candidates"][0]["id"], "c03-t001")
        self.assertEqual(serial_factory.calls, [])
        self.assertEqual([call[0] for call in opener.calls], [CATALOG_URL])

    def test_test_channel_requires_explicit_candidate_and_sources_are_mutually_exclusive(self):
        serial_factory = SequenceFactory([AssertionError("serial must not open")])
        error_output = io.StringIO()
        with contextlib.redirect_stderr(error_output):
            result = ota.main(
                ["app-ota", "--channel", "test"],
                opener=FakeOpener({}),
                serial_factory=serial_factory,
            )
        self.assertEqual(result, 2)
        self.assertIn("explicit --candidate", error_output.getvalue())
        self.assertEqual(serial_factory.calls, [])

        with contextlib.redirect_stderr(io.StringIO()):
            result = ota.main(
                [
                    "app-ota",
                    "--package",
                    str(self.package_path),
                    "--candidate",
                    "c03-t001",
                ],
                serial_factory=serial_factory,
            )
        self.assertEqual(result, 2)
        self.assertEqual(serial_factory.calls, [])

    def test_public_cli_has_no_confirmation_bypass(self):
        parser = ota.build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                parser.parse_args(
                    ["app-ota", "--package", str(self.package_path), "--yes"]
                )
        self.assertEqual(raised.exception.code, 2)
        help_text = parser.format_help() + parser._subparsers._group_actions[0].choices["app-ota"].format_help()
        self.assertNotIn("--yes", help_text)

    def test_stable_empty_and_any_schema_one_stable_candidate_fail_closed(self):
        empty = catalog_bytes()
        opener = FakeOpener({CATALOG_URL: empty})
        with self.assertRaisesRegex(ota.CatalogError, "has no candidates"):
            ota.acquire_catalog_release(
                catalog_url=CATALOG_URL,
                channel="stable",
                candidate_id="c03-stable",
                opener=opener,
                cache_directory=self.cache,
            )
        self.assertEqual([call[0] for call in opener.calls], [CATALOG_URL])

        stable_entry = candidate_entry(
            self.package_data,
            manifest=self.manifest,
            channel="stable",
            candidate_id="c03-stable",
            release_ready=True,
            source_dirty=False,
        )
        stable = catalog_bytes(stable=[stable_entry])
        opener = FakeOpener({CATALOG_URL: stable})
        with self.assertRaisesRegex(ota.CatalogError, "no signature verifier"):
            ota.acquire_catalog_release(
                catalog_url=CATALOG_URL,
                channel="stable",
                candidate_id="c03-stable",
                opener=opener,
                cache_directory=self.cache,
            )
        self.assertEqual([call[0] for call in opener.calls], [CATALOG_URL])

    def test_schema_one_rejects_non_none_signature_and_duplicate_candidate_ids(self):
        signed_entry = {**self.entry, "signature": "unverified-string"}
        with self.assertRaisesRegex(ota.CatalogError, "signature must be 'none'"):
            ota.parse_catalog(catalog_bytes(test=[signed_entry]))

        duplicate = candidate_entry(self.package_data, manifest=self.manifest)
        with self.assertRaisesRegex(ota.CatalogError, "duplicate candidate id"):
            ota.parse_catalog(catalog_bytes(test=[duplicate, self.entry]))

    def test_catalog_rejects_bad_schema_types_and_candidate_fields(self):
        bad_documents = [
            b"[]",
            catalog_bytes(schema=True),
            catalog_bytes(schema=2),
            catalog_bytes(channels=[]),
            catalog_bytes(channels={"stable": [], "test": [], "preview": []}),
            catalog_bytes(channels={"stable": [], "test": {}}),
            catalog_bytes(test=["not-an-object"]),
            catalog_bytes(test=[{**self.entry, "id": "BAD_ID"}]),
            catalog_bytes(test=[{**self.entry, "channel": "stable"}]),
            catalog_bytes(test=[{**self.entry, "sha256": "0"}]),
            catalog_bytes(test=[{**self.entry, "size": True}]),
            catalog_bytes(test=[{**self.entry, "profile": []}]),
            catalog_bytes(test=[{**self.entry, "release_ready": "false"}]),
        ]
        for index, document in enumerate(bad_documents):
            with self.subTest(index=index):
                with self.assertRaises(ota.CatalogError):
                    ota.parse_catalog(document)

        with self.assertRaisesRegex(ota.CatalogError, "duplicate JSON"):
            ota.parse_catalog(b'{"schema":1,"schema":1,"channels":{"stable":[],"test":[]}}')

    def test_catalog_rejects_unsafe_asset_paths_and_non_https_catalog(self):
        unsafe_assets = (
            "../candidate.zip",
            "/candidate.zip",
            "packages\\candidate.zip",
            "https://other.test/candidate.zip",
            "//other.test/candidate.zip",
            "packages/%2e%2e/candidate.zip",
            "packages/candidate.zip?download=1",
        )
        for asset in unsafe_assets:
            with self.subTest(asset=asset):
                document = catalog_bytes(test=[{**self.entry, "asset": asset}])
                with self.assertRaises(ota.CatalogError):
                    ota.parse_catalog(document)

        opener = FakeOpener({})
        with self.assertRaisesRegex(ota.DownloadError, "HTTPS"):
            ota.acquire_catalog_release(
                catalog_url="http://example.test/firmware/catalog.json",
                channel="test",
                candidate_id="c03-t001",
                opener=opener,
                cache_directory=self.cache,
            )
        self.assertEqual(opener.calls, [])

    def test_runtime_cache_and_audit_paths_cannot_pollute_repository(self):
        with self.assertRaisesRegex(ota.DownloadError, "outside the source repository"):
            ota._ensure_cache_directory(ota.REPOSITORY_ROOT / "runtime-cache")
        with self.assertRaisesRegex(ota.AuditError, "outside the source repository"):
            ota.AuditLogger(ota.REPOSITORY_ROOT / "runtime-audit")
        self.assertFalse((ota.REPOSITORY_ROOT / "runtime-cache").exists())
        self.assertFalse((ota.REPOSITORY_ROOT / "runtime-audit").exists())

    def test_https_to_http_cross_origin_and_same_origin_redirects_are_never_followed(self):
        destinations = (
            "http://example.test/firmware/catalog.json",
            "https://other.test/firmware/catalog.json",
            "https://example.test/other/catalog.json",
        )
        for destination in destinations:
            with self.subTest(destination=destination):
                transport = FakeRedirectHttpsHandler(destination)
                opener = urllib.request.OpenerDirector()
                opener.add_handler(transport)
                opener.add_handler(ota._NoRedirectHandler())
                opener.add_handler(urllib.request.HTTPErrorProcessor())
                with self.assertRaises(ota.DownloadError):
                    ota._download_bytes(
                        CATALOG_URL,
                        opener=lambda request, timeout: opener.open(request, timeout=timeout),
                        timeout=1.0,
                        limit=ota.MAX_CATALOG_BYTES,
                        label="catalog",
                    )
                self.assertEqual(transport.calls, [CATALOG_URL])

    def test_effective_url_defense_rejects_all_redirect_destinations(self):
        destinations = (
            "http://example.test/firmware/catalog.json",
            "https://other.test/firmware/catalog.json",
            "https://example.test/other/catalog.json",
        )
        for destination in destinations:
            with self.subTest(destination=destination):
                opener = FakeOpener(
                    {
                        CATALOG_URL: lambda _url, destination=destination: FakeHttpResponse(
                            self.catalog_data,
                            destination,
                        )
                    }
                )
                with self.assertRaisesRegex(ota.DownloadError, "redirected"):
                    self.acquire(opener=opener, cache_directory=self.temp / destination.split(":")[0])
                self.assertEqual(len(opener.calls), 1)

    def test_catalog_size_limit_declared_size_mismatch_and_outer_sha_fail_closed(self):
        too_large = catalog_bytes(
            test=[{**self.entry, "size": ota.MAX_PACKAGE_DOWNLOAD_BYTES + 1}]
        )
        with self.assertRaisesRegex(ota.CatalogError, "download limit"):
            ota.parse_catalog(too_large)

        wrong_size = catalog_bytes(test=[{**self.entry, "size": len(self.package_data) + 1}])
        with self.assertRaisesRegex(ota.DownloadError, "size does not match"):
            self.acquire(catalog_data=wrong_size)

        wrong_sha = catalog_bytes(test=[{**self.entry, "sha256": "0" * 64}])
        with self.assertRaisesRegex(ota.DownloadError, "SHA256"):
            self.acquire(catalog_data=wrong_sha)

        opener = FakeOpener({CATALOG_URL: b"x" * 33})
        with self.assertRaisesRegex(ota.DownloadError, "exceeds"):
            ota._download_bytes(
                CATALOG_URL,
                opener=opener,
                timeout=1.0,
                limit=32,
                label="catalog",
            )

    def test_timeout_truncation_and_failed_validation_leave_no_complete_cache(self):
        cases = {
            "timeout": TimeoutError(),
            "truncated": lambda url: FakeHttpResponse(
                self.package_data,
                url,
                content_length=len(self.package_data) + 1,
            ),
        }
        for label, route in cases.items():
            with self.subTest(label=label):
                cache = self.temp / f"cache-{label}"
                opener = self.opener(package_route=route)
                with self.assertRaises(ota.DownloadError):
                    ota.acquire_catalog_release(
                        catalog_url=CATALOG_URL,
                        channel="test",
                        candidate_id="c03-t001",
                        opener=opener,
                        cache_directory=cache,
                        output_func=lambda _message: None,
                    )
                self.assertEqual(list(cache.glob("*.zip")), [])
                self.assertEqual(list(cache.glob(".partial-*")), [])

        invalid_package = b"not a ZIP"
        invalid_catalog = catalog_bytes(
            test=[candidate_entry(invalid_package, manifest=self.manifest)]
        )
        cache = self.temp / "cache-invalid-package"
        opener = FakeOpener({CATALOG_URL: invalid_catalog, PACKAGE_URL: invalid_package})
        with self.assertRaises(ota.PackageValidationError):
            ota.acquire_catalog_release(
                catalog_url=CATALOG_URL,
                channel="test",
                candidate_id="c03-t001",
                opener=opener,
                cache_directory=cache,
                output_func=lambda _message: None,
            )
        self.assertEqual(list(cache.glob("*.zip")), [])
        self.assertEqual(list(cache.glob(".partial-*")), [])

    def test_downloaded_bad_app_sha_and_catalog_manifest_mismatch_are_not_cached(self):
        bad_manifest = copy.deepcopy(self.manifest)
        bad_manifest["sha256"]["app"] = "0" * 64
        bad_package_path = write_zip(
            self.temp / "bad-app-sha.zip",
            manifest=bad_manifest,
        )
        bad_package = bad_package_path.read_bytes()
        bad_catalog = catalog_bytes(test=[candidate_entry(bad_package, manifest=self.manifest)])
        opener = FakeOpener({CATALOG_URL: bad_catalog, PACKAGE_URL: bad_package})
        cache = self.temp / "cache-bad-app"
        with self.assertRaisesRegex(ota.PackageValidationError, "App SHA256"):
            ota.acquire_catalog_release(
                catalog_url=CATALOG_URL,
                channel="test",
                candidate_id="c03-t001",
                opener=opener,
                cache_directory=cache,
                output_func=lambda _message: None,
            )
        self.assertEqual(list(cache.glob("*.zip")), [])

        mismatched_entry = copy.deepcopy(self.entry)
        mismatched_entry["profile"]["id"] = "OTHER"
        mismatch_catalog = catalog_bytes(test=[mismatched_entry])
        cache = self.temp / "cache-mismatch"
        with self.assertRaisesRegex(ota.CatalogError, "ProfileID"):
            self.acquire(catalog_data=mismatch_catalog, cache_directory=cache)
        self.assertEqual(list(cache.glob("*.zip")), [])

    def test_all_catalog_profile_fields_are_compared_with_manifest(self):
        release = ota.load_release_package(self.package_path)
        catalog = ota.parse_catalog(self.catalog_data)
        candidate = catalog.channels["test"][0]
        cases = {
            "ProfileID": replace(candidate.target, profile_id="other"),
            "hardware": replace(candidate.target, hardware="other"),
            "NVS schema": replace(candidate.target, nvs_schema=candidate.target.nvs_schema + 1),
            "Proto": replace(candidate.target, protocol="other"),
            "ProjectVer": replace(candidate.target, project_version="other"),
        }
        for label, target in cases.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(ota.CatalogError, label):
                    ota._validate_catalog_manifest(release, replace(candidate, target=target))

    def test_download_failure_never_opens_serial(self):
        wrong_sha_catalog = catalog_bytes(test=[{**self.entry, "sha256": "0" * 64}])
        opener = self.opener(catalog_data=wrong_sha_catalog)
        serial_factory = SequenceFactory([AssertionError("serial must not open")])
        with contextlib.redirect_stderr(io.StringIO()):
            result = ota.main(
                [
                    "app-ota",
                    "--channel",
                    "test",
                    "--candidate",
                    "c03-t001",
                    "--catalog-url",
                    CATALOG_URL,
                ],
                opener=opener,
                cache_directory=self.cache,
                serial_factory=serial_factory,
                output_func=lambda _message: None,
            )
        self.assertEqual(result, 2)
        self.assertEqual(serial_factory.calls, [])

    def test_local_package_cli_regression_does_not_use_catalog(self):
        release = ota.load_release_package(self.package_path)
        scenario = FirmwareScenario(release)
        pre_serial, post_serial = scenario.serials()
        serial_factory = SequenceFactory([pre_serial, post_serial])
        opener = FakeOpener({})
        clock = FakeClock()
        with mock.patch.object(ota.time, "monotonic", clock.monotonic), mock.patch.object(
            ota.time,
            "sleep",
            clock.sleep,
        ):
            result = ota.main(
                [
                    "app-ota",
                    "--package",
                    str(self.package_path),
                    "--response-timeout",
                    "0.02",
                    "--reconnect-timeout",
                    "1.0",
                    "--log-dir",
                    str(self.temp / "local-logs"),
                ],
                opener=opener,
                serial_factory=serial_factory,
                input_func=lambda _prompt: "UPDATE",
                output_func=lambda _message: None,
            )
        self.assertEqual(result, 0)
        self.assertEqual(opener.calls, [])

    def test_test_metadata_enters_prompt_warning_and_audit_with_injected_confirmation(self):
        release, source = self.acquire(output_func=lambda _message: None)

        prompts = []
        warnings_output = []
        scenario = FirmwareScenario(release)
        pre_serial, post_serial = scenario.serials()
        clock = FakeClock()
        result = ota.run_app_ota(
            release,
            ota.UpdateConfig(
                response_timeout=0.02,
                reconnect_timeout=0.08,
                reconnect_interval=0.005,
                chunk_size=8,
                log_dir=self.temp / "catalog-logs-interactive",
            ),
            source=source,
            serial_factory=SequenceFactory([pre_serial, post_serial]),
            input_func=lambda prompt: prompts.append(prompt) or "UPDATE",
            output_func=warnings_output.append,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        self.assertEqual(result.status, "success")
        self.assertIn("TEST FIRMWARE", prompts[0])
        self.assertIn("source_dirty=true", prompts[0])
        self.assertIn("unsigned", prompts[0])
        self.assertIn("release_ready=false", prompts[0])
        self.assertTrue(any("TEST FIRMWARE" in line for line in warnings_output))

        records = [
            json.loads(line)
            for line in result.audit_path.read_text(encoding="utf-8").splitlines()
        ]
        session = records[0]
        self.assertEqual(session["candidate_id"], "c03-t001")
        self.assertEqual(session["channel"], "test")
        self.assertTrue(session["source_dirty"])
        self.assertFalse(session["release_ready"])
        self.assertEqual(session["signature"], "none")

        scenario = FirmwareScenario(release)
        pre_serial, post_serial = scenario.serials()
        clock = FakeClock()
        injected_output = []
        injected_result = ota.run_app_ota(
            release,
            ota.UpdateConfig(
                response_timeout=0.02,
                reconnect_timeout=0.08,
                reconnect_interval=0.005,
                chunk_size=8,
                log_dir=self.temp / "catalog-logs-yes",
            ),
            source=source,
            serial_factory=SequenceFactory([pre_serial, post_serial]),
            input_func=lambda _prompt: "UPDATE",
            output_func=injected_output.append,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        self.assertEqual(injected_result.status, "success")
        self.assertTrue(any("source_dirty=true" in line for line in injected_output))
        injected_records = [
            json.loads(line)
            for line in injected_result.audit_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertTrue(injected_records[0]["source_dirty"])
        self.assertTrue(any(record["step"] == "candidate_notice" for record in injected_records))


if __name__ == "__main__":
    unittest.main()
