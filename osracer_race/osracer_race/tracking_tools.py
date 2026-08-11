import math

from osracer_race.common import clamp, curvature_speed, normalize_angle


def nearest_index(raceline, x, y):
    return min(
        range(len(raceline)),
        key=lambda idx: math.hypot(raceline[idx][0] - x, raceline[idx][1] - y),
    )


def find_lookahead_target(raceline, x, y, lookahead):
    nearest = nearest_index(raceline, x, y)
    for step in range(len(raceline)):
        point = raceline[(nearest + step) % len(raceline)]
        if math.hypot(point[0] - x, point[1] - y) >= lookahead:
            return point
    return raceline[nearest]


def path_yaw_at(raceline, index):
    p0 = raceline[index]
    p1 = raceline[(index + 1) % len(raceline)]
    return math.atan2(p1[1] - p0[1], p1[0] - p0[0])


def point_speed(point, default_speed):
    return point[2] if len(point) >= 3 and point[2] is not None else default_speed


def point_curvature(point, default_curvature=0.0):
    return point[3] if len(point) >= 4 and point[3] is not None else default_curvature


def pure_pursuit_command(raceline, x, y, yaw, params):
    lookahead = params['lookahead_distance_m']
    target = find_lookahead_target(raceline, x, y, lookahead)
    dx = target[0] - x
    dy = target[1] - y
    target_angle = normalize_angle(math.atan2(dy, dx) - yaw)

    wheelbase = params['wheelbase']
    steering = math.atan2(2.0 * wheelbase * math.sin(target_angle), lookahead)
    max_steering = params['max_steering_angle']
    steering = clamp(steering, -max_steering, max_steering)

    fallback_curvature = abs(2.0 * math.sin(target_angle) / max(lookahead, 1e-3))
    curvature = point_curvature(target, fallback_curvature)
    speed = point_speed(target, params['default_speed_mps'])
    max_speed = params['max_straight_speed_mps']
    speed = min(speed, curvature_speed(curvature, params['max_lateral_accel_mps2'], max_speed))
    return clamp(speed, 0.0, max_speed), steering


def stanley_command(raceline, x, y, yaw, current_speed, params):
    speed_for_gain = max(abs(current_speed), 0.05)
    idx = nearest_index(raceline, x, y)
    p0 = raceline[idx]
    path_yaw = path_yaw_at(raceline, idx)
    heading_error = normalize_angle(path_yaw - yaw)
    dx = x - p0[0]
    dy = y - p0[1]
    cross_track_error = math.sin(path_yaw) * dx - math.cos(path_yaw) * dy
    steering = heading_error + math.atan2(
        params['stanley_gain'] * cross_track_error,
        speed_for_gain + params['softening_speed_mps'],
    )
    max_steering = params['max_steering_angle']

    desired_speed = point_speed(p0, params['default_speed_mps'])
    curvature = point_curvature(p0)
    max_speed = params['max_straight_speed_mps']
    desired_speed = min(
        desired_speed,
        curvature_speed(curvature, params['max_lateral_accel_mps2'], max_speed),
    )
    return clamp(desired_speed, 0.0, max_speed), clamp(steering, -max_steering, max_steering)
