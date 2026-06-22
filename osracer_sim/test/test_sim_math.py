import math
import unittest

from osracer_sim.kinematics import (
    ackermann_front_angles,
    ackermann_gazebo_commands,
    clamp,
    ray_segment_distance,
    rectangular_track_segments,
    steering_from_twist,
    synthetic_scan,
    synthetic_track_scan,
    yaw_to_quat,
)


class SimMathTest(unittest.TestCase):
    def test_clamp_limits_values(self):
        self.assertEqual(clamp(2.0, -1.0, 1.0), 1.0)
        self.assertEqual(clamp(-2.0, -1.0, 1.0), -1.0)
        self.assertEqual(clamp(0.2, -1.0, 1.0), 0.2)

    def test_twist_to_steering_uses_wheelbase(self):
        steering = steering_from_twist(1.0, 1.0, 0.285)
        self.assertAlmostEqual(steering, math.atan(0.285))

    def test_ackermann_front_angles_split_inner_outer_wheels(self):
        left, right = ackermann_front_angles(0.3, 0.285, 0.215)
        self.assertGreater(left, right)
        self.assertGreater(left, 0.0)
        self.assertGreater(right, 0.0)

    def test_ackermann_gazebo_commands_convert_speed_to_wheel_velocity(self):
        left, right, velocity = ackermann_gazebo_commands(1.0, 0.3, 0.285, 0.215, 0.0425)
        self.assertGreater(left, right)
        self.assertAlmostEqual(velocity, 1.0 / 0.0425)

    def test_yaw_to_quat_is_normalized(self):
        quat = yaw_to_quat(0.7)
        norm = math.sqrt(sum(value * value for value in quat))
        self.assertAlmostEqual(norm, 1.0)

    def test_synthetic_scan_returns_positive_ranges(self):
        ranges = synthetic_scan(11, -1.0, 0.2, 8.0)
        self.assertEqual(len(ranges), 11)
        self.assertTrue(all(0.0 < value <= 8.0 for value in ranges))

    def test_ray_segment_distance_hits_wall(self):
        distance = ray_segment_distance(0.0, 0.0, 0.0, ((2.0, -1.0), (2.0, 1.0)), 8.0)
        self.assertAlmostEqual(distance, 2.0)

    def test_rectangular_track_segments_include_inner_and_outer_walls(self):
        segments = rectangular_track_segments(7.0, 4.5, 1.1)
        self.assertEqual(len(segments), 8)
        self.assertIn(((-3.5, -2.25), (3.5, -2.25)), segments)
        self.assertIn(((-2.4, -1.15), (2.4, -1.15)), segments)

    def test_track_scan_uses_vehicle_pose(self):
        segments = rectangular_track_segments(7.0, 4.5, 1.1)
        ranges = synthetic_track_scan(0.0, -1.7, 0.0, 5, -0.4, 0.2, 8.0, segments)
        self.assertEqual(len(ranges), 5)
        self.assertTrue(all(0.0 < value <= 8.0 for value in ranges))
        self.assertLess(ranges[2], 3.6)


if __name__ == '__main__':
    unittest.main()
