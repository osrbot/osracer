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

    return LaunchDescription([
        DeclareLaunchArgument(
            'race_config',
            default_value=PathJoinSubstitution([
                FindPackageShare('osracer_race'), 'config', 'race_safe.yaml']),
            description='Race parameter file'),
        DeclareLaunchArgument(
            'output_csv',
            default_value='/tmp/osracer_recorded_track.csv',
            description='Output track CSV path'),
        Node(
            package='osracer_race',
            executable='track_recorder_node',
            name='track_recorder_node',
            parameters=[base_profile, vehicle_config, race_config, {'output_csv': LaunchConfiguration('output_csv')}],
            output='screen'),
    ])
