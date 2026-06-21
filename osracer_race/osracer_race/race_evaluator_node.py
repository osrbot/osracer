#!/usr/bin/env python3

import csv
from pathlib import Path

import rclpy
from ackermann_msgs.msg import AckermannDrive
from nav_msgs.msg import Odometry
from rclpy.node import Node

from osracer_race.common import load_raceline, yaw_from_quaternion
from osracer_race.eval_tools import EVAL_HEADER, format_eval_row, track_errors


class RaceEvaluatorNode(Node):
    def __init__(self):
        super().__init__('race_evaluator_node')
        self.declare_parameter('odom_topic', '/odometry/filtered')
        self.declare_parameter('ackermann_topic', '/ackermann_cmd')
        self.declare_parameter('raceline_file', '')
        self.declare_parameter('output_csv', '')
        self.declare_parameter('eval_output_csv', '/tmp/osracer_race_eval.csv')
        self.declare_parameter('log_period_s', 0.1)

        path = self.get_parameter('raceline_file').value
        self.raceline = load_raceline(path) if path else []
        self.latest_odom = None
        self.latest_cmd = None
        self.sample_count = 0
        self.track_error_count = 0
        self.max_speed = 0.0
        self.max_track_error = 0.0
        self.error_sum = 0.0

        output_param = self.get_parameter('output_csv').value
        output = Path(output_param or self.get_parameter('eval_output_csv').value)
        output.parent.mkdir(parents=True, exist_ok=True)
        self.output = output.open('w', newline='', encoding='utf-8')
        self.writer = csv.writer(self.output)
        self.writer.writerow(EVAL_HEADER)

        self.create_subscription(
            Odometry, self.get_parameter('odom_topic').value, self.odom_callback, 10)
        self.create_subscription(
            AckermannDrive, self.get_parameter('ackermann_topic').value, self.cmd_callback, 10)
        self.create_timer(float(self.get_parameter('log_period_s').value), self.log_sample)
        self.get_logger().info(f'Race evaluator logging to {output}')

    def odom_callback(self, msg):
        self.latest_odom = msg

    def cmd_callback(self, msg):
        self.latest_cmd = msg

    def log_sample(self):
        if self.latest_odom is None:
            return
        odom = self.latest_odom
        cmd = self.latest_cmd
        x = odom.pose.pose.position.x
        y = odom.pose.pose.position.y
        yaw = yaw_from_quaternion(odom.pose.pose.orientation)
        speed = odom.twist.twist.linear.x
        track_error, heading_error = track_errors(self.raceline, x, y, yaw)
        command_speed = cmd.speed if cmd else 0.0
        command_steering = cmd.steering_angle if cmd else 0.0
        now = self.get_clock().now().nanoseconds * 1e-9

        self.writer.writerow(format_eval_row(
            now, x, y, yaw, speed, command_speed, command_steering, track_error, heading_error))
        self.output.flush()
        self.sample_count += 1
        self.max_speed = max(self.max_speed, abs(speed))
        if track_error is not None:
            self.track_error_count += 1
            self.max_track_error = max(self.max_track_error, abs(track_error))
            self.error_sum += abs(track_error)

    def destroy_node(self):
        if self.sample_count:
            if self.track_error_count:
                mean_error = self.error_sum / self.track_error_count
                self.get_logger().info(
                    f'eval summary samples={self.sample_count} max_speed={self.max_speed:.2f}m/s '
                    f'mean_abs_error={mean_error:.3f}m max_abs_error={self.max_track_error:.3f}m')
            else:
                self.get_logger().info(
                    f'eval summary samples={self.sample_count} max_speed={self.max_speed:.2f}m/s '
                    'track_error=unavailable')
        self.output.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = RaceEvaluatorNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
