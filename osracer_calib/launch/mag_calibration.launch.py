from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    params = PathJoinSubstitution(
        [FindPackageShare('osracer_calib'), 'config', 'mag_calibration.yaml']
    )

    return LaunchDescription([
        Node(
            package='osracer_calib',
            executable='mag_calibration_node',
            name='mag_calibration_node',
            output='screen',
            parameters=[params],
        ),
    ])
