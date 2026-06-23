#!/usr/bin/env python3

import argparse
import csv
import math
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Summarize an OSRacer policy replay CSV.")
    parser.add_argument("csv_path", help="CSV produced by tools/policy_replay_csv.py")
    parser.add_argument("--min-rows", type=int, default=1)
    parser.add_argument("--max-clamped-ratio", type=float, default=None)
    parser.add_argument("--max-speed-cmd", type=float, default=None)
    parser.add_argument("--max-abs-steering-cmd", type=float, default=None)
    parser.add_argument("--max-abs-raw-speed", type=float, default=None)
    parser.add_argument("--max-abs-raw-steering", type=float, default=None)
    return parser.parse_args()


def read_float(row, field, row_number):
    try:
        value = float(row[field])
    except KeyError as exc:
        raise ValueError(f"missing required column: {field}") from exc
    except ValueError as exc:
        raise ValueError(f"row {row_number}: invalid {field}={row.get(field)!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"row {row_number}: non-finite {field}={row.get(field)!r}")
    return value


def read_bool(row, field):
    value = row.get(field, "").strip().lower()
    return value in ("1", "true", "yes", "y")


def summarize(csv_path):
    rows = 0
    clamped = 0
    speed_cmd_values = []
    steering_cmd_values = []
    raw_speed_values = []
    raw_steering_values = []

    with Path(csv_path).open(newline="") as input_file:
        reader = csv.DictReader(input_file)
        if not reader.fieldnames:
            raise ValueError("input CSV has no header")
        for row_number, row in enumerate(reader, start=2):
            rows += 1
            speed_cmd_values.append(read_float(row, "speed_cmd", row_number))
            steering_cmd_values.append(read_float(row, "steering_cmd", row_number))
            raw_speed_values.append(read_float(row, "action_speed_raw", row_number))
            raw_steering_values.append(read_float(row, "action_steering_raw", row_number))
            clamped += int(read_bool(row, "clamped"))

    if rows == 0:
        raise ValueError("input CSV has no rows")

    return {
        "rows": rows,
        "clamped": clamped,
        "clamped_ratio": clamped / rows,
        "speed_cmd_min": min(speed_cmd_values),
        "speed_cmd_max": max(speed_cmd_values),
        "steering_cmd_min": min(steering_cmd_values),
        "steering_cmd_max": max(steering_cmd_values),
        "raw_speed_min": min(raw_speed_values),
        "raw_speed_max": max(raw_speed_values),
        "raw_steering_min": min(raw_steering_values),
        "raw_steering_max": max(raw_steering_values),
        "abs_steering_cmd_max": max(abs(v) for v in steering_cmd_values),
        "abs_raw_speed_max": max(abs(v) for v in raw_speed_values),
        "abs_raw_steering_max": max(abs(v) for v in raw_steering_values),
    }


def check_thresholds(summary, args):
    failures = []
    if summary["rows"] < args.min_rows:
        failures.append(f"rows {summary['rows']} < min_rows {args.min_rows}")
    if args.max_clamped_ratio is not None and summary["clamped_ratio"] > args.max_clamped_ratio:
        failures.append(f"clamped_ratio {summary['clamped_ratio']:.6g} > {args.max_clamped_ratio}")
    if args.max_speed_cmd is not None and summary["speed_cmd_max"] > args.max_speed_cmd:
        failures.append(f"speed_cmd_max {summary['speed_cmd_max']:.6g} > {args.max_speed_cmd}")
    if args.max_abs_steering_cmd is not None and summary["abs_steering_cmd_max"] > args.max_abs_steering_cmd:
        failures.append(
            f"abs_steering_cmd_max {summary['abs_steering_cmd_max']:.6g} > {args.max_abs_steering_cmd}"
        )
    if args.max_abs_raw_speed is not None and summary["abs_raw_speed_max"] > args.max_abs_raw_speed:
        failures.append(f"abs_raw_speed_max {summary['abs_raw_speed_max']:.6g} > {args.max_abs_raw_speed}")
    if args.max_abs_raw_steering is not None and summary["abs_raw_steering_max"] > args.max_abs_raw_steering:
        failures.append(
            f"abs_raw_steering_max {summary['abs_raw_steering_max']:.6g} > {args.max_abs_raw_steering}"
        )
    return failures


def print_summary(summary):
    for key in (
        "rows",
        "clamped",
        "clamped_ratio",
        "speed_cmd_min",
        "speed_cmd_max",
        "steering_cmd_min",
        "steering_cmd_max",
        "raw_speed_min",
        "raw_speed_max",
        "raw_steering_min",
        "raw_steering_max",
    ):
        value = summary[key]
        if isinstance(value, float):
            print(f"{key}={value:.9g}")
        else:
            print(f"{key}={value}")


def main():
    args = parse_args()
    try:
        summary = summarize(args.csv_path)
        print_summary(summary)
        failures = check_thresholds(summary, args)
    except Exception as exc:
        print(f"policy_replay_summary: {exc}", file=sys.stderr)
        return 1

    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
