import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch.substitutions import TextSubstitution

def generate_launch_description():
    frame_id = LaunchConfiguration('frame_id')            # <!--frame_id setting-->
    output_topic0 = LaunchConfiguration('output_topic0')  # <!--topic setting-->
    inverted = LaunchConfiguration('inverted')            # <!--inverted mount config, true if inverted-->
    hostip = LaunchConfiguration('hostip')                # <!--host listen address, 0.0.0.0 for all-->
    port0 = LaunchConfiguration('port0')                  # <!--host listen port-->
    angle_offset = LaunchConfiguration('angle_offset')    # <!--point cloud rotation angle, can be negative-->
    scanfreq = LaunchConfiguration('scanfreq')            # <!--scan frequency, range: 10, 20, 25, 30-->
    filter = LaunchConfiguration('filter')                # <!--filter options, range: 0, 1, 2, 3-->
    laser_enable = LaunchConfiguration('laser_enable')    # <!--laser scan enable, true/false-->
    scan_range_start = LaunchConfiguration('scan_range_start')  # <!--scan range start angle, range: 45~315-->
    scan_range_stop = LaunchConfiguration('scan_range_stop')    # <!--scan range stop angle, range: 45~315, must be > start-->
    sensorip = LaunchConfiguration('sensorip')

    declare_frame_id_cmd = DeclareLaunchArgument(
    'frame_id',
    default_value="laser",
    )
    declare_output_topic0_cmd = DeclareLaunchArgument(
    'output_topic0',
    default_value='scan',
    )
    declare_output_topic1_cmd = DeclareLaunchArgument(
    'output_topic1',
    default_value='scan1',
    )
    declare_inverted_cmd = DeclareLaunchArgument(
    'inverted',
    default_value='false',
    )
    declare_hostip_cmd = DeclareLaunchArgument(
    'hostip',
    default_value='0.0.0.0',
    )
    declare_port0_cmd = DeclareLaunchArgument(
    'port0',
    default_value=TextSubstitution(text='"2368"'),
    )
    declare_port1_cmd = DeclareLaunchArgument(
    'port1',
    default_value='"2369"',
    )
    declare_angle_offset_cmd = DeclareLaunchArgument(
    'angle_offset',
    default_value='0',
    )
    declare_filter_cmd = DeclareLaunchArgument(
    'filter',
    default_value='"0"',
    )
    declare_scanfreq_cmd = DeclareLaunchArgument(
    'scanfreq',
    default_value='"30"',
    )
    declare_laser_enable_cmd = DeclareLaunchArgument(
    'laser_enable',
    default_value='"true"',
    )
    declare_scan_range_start_cmd = DeclareLaunchArgument(
    'scan_range_start',
    default_value='"45"',
    )
    declare_scan_range_stop_cmd = DeclareLaunchArgument(
    'scan_range_stop',
    default_value='"315"',
    )
    declare_sensorip_cmd = DeclareLaunchArgument(
    'sensorip',
    default_value='192.168.198.2',
    )

    richbeam_lidar_node0 = Node(
        package='lakibeam1',
        name='richbeam_lidar_node0',
        executable='lakibeam1_scan_node',
        parameters=[{
            'frame_id':frame_id,
            'output_topic':output_topic0,
            'inverted':inverted,
            'hostip':hostip,
            'port':port0,
            'angle_offset':angle_offset,
            'sensorip':sensorip,
            'scanfreq':scanfreq,
            'filter':filter,
            'laser_enable':laser_enable,
            'scan_range_start':scan_range_start,
            'scan_range_stop':scan_range_stop
        }],
        # output='screen'
    )

    ld = LaunchDescription()

    ld.add_action(declare_frame_id_cmd)
    ld.add_action(declare_output_topic0_cmd)
    ld.add_action(declare_output_topic1_cmd)
    ld.add_action(declare_inverted_cmd)
    ld.add_action(declare_hostip_cmd)
    ld.add_action(declare_port0_cmd)
    ld.add_action(declare_port1_cmd)
    ld.add_action(declare_angle_offset_cmd)
    ld.add_action(declare_filter_cmd)
    ld.add_action(declare_scanfreq_cmd)
    ld.add_action(declare_laser_enable_cmd)
    ld.add_action(declare_scan_range_start_cmd)
    ld.add_action(declare_scan_range_stop_cmd)
    ld.add_action(declare_sensorip_cmd)
    ld.add_action(richbeam_lidar_node0)
    return ld
