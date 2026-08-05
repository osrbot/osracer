from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():

    image_topic_arg = LaunchConfiguration('image_topic')
    image_transport_arg = LaunchConfiguration('image_transport', default='compressed')

    declare_use_sim_time_argument = DeclareLaunchArgument(
        'image_topic',
        default_value='/rgb/image_raw/compressed',
        description='Topic of USB Camera image')
    
    return LaunchDescription([
        declare_use_sim_time_argument,

        Node(
            package="image_transport",
            executable="republish",
            name="republish",
            arguments=[ # Array of strings/parametric arguments that will end up in process's argv
                'raw',
                'compressed',
            ],
            remappings=[
                ("in/raw", "/rgb/image_raw"), 
                ("out/compressed", "/rgb/image_raw/compressed")
            ],
            output="screen",
        ),
        Node(
            package='rqt_image_view',
            executable='rqt_image_view',
            name='image_view',
            parameters=[
                {'autosize': "True"},
                {'image_transport': image_transport_arg},
            ],
            remappings=[('image', image_topic_arg)],
            output='screen',
        )
    ])