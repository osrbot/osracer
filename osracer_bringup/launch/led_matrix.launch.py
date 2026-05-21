import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, LogInfo
from launch.substitutions import LaunchConfiguration,TextSubstitution
from launch_ros.actions import Node

def generate_launch_description():
    serial_port = "/dev/osrbot_led_matrix"
    serial_baudrate = "115200"
    
    # Serial port and baud rate for LED driver
    serial_port_arg = DeclareLaunchArgument(
        'serial_port',
        default_value=TextSubstitution(text=serial_port)
    )
    serial_baudrate_arg = DeclareLaunchArgument(
        'serial_baudrate',
        default_value=TextSubstitution(text=serial_baudrate)
    )

    led_driver_node = Node(
        package='osracer_bringup',
        executable='led_matrix.py',
        name='osrbot_led_matrix',
        output='screen',
        parameters=[{'serial_port': LaunchConfiguration('serial_port'),
                    'serial_baudrate': LaunchConfiguration('serial_baudrate'),
                    }],
        emulate_tty=True,
    )


    return LaunchDescription([
        serial_port_arg,
        serial_baudrate_arg,
        led_driver_node
    ])
