#!/usr/bin/env python3

from launch import LaunchDescription
from launch_ros.actions import Node
from launch.substitutions import PathJoinSubstitution, LaunchConfiguration
from launch.actions import DeclareLaunchArgument
from launch_ros.substitutions import FindPackageShare

def generate_launch_description():
    # 声明启动参数
    port_name_arg = DeclareLaunchArgument(
        'port_name',
        default_value='/dev/osrbot_base',
        description='串口设备名称，如 /dev/ttyACM0 或 /dev/osrbot_base'
    )
    
    baud_rate_arg = DeclareLaunchArgument(
        'baud_rate',
        default_value='460800',
        description='串口波特率'
    )
    
    odom_frame_arg = DeclareLaunchArgument(
        'odom_frame',
        default_value='odom',
        description='里程计TF框架名称'
    )
    
    base_frame_arg = DeclareLaunchArgument(
        'base_frame',
        default_value='base_footprint',
        description='机器人基础TF框架名称'
    )
    
    imu_frame_arg = DeclareLaunchArgument(
        'imu_frame',
        default_value='imu_link',
        description='IMU传感器TF框架名称'
    )
    
    wheelbase_arg = DeclareLaunchArgument(
        'wheelbase',
        default_value='0.285',
        description='车辆轴距（米）'
    )
    
    max_steering_angle_arg = DeclareLaunchArgument(
        'max_steering_angle_deg',
        default_value='30.0',
        description='最大转向角度（度）'
    )
    
    cmd_timeout_arg = DeclareLaunchArgument(
        'cmd_watchdog_timeout_s',
        default_value='0.5',
        description='命令看门狗超时时间（秒）'
    )
    
    # 创建节点
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
        }],
        output='screen',
        emulate_tty=True,
        remappings=[
            ('/cmd_vel', 'cmd_vel'),
            ('/ackermann_cmd', 'ackermann_cmd'),
            ('/imu/data', 'imu_filter'),
            ('/odom', '/odometry/filtered')
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
        osracer_chassis_node,
    ])
