import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():

    rviz_config_file = os.path.join(
        get_package_share_directory('osracer_debug'), 'config', 'mapping.rviz')
    
    return LaunchDescription([
        Node(
            package='rviz2',
            executable='rviz2',
            name='rviz2',
            arguments=['-d', rviz_config_file,
                       '-f', "map"
                       ],
            output='screen',
        )
    ])
