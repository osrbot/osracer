#!/usr/bin/env python3

import rclpy
from ackermann_msgs.msg import AckermannDrive
from nav_msgs.msg import Odometry
from rclpy.node import Node
from std_msgs.msg import Bool

from osracer_race.common import command_timed_out
from osracer_race.speed_profile_tools import limit_race_command


class SpeedProfileNode(Node):
    def __init__(self):
        super().__init__('speed_profile_node')
        self.declare_parameter('input_ackermann_topic', '/race/raw_ackermann_cmd')
        self.declare_parameter('output_ackermann_topic', '/ackermann_cmd')
        self.declare_parameter('odom_topic', '/odometry/filtered')
        self.declare_parameter('safety_stop_topic', '/race/safety_stop')
        self.declare_parameter('wheelbase', 0.285)
        self.declare_parameter('max_straight_speed_mps', 3.0)
        self.declare_parameter('min_speed_mps', 0.8)
        self.declare_parameter('max_accel_mps2', 2.5)
        self.declare_parameter('max_brake_mps2', 3.5)
        self.declare_parameter('max_lateral_accel_mps2', 4.5)
        self.declare_parameter('max_steering_angle_deg', 30.0)
        self.declare_parameter('command_timeout_s', 0.30)
        self.declare_parameter('watchdog_period_s', 0.05)

        self.current_speed = 0.0
        self.last_output_speed = 0.0
        self.last_command_time = None
        self.timeout_active = False
        self.safety_stop = False
        self.pub = self.create_publisher(
            AckermannDrive, self.get_parameter('output_ackermann_topic').value, 10)
        self.create_subscription(
            Odometry, self.get_parameter('odom_topic').value, self.odom_callback, 10)
        self.create_subscription(
            Bool, self.get_parameter('safety_stop_topic').value, self.safety_callback, 10)
        self.create_subscription(
            AckermannDrive,
            self.get_parameter('input_ackermann_topic').value,
            self.command_callback,
            10,
        )
        self.create_timer(
            float(self.get_parameter('watchdog_period_s').value),
            self.watchdog_callback,
        )

    def odom_callback(self, msg):
        self.current_speed = msg.twist.twist.linear.x

    def safety_callback(self, msg):
        self.safety_stop = msg.data
        if self.safety_stop:
            self.publish_stop()

    def command_callback(self, msg):
        if self.safety_stop:
            self.publish_stop()
            return
        self.timeout_active = False

        now = self.get_clock().now()
        if self.last_command_time is None:
            dt = 0.05
            base_speed = self.current_speed
        else:
            dt = max((now - self.last_command_time).nanoseconds * 1e-9, 1e-3)
            base_speed = self.last_output_speed
        output_speed, steering = limit_race_command(
            msg.speed,
            msg.steering_angle,
            base_speed,
            dt,
            self.limiter_params(),
        )
        self.last_command_time = now
        self.last_output_speed = output_speed

        out = AckermannDrive()
        out.steering_angle = steering
        out.speed = output_speed
        self.pub.publish(out)

    def limiter_params(self):
        return {
            'wheelbase': float(self.get_parameter('wheelbase').value),
            'max_straight_speed_mps': float(self.get_parameter('max_straight_speed_mps').value),
            'min_speed_mps': float(self.get_parameter('min_speed_mps').value),
            'max_accel_mps2': float(self.get_parameter('max_accel_mps2').value),
            'max_brake_mps2': float(self.get_parameter('max_brake_mps2').value),
            'max_lateral_accel_mps2': float(self.get_parameter('max_lateral_accel_mps2').value),
            'max_steering_angle_deg': float(self.get_parameter('max_steering_angle_deg').value),
        }

    def watchdog_callback(self):
        if self.safety_stop:
            self.publish_stop()
            return
        if self.last_command_time is None:
            return
        elapsed = (self.get_clock().now() - self.last_command_time).nanoseconds * 1e-9
        timeout = float(self.get_parameter('command_timeout_s').value)
        if not command_timed_out(elapsed, timeout):
            return
        if not self.timeout_active:
            self.get_logger().warn(
                f'Race command timeout after {elapsed:.2f}s; publishing stop',
                throttle_duration_sec=1.0)
        self.timeout_active = True
        self.publish_stop()

    def publish_stop(self):
        self.last_output_speed = 0.0
        out = AckermannDrive()
        out.speed = 0.0
        out.steering_angle = 0.0
        self.pub.publish(out)


def main(args=None):
    rclpy.init(args=args)
    node = SpeedProfileNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
