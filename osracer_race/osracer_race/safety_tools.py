import math

from osracer_race.common import scan_angle, should_stop_for_front_scan


def front_scan_metrics(scan, speed, front_fov_deg):
    half_fov = math.radians(front_fov_deg) * 0.5
    min_ttc = math.inf
    min_front = math.inf
    valid_front_count = 0

    for idx, distance in enumerate(scan.ranges):
        if not math.isfinite(distance) or distance < scan.range_min or distance > scan.range_max:
            continue
        angle = scan_angle(scan, idx)
        if abs(angle) > half_fov:
            continue
        valid_front_count += 1
        min_front = min(min_front, distance)
        closing_speed = max(speed * math.cos(angle), 0.0)
        if closing_speed > 0.05:
            min_ttc = min(min_ttc, distance / closing_speed)

    return {
        'valid_front_count': valid_front_count,
        'min_front': min_front,
        'min_ttc': min_ttc,
    }


def race_safety_stop(scan, speed, params):
    metrics = front_scan_metrics(scan, speed, params['front_fov_deg'])
    stop = should_stop_for_front_scan(
        metrics['valid_front_count'],
        metrics['min_front'],
        metrics['min_ttc'],
        params['emergency_distance_m'],
        params['ttc_threshold_s'],
        params['stop_on_no_front_scan'],
    )
    return stop, metrics
