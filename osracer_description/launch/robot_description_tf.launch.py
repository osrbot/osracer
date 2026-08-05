from launch_ros.actions import Node
from launch import LaunchDescription
from launch.actions import OpaqueFunction

def launch_setup(context, *args, **kwargs):
    return_node = []

    return_node.append(
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="base_footprint2base_link",
            output="screen",
            arguments=[
                "--x", "0.0",
                "--y", "0.0",
                "--z", "0.055",
                "--roll", "0.0",
                "--pitch", "0.0",
                "--yaw", "0.0",
                "--frame-id", "base_footprint",
                "--child-frame-id", "base_link",
            ],
        )
    )

    return_node.append(
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="base_link2laser",
            output="screen",
            arguments=[
                "--x", "0.10",
                "--y", "0.0",
                "--z", "0.13",
                "--roll", "0.0",
                "--pitch", "0.0",
                "--yaw", "0.0",
                "--frame-id", "base_link",
                "--child-frame-id", "laser",
            ],
        )
    )

    return_node.append(
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="base_link2imu",
            output="screen",
            arguments=[
                "--x", "0.22",
                "--y", "0.0",
                "--z", "0.03",
                "--roll", "0.0",
                "--pitch", "0.0",
                "--yaw", "0.0",
                "--frame-id", "base_link",
                "--child-frame-id", "imu_link",
            ],
        )
    )

    return_node.append(
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="base_link2camera",
            output="screen",
            arguments=[
                "--x", "0.30",
                "--y", "0.0",
                "--z", "0.075",
                "--roll", "0.0",
                "--pitch", "0.0",
                "--yaw", "0.0",
                "--frame-id", "base_link",
                "--child-frame-id", "camera_link",
            ],
        )
    )

    return return_node

def generate_launch_description():

    declared_args = []

    return LaunchDescription(
        declared_args + [OpaqueFunction(function=launch_setup)],
    )
