#!/usr/bin/env python3

import math
import os
import re
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ackermann_msgs = types.ModuleType('ackermann_msgs')
ackermann_msgs_msg = types.ModuleType('ackermann_msgs.msg')


class AckermannDrive:
    def __init__(self):
        self.speed = 0.0
        self.steering_angle = 0.0


ackermann_msgs_msg.AckermannDrive = AckermannDrive
sys.modules.setdefault('ackermann_msgs', ackermann_msgs)
sys.modules.setdefault('ackermann_msgs.msg', ackermann_msgs_msg)

std_msgs = types.ModuleType('std_msgs')
std_msgs_msg = types.ModuleType('std_msgs.msg')


class Bool:
    def __init__(self, data=False):
        self.data = data


std_msgs_msg.Bool = Bool
sys.modules.setdefault('std_msgs', std_msgs)
sys.modules.setdefault('std_msgs.msg', std_msgs_msg)

from osracer_race.common import (
    clamp,
    choose_wider_side,
    command_timed_out,
    curvature_limited_speed,
    curvature_speed,
    finite_or_default,
    load_raceline,
    normalize_angle,
    overtake_speed,
    rate_limited_speed,
    should_stop_for_front_scan,
)
from osracer_race.controller_base import RaceControllerMixin
from osracer_race.eval_tools import (
    EVAL_HEADER,
    TRACK_HEADER,
    format_eval_row,
    format_track_row,
    recorded_track_speed,
    should_record_track_point,
    track_errors,
)
from osracer_race.gap_follow_tools import gap_follow_command, speed_for_steering
from osracer_race.mpc_tools import (
    curvature_limited_mpc_speed,
    mpc_command,
    reachable_speed_bounds,
    rollout_pose,
)
from osracer_race.overtake_tools import overtake_command, scan_summary, update_overtake_active
from osracer_race.raceline_tools import build_profile, load_points, triangle_curvature, write_profile
from osracer_race.race_report_tools import summarize_csv
from osracer_race.safety_tools import front_scan_metrics, race_safety_stop
from osracer_race.speed_profile_tools import limit_race_command
from osracer_race.tracking_tools import pure_pursuit_command, stanley_command
from osracer_race.vehicle_id_tools import VehicleObservation


class DummyPublisher:
    def __init__(self):
        self.messages = []

    def publish(self, msg):
        self.messages.append(msg)


class DummyController(RaceControllerMixin):
    def __init__(self):
        self.safety_stop = False
        self.cmd_pub = DummyPublisher()


class DummyScan:
    def __init__(self, ranges, angle_min=-0.5, angle_increment=0.1, range_min=0.05, range_max=10.0):
        self.ranges = ranges
        self.angle_min = angle_min
        self.angle_increment = angle_increment
        self.range_min = range_min
        self.range_max = range_max


class RaceMathTest(unittest.TestCase):
    def source_package_root(self):
        package_root = Path(__file__).resolve().parents[1]
        if not (package_root / 'setup.py').exists():
            self.skipTest('source-tree package metadata is not installed with osracer_race')
        return package_root

    def test_clamp(self):
        self.assertEqual(clamp(2.0, 0.0, 1.0), 1.0)
        self.assertEqual(clamp(-1.0, 0.0, 1.0), 0.0)
        self.assertEqual(clamp(0.5, 0.0, 1.0), 0.5)

    def test_finite_or_default(self):
        self.assertEqual(finite_or_default(1.2, 0.0), 1.2)
        self.assertEqual(finite_or_default(math.nan, 0.0), 0.0)
        self.assertEqual(finite_or_default(math.inf, 0.0), 0.0)

    def test_normalize_angle(self):
        self.assertAlmostEqual(normalize_angle(3.0 * math.pi), math.pi)
        self.assertAlmostEqual(normalize_angle(-3.0 * math.pi), -math.pi)

    def test_curvature_speed(self):
        self.assertAlmostEqual(curvature_speed(0.0, 4.5, 3.0), 3.0)
        self.assertAlmostEqual(curvature_speed(0.5, 4.5, 5.0), 3.0)
        self.assertAlmostEqual(curvature_speed(-0.5, 4.5, 5.0), 3.0)

    def test_command_limiter_math(self):
        limited = curvature_limited_speed(
            speed=4.0,
            steering=math.radians(30.0),
            wheelbase=0.285,
            max_speed=4.0,
            min_speed=0.8,
            max_lateral_accel=4.5,
        )
        self.assertLess(limited, 4.0)
        self.assertAlmostEqual(rate_limited_speed(0.0, 3.0, 0.1, 2.5, 3.5), 0.25)
        self.assertAlmostEqual(rate_limited_speed(3.0, 0.0, 0.1, 2.5, 3.5), 2.65)

    def test_command_timeout(self):
        self.assertFalse(command_timed_out(0.20, 0.30))
        self.assertTrue(command_timed_out(0.31, 0.30))
        self.assertFalse(command_timed_out(10.0, 0.0))

    def test_speed_profile_limiter_sanitizes_nonfinite_command(self):
        speed, steering = limit_race_command(
            command_speed=math.nan,
            command_steering=math.inf,
            base_speed=1.0,
            dt=0.1,
            params=self.speed_profile_params(),
        )
        self.assertAlmostEqual(speed, 0.65)
        self.assertAlmostEqual(steering, 0.0)

    def test_speed_profile_limiter_clamps_steering_and_accel(self):
        speed, steering = limit_race_command(
            command_speed=3.0,
            command_steering=math.radians(60.0),
            base_speed=0.0,
            dt=0.1,
            params=self.speed_profile_params(),
        )
        self.assertAlmostEqual(speed, 0.25)
        self.assertAlmostEqual(steering, math.radians(30.0))

    def test_speed_profile_limiter_applies_braking_limit(self):
        speed, _ = limit_race_command(
            command_speed=0.0,
            command_steering=0.0,
            base_speed=3.0,
            dt=0.1,
            params=self.speed_profile_params(),
        )
        self.assertAlmostEqual(speed, 2.65)

    def speed_profile_params(self):
        return {
            'wheelbase': 0.285,
            'max_straight_speed_mps': 3.0,
            'min_speed_mps': 0.8,
            'max_accel_mps2': 2.5,
            'max_brake_mps2': 3.5,
            'max_lateral_accel_mps2': 4.5,
            'max_steering_angle_deg': 30.0,
        }

    def test_choose_wider_side(self):
        self.assertEqual(choose_wider_side([2.0, 2.2], [1.0, 1.1]), 1.0)
        self.assertEqual(choose_wider_side([0.8, 0.9], [2.0, 2.1]), -1.0)

    def test_overtake_speed_does_not_turn_stop_or_reverse_into_forward_motion(self):
        self.assertEqual(overtake_speed(2.0, 1.0), 1.0)
        self.assertEqual(overtake_speed(0.5, 1.0), 0.5)
        self.assertEqual(overtake_speed(0.0, 1.0), 0.0)
        self.assertEqual(overtake_speed(-0.4, 1.0), -0.4)

    def test_overtake_active_uses_trigger_and_clear_hysteresis(self):
        self.assertTrue(update_overtake_active(1.0, False, 1.2, 1.8))
        self.assertFalse(update_overtake_active(1.4, False, 1.2, 1.8))
        self.assertTrue(update_overtake_active(1.4, True, 1.2, 1.8))
        self.assertFalse(update_overtake_active(1.9, True, 1.2, 1.8))

    def test_overtake_command_turns_toward_wider_side(self):
        active, command = overtake_command(
            command_speed=2.0,
            front_distance=1.0,
            left_ranges=[3.0, 3.5],
            right_ranges=[0.8, 1.0],
            was_active=False,
            params=self.overtake_params(),
        )
        self.assertTrue(active)
        self.assertIsNotNone(command)
        speed, steering = command
        self.assertAlmostEqual(speed, 1.0)
        self.assertGreater(steering, 0.0)

    def test_overtake_command_does_not_modify_stop_or_clear_path(self):
        active, command = overtake_command(0.0, 1.0, [3.0], [1.0], False, self.overtake_params())
        self.assertFalse(active)
        self.assertIsNone(command)
        active, command = overtake_command(2.0, 2.0, [3.0], [1.0], False, self.overtake_params())
        self.assertFalse(active)
        self.assertIsNone(command)

    def test_overtake_scan_summary_splits_front_left_right(self):
        scan = DummyScan([2.0, 1.0, 0.8, 2.5, 3.0], angle_min=-0.4, angle_increment=0.2)
        front, left, right = scan_summary(scan, front_fov_deg=30.0, overtake_fov_deg=90.0)
        self.assertAlmostEqual(front, 0.8)
        self.assertIn(2.5, left)
        self.assertIn(1.0, right)

    def overtake_params(self):
        return {
            'overtake_trigger_distance_m': 1.2,
            'overtake_clear_distance_m': 1.8,
            'overtake_speed_mps': 1.0,
            'overtake_steering_deg': 18.0,
            'max_steering_angle_deg': 30.0,
        }

    def test_triangle_curvature(self):
        curvature = triangle_curvature((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0))
        self.assertAlmostEqual(curvature, 1.0, places=6)

    def test_profile_limits_speed_in_curves(self):
        points = [
            (0.0, 0.0, None),
            (1.0, 0.0, None),
            (1.0, 1.0, None),
            (0.0, 1.0, None),
        ]
        profile = build_profile(points, max_speed=3.0, min_speed=0.8, max_lateral_accel=1.0)
        self.assertTrue(any(row[2] < 3.0 for row in profile))
        self.assertTrue(all(row[2] >= 0.8 for row in profile))

    def test_load_profiled_raceline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'raceline.csv'
            write_profile(path, [(0.0, 0.0, 1.2, 0.1), (1.0, 0.0, 1.5, 0.2)])
            points = load_raceline(path)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0], (0.0, 0.0, 1.2, 0.1))

    def test_write_profile_creates_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tracks' / 'generated' / 'raceline.csv'
            write_profile(path, [(0.0, 0.0, 1.2, 0.1), (1.0, 0.0, 1.5, 0.2)])
            points = load_raceline(path)
        self.assertEqual(len(points), 2)

    def test_load_raceline_accepts_plain_csv_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'raceline.csv'
            path.write_text(
                'x,y,speed,curvature\n'
                '0.0,0.0,1.2,0.1\n'
                '1.0,0.0,1.5,0.2\n',
                encoding='utf-8',
            )
            points = load_raceline(path)
            profile_input = load_points(path)
        self.assertEqual(points[0], (0.0, 0.0, 1.2, 0.1))
        self.assertEqual(profile_input[0], (0.0, 0.0, 1.2))

    def test_pure_pursuit_tracks_straight_raceline_without_steering(self):
        raceline = [(0.0, 0.0, 2.0, 0.0), (1.0, 0.0, 2.0, 0.0), (2.0, 0.0, 2.0, 0.0)]
        speed, steering = pure_pursuit_command(raceline, 0.0, 0.0, 0.0, self.pure_pursuit_params())
        self.assertAlmostEqual(speed, 2.0)
        self.assertAlmostEqual(steering, 0.0)

    def test_pure_pursuit_limits_speed_for_raceline_curvature(self):
        raceline = [(0.0, 0.0, 4.0, 1.0), (1.0, 0.0, 4.0, 1.0), (2.0, 0.0, 4.0, 1.0)]
        params = self.pure_pursuit_params()
        params['max_straight_speed_mps'] = 4.0
        params['max_lateral_accel_mps2'] = 1.0
        speed, _ = pure_pursuit_command(raceline, 0.0, 0.0, 0.0, params)
        self.assertAlmostEqual(speed, 1.0)

    def test_stanley_tracks_straight_raceline_without_steering(self):
        raceline = [(0.0, 0.0, 2.0, 0.0), (1.0, 0.0, 2.0, 0.0), (2.0, 0.0, 2.0, 0.0)]
        speed, steering = stanley_command(raceline, 0.0, 0.0, 0.0, 1.0, self.stanley_params())
        self.assertAlmostEqual(speed, 2.0)
        self.assertAlmostEqual(steering, 0.0)

    def test_stanley_steers_back_toward_path_from_left_side(self):
        raceline = [(0.0, 0.0, 2.0, 0.0), (1.0, 0.0, 2.0, 0.0), (2.0, 0.0, 2.0, 0.0)]
        _, steering = stanley_command(raceline, 0.0, 1.0, 0.0, 1.0, self.stanley_params())
        self.assertLess(steering, 0.0)

    def test_eval_track_errors_and_rows_are_stable(self):
        raceline = [(0.0, 0.0, 2.0, 0.0), (1.0, 0.0, 2.0, 0.0), (2.0, 0.0, 2.0, 0.0)]
        cross_track, heading = track_errors(raceline, 0.0, 1.0, 0.0)
        self.assertAlmostEqual(cross_track, -1.0)
        self.assertAlmostEqual(heading, 0.0)
        self.assertEqual(EVAL_HEADER[0], 'time_s')
        self.assertEqual(EVAL_HEADER[-1], 'heading_error_rad')
        self.assertEqual(
            format_eval_row(1.2345, 1.0, 2.0, 0.5, 3.0, 2.5, -0.1, None, None),
            ['1.234', '1.0000', '2.0000', '0.5000', '3.0000', '2.5000', '-0.1000', '', ''],
        )

    def test_track_recorder_helpers_filter_spacing_and_format_rows(self):
        self.assertEqual(TRACK_HEADER, ['# x', 'y', 'speed'])
        self.assertTrue(should_record_track_point(None, 0.0, 0.0, 0.1))
        self.assertFalse(should_record_track_point((0.0, 0.0), 0.05, 0.0, 0.1))
        self.assertTrue(should_record_track_point((0.0, 0.0), 0.11, 0.0, 0.1))
        self.assertAlmostEqual(recorded_track_speed(0.0, 1.2), 1.2)
        self.assertAlmostEqual(recorded_track_speed(-0.8, 1.2), 0.8)
        self.assertEqual(format_track_row(1.23456, -2.0, 0.8), ['1.2346', '-2.0000', '0.800'])

    def test_mpc_rollout_pose_stays_straight_without_steering(self):
        x, y, yaw = rollout_pose(
            x=0.0,
            y=0.0,
            yaw=0.0,
            speed=2.0,
            steering=0.0,
            wheelbase=0.285,
            steps=5,
            dt=0.1,
        )
        self.assertAlmostEqual(x, 1.0)
        self.assertAlmostEqual(y, 0.0)
        self.assertAlmostEqual(yaw, 0.0)

    def test_mpc_curvature_speed_limit_reduces_corner_speed(self):
        limited = curvature_limited_mpc_speed(
            steering=math.radians(30.0),
            wheelbase=0.285,
            max_lateral_accel=4.5,
            max_speed=4.0,
        )
        self.assertLess(limited, 4.0)

    def test_mpc_command_prefers_straight_steering_on_straight_path(self):
        raceline = [(0.0, 0.0, 2.0, 0.0), (1.0, 0.0, 2.0, 0.0), (2.0, 0.0, 2.0, 0.0)]
        speed, steering = mpc_command(raceline, 0.0, 0.0, 0.0, 1.0, self.mpc_params())
        self.assertGreaterEqual(speed, self.mpc_params()['min_speed_mps'])
        self.assertAlmostEqual(steering, 0.0)

    def test_mpc_speed_candidates_respect_vehicle_response_limits(self):
        params = self.mpc_params()
        lower, upper = reachable_speed_bounds(
            speed_now=1.0,
            min_speed=params['min_speed_mps'],
            max_speed=params['max_straight_speed_mps'],
            params=params,
        )
        self.assertAlmostEqual(lower, 0.8)
        self.assertAlmostEqual(upper, 1.75)

        raceline = [(0.0, 0.0, 3.0, 0.0), (1.0, 0.0, 3.0, 0.0), (2.0, 0.0, 3.0, 0.0)]
        speed, _ = mpc_command(raceline, 0.0, 0.0, 0.0, 1.0, params)
        self.assertLessEqual(speed, upper)

    def test_mpc_speed_bounds_clamp_when_current_speed_exceeds_configured_limit(self):
        params = self.mpc_params()
        lower, upper = reachable_speed_bounds(
            speed_now=4.0,
            min_speed=params['min_speed_mps'],
            max_speed=params['max_straight_speed_mps'],
            params=params,
        )
        self.assertLessEqual(lower, params['max_straight_speed_mps'])
        self.assertAlmostEqual(upper, params['max_straight_speed_mps'])

    def test_mpc_uses_raceline_target_speed_and_progress_reward(self):
        params = self.mpc_params()
        params['target_speed_weight'] = 2.0
        params['progress_weight'] = 2.0
        raceline = [(0.0, 0.0, 3.0, 0.0), (1.0, 0.0, 3.0, 0.0), (2.0, 0.0, 3.0, 0.0)]
        speed, steering = mpc_command(raceline, 0.0, 0.0, 0.0, 1.0, params)
        _, upper = reachable_speed_bounds(
            speed_now=1.0,
            min_speed=params['min_speed_mps'],
            max_speed=params['max_straight_speed_mps'],
            params=params,
        )
        self.assertAlmostEqual(speed, upper)
        self.assertAlmostEqual(steering, 0.0)

    def pure_pursuit_params(self):
        return {
            'wheelbase': 0.285,
            'lookahead_distance_m': 1.0,
            'max_steering_angle_deg': 30.0,
            'default_speed_mps': 1.2,
            'max_straight_speed_mps': 3.0,
            'max_lateral_accel_mps2': 4.5,
        }

    def stanley_params(self):
        return {
            'stanley_gain': 0.8,
            'softening_speed_mps': 0.6,
            'max_steering_angle_deg': 30.0,
            'default_speed_mps': 1.2,
            'max_straight_speed_mps': 3.0,
            'max_lateral_accel_mps2': 4.5,
        }

    def mpc_params(self):
        return {
            'wheelbase': 0.285,
            'max_steering_angle_deg': 30.0,
            'max_straight_speed_mps': 3.0,
            'min_speed_mps': 0.8,
            'max_accel_mps2': 2.5,
            'max_brake_mps2': 3.5,
            'speed_response_time_s': 0.30,
            'max_lateral_accel_mps2': 4.5,
            'horizon_steps': 8,
            'dt_s': 0.08,
            'path_weight': 4.0,
            'heading_weight': 1.5,
            'steering_weight': 0.2,
            'target_speed_weight': 0.35,
            'progress_weight': 0.15,
        }

    def test_safety_gate_forces_stop(self):
        controller = DummyController()
        controller.safety_stop = True
        controller.publish_command(2.0, 0.2)
        msg = controller.cmd_pub.messages[-1]
        self.assertEqual(msg.speed, 0.0)
        self.assertEqual(msg.steering_angle, 0.0)

    def test_front_scan_safety_stops_when_no_valid_front_scan(self):
        self.assertTrue(should_stop_for_front_scan(
            valid_front_count=0,
            min_front=math.inf,
            min_ttc=math.inf,
            emergency_distance=0.45,
            ttc_threshold=0.65,
            stop_on_no_front_scan=True,
        ))
        self.assertFalse(should_stop_for_front_scan(
            valid_front_count=0,
            min_front=math.inf,
            min_ttc=math.inf,
            emergency_distance=0.45,
            ttc_threshold=0.65,
            stop_on_no_front_scan=False,
        ))

    def test_front_scan_metrics_compute_min_distance_and_ttc(self):
        scan = DummyScan([3.0, 2.0, 1.0, 2.0, 3.0], angle_min=-0.2, angle_increment=0.1)
        metrics = front_scan_metrics(scan, speed=2.0, front_fov_deg=12.0)
        self.assertEqual(metrics['valid_front_count'], 3)
        self.assertAlmostEqual(metrics['min_front'], 1.0)
        self.assertAlmostEqual(metrics['min_ttc'], 0.5)

    def test_race_safety_stop_for_ttc_and_emergency_distance(self):
        params = self.safety_params()
        stop, metrics = race_safety_stop(
            DummyScan([2.0, 2.0, 1.0, 2.0, 2.0], angle_min=-0.2, angle_increment=0.1),
            speed=2.0,
            params=params,
        )
        self.assertTrue(stop)
        self.assertLess(metrics['min_ttc'], params['ttc_threshold_s'])

        stop, metrics = race_safety_stop(
            DummyScan([2.0, 2.0, 0.4, 2.0, 2.0], angle_min=-0.2, angle_increment=0.1),
            speed=0.0,
            params=params,
        )
        self.assertTrue(stop)
        self.assertLess(metrics['min_front'], params['emergency_distance_m'])

    def test_race_safety_does_not_ttc_stop_when_reversing_from_clear_obstacle(self):
        stop, metrics = race_safety_stop(
            DummyScan([2.0, 2.0, 1.0, 2.0, 2.0], angle_min=-0.2, angle_increment=0.1),
            speed=-1.0,
            params=self.safety_params(),
        )
        self.assertFalse(stop)
        self.assertTrue(math.isinf(metrics['min_ttc']))

    def safety_params(self):
        return {
            'ttc_threshold_s': 0.65,
            'emergency_distance_m': 0.45,
            'front_fov_deg': 30.0,
            'stop_on_no_front_scan': True,
        }

    def test_gap_follow_command_drives_straight_on_clear_scan(self):
        params = self.gap_follow_params()
        command = gap_follow_command(DummyScan([3.0] * 11), params)
        self.assertIsNotNone(command)
        speed, steering = command
        self.assertAlmostEqual(speed, params['max_straight_speed_mps'])
        self.assertAlmostEqual(steering, 0.0)

    def test_gap_follow_command_turns_toward_wider_open_side(self):
        params = self.gap_follow_params()
        scan = DummyScan([0.4, 0.4, 0.4, 0.4, 3.0, 4.0, 5.0, 5.0, 5.0, 5.0, 5.0])
        command = gap_follow_command(scan, params)
        self.assertIsNotNone(command)
        speed, steering = command
        self.assertGreater(steering, 0.0)
        self.assertGreaterEqual(speed, params['min_speed_mps'])
        self.assertLessEqual(speed, params['max_straight_speed_mps'])

    def test_gap_follow_command_stops_when_no_gap_is_available(self):
        params = self.gap_follow_params()
        self.assertIsNone(gap_follow_command(DummyScan([0.2] * 11), params))

    def test_gap_follow_speed_reduces_with_steering(self):
        max_steering = math.radians(30.0)
        straight = speed_for_steering(0.0, max_steering, 3.0, 0.8, 1.4)
        corner = speed_for_steering(max_steering, max_steering, 3.0, 0.8, 1.4)
        self.assertAlmostEqual(straight, 3.0)
        self.assertAlmostEqual(corner, 0.8)

    def gap_follow_params(self):
        return {
            'gap_fov_deg': 140.0,
            'obstacle_bubble_radius_m': 0.28,
            'gap_min_range_m': 0.65,
            'max_straight_speed_mps': 3.0,
            'min_speed_mps': 0.8,
            'max_steering_angle_deg': 30.0,
            'follow_gain': 0.75,
            'speed_steering_gain': 1.4,
        }

    def test_report_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'eval.csv'
            path.write_text(
                'time_s,x,y,yaw,speed_mps,command_speed_mps,command_steering_rad,track_error_m,heading_error_rad\n'
                '0.0,0,0,0,1.0,1.2,0.1,0.05,0.02\n'
                '0.1,0,0,0,2.0,2.2,-0.2,-0.15,-0.04\n',
                encoding='utf-8',
            )
            summary = summarize_csv(path)
        self.assertEqual(summary['samples'], 2)
        self.assertAlmostEqual(summary['max_speed_mps'], 2.0)
        self.assertAlmostEqual(summary['mean_abs_track_error_m'], 0.1)
        self.assertAlmostEqual(summary['max_abs_steering_rad'], 0.2)

    def test_report_summary_ignores_empty_and_nonfinite_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'eval.csv'
            path.write_text(
                'time_s,x,y,yaw,speed_mps,command_speed_mps,command_steering_rad,track_error_m,heading_error_rad\n'
                '0.0,0,0,0,nan,1.2,0.1,,\n'
                '0.1,0,0,0,2.0,inf,-0.2,,nan\n',
                encoding='utf-8',
            )
            summary = summarize_csv(path)
        self.assertEqual(summary['samples'], 2)
        self.assertEqual(summary['speed_samples'], 1)
        self.assertEqual(summary['track_error_samples'], 0)
        self.assertAlmostEqual(summary['max_speed_mps'], 2.0)
        self.assertIsNone(summary['mean_abs_track_error_m'])
        self.assertIsNone(summary['mean_abs_heading_error_rad'])

    def test_race_launches_accept_race_config(self):
        launch_dir = Path(__file__).resolve().parents[1] / 'launch'
        for filename in (
            'gap_follow.launch.py',
            'pure_pursuit.launch.py',
            'stanley.launch.py',
            'mpc.launch.py',
            'track_record.launch.py',
            'vehicle_id.launch.py',
        ):
            text = (launch_dir / filename).read_text(encoding='utf-8')
            self.assertIn("LaunchConfiguration('race_config')", text, filename)
            self.assertIn("'race_config'", text, filename)
        for filename in ('gap_follow.launch.py', 'pure_pursuit.launch.py', 'stanley.launch.py', 'mpc.launch.py'):
            text = (launch_dir / filename).read_text(encoding='utf-8')
            self.assertIn("LaunchConfiguration('eval_output_csv')", text, filename)
            self.assertIn("'eval_output_csv'", text, filename)

    def test_race_configs_declare_key_runtime_parameters(self):
        package_root = Path(__file__).resolve().parents[1]
        required = {
            'scan_topic',
            'odom_topic',
            'ackermann_topic',
            'input_ackermann_topic',
            'output_ackermann_topic',
            'safety_stop_topic',
            'max_straight_speed_mps',
            'min_speed_mps',
            'max_accel_mps2',
            'max_brake_mps2',
            'speed_response_time_s',
            'max_lateral_accel_mps2',
            'max_steering_angle_deg',
            'command_timeout_s',
            'watchdog_period_s',
            'ttc_threshold_s',
            'emergency_distance_m',
            'front_fov_deg',
            'stop_on_no_front_scan',
            'scan_timeout_s',
            'stop_repeat',
            'gap_fov_deg',
            'obstacle_bubble_radius_m',
            'gap_min_range_m',
            'follow_gain',
            'speed_steering_gain',
            'lookahead_distance_m',
            'default_speed_mps',
            'stanley_gain',
            'softening_speed_mps',
            'horizon_steps',
            'dt_s',
            'path_weight',
            'heading_weight',
            'steering_weight',
            'target_speed_weight',
            'progress_weight',
            'lap_trigger_radius_m',
            'eval_output_csv',
            'log_period_s',
            'min_point_spacing_m',
            'overtake_trigger_distance_m',
            'overtake_clear_distance_m',
            'overtake_speed_mps',
            'overtake_steering_deg',
            'overtake_fov_deg',
        }
        for filename in ('race_safe.yaml', 'race_fast.yaml'):
            with (package_root / 'config' / filename).open(encoding='utf-8') as handle:
                params = yaml.safe_load(handle)['/**']['ros__parameters']
            self.assertTrue(required.issubset(params), filename)

    def test_vehicle_config_matches_measured_ackermann_chassis(self):
        package_root = Path(__file__).resolve().parents[1]
        with (package_root / 'config' / 'vehicle.yaml').open(encoding='utf-8') as handle:
            params = yaml.safe_load(handle)['/**']['ros__parameters']

        self.assertAlmostEqual(params['wheel_radius'], 0.0425)
        self.assertAlmostEqual(params['wheel_diameter'], 0.085)
        self.assertAlmostEqual(params['wheel_diameter'], params['wheel_radius'] * 2.0)
        self.assertAlmostEqual(params['wheelbase'], 0.285)
        self.assertAlmostEqual(params['track_width'], 0.215)
        self.assertEqual(params['encoder_cpr_motor'], 1024)
        self.assertEqual(params['encoder_multiplier'], 1)

        gear_ratio = (
            params['differential_ring_teeth']
            / params['differential_pinion_teeth']
            * params['spur_gear_teeth']
            / params['motor_pinion_teeth']
        )
        self.assertAlmostEqual(params['gear_ratio'], gear_ratio, places=2)

        wheel_rps = params['motor_no_load_rpm'] / 60.0 / params['gear_ratio']
        theoretical_speed = wheel_rps * math.pi * params['wheel_diameter']
        self.assertAlmostEqual(params['theoretical_max_speed_mps'], theoretical_speed, places=2)

        steering_rad = math.radians(params['max_steering_angle_deg'])
        turning_radius = params['wheelbase'] / math.tan(steering_rad)
        self.assertAlmostEqual(params['minimum_turning_radius_m'], turning_radius, delta=0.01)

    def test_primary_ackermann_packages_share_vehicle_geometry(self):
        package_root = self.source_package_root()
        repo_root = package_root.parent
        expected = {
            'wheel_radius': '0.0425',
            'wheelbase': '0.285',
            'track_width': '0.215',
            'max_steering_angle_deg': '30.0',
        }
        paths = [
            repo_root / 'osracer_bringup' / 'launch' / 'chassis_ackermann.launch.py',
            repo_root / 'osracer_description' / 'launch' / 'osracer_description.launch.py',
            repo_root / 'osracer_navigation' / 'params' / 'teb_nav2_params.yaml',
        ]
        missing = [path for path in paths if not path.exists()]
        if missing:
            self.skipTest(f'source-tree package missing: {missing[0]}')

        chassis_text = paths[0].read_text(encoding='utf-8')
        description_text = paths[1].read_text(encoding='utf-8')
        teb_text = paths[2].read_text(encoding='utf-8')

        self.assertIn(f"default_value='{expected['wheelbase']}'", chassis_text)
        self.assertIn(f"default_value='{expected['max_steering_angle_deg']}'", chassis_text)
        self.assertIn(f'default_value="{expected["wheel_radius"]}"', description_text)
        self.assertIn(f'default_value="{expected["wheelbase"]}"', description_text)
        self.assertIn(f'default_value="{expected["track_width"]}"', description_text)
        self.assertIn(f'default_value="{expected["max_steering_angle_deg"]}"', description_text)
        self.assertIn(f"wheelbase: {expected['wheelbase']}", teb_text)
        self.assertIn(f"line_end: [{expected['wheelbase']}, 0.0]", teb_text)

    def test_ackermann_bridge_and_chassis_publish_ekf_covariance(self):
        package_root = self.source_package_root()
        repo_root = package_root.parent
        bridge_path = repo_root / 'osracer_bringup' / 'script' / 'twist_bridge.py'
        chassis_path = repo_root / 'osracer_bringup' / 'script' / 'chassis_ackermann.py'
        if not bridge_path.exists() or not chassis_path.exists():
            self.skipTest('source-tree osracer_bringup package is not installed with osracer_race')

        bridge_text = bridge_path.read_text(encoding='utf-8')
        chassis_text = chassis_path.read_text(encoding='utf-8')
        self.assertIn("declare_parameter('wheelbase', 0.285)", bridge_text)
        self.assertIn("declare_parameter('odom_twist_covariance'", chassis_text)
        self.assertIn('self.odom_twist_covariance = self.diagonal_covariance_6d', chassis_text)
        self.assertIn('odom_msg.twist.covariance = self.odom_twist_covariance', chassis_text)
        self.assertIn("declare_parameter('firmware_version_timeout_s', 0.3)", chassis_text)
        self.assertIn("declare_parameter('link_status_enabled', True)", chassis_text)
        self.assertIn("declare_parameter('link_ping_period_s', 1.0)", chassis_text)
        self.assertIn('self.log_firmware_project_version()', chassis_text)
        self.assertIn('serial_conn.write(b"fw version\\n")', chassis_text)
        self.assertIn('OSRCORE ProjectVer:', chassis_text)
        self.assertIn('self.write_serial("stream sync\\n")', chassis_text)
        self.assertIn('self.write_serial("s\\n")', chassis_text)
        self.assertIn('self.send_link_command("up")', chassis_text)
        self.assertIn('self.send_link_command("ping")', chassis_text)
        self.assertIn('serial_conn.write(b"link down ros\\n")', chassis_text)
        self.assertIn("cmd_type.startswith(('FW', 'DIAG', 'LINK', 'OK', 'ERROR'))", chassis_text)

    def test_vehicle_observation_tracks_identified_limits(self):
        observation = VehicleObservation()
        observation.update(speed=0.0, yaw_rate=0.0, time_s=0.0)
        observation.update(speed=1.5, yaw_rate=0.3, time_s=0.5)
        observation.update(speed=0.5, yaw_rate=1.0, time_s=1.0)
        observation.update_command(speed=0.0, steering=0.0, time_s=1.0)
        observation.update_command(speed=2.0, steering=0.0, time_s=1.1)
        observation.update(speed=1.45, yaw_rate=0.0, time_s=1.4)
        observation.update_command(speed=2.0, steering=0.20, time_s=1.5)
        observation.update(speed=1.45, yaw_rate=0.20, time_s=1.7)

        self.assertAlmostEqual(observation.max_speed, 1.5)
        self.assertAlmostEqual(observation.max_accel, 3.0)
        self.assertAlmostEqual(observation.max_brake, -2.0)
        self.assertAlmostEqual(observation.max_yaw_rate, 1.0)
        self.assertAlmostEqual(observation.max_lateral_accel, 0.5)
        self.assertAlmostEqual(observation.min_turning_radius, 0.5)
        self.assertAlmostEqual(observation.motor_response_tau_s, 0.3)
        self.assertAlmostEqual(observation.steering_response_delay_s, 0.2)

        yaml_text = observation.to_ros_parameters_yaml({
            'wheel_radius': 0.0425,
            'wheelbase': 0.285,
            'track_width': 0.215,
            'gear_ratio': 10.55,
            'mass_kg': 3.2,
            'max_steering_angle_deg': 30.0,
        })
        self.assertIn('observed_max_speed_mps: 1.500', yaml_text)
        self.assertIn('observed_max_accel_mps2: 3.000', yaml_text)
        self.assertIn('observed_max_brake_mps2: 2.000', yaml_text)
        self.assertIn('observed_max_lateral_accel_mps2: 0.500', yaml_text)
        self.assertIn('observed_min_turning_radius_m: 0.500', yaml_text)
        self.assertIn('observed_motor_response_tau_s: 0.300', yaml_text)
        self.assertIn('observed_steering_response_delay_s: 0.200', yaml_text)

    def test_race_launch_topic_chain_uses_final_limiter(self):
        launch_dir = Path(__file__).resolve().parents[1] / 'launch'
        gap_text = (launch_dir / 'gap_follow.launch.py').read_text(encoding='utf-8')
        self.assertIn("'ackermann_topic': '/race/raw_ackermann_cmd'", gap_text)
        self.assertIn("executable='speed_profile_node'", gap_text)

        for filename in ('pure_pursuit.launch.py', 'stanley.launch.py', 'mpc.launch.py', 'race_bringup.launch.py'):
            text = (launch_dir / filename).read_text(encoding='utf-8')
            self.assertIn("'ackermann_topic': '/race/tracking_ackermann_cmd'", text, filename)
            self.assertIn("executable='obstacle_overtake_node'", text, filename)
            self.assertIn("executable='speed_profile_node'", text, filename)

    def test_speed_profile_watchdog_repeats_stop_during_safety_stop(self):
        package_root = self.source_package_root()
        text = (package_root / 'osracer_race' / 'speed_profile_node.py').read_text(encoding='utf-8')
        self.assertRegex(
            text,
            r'def watchdog_callback\(self\):\n\s+if self\.safety_stop:\n\s+self\.publish_stop\(\)\n\s+return',
        )

    def test_safety_node_watchdog_stops_when_scan_stream_times_out(self):
        package_root = self.source_package_root()
        text = (package_root / 'osracer_race' / 'safety_node.py').read_text(encoding='utf-8')
        self.assertIn("self.declare_parameter('scan_timeout_s', 0.50)", text)
        self.assertIn("self.declare_parameter('watchdog_period_s', 0.05)", text)
        self.assertIn('def scan_watchdog_callback(self):', text)
        self.assertIn('self.stop_pub.publish(Bool(data=True))', text)
        self.assertIn("Race safety stop: no scan received", text)

    def test_race_bringup_includes_existing_robot_bringup(self):
        repo_root = Path(__file__).resolve().parents[2]
        race_bringup = (repo_root / 'osracer_race' / 'launch' / 'race_bringup.launch.py').read_text(
            encoding='utf-8')
        self.assertIn("FindPackageShare('osracer_bringup')", race_bringup)
        self.assertIn("'bringup.launch.py'", race_bringup)
        self.assertIn("choices=['gap_follow', 'pure_pursuit', 'stanley', 'mpc']", race_bringup)
        for controller in ('gap_follow', 'pure_pursuit', 'stanley', 'mpc'):
            self.assertIn(controller, race_bringup)
        self.assertIn("' in ['pure_pursuit', 'stanley', 'mpc']", race_bringup)
        self.assertNotIn("' != 'gap_follow'", race_bringup)
        if not (repo_root / 'osracer_bringup').exists():
            self.skipTest('source-tree osracer_bringup package is not installed with osracer_race')
        self.assertTrue((repo_root / 'osracer_bringup' / 'launch' / 'bringup.launch.py').exists())

    def test_raceline_controllers_apply_curvature_speed_limit(self):
        package_root = self.source_package_root()
        for module_name in ('pure_pursuit_node.py', 'stanley_node.py'):
            text = (package_root / 'osracer_race' / module_name).read_text(encoding='utf-8')
            self.assertIn('max_lateral_accel_mps2', text, module_name)
            self.assertIn('tracking_tools', text, module_name)
        tracking_text = (package_root / 'osracer_race' / 'tracking_tools.py').read_text(encoding='utf-8')
        self.assertIn('curvature_speed', tracking_text)

    def test_console_scripts_match_modules(self):
        package_root = self.source_package_root()
        setup_text = (package_root / 'setup.py').read_text(encoding='utf-8')
        expected = {
            'gap_follow_node.py': 'gap_follow_node = osracer_race.gap_follow_node:main',
            'lap_timer_node.py': 'lap_timer_node = osracer_race.lap_timer_node:main',
            'mpc_controller_node.py': 'mpc_controller_node = osracer_race.mpc_controller_node:main',
            'obstacle_overtake_node.py': 'obstacle_overtake_node = osracer_race.obstacle_overtake_node:main',
            'pure_pursuit_node.py': 'pure_pursuit_node = osracer_race.pure_pursuit_node:main',
            'race_evaluator_node.py': 'race_evaluator_node = osracer_race.race_evaluator_node:main',
            'race_report_tools.py': 'race_report_tools = osracer_race.race_report_tools:main',
            'raceline_tools.py': 'raceline_tools = osracer_race.raceline_tools:main',
            'safety_node.py': 'safety_node = osracer_race.safety_node:main',
            'speed_profile_node.py': 'speed_profile_node = osracer_race.speed_profile_node:main',
            'stanley_node.py': 'stanley_node = osracer_race.stanley_node:main',
            'track_recorder_node.py': 'track_recorder_node = osracer_race.track_recorder_node:main',
            'vehicle_id_node.py': 'vehicle_id_node = osracer_race.vehicle_id_node:main',
        }
        for module_name, entry_point in expected.items():
            self.assertTrue((package_root / 'osracer_race' / module_name).exists(), module_name)
            self.assertIn(entry_point, setup_text)

    def test_all_node_modules_define_main_and_console_entry(self):
        package_root = self.source_package_root()
        setup_text = (package_root / 'setup.py').read_text(encoding='utf-8')
        for path in sorted((package_root / 'osracer_race').glob('*_node.py')):
            module_name = path.stem
            text = path.read_text(encoding='utf-8')
            self.assertIn('def main(args=None):', text, module_name)
            self.assertIn(
                f'{module_name} = osracer_race.{module_name}:main',
                setup_text,
                module_name,
            )

    def test_launch_executables_are_installed_console_scripts(self):
        package_root = self.source_package_root()
        setup_text = (package_root / 'setup.py').read_text(encoding='utf-8')
        launch_dir = package_root / 'launch'
        installed = set(re.findall(r"'([^']+) = osracer_race\.[^']+:main'", setup_text))
        referenced = set()
        for path in launch_dir.glob('*.launch.py'):
            text = path.read_text(encoding='utf-8')
            referenced.update(re.findall(r"executable='([^']+)'", text))
        self.assertTrue(referenced)
        self.assertTrue(referenced.issubset(installed), sorted(referenced - installed))

    def test_package_xml_declares_runtime_import_dependencies(self):
        package_root = self.source_package_root()
        root = ET.parse(package_root / 'package.xml').getroot()
        dependencies = {
            element.text
            for tag in ('depend', 'exec_depend', 'buildtool_depend')
            for element in root.findall(tag)
        }
        expected = {
            'ament_python',
            'rclpy',
            'ackermann_msgs',
            'nav_msgs',
            'sensor_msgs',
            'std_msgs',
            'launch',
            'launch_ros',
            'ros2launch',
            'osracer_bringup',
            'python3-yaml',
        }
        self.assertTrue(expected.issubset(dependencies), sorted(expected - dependencies))

    def test_user_docs_are_installed(self):
        package_root = Path(__file__).resolve().parents[1]
        readme_text = (package_root / 'README_zh.md').read_text(encoding='utf-8')
        validation_text = (package_root / 'ROS_VALIDATION_zh.md').read_text(encoding='utf-8')
        self.assertTrue((package_root / 'README_zh.md').exists())
        self.assertTrue((package_root / 'PHASES_zh.md').exists())
        self.assertTrue((package_root / 'ROS_VALIDATION_zh.md').exists())
        if (package_root / 'setup.py').exists():
            setup_text = (package_root / 'setup.py').read_text(encoding='utf-8')
            self.assertIn("'README_zh.md'", setup_text)
            self.assertIn("'PHASES_zh.md'", setup_text)
            self.assertIn("'ROS_VALIDATION_zh.md'", setup_text)
        self.assertIn('bash osracer_race/scripts/check_race_package.sh', readme_text)
        for section in (
            '推荐上手顺序',
            '安装和自检',
            'Topic 链路和安全边界',
            '参数文件说明',
            '车端验证和交付检查',
            '常见问题',
        ):
            self.assertIn(section, readme_text)
        for command in (
            'colcon build --symlink-install --packages-select osracer_race',
            'ros2 topic echo /race/safety_stop',
            'ros2 topic hz /scan',
            'ros2 topic hz /odometry/filtered',
            'bash osracer_race/scripts/check_race_package.sh',
            'bash $(ros2 pkg prefix osracer_race)/share/osracer_race/scripts/validate_race_ros.sh',
        ):
            self.assertIn(command, readme_text)
        self.assertIn('bash $(ros2 pkg prefix osracer_race)/share/osracer_race/scripts/check_race_package.sh', validation_text)
        self.assertIn('bash $(ros2 pkg prefix osracer_race)/share/osracer_race/scripts/validate_race_ros.sh', validation_text)
        self.assertIn('race_fast.yaml` 是完整运行参数文件', readme_text)
        self.assertIn('race_fast.yaml` 是完整运行参数文件', validation_text)
        self.assertIn('helper 模块 import smoke test 通过', validation_text)
        self.assertIn('ros2 launch osracer_race gap_follow.launch.py --show-args', validation_text)
        self.assertIn('ros2 launch osracer_race race_bringup.launch.py --show-args', validation_text)
        self.assertIn('其他值会在 launch 参数解析阶段被拒绝', readme_text)
        self.assertIn('其他值应在 launch 参数解析阶段被拒绝', validation_text)
        for controller in ('gap_follow', 'pure_pursuit', 'stanley', 'mpc'):
            self.assertIn(controller, readme_text)
            self.assertIn(controller, validation_text)

    def test_documented_commands_match_installed_entries(self):
        package_root = self.source_package_root()
        docs = (
            (package_root / 'README_zh.md').read_text(encoding='utf-8')
            + (package_root / 'ROS_VALIDATION_zh.md').read_text(encoding='utf-8')
        )
        setup_text = (package_root / 'setup.py').read_text(encoding='utf-8')
        for launch_name in (
            'gap_follow.launch.py',
            'race_bringup.launch.py',
            'track_record.launch.py',
            'pure_pursuit.launch.py',
            'stanley.launch.py',
            'vehicle_id.launch.py',
            'mpc.launch.py',
        ):
            self.assertIn(f'ros2 launch osracer_race {launch_name}', docs)
            self.assertTrue((package_root / 'launch' / launch_name).exists(), launch_name)
        for command_name in ('raceline_tools', 'race_report_tools'):
            self.assertIn(f'ros2 run osracer_race {command_name}', docs)
            self.assertIn(f'{command_name} = osracer_race.{command_name}:main', setup_text)
        for launch_name in ('pure_pursuit.launch.py', 'stanley.launch.py', 'mpc.launch.py'):
            launch_text = (package_root / 'launch' / launch_name).read_text(encoding='utf-8')
            self.assertIn('x,y,speed,curvature', launch_text, launch_name)

    def test_ros_validation_documents_vehicle_side_scope(self):
        package_root = Path(__file__).resolve().parents[1]
        review_text = (package_root / 'ROS_VALIDATION_zh.md').read_text(encoding='utf-8')
        for expected in (
            'Ubuntu 22.04 + ROS 2 Humble',
            'Jetson Orin Nano',
            '不替代实车低速验证',
            'helper 模块 import smoke test',
            'validate_race_ros.sh',
            'colcon build --symlink-install --packages-select osracer_race',
            '真车低速安全验证',
            '不会发布运动命令',
        ):
            self.assertIn(expected, review_text)

    def test_phase_plan_matches_delivered_artifacts(self):
        package_root = Path(__file__).resolve().parents[1]
        phases_text = (package_root / 'PHASES_zh.md').read_text(encoding='utf-8')
        for section in (
            '第一阶段：安全无地图跑圈',
            '第二阶段：有地图轨迹跟踪',
            '第三阶段：车辆能力标定',
            '第四阶段：高级比赛/科研算法',
            '当前验证状态',
        ):
            self.assertIn(section, phases_text)
        for artifact in (
            'safety_node.py',
            'gap_follow_node.py',
            'speed_profile_node.py',
            'lap_timer_node.py',
            'raceline_tools.py',
            'track_recorder_node.py',
            'pure_pursuit_node.py',
            'stanley_node.py',
            'obstacle_overtake_node.py',
            'vehicle_id_node.py',
            'mpc_controller_node.py',
            'race_evaluator_node.py',
            'race_report_tools.py',
            'gap_follow.launch.py',
            'track_record.launch.py',
            'pure_pursuit.launch.py',
            'stanley.launch.py',
            'vehicle_id.launch.py',
            'mpc.launch.py',
            'race_bringup.launch.py',
        ):
            self.assertIn(artifact, phases_text)
        for verification in (
            'helper 模块 import smoke test',
            '安装布局模拟检查',
            'ROS_VALIDATION_zh.md',
            'validate_race_ros.sh',
        ):
            self.assertIn(verification, phases_text)

    def test_root_readme_links_to_race_docs(self):
        repo_root = Path(__file__).resolve().parents[2]
        if not (repo_root / 'README.md').exists():
            self.skipTest('source-tree README.md is not installed with osracer_race')
        readme_text = (repo_root / 'README.md').read_text(encoding='utf-8')
        self.assertIn('osracer_race/README_zh.md', readme_text)
        self.assertIn('osracer_race/PHASES_zh.md', readme_text)
        self.assertIn('osracer_race/ROS_VALIDATION_zh.md', readme_text)
        self.assertIn('scripts/validate_race_ros.sh', readme_text)
        self.assertIn('Recommended first run sequence', readme_text)
        self.assertIn('race_safe.yaml', readme_text)
        self.assertIn('race_fast.yaml', readme_text)
        self.assertIn('git submodule update --init --recursive', readme_text)
        self.assertIn('Development and Runtime Split', readme_text)
        self.assertIn('Jetson Orin Nano', readme_text)

    def test_public_tree_does_not_include_local_dev_check_scripts(self):
        repo_root = Path(__file__).resolve().parents[2]
        self.assertFalse((repo_root / 'tools' / 'local_dev_check').exists())

    def test_package_metadata_is_trimmed_and_scripts_are_executable(self):
        package_root = self.source_package_root()
        setup_text = (package_root / 'setup.py').read_text(encoding='utf-8')
        setup_cfg = (package_root / 'setup.cfg').read_text(encoding='utf-8')
        root = ET.parse(package_root / 'package.xml').getroot()
        dependencies = {element.text for element in root.findall('depend')}
        exec_dependencies = {element.text for element in root.findall('exec_depend')}
        package_text = (package_root / 'package.xml').read_text(encoding='utf-8')
        resource_text = (package_root / 'resource' / 'osracer_race').read_text(encoding='utf-8').strip()
        self.assertEqual(root.findtext('name'), 'osracer_race')
        self.assertEqual(resource_text, 'osracer_race')
        self.assertIn('script_dir=$base/lib/osracer_race', setup_cfg)
        self.assertIn('install_scripts=$base/lib/osracer_race', setup_cfg)
        self.assertNotIn('ament_index_python', dependencies)
        self.assertNotIn('geometry_msgs', dependencies)
        self.assertIn('python3-yaml', exec_dependencies)
        placeholder_domain = 'example' + '.com'
        self.assertNotIn(placeholder_domain, setup_text)
        self.assertNotIn(placeholder_domain, package_text)
        self.assertTrue(os.access(package_root / 'scripts' / 'check_race_package.sh', os.X_OK))
        self.assertTrue(os.access(package_root / 'scripts' / 'validate_race_ros.sh', os.X_OK))

    def test_race_package_license_matches_root_license(self):
        package_root = self.source_package_root()
        repo_root = package_root.parent
        if not (repo_root / 'LICENSE').exists():
            self.skipTest('source-tree root LICENSE is not installed with osracer_race')
        setup_text = (package_root / 'setup.py').read_text(encoding='utf-8')
        package_xml = ET.parse(package_root / 'package.xml').getroot()
        license_text = (repo_root / 'LICENSE').read_text(encoding='utf-8')
        self.assertEqual(package_xml.findtext('license'), 'MIT')
        self.assertIn("license='MIT'", setup_text)
        self.assertTrue(license_text.startswith('MIT License'))
        self.assertIn('Copyright (c) 2025 osrbot', license_text)

    def test_setup_installs_runtime_resources(self):
        package_root = self.source_package_root()
        setup_text = (package_root / 'setup.py').read_text(encoding='utf-8')
        for expected in (
            "'package.xml'",
            "'README_zh.md'",
            "'PHASES_zh.md'",
            "'ROS_VALIDATION_zh.md'",
            "glob('config/*.yaml')",
            "glob('config/tracks/*')",
            "glob('launch/*.launch.py')",
            "glob('scripts/*')",
            "glob('test/*.py')",
        ):
            self.assertIn(expected, setup_text)

    def test_vehicle_side_ros_validation_script_covers_public_entries(self):
        package_root = Path(__file__).resolve().parents[1]
        script = package_root / 'scripts' / 'validate_race_ros.sh'
        script_text = script.read_text(encoding='utf-8')
        self.assertIn('ros2 pkg prefix osracer_race', script_text)
        self.assertIn('trap cleanup EXIT', script_text)
        self.assertIn('mktemp /tmp/osracer_race_profile.XXXXXX', script_text)
        self.assertIn('mktemp /tmp/osracer_race_eval.XXXXXX', script_text)
        self.assertIn('ros2 run osracer_race raceline_tools --help', script_text)
        self.assertIn('ros2 run osracer_race race_report_tools --help', script_text)
        for launch_name in (
            'gap_follow.launch.py',
            'pure_pursuit.launch.py',
            'stanley.launch.py',
            'mpc.launch.py',
            'track_record.launch.py',
            'vehicle_id.launch.py',
            'race_bringup.launch.py',
        ):
            self.assertIn(f'ros2 launch osracer_race {launch_name} --show-args', script_text)
        self.assertIn('/scan /odometry/filtered /ackermann_cmd /race/safety_stop', script_text)

    def test_self_check_script_supports_source_and_installed_modes(self):
        package_root = Path(__file__).resolve().parents[1]
        script_text = (package_root / 'scripts' / 'check_race_package.sh').read_text(encoding='utf-8')
        self.assertIn('CHECK_MODE="source"', script_text)
        self.assertIn('CHECK_MODE="installed"', script_text)
        self.assertIn('for script in "${SCRIPT_DIR}"/*.sh', script_text)
        self.assertIn('bash -n "${script}"', script_text)
        self.assertIn('[5/7] Helper import smoke tests', script_text)
        for module in (
            'osracer_race.eval_tools',
            'osracer_race.gap_follow_tools',
            'osracer_race.mpc_tools',
            'osracer_race.overtake_tools',
            'osracer_race.safety_tools',
            'osracer_race.speed_profile_tools',
            'osracer_race.tracking_tools',
            'osracer_race.vehicle_id_tools',
        ):
            self.assertIn(module, script_text)
        self.assertIn('python3 -m osracer_race.raceline_tools --help', script_text)
        self.assertIn('python3 -m osracer_race.race_report_tools --help', script_text)
        self.assertIn('ros2 run osracer_race raceline_tools --help', script_text)
        self.assertIn('ros2 run osracer_race race_report_tools --help', script_text)
        for launch_name in (
            'gap_follow.launch.py',
            'pure_pursuit.launch.py',
            'stanley.launch.py',
            'mpc.launch.py',
            'track_record.launch.py',
            'vehicle_id.launch.py',
            'race_bringup.launch.py',
        ):
            self.assertIn(f'ros2 launch osracer_race {launch_name} --show-args', script_text)
        self.assertIn('Installed package check; skipped source-tree colcon build.', script_text)


if __name__ == '__main__':
    unittest.main()
