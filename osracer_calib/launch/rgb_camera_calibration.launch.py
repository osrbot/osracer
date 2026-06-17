import os

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    calib_yaml_path = os.path.expanduser(
        '~/osracer_ws/src/osracer/osracer_bringup/param/camera_info/rgb.yaml')

    return LaunchDescription([
        Node(
            package='camera_calibration',
            executable='cameracalibrator',
            name='cameracalibrator',
            output='screen',
            emulate_tty=True,
            arguments=[
                '--size', '8x6',
                '--square', '0.03',
                '--calib_yaml_path', calib_yaml_path,
            ],
            remappings=[
                ('camera', '/rgb'),
                ('image', '/rgb/image_raw'),
            ],
        ),
    ])
