#!/usr/bin/env python3

import rclpy
from ackermann_msgs.msg import AckermannDrive
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Bool

from osracer_race.safety_tools import race_safety_stop


class RaceSafetyNode(Node):
    def __init__(self):
        super().__init__('race_safety_node')
        self.declare_parameter('scan_topic', '/scan')
        self.declare_parameter('odom_topic', '/odometry/filtered')
        self.declare_parameter('ackermann_topic', '/ackermann_cmd')
        self.declare_parameter('safety_stop_topic', '/race/safety_stop')
        self.declare_parameter('ttc_threshold_s', 0.65)
        self.declare_parameter('emergency_distance_m', 0.45)
        self.declare_parameter('front_fov_deg', 70.0)
        self.declare_parameter('stop_on_no_front_scan', True)
        self.declare_parameter('scan_timeout_s', 0.50)
        self.declare_parameter('watchdog_period_s', 0.05)
        self.declare_parameter('stop_repeat', 3)

        self.speed = 0.0
        self.stop_active = False
        self.last_scan_time = None

        self.stop_pub = self.create_publisher(
            Bool, self.get_parameter('safety_stop_topic').value, 10)
        self.cmd_pub = self.create_publisher(
            AckermannDrive, self.get_parameter('ackermann_topic').value, 10)
        self.create_subscription(
            Odometry, self.get_parameter('odom_topic').value, self.odom_callback, 10)
        self.create_subscription(
            LaserScan, self.get_parameter('scan_topic').value, self.scan_callback, 10)
        self.create_timer(
            float(self.get_parameter('watchdog_period_s').value),
            self.scan_watchdog_callback)

        self.get_logger().info('Race safety node ready')

    def odom_callback(self, msg):
        self.speed = msg.twist.twist.linear.x

    def scan_callback(self, scan):
        self.last_scan_time = self.get_clock().now()
        stop, metrics = race_safety_stop(scan, self.speed, self.safety_params())
        self.stop_active = stop
        self.stop_pub.publish(Bool(data=stop))
        if stop:
            self.publish_stop()
            self.get_logger().warn(
                f'Race safety stop: valid_front={metrics["valid_front_count"]} '
                f'min_front={metrics["min_front"]:.2f}m min_ttc={metrics["min_ttc"]:.2f}s',
                throttle_duration_sec=1.0)

    def scan_watchdog_callback(self):
        timeout = float(self.get_parameter('scan_timeout_s').value)
        if timeout <= 0.0:
            return
        now = self.get_clock().now()
        elapsed = (
            (now - self.last_scan_time).nanoseconds * 1e-9
            if self.last_scan_time is not None else timeout + 1.0
        )
        if elapsed <= timeout:
            return

        self.stop_active = True
        self.stop_pub.publish(Bool(data=True))
        self.publish_stop()
        self.get_logger().warn(
            f'Race safety stop: no scan received for {elapsed:.2f}s',
            throttle_duration_sec=1.0)

    def safety_params(self):
        return {
            'ttc_threshold_s': float(self.get_parameter('ttc_threshold_s').value),
            'emergency_distance_m': float(self.get_parameter('emergency_distance_m').value),
            'front_fov_deg': float(self.get_parameter('front_fov_deg').value),
            'stop_on_no_front_scan': bool(self.get_parameter('stop_on_no_front_scan').value),
        }

    def publish_stop(self):
        msg = AckermannDrive()
        msg.speed = 0.0
        msg.steering_angle = 0.0
        for _ in range(int(self.get_parameter('stop_repeat').value)):
            self.cmd_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = RaceSafetyNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
