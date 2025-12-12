import os
from ament_index_python.packages import get_package_share_directory
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():

    image_topic_arg = LaunchConfiguration('image_topic')
    image_transport_arg = LaunchConfiguration('image_transport', default='compressed')

    declare_use_sim_time_argument = DeclareLaunchArgument(
        'image_topic',
        default_value='/usb_cam/image_raw',
        description='Topic of USB Camera image')
    
    return LaunchDescription([
        declare_use_sim_time_argument,

        Node(
            package='rqt_image_view',
            executable='rqt_image_view',
            name='image_view',
            arguments=['autosize', "True",
                       'image_transport', image_transport_arg
                       ],
            remappings=[('image', image_topic_arg)],
            output='screen',
        )
    ])
