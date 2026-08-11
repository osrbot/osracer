import math

from osracer_race.common import choose_wider_side, clamp, overtake_speed, scan_angle


def scan_summary(scan, front_fov_deg, overtake_fov_deg):
    front_half = math.radians(front_fov_deg) * 0.5
    overtake_half = math.radians(overtake_fov_deg) * 0.5
    front_distance = math.inf
    left_ranges = []
    right_ranges = []
    for idx, distance in enumerate(scan.ranges):
        if not math.isfinite(distance) or distance < scan.range_min or distance > scan.range_max:
            continue
        angle = scan_angle(scan, idx)
        if abs(angle) <= front_half:
            front_distance = min(front_distance, distance)
        if 0.0 < angle <= overtake_half:
            left_ranges.append(distance)
        elif -overtake_half <= angle < 0.0:
            right_ranges.append(distance)
    return front_distance, left_ranges, right_ranges


def update_overtake_active(front_distance, was_active, trigger_distance, clear_distance):
    if was_active:
        return front_distance < clear_distance
    return front_distance < trigger_distance


def overtake_command(command_speed, front_distance, left_ranges, right_ranges, was_active, params):
    if command_speed <= 0.0:
        return False, None

    active = update_overtake_active(
        front_distance,
        was_active,
        params['overtake_trigger_distance_m'],
        params['overtake_clear_distance_m'],
    )
    if not active:
        return False, None

    side = choose_wider_side(left_ranges, right_ranges)
    steering_deg = params['overtake_steering_deg'] * side
    steering = clamp(
        math.radians(steering_deg),
        -params['max_steering_angle'],
        params['max_steering_angle'],
    )
    speed = overtake_speed(command_speed, params['overtake_speed_mps'])
    return True, (speed, steering)
