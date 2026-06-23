import math
import unittest
from pathlib import Path

from osracer_sim.kinematics import (
    ackermann_front_angles,
    ackermann_gazebo_commands,
    clamp,
    obstacle_preset,
    ray_circle_distance,
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
        left, right, velocities = ackermann_gazebo_commands(1.0, 0.0, 0.285, 0.215, 0.0425)
        self.assertEqual(left, 0.0)
        self.assertEqual(right, 0.0)
        self.assertEqual(len(velocities), 4)
        self.assertTrue(all(math.isclose(value, 1.0 / 0.0425) for value in velocities))

    def test_ackermann_gazebo_commands_split_inner_outer_wheel_speed(self):
        left, right, velocities = ackermann_gazebo_commands(1.0, 0.3, 0.285, 0.215, 0.0425)
        self.assertGreater(left, right)
        left_front, right_front, left_rear, right_rear = velocities
        self.assertLess(left_front, right_front)
        self.assertLess(left_rear, right_rear)

        _, _, right_turn_velocities = ackermann_gazebo_commands(1.0, -0.3, 0.285, 0.215, 0.0425)
        left_front, right_front, left_rear, right_rear = right_turn_velocities
        self.assertGreater(left_front, right_front)
        self.assertGreater(left_rear, right_rear)

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

    def test_ray_circle_distance_hits_obstacle(self):
        distance = ray_circle_distance(0.0, 0.0, 0.0, (2.0, 0.0, 0.25), 8.0)
        self.assertAlmostEqual(distance, 1.75)

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

    def test_track_scan_can_include_obstacles(self):
        segments = rectangular_track_segments(7.0, 4.5, 1.1)
        clear_ranges = synthetic_track_scan(0.0, -1.7, 0.0, 5, -0.4, 0.2, 8.0, segments)
        blocked_ranges = synthetic_track_scan(
            0.0, -1.7, 0.0, 5, -0.4, 0.2, 8.0, segments, [(1.0, -1.7, 0.2)])
        self.assertLess(blocked_ranges[2], clear_ranges[2])
        self.assertAlmostEqual(blocked_ranges[2], 0.8)

    def test_obstacle_presets_cover_common_sim_scenarios(self):
        self.assertEqual(obstacle_preset('off'), [])
        self.assertEqual(obstacle_preset('front'), [(2.0, -1.7, 0.25)])
        self.assertEqual(obstacle_preset('left'), [(1.6, -1.15, 0.25)])
        self.assertEqual(obstacle_preset('right'), [(1.6, -2.25, 0.25)])
        self.assertEqual(obstacle_preset('unknown'), [])

    def test_race_sim_exposes_eval_output_csv(self):
        package_dir = Path(__file__).resolve().parents[1]
        launch_text = (package_dir / 'launch' / 'race_sim.launch.py').read_text(encoding='utf-8')
        self.assertIn("LaunchConfiguration('eval_output_csv')", launch_text)
        self.assertIn("'eval_output_csv': eval_output_csv", launch_text)
        self.assertIn("DeclareLaunchArgument('eval_output_csv'", launch_text)
        self.assertIn("LaunchConfiguration('obstacle_preset')", launch_text)

    def test_validate_sim_ros_covers_sim_launches_and_stages(self):
        package_dir = Path(__file__).resolve().parents[1]
        script_text = (package_dir / 'scripts' / 'validate_sim_ros.sh').read_text(encoding='utf-8')
        for launch_name in (
            'base_sim.launch.py',
            'gazebo.launch.py',
            'slam_sim.launch.py',
            'navigation_sim.launch.py',
            'race_sim.launch.py',
        ):
            self.assertIn(f'ros2 launch osracer_sim {launch_name}', script_text)
        for stage in ('gap_follow', 'track_record', 'pure_pursuit', 'stanley', 'vehicle_id', 'mpc'):
            self.assertIn(stage, script_text)
        self.assertIn('obstacle_enabled:=true', script_text)
        self.assertIn('use_gz_control:=true', script_text)
        self.assertIn('osracer_rect_track_obstacle.sdf', script_text)
        self.assertIn('print_sim_scenarios.sh', script_text)

    def test_scenario_matrix_covers_four_stage_workflow(self):
        package_dir = Path(__file__).resolve().parents[1]
        script_text = (package_dir / 'scripts' / 'print_sim_scenarios.sh').read_text(
            encoding='utf-8')
        for stage in ('gap_follow', 'track_record', 'pure_pursuit', 'stanley', 'vehicle_id', 'mpc'):
            self.assertIn(f'stage:={stage}', script_text)
        self.assertIn('osracer_rect_track_obstacle.sdf', script_text)
        self.assertIn('race_report_tools', script_text)

    def test_gazebo_obstacle_world_matches_front_preset(self):
        package_dir = Path(__file__).resolve().parents[1]
        world_text = (package_dir / 'worlds' / 'osracer_rect_track_obstacle.sdf').read_text(
            encoding='utf-8')
        self.assertIn('<model name="front_obstacle">', world_text)
        self.assertIn('<pose>2.0 -1.7 0.18 0 0 0</pose>', world_text)
        self.assertIn('<radius>0.25</radius>', world_text)


if __name__ == '__main__':
    unittest.main()
