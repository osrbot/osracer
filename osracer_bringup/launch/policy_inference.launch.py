#!/usr/bin/env python3

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    policy_path_arg = DeclareLaunchArgument(
        "policy_path",
        default_value="",
        description="TorchScript policy.pt path exported from osracer_lab",
    )
    enabled_arg = DeclareLaunchArgument(
        "enabled",
        default_value="False",
        description="Set True only when ready to publish non-zero policy commands",
    )
    max_speed_arg = DeclareLaunchArgument(
        "max_speed_mps",
        default_value="0.3",
        description="Low-speed safety clamp for first real-car tests",
    )
    max_steering_arg = DeclareLaunchArgument(
        "max_steering_rad",
        default_value="0.488",
        description="Steering clamp matching the IsaacLab action envelope",
    )

    policy_node = Node(
        package="osracer_bringup",
        executable="policy_inference.py",
        name="osracer_policy_inference",
        output="screen",
        emulate_tty=True,
        parameters=[
            {
                "policy_path": LaunchConfiguration("policy_path"),
                "enabled": LaunchConfiguration("enabled"),
                "max_speed_mps": LaunchConfiguration("max_speed_mps"),
                "max_steering_rad": LaunchConfiguration("max_steering_rad"),
            }
        ],
    )

    return LaunchDescription(
        [
            policy_path_arg,
            enabled_arg,
            max_speed_arg,
            max_steering_arg,
            policy_node,
        ]
    )
