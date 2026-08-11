from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    base_profile = PathJoinSubstitution([
        FindPackageShare('osracer_base'), 'config', 'vehicles', 'red.yaml'])
    vehicle_config = PathJoinSubstitution([
        FindPackageShare('osracer_race'), 'config', 'vehicle.yaml'])
    race_config = LaunchConfiguration('race_config')
    eval_output_csv = LaunchConfiguration('eval_output_csv')

    return LaunchDescription([
        DeclareLaunchArgument(
            'race_config',
            default_value=PathJoinSubstitution([
                FindPackageShare('osracer_race'), 'config', 'race_safe.yaml']),
            description='Race parameter file'),
        DeclareLaunchArgument(
            'eval_output_csv',
            default_value='/tmp/osracer_race_eval_gap_follow.csv',
            description='Race evaluation CSV output path'),
        Node(
            package='osracer_race',
            executable='safety_node',
            name='race_safety_node',
            parameters=[base_profile, vehicle_config, race_config],
            output='screen'),
        Node(
            package='osracer_race',
            executable='gap_follow_node',
            name='gap_follow_node',
            parameters=[base_profile, vehicle_config, race_config, {'ackermann_topic': '/race/raw_ackermann_cmd'}],
            output='screen'),
        Node(
            package='osracer_race',
            executable='speed_profile_node',
            name='speed_profile_node',
            parameters=[base_profile, vehicle_config, race_config],
            output='screen'),
        Node(
            package='osracer_race',
            executable='lap_timer_node',
            name='lap_timer_node',
            parameters=[base_profile, vehicle_config, race_config],
            output='screen'),
        Node(
            package='osracer_race',
            executable='race_evaluator_node',
            name='race_evaluator_node',
            parameters=[base_profile, vehicle_config, race_config, {'output_csv': eval_output_csv}],
            output='screen'),
    ])
