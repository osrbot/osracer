from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    world = LaunchConfiguration('world')

    base_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([
            FindPackageShare('osracer_sim'), 'launch', 'base_sim.launch.py'
        ])),
        launch_arguments={
            'use_sim_time': 'true',
            'use_rviz': LaunchConfiguration('use_rviz'),
        }.items(),
    )

    gazebo = ExecuteProcess(
        cmd=['gz', 'sim', '-r', world],
        output='screen',
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            'world',
            default_value=PathJoinSubstitution([
                FindPackageShare('osracer_sim'), 'worlds', 'osracer_rect_track.sdf'
            ]),
            description='Gazebo Sim world file'),
        DeclareLaunchArgument('use_rviz', default_value='false', choices=['true', 'false']),
        gazebo,
        base_sim,
    ])
