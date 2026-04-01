import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.substitutions import LaunchConfiguration,TextSubstitution
from launch_ros.descriptions import ParameterFile
from nav2_common.launch import RewrittenYaml, ReplaceString

from launch_ros.actions import (
    Node,
)

from launch.actions import (
    DeclareLaunchArgument,
    GroupAction,
    SetEnvironmentVariable,
)

default_map_frame_id = "map"
default_odom_frame_id = "odom"
default_base_link_frame_id = "base_footprint"
default_base_frame_id = "base_link"
default_imu_frame_id = "imu_link"

def generate_launch_description():

    serial_port = "/dev/ttyACM0"
    serial_baudrate = "115200"
    
    pkg_share = get_package_share_directory("osracer_bringup")
    
    use_sim_time = LaunchConfiguration('use_sim_time')
    params_file = LaunchConfiguration('params_file')

    autostart = LaunchConfiguration("autostart")
    params_file = LaunchConfiguration("params_file")
    use_respawn = LaunchConfiguration("use_respawn")
    log_level = LaunchConfiguration("log_level")

    map_frame_id = LaunchConfiguration('map_frame_id')
    base_link_frame_id = LaunchConfiguration('base_link_frame_id')
    base_frame_id = LaunchConfiguration('base_frame_id')
    odom_frame_id = LaunchConfiguration('odom_frame_id')
    imu_frame_id = LaunchConfiguration('imu_frame_id')


    # Create our own temporary YAML files that include substitutions
    param_substitutions = {
        'use_sim_time': use_sim_time,
        'autostart': autostart,
        }

    

    stdout_linebuf_envvar = SetEnvironmentVariable(
        "RCUTILS_LOGGING_BUFFERED_STREAM", "1"
    )

    colorized_output_envvar = SetEnvironmentVariable("RCUTILS_COLORIZED_OUTPUT", "1")

    declare_use_sim_time_cmd = DeclareLaunchArgument(
        'use_sim_time',
        default_value='false',
        description='Use simulation (Gazebo) clock if true'
    )

    declare_params_file_cmd = DeclareLaunchArgument(
        'params_file',
        default_value=os.path.join(pkg_share, 'param', 'chassis_ekf_params.yaml'),
        description='Full path to the ROS2 parameters file to use for all launched nodes'
    )

    # Serial port and baud rate for LED driver
    declare_serial_port_cmd = DeclareLaunchArgument(
        'serial_port',
        default_value=TextSubstitution(text=serial_port),
        description='Serial port of the base'
    )
    declare_serial_baudrate_cmd = DeclareLaunchArgument(
        'serial_baudrate',
        default_value=TextSubstitution(text=serial_baudrate),
        description='Baudrate of the base'
    )
    
    declare_autostart_cmd = DeclareLaunchArgument(
        "autostart",
        default_value="true",
        description="Automatically startup the nav2 stack",
    )

    declare_use_respawn_cmd = DeclareLaunchArgument(
        "use_respawn",
        default_value="False",
        description="Whether to respawn if a node crashes. Applied when composition is disabled.",
    )

    declare_log_level_cmd = DeclareLaunchArgument(
        "log_level", default_value="info", description="log level"
    )

    declare_map_frame_id_cmd = DeclareLaunchArgument(
        'map_frame_id',
        default_value=default_map_frame_id,
        description='map frame_id of the base'
    )

    declare_base_link_frame_id_cmd = DeclareLaunchArgument(
        'base_link_frame_id',
        default_value=default_base_link_frame_id,
        description='base_link frame_id of the base'
    )

    declare_base_frame_id_cmd = DeclareLaunchArgument(
        'base_frame_id',
        default_value=default_base_frame_id,
        description='base frame_id of the base'
    )

    declare_odom_frame_id_cmd = DeclareLaunchArgument(
        'odom_frame_id',
        default_value=default_odom_frame_id,
        description='odom frame_id of the base'
    )

    declare_imu_frame_id_cmd = DeclareLaunchArgument(
        'imu_frame_id',
        default_value=default_imu_frame_id,
        description='imu frame_id of the base'
    )

    osracer_core_cmd_group = GroupAction(
        [
            Node(
                package='osracer_bringup',
                executable='chassis_ackermann.py',
                name='osracer_chassis',
                respawn=use_respawn,
                respawn_delay=2.0,
                output='screen',
                parameters=[{'serial_port': LaunchConfiguration('serial_port'),
                             'serial_baudrate': LaunchConfiguration('serial_baudrate'),
                             'autostart': autostart,
                             'publish_tf': False,
                             'base_frame': base_frame_id,
                             'odom_frame': odom_frame_id,
                             'imu_frame': imu_frame_id,
                }],
                arguments=["--ros-args", "--log-level", log_level],
            ),

            Node(
                package='osracer_bringup',
                executable='twist_bridge.py',
                name='twist2ackermann',
                respawn=use_respawn,
                respawn_delay=2.0,
                output='screen',
                parameters=[{'wist_cmd_topic': '/cmd_vel',
                             'ackermann_cmd_topic': '/ackermann_cmd',
                             'wheelbase': 0.21 ,
                }],
                arguments=["--ros-args", "--log-level", log_level],
            ),

            Node(
                package='imu_complementary_filter',
                executable='complementary_filter_node',
                name='complementary_filter_gain_node',
                respawn=use_respawn,
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
                arguments=["--ros-args", "--log-level", log_level],
            ),

            Node(
                package='robot_localization',
                executable='ekf_node',
                name='ekf_filter_node',
                respawn=use_respawn,
                respawn_delay=2.0,
                output='screen',
                parameters=[params_file, {
                            'autostart': autostart,
                            'map_frame': map_frame_id,
                            'odom_frame': odom_frame_id,
                            'base_link_frame': base_link_frame_id,
                            'world_frame': odom_frame_id
                }],
                arguments=["--ros-args", "--log-level", log_level],
            )
        ]
    )

    return LaunchDescription([
        # Set environment variables
        stdout_linebuf_envvar,
        colorized_output_envvar,

        # Declare the launch options
        declare_use_sim_time_cmd,
        declare_params_file_cmd,
        declare_autostart_cmd,
        declare_use_respawn_cmd,
        declare_log_level_cmd,

        declare_serial_port_cmd,
        declare_serial_baudrate_cmd,
        declare_map_frame_id_cmd,
        declare_odom_frame_id_cmd,
        declare_base_link_frame_id_cmd,
        declare_base_frame_id_cmd,
        declare_imu_frame_id_cmd,

        # Add the actions to launch all of the navigation nodes
        osracer_core_cmd_group
    ])
