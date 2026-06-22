from __future__ import annotations

import math
from typing import Optional

Segment = tuple[tuple[float, float], tuple[float, float]]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def yaw_to_quat(yaw: float) -> tuple[float, float, float, float]:
    half = yaw * 0.5
    return 0.0, 0.0, math.sin(half), math.cos(half)


def steering_from_twist(linear_x: float, angular_z: float, wheelbase: float) -> float:
    if abs(linear_x) < 1e-4 or abs(angular_z) < 1e-4:
        return 0.0
    return math.atan(wheelbase * angular_z / linear_x)


def ackermann_front_angles(center_steering: float, wheelbase: float, track_width: float) -> tuple[float, float]:
    if abs(center_steering) < 1e-5:
        return 0.0, 0.0
    tan_steer = math.tan(center_steering)
    left = math.atan2(tan_steer, 1.0 - track_width * tan_steer / (2.0 * wheelbase))
    right = math.atan2(tan_steer, 1.0 + track_width * tan_steer / (2.0 * wheelbase))
    return left, right


def synthetic_scan(points: int, angle_min: float, angle_increment: float, max_range: float) -> list[float]:
    ranges = []
    for index in range(points):
        angle = angle_min + index * angle_increment
        side_wall = max_range
        if abs(math.sin(angle)) > 1e-3:
            side_wall = 1.5 / abs(math.sin(angle))
        front_wall = max_range
        if math.cos(angle) > 1e-3:
            front_wall = 6.0 / math.cos(angle)
        ranges.append(min(max_range, side_wall, front_wall))
    return ranges


def rectangle_segments(length: float, width: float) -> list[Segment]:
    hx = length * 0.5
    hy = width * 0.5
    return [
        ((-hx, -hy), (hx, -hy)),
        ((hx, -hy), (hx, hy)),
        ((hx, hy), (-hx, hy)),
        ((-hx, hy), (-hx, -hy)),
    ]


def rectangular_track_segments(
    outer_length: float,
    outer_width: float,
    lane_width: float,
) -> list[Segment]:
    inner_length = max(outer_length - 2.0 * lane_width, lane_width)
    inner_width = max(outer_width - 2.0 * lane_width, lane_width)
    return rectangle_segments(outer_length, outer_width) + rectangle_segments(inner_length, inner_width)


def ray_segment_distance(
    origin_x: float,
    origin_y: float,
    angle: float,
    segment: Segment,
    max_range: float,
) -> Optional[float]:
    (x1, y1), (x2, y2) = segment
    ray_dx = math.cos(angle)
    ray_dy = math.sin(angle)
    seg_dx = x2 - x1
    seg_dy = y2 - y1
    denom = cross(ray_dx, ray_dy, seg_dx, seg_dy)
    if abs(denom) < 1e-9:
        return None
    diff_x = x1 - origin_x
    diff_y = y1 - origin_y
    distance = cross(diff_x, diff_y, seg_dx, seg_dy) / denom
    segment_u = cross(diff_x, diff_y, ray_dx, ray_dy) / denom
    if 0.0 <= distance <= max_range and 0.0 <= segment_u <= 1.0:
        return distance
    return None


def synthetic_track_scan(
    x: float,
    y: float,
    yaw: float,
    points: int,
    angle_min: float,
    angle_increment: float,
    max_range: float,
    segments: list[Segment],
) -> list[float]:
    ranges = []
    for index in range(points):
        angle = yaw + angle_min + index * angle_increment
        best = max_range
        for segment in segments:
            distance = ray_segment_distance(x, y, angle, segment, max_range)
            if distance is not None and distance < best:
                best = distance
        ranges.append(best)
    return ranges


def cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx
