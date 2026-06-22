from __future__ import annotations

import math


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
