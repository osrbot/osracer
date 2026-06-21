import csv
import math
from pathlib import Path


def clamp(value, lower, upper):
    return max(lower, min(upper, value))


def finite_or_default(value, default):
    return value if math.isfinite(value) else default


def yaw_from_quaternion(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def normalize_angle(angle):
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


def scan_angle(scan, index):
    return scan.angle_min + index * scan.angle_increment


def load_raceline(path):
    points = []
    with Path(path).open(newline='', encoding='utf-8') as handle:
        for row in csv.reader(handle):
            if not row or row[0].strip().startswith('#'):
                continue
            if len(row) < 2:
                continue
            try:
                x = float(row[0])
                y = float(row[1])
                speed = float(row[2]) if len(row) >= 3 and row[2] else None
                curvature = float(row[3]) if len(row) >= 4 and row[3] else None
            except ValueError:
                if not points:
                    continue
                raise
            points.append((x, y, speed, curvature))
    return points


def curvature_speed(curvature, max_lateral_accel, max_speed):
    if abs(curvature) < 1e-6:
        return max_speed
    return min(max_speed, math.sqrt(max_lateral_accel / abs(curvature)))


def curvature_limited_speed(speed, steering, wheelbase, max_speed, min_speed, max_lateral_accel):
    curvature = abs(math.tan(steering) / max(wheelbase, 1e-3))
    limited_speed = max_speed if curvature < 1e-6 else math.sqrt(max_lateral_accel / curvature)
    upper = min(abs(speed), limited_speed, max_speed)
    lower = min_speed if abs(speed) > 0.01 else 0.0
    result = clamp(upper, lower, max_speed)
    return -result if speed < 0.0 else result


def rate_limited_speed(current_speed, target_speed, dt, max_accel, max_brake):
    if dt <= 0.0:
        return current_speed
    delta = target_speed - current_speed
    if delta >= 0.0:
        return current_speed + min(delta, max_accel * dt)
    return current_speed + max(delta, -max_brake * dt)


def command_timed_out(elapsed_s, timeout_s):
    return timeout_s > 0.0 and elapsed_s > timeout_s


def choose_wider_side(left_ranges, right_ranges):
    left = [value for value in left_ranges if math.isfinite(value) and value > 0.0]
    right = [value for value in right_ranges if math.isfinite(value) and value > 0.0]
    left_mean = sum(left) / len(left) if left else 0.0
    right_mean = sum(right) / len(right) if right else 0.0
    return 1.0 if left_mean >= right_mean else -1.0


def overtake_speed(command_speed, overtake_speed_limit):
    if command_speed <= 0.0:
        return command_speed
    return min(command_speed, overtake_speed_limit)


def should_stop_for_front_scan(
    valid_front_count,
    min_front,
    min_ttc,
    emergency_distance,
    ttc_threshold,
    stop_on_no_front_scan,
):
    if stop_on_no_front_scan and valid_front_count == 0:
        return True
    return min_front < emergency_distance or min_ttc < ttc_threshold
