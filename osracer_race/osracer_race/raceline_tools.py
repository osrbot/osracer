#!/usr/bin/env python3

import argparse
import csv
import math
from pathlib import Path


def load_points(path):
    points = []
    with Path(path).open(newline='', encoding='utf-8') as handle:
        for row in csv.reader(handle):
            if not row or row[0].strip().startswith('#'):
                continue
            if len(row) < 2:
                continue
            try:
                speed = float(row[2]) if len(row) >= 3 and row[2] else None
                points.append((float(row[0]), float(row[1]), speed))
            except ValueError:
                if not points:
                    continue
                raise
    return points


def triangle_curvature(prev_point, point, next_point):
    ax, ay = prev_point[:2]
    bx, by = point[:2]
    cx, cy = next_point[:2]
    ab = math.hypot(bx - ax, by - ay)
    bc = math.hypot(cx - bx, cy - by)
    ca = math.hypot(ax - cx, ay - cy)
    area2 = abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
    denom = ab * bc * ca
    if denom < 1e-9:
        return 0.0
    return 2.0 * area2 / denom


def build_profile(points, max_speed, min_speed, max_lateral_accel, closed=True):
    profiled = []
    count = len(points)
    for idx, point in enumerate(points):
        if count < 3:
            curvature = 0.0
        elif closed:
            curvature = triangle_curvature(points[idx - 1], point, points[(idx + 1) % count])
        elif idx == 0 or idx == count - 1:
            curvature = 0.0
        else:
            curvature = triangle_curvature(points[idx - 1], point, points[idx + 1])

        curve_speed = max_speed if curvature < 1e-6 else math.sqrt(max_lateral_accel / curvature)
        requested_speed = point[2] if point[2] is not None else max_speed
        speed = max(min_speed, min(max_speed, requested_speed, curve_speed))
        profiled.append((point[0], point[1], speed, curvature))
    return profiled


def write_profile(path, profiled):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['# x', 'y', 'speed', 'curvature'])
        for row in profiled:
            writer.writerow([f'{row[0]:.4f}', f'{row[1]:.4f}', f'{row[2]:.3f}', f'{row[3]:.6f}'])


def main(argv=None):
    parser = argparse.ArgumentParser(description='Generate OSRacer raceline speed profile.')
    parser.add_argument('input_csv')
    parser.add_argument('output_csv')
    parser.add_argument('--max-speed', type=float, default=3.0)
    parser.add_argument('--min-speed', type=float, default=0.8)
    parser.add_argument('--max-lateral-accel', type=float, default=4.5)
    parser.add_argument('--open', action='store_true', help='Treat raceline as open path instead of loop')
    args = parser.parse_args(argv)

    points = load_points(args.input_csv)
    if len(points) < 2:
        raise SystemExit('raceline needs at least two points')
    profiled = build_profile(
        points,
        max_speed=args.max_speed,
        min_speed=args.min_speed,
        max_lateral_accel=args.max_lateral_accel,
        closed=not args.open,
    )
    write_profile(args.output_csv, profiled)
    print(f'wrote {args.output_csv}')


if __name__ == '__main__':
    main()
