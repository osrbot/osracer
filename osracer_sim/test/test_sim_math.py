import math
import unittest

from osracer_sim.kinematics import (
    ackermann_front_angles,
    clamp,
    steering_from_twist,
    synthetic_scan,
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

    def test_yaw_to_quat_is_normalized(self):
        quat = yaw_to_quat(0.7)
        norm = math.sqrt(sum(value * value for value in quat))
        self.assertAlmostEqual(norm, 1.0)

    def test_synthetic_scan_returns_positive_ranges(self):
        ranges = synthetic_scan(11, -1.0, 0.2, 8.0)
        self.assertEqual(len(ranges), 11)
        self.assertTrue(all(0.0 < value <= 8.0 for value in ranges))


if __name__ == '__main__':
    unittest.main()
