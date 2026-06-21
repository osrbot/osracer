#!/usr/bin/env python3

from pathlib import Path

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node

from osracer_race.vehicle_id_tools import VehicleObservation


class VehicleIdNode(Node):
    def __init__(self):
        super().__init__('vehicle_id_node')
        self.declare_parameter('odom_topic', '/odometry/filtered')
        self.declare_parameter('log_period_s', 1.0)
        self.declare_parameter('output_file', '/tmp/osracer_vehicle_identified.yaml')
        self.declare_parameter('wheel_radius', 0.0425)
        self.declare_parameter('wheelbase', 0.285)
        self.declare_parameter('track_width', 0.215)
        self.declare_parameter('gear_ratio', 10.55)
        self.declare_parameter('mass_kg', 3.2)
        self.declare_parameter('max_steering_angle_deg', 30.0)
        self.observation = VehicleObservation()
        self.create_subscription(
            Odometry, self.get_parameter('odom_topic').value, self.odom_callback, 10)
        self.create_timer(float(self.get_parameter('log_period_s').value), self.report)

    def odom_callback(self, msg):
        speed = msg.twist.twist.linear.x
        yaw_rate = msg.twist.twist.angular.z
        now = self.get_clock().now().nanoseconds * 1e-9
        self.observation.update(speed, yaw_rate, now)

    def report(self):
        self.get_logger().info(
            f'observed max_speed={self.observation.max_speed:.2f}m/s '
            f'max_accel={self.observation.max_accel:.2f}m/s^2 '
            f'max_brake={self.observation.max_brake:.2f}m/s^2 '
            f'max_yaw_rate={self.observation.max_yaw_rate:.2f}rad/s')
        self.write_result()

    def write_result(self):
        output = Path(self.get_parameter('output_file').value)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.observation.to_ros_parameters_yaml(self.vehicle_params()), encoding='utf-8')

    def vehicle_params(self):
        return {
            'wheel_radius': self.get_parameter('wheel_radius').value,
            'wheelbase': self.get_parameter('wheelbase').value,
            'track_width': self.get_parameter('track_width').value,
            'gear_ratio': self.get_parameter('gear_ratio').value,
            'mass_kg': self.get_parameter('mass_kg').value,
            'max_steering_angle_deg': self.get_parameter('max_steering_angle_deg').value,
        }


def main(args=None):
    rclpy.init(args=args)
    node = VehicleIdNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
