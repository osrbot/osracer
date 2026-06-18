#!/usr/bin/env python3

from launch import LaunchDescription
from launch_ros.actions import Node
from launch.substitutions import PathJoinSubstitution, LaunchConfiguration, PythonExpression
from launch.actions import DeclareLaunchArgument, GroupAction
from launch.conditions import IfCondition, UnlessCondition
from launch_ros.substitutions import FindPackageShare

def generate_launch_description():
    # Base parameter declarations
    port_name_arg = DeclareLaunchArgument(
        'port_name',
        default_value='/dev/osrbot_base',
        description='Serial port device name'
    )
    
    baud_rate_arg = DeclareLaunchArgument(
        'baud_rate',
        default_value='460800',
        description='Serial port baud rate'
    )
    
    odom_frame_arg = DeclareLaunchArgument(
        'odom_frame',
        default_value='odom',
        description='Odometry TF frame name'
    )
    
    base_frame_arg = DeclareLaunchArgument(
        'base_frame',
        default_value='base_footprint',
        description='Robot base TF frame name'
    )
    
    imu_frame_arg = DeclareLaunchArgument(
        'imu_frame',
        default_value='imu_link',
        description='IMU sensor TF frame name'
    )
    
    wheelbase_arg = DeclareLaunchArgument(
        'wheelbase',
        default_value='0.285',
        description='Vehicle wheelbase (meters)'
    )
    
    max_steering_angle_arg = DeclareLaunchArgument(
        'max_steering_angle_deg',
        default_value='30.0',
        description='Maximum steering angle (degrees)'
    )

    cmd_timeout_arg = DeclareLaunchArgument(
        'cmd_watchdog_timeout_s',
        default_value='0.5',
        description='Command watchdog timeout (seconds)'
    )

    # EKF related parameters
    use_ekf_arg = DeclareLaunchArgument(
        'use_ekf',
        default_value='False',
        description='Whether to enable EKF fusion localization'
    )

    publish_tf_arg = DeclareLaunchArgument(
        'publish_tf',
        default_value=PythonExpression(["'False' if ", LaunchConfiguration('use_ekf'), " else 'True'"]),
        description='Whether the chassis node publishes TF'
    )    
    
    use_respawn_arg = DeclareLaunchArgument(
        'use_respawn',
        default_value='False',
        description='Whether to enable node auto-respawn'
    )
    
    log_level_arg = DeclareLaunchArgument(
        'log_level',
        default_value='info',
        description='Log level'
    )
    
    ekf_params_file_arg = DeclareLaunchArgument(
        'ekf_params_file',
        default_value=PathJoinSubstitution([
            FindPackageShare('osracer_bringup'),
            'param', 'chassis_ekf_params.yaml'
        ]),
        description='Path to EKF parameter file'
    )

    map_frame_arg = DeclareLaunchArgument(
        'map_frame',
        default_value='map',
        description='Map coordinate frame name'
    )

    # Chassis node
    osracer_chassis_node = Node(
        package='osracer_bringup', 
        executable='chassis_ackermann.py',
        name='osracer_chassis',
        parameters=[{
            'port_name': LaunchConfiguration('port_name'),
            'baud_rate': LaunchConfiguration('baud_rate'),
            'odom_frame': LaunchConfiguration('odom_frame'),
            'base_frame': LaunchConfiguration('base_frame'),
            'imu_frame': LaunchConfiguration('imu_frame'),
            'wheelbase': LaunchConfiguration('wheelbase'),
            'max_steering_angle_deg': LaunchConfiguration('max_steering_angle_deg'),
            'cmd_watchdog_timeout_s': LaunchConfiguration('cmd_watchdog_timeout_s'),
            'publish_tf': LaunchConfiguration('publish_tf'),
        }],
        output='screen',
        emulate_tty=True,
        remappings=[
            ('/cmd_vel', 'cmd_vel'),
            ('/ackermann_cmd', 'ackermann_cmd'),
            ('/imu/data', PythonExpression(["'imu' if ", LaunchConfiguration('use_ekf'), " else 'imu_filter'"])),
            ('/odom', PythonExpression(["'odom' if ", LaunchConfiguration('use_ekf'), " else '/odometry/filtered'"]))
        ]
    )

    # EKF related node group
    ekf_group = GroupAction(
        condition=IfCondition(LaunchConfiguration('use_ekf')),
        actions=[
            # Complementary Filter
            Node(
                package='imu_complementary_filter',
                executable='complementary_filter_node',
                name='complementary_filter_gain_node',
                respawn=LaunchConfiguration('use_respawn'),
                respawn_delay=2.0,
                output='screen',
                remappings=[
                    ("imu/data", "imu_filter"),
                    ("imu/data_raw", "imu")
                ],
                parameters=[{
                    'do_bias_estimation': True,
                    'do_adaptive_gain': True,
                    'use_mag': False,
                    'gain_acc': 0.01,
                    'gain_mag': 0.01
                }],
                arguments=["--ros-args", "--log-level", LaunchConfiguration('log_level')],
            ),
            # EKF Node
            Node(
                package='robot_localization',
                executable='ekf_node',
                name='ekf_filter_node',
                respawn=LaunchConfiguration('use_respawn'),
                respawn_delay=2.0,
                output='screen',
                parameters=[
                    LaunchConfiguration('ekf_params_file'),
                    {
                        'map_frame': LaunchConfiguration('map_frame'),
                        'odom_frame': LaunchConfiguration('odom_frame'),
                        'base_link_frame': LaunchConfiguration('base_frame'),
                        'world_frame': LaunchConfiguration('odom_frame'),
                        'publish_tf': True
                    }
                ],
                arguments=["--ros-args", "--log-level", LaunchConfiguration('log_level')],
            )
        ]
    )

    return LaunchDescription([
        port_name_arg,
        baud_rate_arg,
        odom_frame_arg,
        base_frame_arg,
        imu_frame_arg,
        wheelbase_arg,
        max_steering_angle_arg,
        cmd_timeout_arg,
        use_ekf_arg,
        publish_tf_arg,
        use_respawn_arg,
        log_level_arg,
        ekf_params_file_arg,
        map_frame_arg,
        osracer_chassis_node,
        ekf_group
    ])
