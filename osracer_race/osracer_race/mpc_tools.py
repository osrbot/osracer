import math

from osracer_race.common import normalize_angle
from osracer_race.tracking_tools import nearest_index, path_yaw_at, point_speed


def steering_candidates(max_steering):
    return [
        -max_steering,
        -0.6 * max_steering,
        -0.3 * max_steering,
        0.0,
        0.3 * max_steering,
        0.6 * max_steering,
        max_steering,
    ]


def speed_candidates(min_speed, max_speed, speed_now, params):
    lower, upper = reachable_speed_bounds(speed_now, min_speed, max_speed, params)
    candidates = [
        lower,
        min_speed,
        max(speed_now, 0.0),
        0.55 * max_speed,
        0.8 * max_speed,
        max_speed,
        upper,
    ]
    reachable = []
    for speed in candidates:
        clamped = min(max(speed, lower), upper)
        if not any(abs(clamped - existing) < 1e-6 for existing in reachable):
            reachable.append(clamped)
    return reachable


def reachable_speed_bounds(speed_now, min_speed, max_speed, params):
    current = max(speed_now, 0.0)
    response_time = max(params.get('speed_response_time_s', params['dt_s']), params['dt_s'])
    lower = min(max_speed, max(min_speed, current - params['max_brake_mps2'] * response_time))
    upper = min(max_speed, current + params['max_accel_mps2'] * response_time)
    if upper < lower:
        upper = lower
    return lower, upper


def curvature_limited_mpc_speed(steering, wheelbase, max_lateral_accel, max_speed):
    curvature = abs(math.tan(steering) / max(wheelbase, 1e-3))
    if curvature < 1e-6:
        return max_speed
    return min(max_speed, math.sqrt(max_lateral_accel / curvature))


def rollout_pose(x, y, yaw, speed, steering, wheelbase, steps, dt):
    px, py, psi = x, y, yaw
    for _ in range(steps):
        px += speed * math.cos(psi) * dt
        py += speed * math.sin(psi) * dt
        psi += speed / max(wheelbase, 1e-3) * math.tan(steering) * dt
    return px, py, psi


def rollout_cost(raceline, x, y, yaw, speed, steering, speed_now, params):
    px, py, psi = rollout_pose(
        x,
        y,
        yaw,
        speed,
        steering,
        params['wheelbase'],
        params['horizon_steps'],
        params['dt_s'],
    )
    idx = nearest_index(raceline, px, py)
    p0 = raceline[idx]
    path_yaw = path_yaw_at(raceline, idx)
    path_error = math.hypot(px - p0[0], py - p0[1])
    heading_error = abs(normalize_angle(path_yaw - psi))
    target_speed = point_speed(p0, params['default_speed_mps'])
    target_speed_error = abs(speed - target_speed)
    progress = (px - x) * math.cos(path_yaw) + (py - y) * math.sin(path_yaw)
    speed_penalty = abs(speed - max(speed_now, 0.0)) * 0.05
    return (
        params['path_weight'] * path_error
        + params['heading_weight'] * heading_error
        + params['steering_weight'] * abs(steering)
        + params['target_speed_weight'] * target_speed_error
        + speed_penalty
        - params['progress_weight'] * max(progress, 0.0)
    )


def mpc_command(raceline, x, y, yaw, speed_now, params):
    max_steer = params['max_steering_angle']
    max_speed = params['max_straight_speed_mps']
    min_speed = params['min_speed_mps']
    best_cost = math.inf
    best_command = (0.0, 0.0)

    for steering in steering_candidates(max_steer):
        limited_speed = curvature_limited_mpc_speed(
            steering,
            params['wheelbase'],
            params['max_lateral_accel_mps2'],
            max_speed,
        )
        for speed in speed_candidates(min_speed, max_speed, speed_now, params):
            command_speed = min(speed, limited_speed)
            cost = rollout_cost(raceline, x, y, yaw, command_speed, steering, speed_now, params)
            if cost < best_cost:
                best_cost = cost
                best_command = (command_speed, steering)
    return best_command
