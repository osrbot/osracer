from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def launch_setup(context, *args, **kwargs):
    urdf_model = LaunchConfiguration("urdf_model").perform(context)
    rviz_config_file = LaunchConfiguration("rviz_config_file").perform(context)
    use_sim_time = LaunchConfiguration("use_sim_time")
    jsp_gui = LaunchConfiguration("jsp_gui").perform(context).lower() == "true"
    start_jsp = LaunchConfiguration("start_jsp").perform(context).lower() == "true"
    use_rviz = LaunchConfiguration("use_rviz")

    robot_description = Path(urdf_model).read_text(encoding="utf-8")
    rviz_arguments = ["-d", rviz_config_file] if rviz_config_file else []

    nodes = [
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            name="robot_state_publisher",
            output="screen",
            parameters=[
                {
                    "robot_description": robot_description,
                    "use_sim_time": use_sim_time,
                }
            ],
        ),
        Node(
            condition=IfCondition(use_rviz),
            package="rviz2",
            executable="rviz2",
            name="rviz2",
            output="screen",
            arguments=rviz_arguments,
            parameters=[{"use_sim_time": use_sim_time}],
        ),
    ]

    if start_jsp:
        if jsp_gui:
            nodes.insert(
                1,
                Node(
                    package="joint_state_publisher_gui",
                    executable="joint_state_publisher_gui",
                    name="joint_state_publisher_gui",
                    output="screen",
                    parameters=[{"use_sim_time": use_sim_time}],
                ),
            )
        else:
            nodes.insert(
                1,
                Node(
                    package="joint_state_publisher",
                    executable="joint_state_publisher",
                    name="joint_state_publisher",
                    output="screen",
                    parameters=[{"use_sim_time": use_sim_time}],
                ),
            )

    return nodes


def generate_launch_description():
    default_urdf_model_path = PathJoinSubstitution(
        [FindPackageShare("osracer_description"), "urdf", "osracer.urdf"]
    )

    default_urdf_rviz_path = PathJoinSubstitution(
        [FindPackageShare("osracer_debug"), "config", "robot.rviz"]
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "urdf_model",
                default_value=default_urdf_model_path,
                description="Absolute path to the URDF file",
            ),
            DeclareLaunchArgument(
                "jsp_gui",
                default_value="false",
                choices=["true", "false"],
                description="Start joint_state_publisher_gui",
            ),
            DeclareLaunchArgument(
                "start_jsp",
                default_value="true",
                choices=["true", "false"],
                description="Start joint_state_publisher or joint_state_publisher_gui",
            ),
            DeclareLaunchArgument(
                "use_rviz",
                default_value="false",
                choices=["true", "false"],
                description="Start RViz2",
            ),
            DeclareLaunchArgument(
                "rviz_config_file",
                default_value=default_urdf_rviz_path,
                description="Absolute path to an RViz2 config file",
            ),
            DeclareLaunchArgument(
                "use_sim_time",
                default_value="false",
                choices=["true", "false"],
                description="Use simulation time",
            ),
            OpaqueFunction(function=launch_setup),
        ]
    )
