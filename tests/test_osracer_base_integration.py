#!/usr/bin/env python3
"""Static integration checks for the pinned osracer_base runtime dependency."""

import ast
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
BASE_SHA = "7f86bd99100bd5f3acb866077c8f4623c1f93565"
BASE_URL = "https://github.com/osrbot/osracer_base.git"
DEPENDENCY_SHA = "4317556c6ea38bd149144136c9dbee6a53a1076e"
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

    def test_navigation_uses_pinned_aggressive_backup_behavior(self):
        required_parameters = {
            "default_distance",
            "default_speed",
            "odom_topic",
            "stopped_velocity_threshold",
            "fallback_recovery_direction",
            "enable_second_phase",
            "scan_topic",
            "scan_base_frame",
            "first_phase_distance_ratio",
            "second_phase_distance_ratio",
            "min_exit_clearance",
            "front_sector_deg",
            "rear_sector_deg",
            "scan_timeout",
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
            self.assertEqual(
                source.count('plugin: "osracer_aggressive_backup/AggressiveBackUp"'),
                1,
            )
            self.assertNotIn('plugin: "nav2_behaviors/BackUp"', source)
            for parameter in required_parameters:
                self.assertIn(f"{parameter}:", source)

        package_root = ET.parse(ROOT / "osracer_navigation" / "package.xml").getroot()
        dependencies = {element.text for element in package_root.findall("exec_depend")}
        self.assertIn("osracer_aggressive_backup", dependencies)
        self.assertIn("nav2_behaviors", dependencies)

        dependency_root = ROOT / "osracer_dependency"
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(dependency_root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            DEPENDENCY_SHA,
        )
        plugin_root = ET.parse(
            dependency_root / "osracer_aggressive_backup" / "package.xml"
        ).getroot()
        self.assertEqual(plugin_root.findtext("name"), "osracer_aggressive_backup")

        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("submodules: recursive", workflow)
        self.assertIn(DEPENDENCY_SHA, workflow)
        self.assertIn("Lakibeam_ROS2_Driver", workflow)
        self.assertIn("camera_calibration", workflow)
        self.assertIn("ros2_gmapping", workflow)
        self.assertIn("teb_local_planner", workflow)
        self.assertIn("osracer_dependency/$dependency_path/COLCON_IGNORE", workflow)
        self.assertNotIn("cp -a", workflow)
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
        self.assertEqual(defaults["odom_frame"], "odom")
        self.assertEqual(defaults["base_frame"], "base_footprint")
        self.assertEqual(defaults["imu_frame"], "imu_link")
        self.assertEqual(defaults["map_frame"], "map")
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

    def test_launch_passes_only_runtime_and_tf_parameters(self):
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
        self.assertEqual(len(parameters.elts), 1)
        parameter_dict = parameters.elts[0]
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
        self.assertNotIn("FindPackageShare('osracer_base')", self.launch_source)
        self.assertNotIn("config/vehicles", self.launch_source)
        self.assertNotIn("base_profile", self.launch_source)
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

    def test_imported_base_source_matches_exact_published_pin(self):
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
        self.assertEqual(package_root.findtext("version"), "0.3.0")

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

    def test_race_and_simulation_keep_model_parameters_local(self):
        for package in ("osracer_race", "osracer_sim"):
            package_root = ET.parse(ROOT / package / "package.xml").getroot()
            dependencies = {
                element.text
                for tag in ("depend", "exec_depend")
                for element in package_root.findall(tag)
            }
            self.assertNotIn("osracer_base", dependencies)

        for path in sorted((ROOT / "osracer_race" / "launch").glob("*.launch.py")):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("FindPackageShare('osracer_base')", source, path)
            self.assertNotIn("config/vehicles", source, path)
            self.assertIn("FindPackageShare('osracer_race')", source, path)
            self.assertIn("'config', 'vehicle.yaml'", source, path)

        for path in sorted((ROOT / "osracer_sim" / "launch").glob("*.launch.py")):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("FindPackageShare('osracer_base')", source, path)
            self.assertNotIn("config/vehicles", source, path)

        base_sim = (ROOT / "osracer_sim/launch/base_sim.launch.py").read_text(
            encoding="utf-8"
        )
        for name, default in (
            ("wheelbase", "0.285"),
            ("max_speed", "4.64"),
            ("max_steering_angle", "0.5235987756"),
        ):
            self.assertIn(
                f"DeclareLaunchArgument('{name}', default_value='{default}')",
                base_sim,
            )
            self.assertIn(f"LaunchConfiguration('{name}')", base_sim)
        self.assertEqual(base_sim.count("executable='gazebo_ackermann_bridge_node'"), 1)

        gazebo = (ROOT / "osracer_sim/launch/gazebo.launch.py").read_text(
            encoding="utf-8"
        )
        for name in (
            "wheelbase",
            "track_width",
            "wheel_radius",
            "max_speed",
            "max_steering_angle",
        ):
            self.assertNotIn(f"DeclareLaunchArgument('{name}'", gazebo)
        self.assertIn("'use_gz_control': LaunchConfiguration('use_gz_control')", gazebo)
        self.assertIn("'ackermann_topic': LaunchConfiguration('ackermann_topic')", gazebo)
        self.assertNotIn("executable='gazebo_ackermann_bridge_node'", gazebo)

    def test_description_and_static_tf_files_are_unchanged(self):
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "osracer_description/**"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        tracked = [
            path for path in tracked if path != "osracer_description/package.xml"
        ]
        tracked.extend(
            [
                "osracer_navigation/launch/bringup_launch.py",
                "osracer_navigation/launch/localization_launch.py",
                "osracer_navigation/launch/navigation_launch.py",
                "osracer_navigation/launch/rviz_launch.py",
            ]
        )
        digest = sha256()
        for relative in sorted(set(tracked)):
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update((ROOT / relative).read_bytes())
            digest.update(b"\0")
        self.assertEqual(
            digest.hexdigest(),
            "b29560b7d998e291b8841d1f2d6aaaecd4367443b7a27266068e1cd88bbea846",
        )

        root = ET.parse(ROOT / "osracer_description/urdf/osracer.urdf").getroot()
        links = {}
        for joint in root.findall("joint"):
            links[joint.attrib["name"]] = (
                joint.find("parent").attrib["link"],
                joint.find("child").attrib["link"],
            )
        self.assertEqual(
            links["base_footprint_to_base_link"],
            ("base_footprint", "base_link"),
        )
        self.assertEqual(links["laser_joint"], ("base_link", "laser"))
        self.assertEqual(links["imu_joint"], ("base_link", "imu_link"))
        self.assertEqual(links["camera_joint"], ("base_link", "camera_link"))

    def test_ci_keeps_the_published_base_revision_exact(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn(BASE_SHA, workflow)
        self.assertNotIn("version: main", workflow)
        self.assertIn("timeout --signal=INT", workflow)
        self.assertIn("port_name:=/dev/osracer_ci_missing", workflow)
        self.assertIn('test "$smoke_status" -eq 124', workflow)
        self.assertIn(
            "test_protocol_profile_and_vehicle_contract_fail_before_stream",
            workflow,
        )
        self.assertNotIn(
            "test_unsupported_protocol_and_profile_mismatch_fail_before_stream",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
