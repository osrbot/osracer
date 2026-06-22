from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    base_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([
            FindPackageShare('osracer_sim'), 'launch', 'base_sim.launch.py'
        ])),
        launch_arguments={
            'use_sim_time': 'true',
            'use_rviz': LaunchConfiguration('use_rviz'),
        }.items(),
    )

    nav = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([
            FindPackageShare('osracer_navigation'), 'launch', 'nav2.launch.py'
        ])),
        launch_arguments={
            'use_sim_time': 'true',
            'use_rviz': LaunchConfiguration('use_rviz'),
            'use_planner': 'teb',
        }.items(),
    )

    return LaunchDescription([
        DeclareLaunchArgument('use_rviz', default_value='false', choices=['true', 'false']),
        base_sim,
        TimerAction(period=2.0, actions=[nav]),
    ])
