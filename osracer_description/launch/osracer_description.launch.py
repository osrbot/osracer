from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare


def launch_setup(context, *args, **kwargs):
    urdf_model = LaunchConfiguration("urdf_model").perform(context)
    rviz_config_file = LaunchConfiguration("rviz_config_file").perform(context)
    use_sim_time = LaunchConfiguration("use_sim_time")
    publish_frequency = LaunchConfiguration("publish_frequency")
    wheel_radius = LaunchConfiguration("wheel_radius")
    wheelbase = LaunchConfiguration("wheelbase")
    track_width = LaunchConfiguration("track_width")
    max_steering_angle_deg = LaunchConfiguration("max_steering_angle_deg")
    steering_joint_sign = LaunchConfiguration("steering_joint_sign")
    odom_topic = LaunchConfiguration("odom_topic")
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
                    "publish_frequency": ParameterValue(publish_frequency, value_type=float),
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

    if not start_jsp:
        nodes.insert(
            1,
            Node(
                package="osracer_description",
                executable="osracer_joint_state_publisher.py",
                name="osracer_joint_state_publisher",
                output="screen",
                parameters=[
                    {
                        "urdf_model": urdf_model,
                        "use_sim_time": use_sim_time,
                        "wheel_radius": ParameterValue(wheel_radius, value_type=float),
                        "wheelbase": ParameterValue(wheelbase, value_type=float),
                        "track_width": ParameterValue(track_width, value_type=float),
                        "max_steering_angle_deg": ParameterValue(max_steering_angle_deg, value_type=float),
                        "steering_joint_sign": ParameterValue(steering_joint_sign, value_type=float),
                        "odom_topic": odom_topic,
                    }
                ],
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
            DeclareLaunchArgument(
                "publish_frequency",
                default_value="100.0",
                description="Dynamic TF publish frequency for robot_state_publisher",
            ),
            DeclareLaunchArgument(
                "wheel_radius",
                default_value="0.0425",
                description="Wheel radius for model joint animation (meters)",
            ),
            DeclareLaunchArgument(
                "wheelbase",
                default_value="0.285",
                description="Vehicle wheelbase for Ackermann model joint animation (meters)",
            ),
            DeclareLaunchArgument(
                "track_width",
                default_value="0.215",
                description="Vehicle track width for Ackermann model joint animation (meters)",
            ),
            DeclareLaunchArgument(
                "max_steering_angle_deg",
                default_value="30.0",
                description="Maximum steering angle for model joint animation (degrees)",
            ),
            DeclareLaunchArgument(
                "steering_joint_sign",
                default_value="-1.0",
                description="Sign applied to steering angles for URDF steering joints",
            ),
            DeclareLaunchArgument(
                "odom_topic",
                default_value="odom",
                description="Odometry topic used by model joint animation",
            ),
            OpaqueFunction(function=launch_setup),
        ]
    )
