import math

from osracer_race.common import clamp, scan_angle


def filtered_fov_ranges(scan, half_fov):
    ranges = []
    for idx, distance in enumerate(scan.ranges):
        angle = scan_angle(scan, idx)
        if abs(angle) > half_fov:
            continue
        if not math.isfinite(distance) or distance < scan.range_min:
            distance = 0.0
        ranges.append([idx, min(distance, scan.range_max)])
    return ranges


def apply_obstacle_bubble(scan, ranges, bubble_radius):
    if not ranges:
        return
    closest_idx, closest_distance = min(ranges, key=lambda item: item[1])
    blocked_half_angle = bubble_radius / max(closest_distance, 0.05)
    closest_angle = scan_angle(scan, closest_idx)
    for item in ranges:
        if abs(scan_angle(scan, item[0]) - closest_angle) < blocked_half_angle:
            item[1] = 0.0


def find_gap_target(ranges, min_range):
    best_start = None
    best_end = None
    start = None

    for offset, (_, distance) in enumerate(ranges):
        if distance >= min_range:
            if start is None:
                start = offset
        elif start is not None:
            if best_start is None or offset - start > best_end - best_start:
                best_start, best_end = start, offset
            start = None
    if start is not None and (best_start is None or len(ranges) - start > best_end - best_start):
        best_start, best_end = start, len(ranges)
    if best_start is None:
        return None

    target_offset = (best_start + best_end - 1) // 2
    return ranges[target_offset][0]


def speed_for_steering(steering_abs, max_steering, max_speed, min_speed, gain):
    ratio = clamp(steering_abs / max(max_steering, 1e-6), 0.0, 1.0)
    return clamp(max_speed * (1.0 - gain * ratio), min_speed, max_speed)


def gap_follow_command(scan, params):
    half_fov = math.radians(params['gap_fov_deg']) * 0.5
    ranges = filtered_fov_ranges(scan, half_fov)
    if not ranges:
        return None

    apply_obstacle_bubble(scan, ranges, params['obstacle_bubble_radius_m'])
    target_idx = find_gap_target(ranges, params['gap_min_range_m'])
    if target_idx is None:
        return None

    target_angle = scan_angle(scan, target_idx)
    max_steering = math.radians(params['max_steering_angle_deg'])
    steering = clamp(params['follow_gain'] * target_angle, -max_steering, max_steering)
    speed = speed_for_steering(
        abs(steering),
        max_steering,
        params['max_straight_speed_mps'],
        params['min_speed_mps'],
        params['speed_steering_gain'],
    )
    return speed, steering
