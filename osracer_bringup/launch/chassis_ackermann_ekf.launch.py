#!/usr/bin/env python3

from launch import LaunchDescription
from launch_ros.actions import Node
from launch.substitutions import PathJoinSubstitution, LaunchConfiguration
from launch.actions import DeclareLaunchArgument
from launch_ros.substitutions import FindPackageShare

def generate_launch_description():
    # 原有参数声明
    port_name_arg = DeclareLaunchArgument(
        'port_name',
        default_value='/dev/osrbot_base',
        description='串口设备名称，如 /dev/ttyACM0 或 /dev/ttyUSB0'
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

    publish_tf_arg = DeclareLaunchArgument(
        'publish_tf',
        default_value='False',
        description='发布TF变换'
    )    
    
    # ========== 新增参数声明 ==========
    use_respawn_arg = DeclareLaunchArgument(
        'use_respawn',
        default_value='false',
        description='是否启用节点自动重启'
    )
    
    log_level_arg = DeclareLaunchArgument(
        'log_level',
        default_value='info',
        description='日志级别 (debug/info/warn/error)'
    )
    
    ekf_params_file_arg = DeclareLaunchArgument(
        'ekf_params_file',
        default_value=PathJoinSubstitution([
            FindPackageShare('osracer_bringup'),
            'param', 'chassis_ekf_params.yaml'
        ]),
        description='robot_localization EKF 参数文件路径'
    )
    
    autostart_arg = DeclareLaunchArgument(
        'autostart',
        default_value='true',
        description='是否自动启动滤波器'
    )
    
    map_frame_arg = DeclareLaunchArgument(
        'map_frame',
        default_value='map',
        description='地图坐标系名称'
    )
    # ==================================
    
    # 底盘节点
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
            ('/imu/data', 'imu'),
            ('/odom', 'odom')
        ]
    )
    
    # 新增：互补滤波器节点（IMU滤波）
    complementary_filter_node = Node(
        package='imu_complementary_filter',
        executable='complementary_filter_node',
        name='complementary_filter_gain_node',
        respawn=LaunchConfiguration('use_respawn'),
        respawn_delay=2.0,
        output='screen',
        remappings=[
            ("imu/data", "imu_filter"),          # 滤波后的IMU数据输出到 /imu_filter
            ("imu/data_raw", "imu")               # 订阅底盘的原始IMU数据 /imu
        ],
        parameters=[{
            'do_bias_estimation': True,
            'do_adaptive_gain': True,
            'use_mag': False,
            'gain_acc': 0.01,
            'gain_mag': 0.01
        }],
        arguments=["--ros-args", "--log-level", LaunchConfiguration('log_level')],
    )
    
    # 新增：robot_localization EKF节点
    ekf_node = Node(
        package='robot_localization',
        executable='ekf_node',
        name='ekf_filter_node',
        respawn=LaunchConfiguration('use_respawn'),
        respawn_delay=2.0,
        output='screen',
        parameters=[
            LaunchConfiguration('ekf_params_file'),
            {
                'autostart': LaunchConfiguration('autostart'),
                'map_frame': LaunchConfiguration('map_frame'),
                'odom_frame': LaunchConfiguration('odom_frame'),
                'base_link_frame': LaunchConfiguration('base_frame'),   # 复用底盘的 base_frame
                'world_frame': LaunchConfiguration('odom_frame')        # world_frame 设为 odom
            }
        ],
        arguments=["--ros-args", "--log-level", LaunchConfiguration('log_level')],
        # 可选：如果参数文件中未指定 imu0 话题，可通过 remapping 将默认输入映射到滤波后的话题
        # remappings=[
        #     ('imu/data', 'imu_filter')   # 使 EKF 订阅滤波后的 IMU 数据
        # ]
    )
    
    return LaunchDescription([
        # 原有参数
        port_name_arg,
        baud_rate_arg,
        odom_frame_arg,
        base_frame_arg,
        imu_frame_arg,
        wheelbase_arg,
        max_steering_angle_arg,
        cmd_timeout_arg,
        publish_tf_arg,
        # 新增参数
        use_respawn_arg,
        log_level_arg,
        ekf_params_file_arg,
        autostart_arg,
        map_frame_arg,
        # 节点
        osracer_chassis_node,
        complementary_filter_node,
        ekf_node,
    ])
