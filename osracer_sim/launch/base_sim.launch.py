from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    use_rviz = LaunchConfiguration('use_rviz')
    use_sim_time = LaunchConfiguration('use_sim_time')
    odom_topic = LaunchConfiguration('odom_topic')
    imu_topic = LaunchConfiguration('imu_topic')

    description_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([
            FindPackageShare('osracer_description'), 'launch', 'osracer_description.launch.py'
        ])),
        launch_arguments={
            'use_sim_time': use_sim_time,
            'use_rviz': use_rviz,
            'start_jsp': 'false',
            'odom_topic': odom_topic,
        }.items(),
    )

    sim_node = Node(
        package='osracer_sim',
        executable='ackermann_kinematic_sim_node',
        name='osracer_ackermann_kinematic_sim',
        output='screen',
        parameters=[{
            'wheelbase': ParameterValue(LaunchConfiguration('wheelbase'), value_type=float),
            'track_width': ParameterValue(LaunchConfiguration('track_width'), value_type=float),
            'wheel_radius': ParameterValue(LaunchConfiguration('wheel_radius'), value_type=float),
            'max_speed_mps': ParameterValue(LaunchConfiguration('max_speed_mps'), value_type=float),
            'max_steering_angle_deg': ParameterValue(
                LaunchConfiguration('max_steering_angle_deg'), value_type=float),
            'odom_topic': odom_topic,
            'imu_topic': imu_topic,
            'publish_tf': ParameterValue(LaunchConfiguration('publish_tf'), value_type=bool),
            'publish_imu': ParameterValue(LaunchConfiguration('publish_imu'), value_type=bool),
            'publish_scan': ParameterValue(LaunchConfiguration('publish_scan'), value_type=bool),
            'publish_clock': ParameterValue(LaunchConfiguration('publish_clock'), value_type=bool),
            'scan_environment': LaunchConfiguration('scan_environment'),
            'track_outer_length_m': ParameterValue(
                LaunchConfiguration('track_outer_length_m'), value_type=float),
            'track_outer_width_m': ParameterValue(
                LaunchConfiguration('track_outer_width_m'), value_type=float),
            'track_lane_width_m': ParameterValue(
                LaunchConfiguration('track_lane_width_m'), value_type=float),
            'obstacle_preset': LaunchConfiguration('obstacle_preset'),
            'obstacle_enabled': ParameterValue(LaunchConfiguration('obstacle_enabled'), value_type=bool),
            'obstacle_x': ParameterValue(LaunchConfiguration('obstacle_x'), value_type=float),
            'obstacle_y': ParameterValue(LaunchConfiguration('obstacle_y'), value_type=float),
            'obstacle_radius': ParameterValue(LaunchConfiguration('obstacle_radius'), value_type=float),
            'initial_x': ParameterValue(LaunchConfiguration('initial_x'), value_type=float),
            'initial_y': ParameterValue(LaunchConfiguration('initial_y'), value_type=float),
            'initial_yaw_deg': ParameterValue(LaunchConfiguration('initial_yaw_deg'), value_type=float),
        }],
    )

    return LaunchDescription([
        DeclareLaunchArgument('use_sim_time', default_value='true', choices=['true', 'false']),
        DeclareLaunchArgument('use_rviz', default_value='false', choices=['true', 'false']),
        DeclareLaunchArgument('odom_topic', default_value='/odometry/filtered'),
        DeclareLaunchArgument('imu_topic', default_value='/imu_filter'),
        DeclareLaunchArgument('wheelbase', default_value='0.285'),
        DeclareLaunchArgument('track_width', default_value='0.215'),
        DeclareLaunchArgument('wheel_radius', default_value='0.0425'),
        DeclareLaunchArgument('max_speed_mps', default_value='3.0'),
        DeclareLaunchArgument('max_steering_angle_deg', default_value='30.0'),
        DeclareLaunchArgument('publish_tf', default_value='true', choices=['true', 'false']),
        DeclareLaunchArgument('publish_imu', default_value='true', choices=['true', 'false']),
        DeclareLaunchArgument('publish_scan', default_value='true', choices=['true', 'false']),
        DeclareLaunchArgument('publish_clock', default_value='true', choices=['true', 'false']),
        DeclareLaunchArgument('scan_environment', default_value='track', choices=['track', 'hallway']),
        DeclareLaunchArgument('track_outer_length_m', default_value='7.0'),
        DeclareLaunchArgument('track_outer_width_m', default_value='4.5'),
        DeclareLaunchArgument('track_lane_width_m', default_value='1.1'),
        DeclareLaunchArgument(
            'obstacle_preset',
            default_value='custom',
            choices=['custom', 'off', 'front', 'left', 'right']),
        DeclareLaunchArgument('obstacle_enabled', default_value='false', choices=['true', 'false']),
        DeclareLaunchArgument('obstacle_x', default_value='2.0'),
        DeclareLaunchArgument('obstacle_y', default_value='-1.7'),
        DeclareLaunchArgument('obstacle_radius', default_value='0.25'),
        DeclareLaunchArgument('initial_x', default_value='0.0'),
        DeclareLaunchArgument('initial_y', default_value='-1.7'),
        DeclareLaunchArgument('initial_yaw_deg', default_value='0.0'),
        description_launch,
        sim_node,
    ])
