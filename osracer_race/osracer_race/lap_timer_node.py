#!/usr/bin/env python3

import math

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node


class LapTimerNode(Node):
    def __init__(self):
        super().__init__('lap_timer_node')
        self.declare_parameter('odom_topic', '/odometry/filtered')
        self.declare_parameter('lap_start_x', 0.0)
        self.declare_parameter('lap_start_y', 0.0)
        self.declare_parameter('lap_trigger_radius_m', 0.55)
        self.declare_parameter('lap_rearm_radius_m', 1.2)
        self.declare_parameter('min_lap_time_s', 3.0)

        self.lap_count = 0
        self.last_cross_time = None
        self.armed = True
        self.best_lap = None
        self.create_subscription(
            Odometry, self.get_parameter('odom_topic').value, self.odom_callback, 10)
        self.get_logger().info('Lap timer node ready')

    def odom_callback(self, msg):
        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        start_x = float(self.get_parameter('lap_start_x').value)
        start_y = float(self.get_parameter('lap_start_y').value)
        distance = math.hypot(x - start_x, y - start_y)
        trigger = float(self.get_parameter('lap_trigger_radius_m').value)
        rearm = float(self.get_parameter('lap_rearm_radius_m').value)
        now = self.get_clock().now()

        if distance > rearm:
            self.armed = True

        if distance <= trigger and self.armed:
            self.armed = False
            if self.last_cross_time is None:
                self.last_cross_time = now
                self.get_logger().info('Lap timer armed at start line')
                return

            lap_time = (now - self.last_cross_time).nanoseconds * 1e-9
            min_lap_time = float(self.get_parameter('min_lap_time_s').value)
            if lap_time < min_lap_time:
                return
            self.last_cross_time = now
            self.lap_count += 1
            self.best_lap = lap_time if self.best_lap is None else min(self.best_lap, lap_time)
            self.get_logger().info(
                f'Lap {self.lap_count}: {lap_time:.2f}s best={self.best_lap:.2f}s')


def main(args=None):
    rclpy.init(args=args)
    node = LapTimerNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
