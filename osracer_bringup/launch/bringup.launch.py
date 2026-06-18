import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():

    return LaunchDescription([
        # chassis driver
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(
                get_package_share_directory("osracer_bringup"),
                'launch', 'chassis_ackermann.launch.py'))
        ),

        # TF boardcaster
        # IncludeLaunchDescription(
        #     PythonLaunchDescriptionSource(os.path.join(
        #         get_package_share_directory("osracer_description"),
        #         'launch', 'robot_description_tf.launch.py'))
        # ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(
                get_package_share_directory("osracer_description"),
                'launch', 'osracer_description.launch.py')),
            launch_arguments={
                'start_jsp': 'false',
            }.items()
        ),

        # lidar driver (2D/3D)
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(
                get_package_share_directory("osracer_bringup"),
                'launch', 'lidar.launch.py'))
        ),
        
        # USB Driver
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(
                get_package_share_directory("osracer_bringup"),
                'launch', 'usb_cam.launch.py'))
        ),

        # LED Matrix Driver
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(
                get_package_share_directory("osracer_bringup"),
                'launch', 'led_matrix.launch.py'))
        )
    ])
