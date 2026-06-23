#!/usr/bin/env python3
"""Summarize logs produced by tools/jetson_runtime_monitor.sh."""

import argparse
import json
import re
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Summarize OSRacer Jetson runtime monitor logs.")
    parser.add_argument("log_dir", help="Directory created by tools/jetson_runtime_monitor.sh")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser.parse_args()


def read_lines(path):
    if not path.is_file():
        return []
    return path.read_text(errors="replace").splitlines()


def parse_summary(log_dir):
    lines = read_lines(log_dir / "summary.log")
    return {
        "warnings": [line for line in lines if "[WARN]" in line],
        "errors": [line for line in lines if "[FAIL]" in line or "[ERROR]" in line],
        "lines": lines,
    }


def parse_topic_hz(log_dir):
    topics = {}
    current = None
    for line in read_lines(log_dir / "topic_hz.log"):
        match = re.match(r"== (.+) ==", line)
        if match:
            current = match.group(1)
            topics.setdefault(current, {"warnings": [], "average_rate_hz": None, "min_delta_s": None, "max_delta_s": None})
            continue
        if current is None:
            continue
        if "WARNING:" in line:
            topics[current]["warnings"].append(line.strip())
        avg = re.search(r"average rate:\s*([0-9.]+)", line)
        if avg:
            topics[current]["average_rate_hz"] = float(avg.group(1))
        minmax = re.search(r"min:\s*([0-9.]+)s\s+max:\s*([0-9.]+)s", line)
        if minmax:
            topics[current]["min_delta_s"] = float(minmax.group(1))
            topics[current]["max_delta_s"] = float(minmax.group(2))
    return topics


def parse_process_resources(log_dir):
    processes = {}
    current = None
    for line in read_lines(log_dir / "process_resources.log"):
        proc_match = re.match(r"-- (.+) --", line)
        if proc_match:
            current = proc_match.group(1)
            processes.setdefault(current, {"samples": 0, "max_cpu_pct": 0.0, "max_mem_pct": 0.0, "max_rss_kb": 0})
            continue
        if current is None or not line.strip() or line.lstrip().startswith("PID"):
            continue
        fields = line.split(maxsplit=6)
        if len(fields) < 7 or not fields[0].isdigit():
            continue
        try:
            cpu = float(fields[2])
            mem = float(fields[3])
            rss = int(fields[4])
        except ValueError:
            continue
        item = processes[current]
        item["samples"] += 1
        item["max_cpu_pct"] = max(item["max_cpu_pct"], cpu)
        item["max_mem_pct"] = max(item["max_mem_pct"], mem)
        item["max_rss_kb"] = max(item["max_rss_kb"], rss)
    return processes


def parse_tegrastats(log_dir):
    result = {
        "samples": 0,
        "max_ram_used_mb": None,
        "max_swap_used_mb": None,
        "max_temp_c": None,
        "max_power_mw": None,
    }
    temp_re = re.compile(r"([A-Za-z0-9_]+)@([0-9.]+)C")
    power_re = re.compile(r"\b(?:VDD_[A-Z0-9_]+|POM_[0-9A-Z_]+|SYS5V) ([0-9]+)/")
    for line in read_lines(log_dir / "tegrastats.log"):
        if not line.strip():
            continue
        result["samples"] += 1
        ram = re.search(r"RAM\s+([0-9]+)/([0-9]+)MB", line)
        if ram:
            result["max_ram_used_mb"] = max(result["max_ram_used_mb"] or 0, int(ram.group(1)))
        swap = re.search(r"SWAP\s+([0-9]+)/([0-9]+)MB", line)
        if swap:
            result["max_swap_used_mb"] = max(result["max_swap_used_mb"] or 0, int(swap.group(1)))
        for _, temp in temp_re.findall(line):
            result["max_temp_c"] = max(result["max_temp_c"] or 0.0, float(temp))
        for power in power_re.findall(line):
            result["max_power_mw"] = max(result["max_power_mw"] or 0, int(power))
    return result


def build_report(log_dir):
    return {
        "log_dir": str(log_dir),
        "summary": parse_summary(log_dir),
        "topics": parse_topic_hz(log_dir),
        "processes": parse_process_resources(log_dir),
        "tegrastats": parse_tegrastats(log_dir),
    }


def print_text(report):
    print(f"runtime_monitor_summary: {report['log_dir']}")
    warnings = report["summary"]["warnings"]
    errors = report["summary"]["errors"]
    print(f"warnings: {len(warnings)}")
    print(f"errors: {len(errors)}")
    print("topics:")
    for topic, data in sorted(report["topics"].items()):
        rate = data["average_rate_hz"]
        status = "missing" if data["warnings"] else "ok"
        print(f"  {topic}: status={status} avg_hz={rate}")
    print("processes:")
    for name, data in sorted(report["processes"].items()):
        print(
            f"  {name}: samples={data['samples']} max_cpu={data['max_cpu_pct']:.1f}% "
            f"max_mem={data['max_mem_pct']:.1f}% max_rss_kb={data['max_rss_kb']}"
        )
    teg = report["tegrastats"]
    print(
        "tegrastats: "
        f"samples={teg['samples']} max_ram_mb={teg['max_ram_used_mb']} "
        f"max_swap_mb={teg['max_swap_used_mb']} max_temp_c={teg['max_temp_c']} "
        f"max_power_mw={teg['max_power_mw']}"
    )
    for line in warnings:
        print(f"warning: {line}")
    for line in errors:
        print(f"error: {line}")


def main():
    args = parse_args()
    log_dir = Path(args.log_dir)
    report = build_report(log_dir)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_text(report)


if __name__ == "__main__":
    main()
