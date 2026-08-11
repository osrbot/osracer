#!/usr/bin/env python3

import rclpy
from ackermann_msgs.msg import AckermannDrive
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Bool

from osracer_race.overtake_tools import overtake_command, scan_summary


class ObstacleOvertakeNode(Node):
    def __init__(self):
        super().__init__('obstacle_overtake_node')
        self.declare_parameter('scan_topic', '/scan')
        self.declare_parameter('input_ackermann_topic', '/race/tracking_ackermann_cmd')
        self.declare_parameter('output_ackermann_topic', '/race/raw_ackermann_cmd')
        self.declare_parameter('safety_stop_topic', '/race/safety_stop')
        self.declare_parameter('front_fov_deg', 55.0)
        self.declare_parameter('overtake_fov_deg', 130.0)
        self.declare_parameter('overtake_trigger_distance_m', 1.2)
        self.declare_parameter('overtake_clear_distance_m', 1.8)
        self.declare_parameter('overtake_speed_mps', 1.0)
        self.declare_parameter('overtake_steering_deg', 18.0)
        self.declare_parameter('max_steering_angle')

        self.latest_scan = None
        self.overtake_active = False
        self.safety_stop = False

        self.pub = self.create_publisher(
            AckermannDrive, self.get_parameter('output_ackermann_topic').value, 10)
        self.create_subscription(
            LaserScan, self.get_parameter('scan_topic').value, self.scan_callback, 10)
        self.create_subscription(
            AckermannDrive,
            self.get_parameter('input_ackermann_topic').value,
            self.command_callback,
            10)
        self.create_subscription(
            Bool, self.get_parameter('safety_stop_topic').value, self.safety_callback, 10)
        self.get_logger().info('Obstacle overtake node ready')

    def safety_callback(self, msg):
        self.safety_stop = msg.data
        if self.safety_stop:
            self.publish_command(0.0, 0.0)

    def scan_callback(self, msg):
        self.latest_scan = msg

    def command_callback(self, msg):
        if self.safety_stop:
            self.publish_command(0.0, 0.0)
            return
        if msg.speed <= 0.0:
            self.overtake_active = False
            self.pub.publish(msg)
            return
        if self.latest_scan is None:
            self.pub.publish(msg)
            return

        front_distance, left_ranges, right_ranges = scan_summary(
            self.latest_scan,
            float(self.get_parameter('front_fov_deg').value),
            float(self.get_parameter('overtake_fov_deg').value),
        )
        self.overtake_active, command = overtake_command(
            msg.speed,
            front_distance,
            left_ranges,
            right_ranges,
            self.overtake_active,
            self.overtake_params(),
        )
        if command is None:
            self.pub.publish(msg)
            return

        speed, steering = command
        self.publish_command(speed, steering)

    def overtake_params(self):
        return {
            'overtake_trigger_distance_m': float(self.get_parameter('overtake_trigger_distance_m').value),
            'overtake_clear_distance_m': float(self.get_parameter('overtake_clear_distance_m').value),
            'overtake_speed_mps': float(self.get_parameter('overtake_speed_mps').value),
            'overtake_steering_deg': float(self.get_parameter('overtake_steering_deg').value),
            'max_steering_angle': float(self.get_parameter('max_steering_angle').value),
        }

    def publish_command(self, speed, steering):
        msg = AckermannDrive()
        msg.speed = float(speed)
        msg.steering_angle = float(steering)
        self.pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = ObstacleOvertakeNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
