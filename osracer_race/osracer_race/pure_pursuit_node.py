#!/usr/bin/env python3

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node

from osracer_race.common import load_raceline, yaw_from_quaternion
from osracer_race.controller_base import RaceControllerMixin
from osracer_race.tracking_tools import pure_pursuit_command


class PurePursuitNode(RaceControllerMixin, Node):
    def __init__(self):
        super().__init__('pure_pursuit_node')
        self.declare_parameter('odom_topic', '/odometry/filtered')
        self.declare_parameter('raceline_file', '')
        self.declare_parameter('wheelbase', 0.285)
        self.declare_parameter('lookahead_distance_m', 0.9)
        self.declare_parameter('max_steering_angle_deg', 30.0)
        self.declare_parameter('default_speed_mps', 1.2)
        self.declare_parameter('max_straight_speed_mps', 3.0)
        self.declare_parameter('max_lateral_accel_mps2', 4.5)

        path = self.get_parameter('raceline_file').value
        self.raceline = load_raceline(path) if path else []
        if not self.raceline:
            self.get_logger().warn('No raceline loaded; pure pursuit will publish stop')

        self.setup_race_controller()
        self.create_subscription(
            Odometry, self.get_parameter('odom_topic').value, self.odom_callback, 10)

    def odom_callback(self, msg):
        if self.safety_stop:
            self.publish_command(0.0, 0.0)
            return
        if len(self.raceline) < 2:
            self.publish_command(0.0, 0.0)
            return

        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        yaw = yaw_from_quaternion(msg.pose.pose.orientation)
        speed, steering = pure_pursuit_command(self.raceline, x, y, yaw, self.controller_params())
        self.publish_command(speed, steering)

    def controller_params(self):
        return {
            'wheelbase': float(self.get_parameter('wheelbase').value),
            'lookahead_distance_m': float(self.get_parameter('lookahead_distance_m').value),
            'max_steering_angle_deg': float(self.get_parameter('max_steering_angle_deg').value),
            'default_speed_mps': float(self.get_parameter('default_speed_mps').value),
            'max_straight_speed_mps': float(self.get_parameter('max_straight_speed_mps').value),
            'max_lateral_accel_mps2': float(self.get_parameter('max_lateral_accel_mps2').value),
        }

def main(args=None):
    rclpy.init(args=args)
    node = PurePursuitNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
