#!/usr/bin/env python3
"""Static integration checks for the pinned osracer_base runtime dependency."""

import ast
import json
import os
from pathlib import Path
import subprocess
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
BASE_SHA = "9b4e1a67ab755fa0a22dca7078b4b98c1b8cc3eb"
BASE_URL = "https://github.com/osrbot/osracer_base.git"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ros2-static.yml"


def _keyword(call, name):
    return next((item.value for item in call.keywords if item.arg == name), None)


def _constant_string(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class OsracerBaseIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch_path = ROOT / "osracer_bringup" / "launch" / "chassis_ackermann.launch.py"
        cls.launch_source = cls.launch_path.read_text(encoding="utf-8")
        cls.launch_tree = ast.parse(cls.launch_source, filename=str(cls.launch_path))

    def test_vcs_manifest_pins_exact_main_commit(self):
        manifest = json.loads((ROOT / "osracer.repos").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest,
            {
                "repositories": {
                    "osracer_base": {
                        "type": "git",
                        "url": BASE_URL,
                        "version": BASE_SHA,
                    }
                }
            },
        )
        self.assertFalse((ROOT / "osracer_base").exists())

    def test_bringup_declares_runtime_dependency(self):
        package_root = ET.parse(ROOT / "osracer_bringup" / "package.xml").getroot()
        dependencies = {element.text for element in package_root.findall("exec_depend")}
        self.assertIn("osracer_base", dependencies)
        self.assertNotIn("geometry_msgs", dependencies)
        self.assertNotIn("tf2_ros", dependencies)

    def test_product_launch_defaults_remain_explicit(self):
        defaults = {}
        for node in ast.walk(self.launch_tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "DeclareLaunchArgument" or not node.args:
                continue
            name = _constant_string(node.args[0])
            default = _constant_string(_keyword(node, "default_value"))
            if name is not None and default is not None:
                defaults[name] = default

        self.assertEqual(defaults["port_name"], "/dev/osrbot_base")
        self.assertEqual(defaults["baud_rate"], "460800")
        self.assertEqual(defaults["wheelbase"], "0.285")
        self.assertEqual(defaults["max_speed"], "4.64")
        self.assertEqual(defaults["max_steering_angle_deg"], "30.0")
        self.assertEqual(defaults["cmd_watchdog_timeout_s"], "0.5")
        self.assertEqual(defaults["reconnect_interval_s"], "2.0")
        self.assertEqual(defaults["firmware_version_timeout_s"], "0.3")
        self.assertEqual(defaults["link_status_enabled"], "true")
        self.assertEqual(defaults["link_ping_period_s"], "1.0")
        self.assertEqual(defaults["use_ekf"], "False")

    def test_default_entry_starts_exactly_one_external_chassis_driver(self):
        nodes = []
        for node in ast.walk(self.launch_tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "Node":
                continue
            nodes.append(
                (
                    _constant_string(_keyword(node, "package")),
                    _constant_string(_keyword(node, "executable")),
                    _constant_string(_keyword(node, "name")),
                )
            )

        self.assertEqual(nodes.count(("osracer_base", "chassis_driver", "osracer_chassis")), 1)
        self.assertFalse(any(executable == "chassis_ackermann.py" for _, executable, _ in nodes))

    def test_launch_maps_legacy_interface_to_base_parameters(self):
        chassis_call = None
        for node in ast.walk(self.launch_tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id == "Node" and _constant_string(_keyword(node, "package")) == "osracer_base":
                chassis_call = node
                break
        self.assertIsNotNone(chassis_call)

        parameters = _keyword(chassis_call, "parameters")
        self.assertIsInstance(parameters, ast.List)
        parameter_dict = parameters.elts[0]
        self.assertIsInstance(parameter_dict, ast.Dict)
        parameter_names = {_constant_string(key) for key in parameter_dict.keys}
        self.assertEqual(
            parameter_names,
            {
                "port",
                "baudrate",
                "vehicle_profile",
                "profile_schema",
                "odom_frame_id",
                "base_frame_id",
                "imu_frame_id",
                "wheelbase",
                "max_speed",
                "speed_mode",
                "max_steering_angle",
                "cmd_timeout",
                "reconnect_interval",
                "firmware_version_timeout",
                "connection_status_enabled",
                "connection_refresh_period",
                "publish_tf",
            },
        )
        parameter_values = {
            _constant_string(key): value
            for key, value in zip(parameter_dict.keys, parameter_dict.values)
        }
        self.assertEqual(_constant_string(parameter_values["vehicle_profile"]), "red")
        self.assertIsInstance(parameter_values["profile_schema"], ast.Constant)
        self.assertEqual(parameter_values["profile_schema"].value, 1)
        self.assertIn("0.017453292519943295", ast.unparse(parameter_dict))
        self.assertIn("'speed_mode': 'high'", ast.unparse(parameter_dict))

        remappings = ast.unparse(_keyword(chassis_call, "remappings"))
        self.assertIn("('/cmd_vel', 'cmd_vel')", remappings)
        self.assertIn("('/ackermann_cmd', 'ackermann_cmd')", remappings)
        self.assertIn("'/odometry/filtered'", remappings)
        self.assertIn("LaunchConfiguration('use_ekf')", remappings)
        self.assertEqual(self.launch_source.count("'.lower() == 'true'"), 3)

    def test_legacy_driver_is_removed_from_source_and_install_manifest(self):
        legacy = ROOT / "osracer_bringup" / "script" / "chassis_ackermann.py"
        self.assertFalse(legacy.exists())

        duplicate_drivers = []
        for path in (ROOT / "osracer_bringup" / "script").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "serial.Serial" in source and "AckermannDrive" in source:
                duplicate_drivers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(duplicate_drivers, [])

        cmake = (ROOT / "osracer_bringup" / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertNotIn("script/chassis_ackermann.py", cmake)
        self.assertNotIn("executable='chassis_ackermann.py'", self.launch_source)
        bringup = (ROOT / "osracer_bringup" / "launch" / "bringup.launch.py").read_text(encoding="utf-8")
        self.assertEqual(bringup.count("chassis_ackermann.launch.py"), 1)

    def test_imported_base_source_matches_pin_and_launch_contract(self):
        source_value = os.environ.get("OSRACER_BASE_SOURCE")
        if not source_value:
            self.skipTest("OSRACER_BASE_SOURCE not supplied")

        source = Path(source_value).resolve()
        self.assertTrue((source / ".git").exists(), source)
        result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), BASE_SHA)

        package_root = ET.parse(source / "package.xml").getroot()
        self.assertEqual(package_root.findtext("name"), "osracer_base")
        self.assertEqual(package_root.findtext("version"), "0.2.0")

        declared_parameters = set()
        base_source = []
        for path in source.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            base_source.append(text)
            tree = ast.parse(text, filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr != "declare_parameter" or not node.args:
                    continue
                name = _constant_string(node.args[0])
                if name is not None:
                    declared_parameters.add(name)

        self.assertTrue(
            {
                "port",
                "baudrate",
                "vehicle_profile",
                "profile_schema",
                "wheelbase",
                "max_speed",
                "speed_mode",
                "max_steering_angle",
                "cmd_timeout",
                "reconnect_interval",
                "firmware_version_timeout",
                "connection_status_enabled",
                "connection_refresh_period",
                "odom_frame_id",
                "base_frame_id",
                "imu_frame_id",
                "publish_tf",
            }.issubset(declared_parameters)
        )
        joined_source = "\n".join(base_source)
        self.assertIn("AckermannDrive", joined_source)
        self.assertIn("ackermann_cmd", joined_source)

    def test_ci_runs_runtime_smoke_and_simulated_profile_mismatch(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "ConnectionLifecycleTests."
            "test_unsupported_protocol_and_profile_mismatch_fail_before_stream",
            workflow,
        )
        self.assertIn("timeout --signal=INT", workflow)
        self.assertIn("port_name:=/dev/osracer_ci_missing", workflow)
        self.assertIn('test "$smoke_status" -eq 124', workflow)
        self.assertIn("vehicle_profile must be selected explicitly", workflow)


if __name__ == "__main__":
    unittest.main()
