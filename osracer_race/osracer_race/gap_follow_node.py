#!/usr/bin/env python3

import rclpy
from ackermann_msgs.msg import AckermannDrive
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Bool

from osracer_race.gap_follow_tools import gap_follow_command


class GapFollowNode(Node):
    def __init__(self):
        super().__init__('gap_follow_node')
        self.declare_parameter('scan_topic', '/scan')
        self.declare_parameter('ackermann_topic', '/ackermann_cmd')
        self.declare_parameter('safety_stop_topic', '/race/safety_stop')
        self.declare_parameter('gap_fov_deg', 140.0)
        self.declare_parameter('obstacle_bubble_radius_m', 0.28)
        self.declare_parameter('gap_min_range_m', 0.65)
        self.declare_parameter('max_straight_speed_mps', 3.0)
        self.declare_parameter('min_speed_mps', 0.8)
        self.declare_parameter('max_steering_angle')
        self.declare_parameter('follow_gain', 0.75)
        self.declare_parameter('speed_steering_gain', 1.4)

        self.safety_stop = False
        self.cmd_pub = self.create_publisher(
            AckermannDrive, self.get_parameter('ackermann_topic').value, 10)
        self.create_subscription(
            Bool, self.get_parameter('safety_stop_topic').value, self.stop_callback, 10)
        self.create_subscription(
            LaserScan, self.get_parameter('scan_topic').value, self.scan_callback, 10)

        self.get_logger().info('Gap follow node ready')

    def stop_callback(self, msg):
        self.safety_stop = msg.data

    def scan_callback(self, scan):
        if self.safety_stop:
            self.publish_command(0.0, 0.0)
            return

        command = gap_follow_command(scan, self.gap_follow_params())
        if command is None:
            self.publish_command(0.0, 0.0)
            return
        speed, steering = command
        self.publish_command(speed, steering)

    def gap_follow_params(self):
        return {
            'gap_fov_deg': float(self.get_parameter('gap_fov_deg').value),
            'obstacle_bubble_radius_m': float(self.get_parameter('obstacle_bubble_radius_m').value),
            'gap_min_range_m': float(self.get_parameter('gap_min_range_m').value),
            'max_straight_speed_mps': float(self.get_parameter('max_straight_speed_mps').value),
            'min_speed_mps': float(self.get_parameter('min_speed_mps').value),
            'max_steering_angle': float(self.get_parameter('max_steering_angle').value),
            'follow_gain': float(self.get_parameter('follow_gain').value),
            'speed_steering_gain': float(self.get_parameter('speed_steering_gain').value),
        }

    def publish_command(self, speed, steering):
        msg = AckermannDrive()
        msg.speed = float(speed)
        msg.steering_angle = float(steering)
        self.cmd_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = GapFollowNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
