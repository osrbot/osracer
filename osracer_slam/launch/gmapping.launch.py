import os
from launch import LaunchDescription
from launch.substitutions import EnvironmentVariable
import launch.actions
import launch_ros.actions
from ament_index_python.packages import get_package_share_directory

def generate_launch_description():
    use_sim_time = launch.substitutions.LaunchConfiguration('use_sim_time', default='false')
    
    return LaunchDescription([
        launch_ros.actions.Node(
            package='slam_gmapping', 
            executable = 'slam_gmapping',
            output = 'screen', 
            parameters=[os.path.join(get_package_share_directory("osracer_slam"), "param", "slam_gmapping.yaml")]),          
    ])