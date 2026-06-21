#!/usr/bin/env python3

import csv
from pathlib import Path

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node

from osracer_race.eval_tools import (
    TRACK_HEADER,
    format_track_row,
    recorded_track_speed,
    should_record_track_point,
)


class TrackRecorderNode(Node):
    def __init__(self):
        super().__init__('track_recorder_node')
        self.declare_parameter('odom_topic', '/odometry/filtered')
        self.declare_parameter('output_csv', '/tmp/osracer_recorded_track.csv')
        self.declare_parameter('min_point_spacing_m', 0.10)
        self.declare_parameter('default_speed_mps', 1.2)

        output = Path(self.get_parameter('output_csv').value)
        output.parent.mkdir(parents=True, exist_ok=True)
        self.output = output.open('w', newline='', encoding='utf-8')
        self.writer = csv.writer(self.output)
        self.writer.writerow(TRACK_HEADER)
        self.last_point = None
        self.point_count = 0

        self.create_subscription(
            Odometry, self.get_parameter('odom_topic').value, self.odom_callback, 10)
        self.get_logger().info(f'Track recorder writing to {output}')

    def odom_callback(self, msg):
        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        spacing = float(self.get_parameter('min_point_spacing_m').value)
        if not should_record_track_point(self.last_point, x, y, spacing):
            return
        speed = recorded_track_speed(
            msg.twist.twist.linear.x,
            float(self.get_parameter('default_speed_mps').value),
        )
        self.writer.writerow(format_track_row(x, y, speed))
        self.output.flush()
        self.last_point = (x, y)
        self.point_count += 1

    def destroy_node(self):
        self.get_logger().info(f'Track recorder saved {self.point_count} points')
        self.output.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = TrackRecorderNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
