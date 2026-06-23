from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, TimerAction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.substitutions import FindPackageShare


def stage_is(name):
    return IfCondition(PythonExpression(["'", LaunchConfiguration('stage'), "' == '", name, "'"]))


def race_launch(name):
    return PythonLaunchDescriptionSource(PathJoinSubstitution([
        FindPackageShare('osracer_race'), 'launch', name
    ]))


def generate_launch_description():
    race_config = LaunchConfiguration('race_config')
    raceline = LaunchConfiguration('raceline_file')

    base_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([
            FindPackageShare('osracer_sim'), 'launch', 'base_sim.launch.py'
        ])),
        launch_arguments={
            'use_sim_time': 'true',
            'use_rviz': LaunchConfiguration('use_rviz'),
            'obstacle_enabled': LaunchConfiguration('obstacle_enabled'),
            'obstacle_x': LaunchConfiguration('obstacle_x'),
            'obstacle_y': LaunchConfiguration('obstacle_y'),
            'obstacle_radius': LaunchConfiguration('obstacle_radius'),
        }.items(),
    )

    launches = [
        IncludeLaunchDescription(
            race_launch('gap_follow.launch.py'),
            condition=stage_is('gap_follow'),
            launch_arguments={'race_config': race_config}.items(),
        ),
        IncludeLaunchDescription(
            race_launch('track_record.launch.py'),
            condition=stage_is('track_record'),
            launch_arguments={
                'race_config': race_config,
                'output_csv': LaunchConfiguration('record_output_csv'),
            }.items(),
        ),
        IncludeLaunchDescription(
            race_launch('pure_pursuit.launch.py'),
            condition=stage_is('pure_pursuit'),
            launch_arguments={'race_config': race_config, 'raceline_file': raceline}.items(),
        ),
        IncludeLaunchDescription(
            race_launch('stanley.launch.py'),
            condition=stage_is('stanley'),
            launch_arguments={'race_config': race_config, 'raceline_file': raceline}.items(),
        ),
        IncludeLaunchDescription(
            race_launch('vehicle_id.launch.py'),
            condition=stage_is('vehicle_id'),
            launch_arguments={'race_config': race_config}.items(),
        ),
        IncludeLaunchDescription(
            race_launch('mpc.launch.py'),
            condition=stage_is('mpc'),
            launch_arguments={'race_config': race_config, 'raceline_file': raceline}.items(),
        ),
    ]

    return LaunchDescription([
        DeclareLaunchArgument(
            'stage',
            default_value='gap_follow',
            choices=['gap_follow', 'track_record', 'pure_pursuit', 'stanley', 'vehicle_id', 'mpc'],
            description='Simulation stage to launch'),
        DeclareLaunchArgument('use_rviz', default_value='false', choices=['true', 'false']),
        DeclareLaunchArgument(
            'race_config',
            default_value=PathJoinSubstitution([
                FindPackageShare('osracer_race'), 'config', 'race_safe.yaml'
            ])),
        DeclareLaunchArgument(
            'raceline_file',
            default_value=PathJoinSubstitution([
                FindPackageShare('osracer_race'), 'config', 'tracks', 'example_raceline.csv'
            ])),
        DeclareLaunchArgument('record_output_csv', default_value='/tmp/osracer_sim_recorded_track.csv'),
        DeclareLaunchArgument('obstacle_enabled', default_value='false', choices=['true', 'false']),
        DeclareLaunchArgument('obstacle_x', default_value='2.0'),
        DeclareLaunchArgument('obstacle_y', default_value='-1.7'),
        DeclareLaunchArgument('obstacle_radius', default_value='0.25'),
        base_sim,
        TimerAction(period=1.0, actions=launches),
    ])
