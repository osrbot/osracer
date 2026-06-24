#!/usr/bin/env python3
"""Create a structured OSRacer Jetson runtime environment report."""

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_REQUIRED_COMMANDS = ("python3", "ros2", "nvpmodel", "jetson_clocks", "tegrastats")
DEFAULT_REQUIRED_MODULES = ("rclpy", "ackermann_msgs", "nav_msgs", "sensor_msgs", "geometry_msgs", "torch")


def parse_args():
    parser = argparse.ArgumentParser(description="Write a structured Jetson environment report for OSRacer.")
    parser.add_argument("--output", default=None, help="Optional JSON output path")
    parser.add_argument("--json", action="store_true", help="Print full JSON report")
    parser.add_argument("--ros-distro", default=os.environ.get("ROS_DISTRO", "humble"))
    parser.add_argument("--allow-non-jetson", action="store_true", help="Do not fail only because this host is not a Jetson image")
    parser.add_argument("--required-command", action="append", default=[], help="Required command; may be repeated")
    parser.add_argument("--required-python-module", action="append", default=[], help="Required Python module; may be repeated")
    return parser.parse_args()


def read_text(path):
    try:
        return Path(path).read_text(errors="replace").replace("\x00", "").strip()
    except OSError:
        return None


def run_output(command, timeout=5):
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=timeout).strip()
    except Exception as exc:
        return f"unavailable: {exc}"


def command_info(name):
    path = shutil.which(name)
    return {"available": path is not None, "path": path}


def python_module_info(name):
    spec = importlib.util.find_spec(name)
    info = {"available": spec is not None, "version": None}
    if spec is None:
        return info
    try:
        module = __import__(name)
        version = getattr(module, "__version__", None)
        if version is not None:
            info["version"] = str(version)
    except Exception as exc:
        info["version_error"] = str(exc)
    return info


def meminfo_value(key):
    text = read_text("/proc/meminfo") or ""
    prefix = f"{key}:"
    for line in text.splitlines():
        if line.startswith(prefix):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return None


def build_report(args):
    required_commands = tuple(args.required_command or DEFAULT_REQUIRED_COMMANDS)
    required_modules = tuple(args.required_python_module or DEFAULT_REQUIRED_MODULES)
    nv_tegra = read_text("/etc/nv_tegra_release")
    device_model = read_text("/proc/device-tree/model")
    is_jetson = bool(nv_tegra or (device_model and "NVIDIA" in device_model.upper()))
    commands = {name: command_info(name) for name in sorted(set(required_commands + ("trtexec",)))}
    modules = {name: python_module_info(name) for name in sorted(set(required_modules + ("onnx", "tensorrt")))}
    ros_setup = f"/opt/ros/{args.ros_distro}/setup.bash"
    failures = []
    warnings = []
    if not is_jetson and not args.allow_non_jetson:
        failures.append("not running on a Jetson image")
    elif not is_jetson:
        warnings.append("not running on a Jetson image")
    for name in required_commands:
        if not commands.get(name, {}).get("available"):
            failures.append(f"required command missing: {name}")
    for name in required_modules:
        if not modules.get(name, {}).get("available"):
            failures.append(f"required Python module missing: {name}")
    if not Path(ros_setup).is_file():
        failures.append(f"ROS setup missing: {ros_setup}")
    report = {
        "overall": "pass" if not failures else "fail",
        "failures": failures,
        "warnings": warnings,
        "platform": {
            "machine": platform.machine(),
            "system": platform.system(),
            "release": platform.release(),
            "python_executable": sys.executable,
            "python_version": sys.version.replace("\n", " "),
        },
        "jetson": {
            "is_jetson": is_jetson,
            "nv_tegra_release": nv_tegra,
            "device_model": device_model,
            "nvpmodel_query": run_output(["nvpmodel", "-q"]) if commands.get("nvpmodel", {}).get("available") else None,
        },
        "ros": {
            "distro": args.ros_distro,
            "setup_path": ros_setup,
            "setup_exists": Path(ros_setup).is_file(),
        },
        "commands": commands,
        "python_modules": modules,
        "memory": {
            "mem_total_kb": meminfo_value("MemTotal"),
            "mem_available_kb": meminfo_value("MemAvailable"),
            "swap_total_kb": meminfo_value("SwapTotal"),
            "swap_free_kb": meminfo_value("SwapFree"),
        },
    }
    return report


def print_text(report):
    print(f"jetson_environment_report: {report['overall']}")
    print(f"is_jetson: {report['jetson']['is_jetson']}")
    print(f"ros_setup_exists: {report['ros']['setup_exists']} ({report['ros']['setup_path']})")
    if report["failures"]:
        print("failures:")
        for item in report["failures"]:
            print(f"  - {item}")
    if report["warnings"]:
        print("warnings:")
        for item in report["warnings"]:
            print(f"  - {item}")


def main():
    args = parse_args()
    report = build_report(args)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {output}")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_text(report)
    return 0 if report["overall"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
