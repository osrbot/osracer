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
BASE_SHA = "6f9fabee09b9f6fe90d78497ba25c1f388a5e885"
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

    def test_navigation_uses_installed_standard_backup_behavior(self):
        obsolete_parameters = {
            "default_distance",
            "default_speed",
            "fallback_recovery_direction",
            "enable_second_phase",
            "first_phase_distance_ratio",
            "second_phase_distance_ratio",
            "min_exit_clearance",
            "front_sector_deg",
            "rear_sector_deg",
            "clear_local_costmap",
            "clear_global_costmap",
            "local_clear_service",
            "global_clear_service",
            "costmap_clear_wait_ms",
        }
        for name in ("dwb_nav2_params.yaml", "teb_nav2_params.yaml"):
            source = (ROOT / "osracer_navigation" / "params" / name).read_text(
                encoding="utf-8"
            )
            self.assertEqual(source.count('plugin: "nav2_behaviors/BackUp"'), 1)
            self.assertNotIn("osracer_aggressive_backup", source)
            for parameter in obsolete_parameters:
                self.assertNotIn(f"{parameter}:", source)

        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("validate_behavior_server.sh", workflow)

    def test_product_launch_keeps_only_product_runtime_defaults(self):
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
        self.assertNotIn("wheelbase", defaults)
        self.assertNotIn("max_speed", defaults)
        self.assertNotIn("max_steering_angle", defaults)
        self.assertNotIn("max_steering_angle_deg", defaults)
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

    def test_launch_loads_base_red_profile_before_product_overrides(self):
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
        self.assertEqual(len(parameters.elts), 2)
        self.assertIsInstance(parameters.elts[0], ast.Name)
        self.assertEqual(parameters.elts[0].id, "base_profile")
        parameter_dict = parameters.elts[1]
        self.assertIsInstance(parameter_dict, ast.Dict)
        parameter_names = {_constant_string(key) for key in parameter_dict.keys}
        self.assertEqual(
            parameter_names,
            {
                "port",
                "baudrate",
                "odom_frame_id",
                "base_frame_id",
                "imu_frame_id",
                "cmd_timeout",
                "reconnect_interval",
                "firmware_version_timeout",
                "connection_status_enabled",
                "connection_refresh_period",
                "publish_tf",
            },
        )
        self.assertIn("FindPackageShare('osracer_base')", self.launch_source)
        self.assertIn("'config', 'vehicles', 'red.yaml'", self.launch_source)
        for duplicated in (
            "'vehicle_profile'",
            "'profile_schema'",
            "'wheelbase'",
            "'max_speed'",
            "'speed_mode'",
            "'max_steering_angle'",
        ):
            self.assertNotIn(duplicated, ast.unparse(parameter_dict))

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

        firmware_contract = json.loads(
            (
                source
                / "test/fixtures/proto_1_1/firmware_contract.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(firmware_contract),
            {"schema_version", "protocol", "command", "profiles"},
        )
        self.assertEqual(firmware_contract["protocol"], "1.1")
        self.assertEqual(
            firmware_contract["command"],
            {
                "name": "v",
                "linear_velocity_unit": "m/s",
                "steering_angle_unit": "deg",
            },
        )
        self.assertEqual(
            firmware_contract["profiles"]["red"], {"profile_schema": 1}
        )
        for profile in firmware_contract["profiles"].values():
            self.assertEqual(set(profile), {"profile_schema"})

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

        profile_text = (source / "config/vehicles/red.yaml").read_text(encoding="utf-8")
        for expected in (
            "vehicle_profile: red",
            "profile_schema: 1",
            "wheelbase: 0.285",
            "max_speed: 4.64",
            "speed_mode: high",
            "max_steering_angle: 0.5235987756",
        ):
            self.assertIn(expected, profile_text)

    def test_reference_packages_load_the_same_base_profile(self):
        for package in ("osracer_race", "osracer_sim"):
            package_root = ET.parse(ROOT / package / "package.xml").getroot()
            dependencies = {
                element.text
                for tag in ("depend", "exec_depend")
                for element in package_root.findall(tag)
            }
            self.assertIn("osracer_base", dependencies)

        for path in sorted((ROOT / "osracer_race" / "launch").glob("*.launch.py")):
            source = path.read_text(encoding="utf-8")
            self.assertIn("FindPackageShare('osracer_base')", source, path)
            self.assertIn("'config', 'vehicles', 'red.yaml'", source, path)

        for path in (
            ROOT / "osracer_sim/launch/base_sim.launch.py",
            ROOT / "osracer_sim/launch/gazebo.launch.py",
        ):
            source = path.read_text(encoding="utf-8")
            self.assertIn("FindPackageShare('osracer_base')", source, path)
            self.assertIn("'config', 'vehicles', 'red.yaml'", source, path)

    def test_description_geometry_matches_approved_vehicle_projection(self):
        root = ET.parse(ROOT / "osracer_description/urdf/osracer.urdf").getroot()
        joints = {}
        for joint in root.findall("joint"):
            origin = joint.find("origin")
            if origin is not None:
                joints[joint.attrib["name"]] = tuple(
                    float(value)
                    for value in origin.attrib.get("xyz", "0 0 0").split()
                )

        front_left = tuple(
            left + wheel
            for left, wheel in zip(
                joints["left_steering_hinge_joint"],
                joints["Left_front_wheel_joint"],
            )
        )
        front_right = tuple(
            right + wheel
            for right, wheel in zip(
                joints["right_steering_hinge_joint"],
                joints["right_front_wheel_joint"],
            )
        )
        rear_left = joints["left_rear_wheel_joint"]
        rear_right = joints["right_rear_wheel_joint"]
        wheelbase = (
            (front_left[0] + front_right[0])
            - (rear_left[0] + rear_right[0])
        ) / 2.0

        self.assertAlmostEqual(wheelbase, 0.285, places=9)
        self.assertAlmostEqual(front_left[1] - front_right[1], 0.215, delta=1e-5)
        self.assertAlmostEqual(rear_left[1] - rear_right[1], 0.215, delta=1e-5)

        wheel_links = {
            "Left_front_wheel_link",
            "right_front_wheel_link",
            "left_rear_wheel_link",
            "right_rear_wheel_link",
        }
        scales = []
        for link in root.findall("link"):
            if link.attrib.get("name") not in wheel_links:
                continue
            for kind in ("visual", "collision"):
                mesh = link.find(f"{kind}/geometry/mesh")
                self.assertIsNotNone(mesh)
                scales.append(
                    tuple(float(value) for value in mesh.attrib["scale"].split())
                )
        self.assertEqual(scales, [(0.941, 1.0, 0.941)] * 8)

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
