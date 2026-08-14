from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, TimerAction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def controller_is(name):
    return IfCondition(PythonExpression(["'", LaunchConfiguration('controller'), "' == '", name, "'"]))


def controller_uses_raceline():
    return IfCondition(PythonExpression([
        "'", LaunchConfiguration('controller'), "' in ['pure_pursuit', 'stanley', 'mpc']"
    ]))


def generate_launch_description():
    vehicle_config = PathJoinSubstitution([
        FindPackageShare('osracer_race'), 'config', 'vehicle.yaml'])
    race_config = LaunchConfiguration('race_config')
    raceline = LaunchConfiguration('raceline_file')
    eval_output_csv = LaunchConfiguration('eval_output_csv')

    bringup = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([
            FindPackageShare('osracer_bringup'), 'launch', 'bringup.launch.py'
        ]))
    )

    race_nodes = [
        Node(
            package='osracer_race',
            executable='safety_node',
            name='race_safety_node',
            parameters=[vehicle_config, race_config],
            output='screen'),
        Node(
            package='osracer_race',
            executable='speed_profile_node',
            name='speed_profile_node',
            parameters=[vehicle_config, race_config],
            output='screen'),
        Node(
            package='osracer_race',
            executable='gap_follow_node',
            name='gap_follow_node',
            condition=controller_is('gap_follow'),
            parameters=[vehicle_config, race_config, {'ackermann_topic': '/race/raw_ackermann_cmd'}],
            output='screen'),
        Node(
            package='osracer_race',
            executable='pure_pursuit_node',
            name='pure_pursuit_node',
            condition=controller_is('pure_pursuit'),
            parameters=[vehicle_config, race_config, {
                'ackermann_topic': '/race/tracking_ackermann_cmd',
                'raceline_file': raceline,
            }],
            output='screen'),
        Node(
            package='osracer_race',
            executable='stanley_node',
            name='stanley_node',
            condition=controller_is('stanley'),
            parameters=[vehicle_config, race_config, {
                'ackermann_topic': '/race/tracking_ackermann_cmd',
                'raceline_file': raceline,
            }],
            output='screen'),
        Node(
            package='osracer_race',
            executable='mpc_controller_node',
            name='mpc_controller_node',
            condition=controller_is('mpc'),
            parameters=[vehicle_config, race_config, {
                'ackermann_topic': '/race/tracking_ackermann_cmd',
                'raceline_file': raceline,
            }],
            output='screen'),
        Node(
            package='osracer_race',
            executable='obstacle_overtake_node',
            name='obstacle_overtake_node',
            condition=controller_uses_raceline(),
            parameters=[vehicle_config, race_config],
            output='screen'),
        Node(
            package='osracer_race',
            executable='lap_timer_node',
            name='lap_timer_node',
            parameters=[vehicle_config, race_config],
            output='screen'),
        Node(
            package='osracer_race',
            executable='race_evaluator_node',
            name='race_evaluator_node',
            parameters=[vehicle_config, race_config, {
                'raceline_file': raceline,
                'output_csv': eval_output_csv,
            }],
            output='screen'),
    ]

    return LaunchDescription([
        DeclareLaunchArgument(
            'controller',
            default_value='gap_follow',
            choices=['gap_follow', 'pure_pursuit', 'stanley', 'mpc'],
            description='Race controller: gap_follow, pure_pursuit, stanley, or mpc'),
        DeclareLaunchArgument(
            'race_config',
            default_value=PathJoinSubstitution([
                FindPackageShare('osracer_race'), 'config', 'race_safe.yaml']),
            description='Race parameter file'),
        DeclareLaunchArgument(
            'raceline_file',
            default_value=PathJoinSubstitution([
                FindPackageShare('osracer_race'), 'config', 'tracks', 'example_raceline.csv']),
            description='CSV raceline file with x,y,speed,curvature columns'),
        DeclareLaunchArgument(
            'eval_output_csv',
            default_value='/tmp/osracer_race_eval.csv',
            description='Race evaluation CSV output path'),
        bringup,
        TimerAction(period=5.0, actions=race_nodes),
    ])
