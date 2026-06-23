#!/usr/bin/env python3

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    output_path_arg = DeclareLaunchArgument(
        "output_path",
        default_value="/tmp/osracer_policy_observations.csv",
        description="CSV path for 14-value policy observations",
    )
    rate_arg = DeclareLaunchArgument(
        "rate_hz",
        default_value="10.0",
        description="Observation recording rate",
    )

    recorder_node = Node(
        package="osracer_bringup",
        executable="policy_observation_recorder.py",
        name="osracer_policy_observation_recorder",
        output="screen",
        emulate_tty=True,
        parameters=[
            {
                "output_path": LaunchConfiguration("output_path"),
                "rate_hz": LaunchConfiguration("rate_hz"),
            }
        ],
    )

    return LaunchDescription([output_path_arg, rate_arg, recorder_node])
