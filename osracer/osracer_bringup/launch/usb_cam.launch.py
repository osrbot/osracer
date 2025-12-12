

import os

from ament_index_python.packages import get_package_share_directory
from launch.substitutions import LaunchConfiguration
from launch.actions import DeclareLaunchArgument
from launch import LaunchDescription  # noqa: E402
from launch.actions import GroupAction  # noqa: E402
from launch_ros.actions import Node  # noqa: E402

camera_name= "usb_cam"
remappings = [
                ('image_raw', f'{camera_name}/image_raw'),
                ('image_raw/compressed', f'{camera_name}/image_compressed'),
                ('image_raw/compressedDepth', f'{camera_name}/compressedDepth'),
                ('image_raw/theora', f'{camera_name}/image_raw/theora'),
                ('camera_info', f'{camera_name}/camera_info'),
            ]

def generate_launch_description():
    pkg_share = get_package_share_directory("osracer_bringup")
    param_path = os.path.join(pkg_share, 'param', 'camera_params_1.yaml')

    frame_id = LaunchConfiguration('frame_id')
    ld = LaunchDescription()

    declare_frame_id_cmd = DeclareLaunchArgument(
        'frame_id',
        default_value="camera_link",
        description='Whether to apply a namespace to the sensor topic frame_id'
    )
    
    camera_nodes = [
        Node(
            package='usb_cam', executable='usb_cam_node_exe', output='screen',
            name=camera_name,
            parameters=[
                param_path, {
                'frame_id': frame_id,
            }],
            remappings=remappings
        )
    ]

    camera_group = GroupAction(camera_nodes)
    ld.add_action(declare_frame_id_cmd)
    ld.add_action(camera_group)
    return ld