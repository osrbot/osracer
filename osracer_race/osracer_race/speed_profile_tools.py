import math

from osracer_race.common import (
    clamp,
    curvature_limited_speed,
    finite_or_default,
    rate_limited_speed,
)


def limit_race_command(command_speed, command_steering, base_speed, dt, params):
    max_steering = math.radians(params['max_steering_angle_deg'])
    speed = finite_or_default(command_speed, 0.0)
    steering = clamp(finite_or_default(command_steering, 0.0), -max_steering, max_steering)
    target_speed = curvature_limited_speed(
        speed,
        steering,
        params['wheelbase'],
        params['max_straight_speed_mps'],
        params['min_speed_mps'],
        params['max_lateral_accel_mps2'],
    )
    output_speed = rate_limited_speed(
        base_speed,
        target_speed,
        dt,
        params['max_accel_mps2'],
        params['max_brake_mps2'],
    )
    return output_speed, steering
