import os

from ament_index_python.packages import get_package_share_directory
from launch.substitutions import LaunchConfiguration
from launch.actions import DeclareLaunchArgument
from launch import LaunchDescription
from launch.actions import GroupAction
from launch_ros.actions import Node

camera_name = "rgb"
remappings = [
    ('image_raw', f'{camera_name}/image_raw'),
    ('image_raw/compressed', f'{camera_name}/image_raw/compressed'),
    ('image_raw/compressedDepth', f'{camera_name}/compressedDepth'),
    ('image_raw/theora', f'{camera_name}/image_raw/theora'),
    ('camera_info', f'{camera_name}/camera_info'),
    ('set_camera_info', f'{camera_name}/set_camera_info'),
]

def generate_launch_description():
    pkg_share = get_package_share_directory("osracer_bringup")

    frame_id = LaunchConfiguration('cam_frame_id')
    ld = LaunchDescription()

    declare_frame_id_cmd = DeclareLaunchArgument(
        'cam_frame_id',
        default_value="camera_link",
        description='Frame ID for the camera'
    )
    
    camera_params = []
    # Add inline parameters
    camera_params.append({
        'frame_id': frame_id,
        'video_device': '/dev/video0',
        'image_width': 640,
        'image_height': 480,
        'framerate': 120.0, 
        'pixel_format': 'mjpeg2rgb',
        'io_method': 'mmap',
        'camera_name': camera_name,
        'camera_info_url': "package://osracer_bringup/param/camera_info/rgb.yaml",
        'buffersize': 3,
        'num_image_buffers': 4,
    })
    
    camera_nodes = [
        Node(
            package='usb_cam', 
            executable='usb_cam_node_exe', 
            output='screen',
            name=camera_name,  # This sets the node name
            parameters=camera_params,
            remappings=remappings,
            arguments=['--ros-args', '--log-level', 'info']
        )
    ]

    camera_group = GroupAction(camera_nodes)
    ld.add_action(declare_frame_id_cmd)
    ld.add_action(camera_group)
    return ld
