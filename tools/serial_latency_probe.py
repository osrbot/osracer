#!/usr/bin/env python3
"""Measure OSRacer firmware serial query response latency with read-only commands."""

import argparse
import datetime as dt
import json
import re
import statistics
import time
from pathlib import Path

try:
    import serial
except ImportError:  # pragma: no cover - exercised on hosts without pyserial
    serial = None


def parse_args():
    parser = argparse.ArgumentParser(
        description="Measure /dev/osrbot_base response latency using a read-only firmware query."
    )
    parser.add_argument("--port", default="/dev/osrbot_base", help="Serial device. Default: /dev/osrbot_base")
    parser.add_argument("--baud", type=int, default=460800, help="Serial baud rate. Default: 460800")
    parser.add_argument("--command", default="sn get", help="Read-only command to send. Default: sn get")
    parser.add_argument("--samples", type=int, default=5, help="Number of command samples. Default: 5")
    parser.add_argument("--timeout", type=float, default=0.5, help="Read/write timeout per sample in seconds. Default: 0.5")
    parser.add_argument("--settle", type=float, default=0.05, help="Delay between samples in seconds. Default: 0.05")
    parser.add_argument(
        "--response-regex",
        default=r"(?i)(sn|serial|osrcore|osr|[0-9a-f]{8,})",
        help="Regex used to identify the command response line.",
    )
    parser.add_argument("--output", default=None, help="Optional JSON output path.")
    return parser.parse_args()


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def summarize(latencies):
    if not latencies:
        return {"min": None, "max": None, "mean": None, "median": None, "p95": None}
    return {
        "min": min(latencies),
        "max": max(latencies),
        "mean": statistics.mean(latencies),
        "median": statistics.median(latencies),
        "p95": percentile(latencies, 0.95),
    }


def read_matching_line(conn, pattern, timeout_s):
    deadline = time.monotonic() + timeout_s
    seen = []
    while time.monotonic() < deadline:
        raw = conn.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        seen.append(line)
        if pattern.search(line):
            return line, seen
    return None, seen


def measure(args):
    if serial is None:
        raise RuntimeError("pyserial is not installed; install python3-serial")
    if args.samples <= 0:
        raise ValueError("--samples must be positive")
    pattern = re.compile(args.response_regex)
    samples = []
    warnings = [
        "Stop chassis_ackermann or any other process using the same serial port before running this probe.",
        "The default command is read-only: sn get. This tool does not send velocity or steering commands.",
    ]
    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=args.timeout) as conn:
        conn.reset_input_buffer()
        conn.reset_output_buffer()
        for index in range(args.samples):
            command = args.command.strip() + "\n"
            started = time.perf_counter()
            conn.write(command.encode("utf-8"))
            conn.flush()
            response, seen = read_matching_line(conn, pattern, args.timeout)
            ended = time.perf_counter()
            sample = {
                "index": index,
                "ok": response is not None,
                "latency_s": ended - started if response is not None else None,
                "response": response,
                "seen_lines": seen[-5:],
            }
            samples.append(sample)
            time.sleep(args.settle)
    latencies = [sample["latency_s"] for sample in samples if sample["latency_s"] is not None]
    return {
        "measured_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "port": args.port,
        "baud_rate": args.baud,
        "command": args.command,
        "samples_requested": args.samples,
        "successful_samples": len(latencies),
        "latency_s": summarize(latencies),
        "samples": samples,
        "warnings": warnings,
        "overall": "pass" if latencies else "fail",
    }


def main():
    args = parse_args()
    report = measure(args)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"wrote {output}")
    print(text, end="")
    return 0 if report["overall"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
