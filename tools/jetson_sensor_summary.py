#!/usr/bin/env python3
"""Summarize logs produced by tools/jetson_sensor_preflight.sh."""

import argparse
import json
import re
import shlex
from pathlib import Path

DEFAULT_REQUIRED_TOPICS = ("/rgb/image_raw", "/scan", "/imu_filter", "/odometry/filtered")


def parse_args():
    parser = argparse.ArgumentParser(description="Summarize OSRacer Jetson sensor preflight logs.")
    parser.add_argument("log_dir", help="Directory created by tools/jetson_sensor_preflight.sh")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--output", default=None, help="Write JSON report to this path")
    parser.add_argument("--strict", action="store_true", help="Return non-zero when required sensor topics are missing")
    parser.add_argument(
        "--required-topic",
        action="append",
        default=[],
        help="Required topic. May be repeated. Defaults to camera/lidar/IMU/odom topics.",
    )
    return parser.parse_args()


def read_text(path):
    if not path.is_file():
        return ""
    return path.read_text(errors="replace")


def read_lines(path):
    return read_text(path).splitlines()


def command_from_log(path):
    lines = read_lines(path)
    if not lines or not lines[0].startswith("$ "):
        return []
    try:
        return shlex.split(lines[0][2:])
    except ValueError:
        return []


def topic_from_info_log(path):
    command = command_from_log(path)
    if len(command) >= 4 and command[:3] == ["ros2", "topic", "info"]:
        return command[3]
    return None


def parse_topic_info(path):
    text = read_text(path)
    topic = topic_from_info_log(path)
    result = {
        "topic": topic,
        "type": None,
        "publisher_count": None,
        "subscription_count": None,
        "warnings": [],
    }
    for line in text.splitlines():
        stripped = line.strip()
        type_match = re.match(r"Type:\s*(.+)", stripped)
        pub_match = re.match(r"Publisher count:\s*(\d+)", stripped)
        sub_match = re.match(r"Subscription count:\s*(\d+)", stripped)
        if type_match:
            result["type"] = type_match.group(1)
        elif pub_match:
            result["publisher_count"] = int(pub_match.group(1))
        elif sub_match:
            result["subscription_count"] = int(sub_match.group(1))
        elif "Unknown topic" in stripped or "not found" in stripped.lower() or "Traceback" in stripped:
            result["warnings"].append(stripped)
    return result


def parse_topic_hz(path):
    text = read_text(path)
    result = {"average_rate_hz": None, "min_delta_s": None, "max_delta_s": None, "warnings": []}
    for line in text.splitlines():
        stripped = line.strip()
        avg = re.search(r"average rate:\s*([0-9.]+)", stripped)
        minmax = re.search(r"min:\s*([0-9.]+)s\s+max:\s*([0-9.]+)s", stripped)
        if avg:
            result["average_rate_hz"] = float(avg.group(1))
        if minmax:
            result["min_delta_s"] = float(minmax.group(1))
            result["max_delta_s"] = float(minmax.group(2))
        if "WARNING:" in stripped or "ERROR:" in stripped or "Traceback" in stripped:
            result["warnings"].append(stripped)
    if text and result["average_rate_hz"] is None and "subscribed to" not in text:
        result["warnings"].append("no topic hz samples parsed")
    return result


def parse_topics(log_dir):
    topics = {}
    for info_path in sorted(log_dir.glob("ros2_topic_info_*.log")):
        info = parse_topic_info(info_path)
        topic = info.pop("topic")
        if not topic:
            continue
        topics.setdefault(topic, {})["info"] = info
        hz_path = log_dir / info_path.name.replace("ros2_topic_info_", "ros2_topic_hz_", 1)
        topics[topic]["hz"] = parse_topic_hz(hz_path)
    return topics


def parse_list_log(path):
    text = read_text(path)
    if not text.strip():
        return []
    return [line for line in text.splitlines()[1:] if line.strip()]


def parse_devices(log_dir):
    video_lines = parse_list_log(log_dir / "ls_dev_video.log")
    chassis_lines = parse_list_log(log_dir / "ls_dev_osrbot.log")
    usb_text = read_text(log_dir / "usb_devices.log")
    return {
        "video_devices": [line for line in video_lines if "/dev/video" in line],
        "chassis_serial_devices": [line for line in chassis_lines if "/dev/" in line],
        "usb_lines": [line for line in usb_text.splitlines()[1:] if line.strip()],
        "v4l2_available": bool(list(log_dir.glob("v4l2_*_formats.log"))),
    }


def parse_network(log_dir):
    brief = parse_list_log(log_dir / "network_brief.log")
    links = {}
    for path in sorted(log_dir.glob("ethtool_*.log")):
        iface = path.stem.replace("ethtool_", "", 1)
        text = read_text(path)
        speed = re.search(r"Speed:\s*(.+)", text)
        detected = re.search(r"Link detected:\s*(.+)", text)
        links[iface] = {
            "speed": speed.group(1).strip() if speed else None,
            "link_detected": detected.group(1).strip() if detected else None,
        }
    return {"interfaces": brief, "ethtool": links}


def topic_status(topic, data):
    info = data.get("info", {})
    hz = data.get("hz", {})
    publishers = info.get("publisher_count")
    rate = hz.get("average_rate_hz")
    warnings = info.get("warnings", []) + hz.get("warnings", [])
    if publishers is None:
        return "unknown"
    if publishers <= 0:
        return "missing"
    if rate is None:
        return "no_rate"
    if warnings:
        return "warn"
    return "ok"


def build_report(log_dir, required_topics):
    topics = parse_topics(log_dir)
    required = list(required_topics or DEFAULT_REQUIRED_TOPICS)
    missing_required = []
    for topic in required:
        if topic_status(topic, topics.get(topic, {})) not in {"ok", "warn"}:
            missing_required.append(topic)
    return {
        "log_dir": str(log_dir),
        "devices": parse_devices(log_dir),
        "network": parse_network(log_dir),
        "topics": topics,
        "required_topics": required,
        "missing_required_topics": missing_required,
        "overall": "pass" if not missing_required else "fail",
    }


def print_text(report):
    print(f"sensor_preflight_summary: {report['overall']}")
    print(f"log_dir: {report['log_dir']}")
    devices = report["devices"]
    print(f"video_devices: {len(devices['video_devices'])}")
    print(f"chassis_serial_devices: {len(devices['chassis_serial_devices'])}")
    print(f"v4l2_available: {devices['v4l2_available']}")
    print("topics:")
    for topic, data in sorted(report["topics"].items()):
        info = data.get("info", {})
        hz = data.get("hz", {})
        print(
            f"  {topic}: status={topic_status(topic, data)} type={info.get('type')} "
            f"publishers={info.get('publisher_count')} avg_hz={hz.get('average_rate_hz')}"
        )
    if report["missing_required_topics"]:
        print("missing_required_topics:")
        for topic in report["missing_required_topics"]:
            print(f"  - {topic}")


def main():
    args = parse_args()
    log_dir = Path(args.log_dir)
    report = build_report(log_dir, args.required_topic)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"wrote {output}")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_text(report)
    return 1 if args.strict and report["overall"] != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())
