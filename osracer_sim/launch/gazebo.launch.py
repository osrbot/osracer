from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription, SetEnvironmentVariable
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import EnvironmentVariable, LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    world = LaunchConfiguration('world')
    world_without_model = LaunchConfiguration('world_without_model')
    models_path = PathJoinSubstitution([FindPackageShare('osracer_sim'), 'models'])
    base_profile = PathJoinSubstitution([
        FindPackageShare('osracer_base'), 'config', 'vehicles', 'red.yaml'
    ])

    base_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([
            FindPackageShare('osracer_sim'), 'launch', 'base_sim.launch.py'
        ])),
        launch_arguments={
            'use_sim_time': 'true',
            'use_rviz': LaunchConfiguration('use_rviz'),
            'publish_clock': LaunchConfiguration('publish_kinematic_clock'),
        }.items(),
    )

    gazebo = ExecuteProcess(
        cmd=['ign', 'gazebo', '-r', world],
        condition=IfCondition(LaunchConfiguration('include_model')),
        output='screen',
    )

    gazebo_without_model = ExecuteProcess(
        cmd=['ign', 'gazebo', '-r', world_without_model],
        condition=UnlessCondition(LaunchConfiguration('include_model')),
        output='screen',
    )

    gz_bridge = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        name='osracer_gz_bridge',
        output='screen',
        arguments=[
            '/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock',
            '/gazebo/scan@sensor_msgs/msg/LaserScan[gz.msgs.LaserScan',
            '/gazebo/imu@sensor_msgs/msg/Imu[gz.msgs.IMU',
            '/gazebo/left_steering_position@std_msgs/msg/Float64]gz.msgs.Double',
            '/gazebo/right_steering_position@std_msgs/msg/Float64]gz.msgs.Double',
            '/model/osracer_simple/joint/Left_front_wheel_joint/cmd_vel@std_msgs/msg/Float64]gz.msgs.Double',
            '/model/osracer_simple/joint/right_front_wheel_joint/cmd_vel@std_msgs/msg/Float64]gz.msgs.Double',
            '/model/osracer_simple/joint/left_rear_wheel_joint/cmd_vel@std_msgs/msg/Float64]gz.msgs.Double',
            '/model/osracer_simple/joint/right_rear_wheel_joint/cmd_vel@std_msgs/msg/Float64]gz.msgs.Double',
        ],
        condition=IfCondition(PythonExpression([
            "'", LaunchConfiguration('use_gz_bridge'), "' == 'true' or '",
            LaunchConfiguration('use_gz_control'), "' == 'true'",
        ])),
    )

    gazebo_ackermann_bridge = Node(
        package='osracer_sim',
        executable='gazebo_ackermann_bridge_node',
        name='osracer_gazebo_ackermann_bridge',
        output='screen',
        parameters=[base_profile, {
            'ackermann_topic': LaunchConfiguration('ackermann_topic'),
        }],
        condition=IfCondition(LaunchConfiguration('use_gz_control')),
    )

    return LaunchDescription([
        SetEnvironmentVariable(
            'IGN_GAZEBO_RESOURCE_PATH',
            [models_path, ':', EnvironmentVariable('IGN_GAZEBO_RESOURCE_PATH', default_value='')]),
        SetEnvironmentVariable(
            'GZ_SIM_RESOURCE_PATH',
            [models_path, ':', EnvironmentVariable('GZ_SIM_RESOURCE_PATH', default_value='')]),
        DeclareLaunchArgument(
            'world',
            default_value=PathJoinSubstitution([
                FindPackageShare('osracer_sim'), 'worlds', 'osracer_rect_track.sdf'
            ]),
            description='Gazebo Sim world file'),
        DeclareLaunchArgument(
            'world_without_model',
            default_value=PathJoinSubstitution([
                FindPackageShare('osracer_sim'), 'worlds', 'osracer_rect_track_nomodel.sdf'
            ]),
            description='Fallback Gazebo Sim world used when include_model is false'),
        DeclareLaunchArgument('use_rviz', default_value='false', choices=['true', 'false']),
        DeclareLaunchArgument('include_model', default_value='true', choices=['true', 'false']),
        DeclareLaunchArgument('use_gz_bridge', default_value='false', choices=['true', 'false']),
        DeclareLaunchArgument('use_gz_control', default_value='false', choices=['true', 'false']),
        DeclareLaunchArgument('ackermann_topic', default_value='/ackermann_cmd'),
        DeclareLaunchArgument('publish_kinematic_clock', default_value='true', choices=['true', 'false']),
        gazebo,
        gazebo_without_model,
        gz_bridge,
        gazebo_ackermann_bridge,
        base_sim,
    ])
