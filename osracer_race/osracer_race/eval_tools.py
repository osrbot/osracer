import math

from osracer_race.common import normalize_angle
from osracer_race.tracking_tools import nearest_index, path_yaw_at


EVAL_HEADER = [
    'time_s',
    'x',
    'y',
    'yaw',
    'speed_mps',
    'command_speed_mps',
    'command_steering_rad',
    'track_error_m',
    'heading_error_rad',
]


TRACK_HEADER = ['# x', 'y', 'speed']


def track_errors(raceline, x, y, yaw):
    if len(raceline) < 2:
        return None, None
    idx = nearest_index(raceline, x, y)
    p0 = raceline[idx]
    path_yaw = path_yaw_at(raceline, idx)
    dx = x - p0[0]
    dy = y - p0[1]
    cross_track = math.sin(path_yaw) * dx - math.cos(path_yaw) * dy
    heading_error = normalize_angle(path_yaw - yaw)
    return cross_track, heading_error


def format_eval_row(time_s, x, y, yaw, speed, command_speed, command_steering, track_error, heading_error):
    return [
        f'{time_s:.3f}',
        f'{x:.4f}',
        f'{y:.4f}',
        f'{yaw:.4f}',
        f'{speed:.4f}',
        f'{command_speed:.4f}',
        f'{command_steering:.4f}',
        f'{track_error:.4f}' if track_error is not None else '',
        f'{heading_error:.4f}' if heading_error is not None else '',
    ]


def should_record_track_point(last_point, x, y, min_spacing):
    if last_point is None:
        return True
    return math.hypot(x - last_point[0], y - last_point[1]) >= min_spacing


def recorded_track_speed(speed, default_speed):
    speed_abs = abs(speed)
    return speed_abs if speed_abs >= 0.01 else default_speed


def format_track_row(x, y, speed):
    return [f'{x:.4f}', f'{y:.4f}', f'{speed:.3f}']
