#!/usr/bin/env python3

import argparse
import csv
import math
from pathlib import Path


def parse_float(row, key):
    value = row.get(key, '')
    if value == '':
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def summarize_csv(path):
    samples = 0
    speeds = []
    command_speeds = []
    steering = []
    track_errors = []
    heading_errors = []
    with Path(path).open(newline='', encoding='utf-8') as handle:
        for row in csv.DictReader(handle):
            samples += 1
            speed = parse_float(row, 'speed_mps')
            command_speed = parse_float(row, 'command_speed_mps')
            command_steering = parse_float(row, 'command_steering_rad')
            track_error = parse_float(row, 'track_error_m')
            heading_error = parse_float(row, 'heading_error_rad')
            if speed is not None:
                speeds.append(abs(speed))
            if command_speed is not None:
                command_speeds.append(abs(command_speed))
            if command_steering is not None:
                steering.append(abs(command_steering))
            if track_error is not None:
                track_errors.append(abs(track_error))
            if heading_error is not None:
                heading_errors.append(abs(heading_error))

    return {
        'file': str(path),
        'samples': samples,
        'speed_samples': len(speeds),
        'track_error_samples': len(track_errors),
        'heading_error_samples': len(heading_errors),
        'max_speed_mps': max(speeds) if speeds else 0.0,
        'mean_speed_mps': sum(speeds) / len(speeds) if speeds else 0.0,
        'max_command_speed_mps': max(command_speeds) if command_speeds else 0.0,
        'mean_abs_track_error_m': sum(track_errors) / len(track_errors) if track_errors else None,
        'max_abs_track_error_m': max(track_errors) if track_errors else None,
        'mean_abs_heading_error_rad': sum(heading_errors) / len(heading_errors) if heading_errors else None,
        'max_abs_steering_rad': max(steering) if steering else 0.0,
    }


def format_optional(value, unit):
    if value is None:
        return 'N/A'
    return f'{value:.3f}{unit}'


def print_summary(summary):
    print(
        f"{summary['file']}: "
        f"samples={summary['samples']} "
        f"max_speed={summary['max_speed_mps']:.3f}m/s "
        f"mean_speed={summary['mean_speed_mps']:.3f}m/s "
        f"track_error_samples={summary['track_error_samples']} "
        f"mean_track_error={format_optional(summary['mean_abs_track_error_m'], 'm')} "
        f"max_track_error={format_optional(summary['max_abs_track_error_m'], 'm')} "
        f"mean_heading_error={format_optional(summary['mean_abs_heading_error_rad'], 'rad')} "
        f"max_steering={summary['max_abs_steering_rad']:.3f}rad"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description='Summarize OSRacer race evaluation CSV logs.')
    parser.add_argument('csv_files', nargs='+')
    args = parser.parse_args(argv)
    for csv_file in args.csv_files:
        print_summary(summarize_csv(csv_file))


if __name__ == '__main__':
    main()
