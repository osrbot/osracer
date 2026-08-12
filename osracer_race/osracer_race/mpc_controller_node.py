#!/usr/bin/env python3

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node

from osracer_race.common import load_raceline, yaw_from_quaternion
from osracer_race.controller_base import RaceControllerMixin
from osracer_race.mpc_tools import mpc_command


class MpcControllerNode(RaceControllerMixin, Node):
    def __init__(self):
        super().__init__('mpc_controller_node')
        self.declare_parameter('odom_topic', '/odometry/filtered')
        self.declare_parameter('raceline_file', '')
        self.declare_parameter('wheelbase')
        self.declare_parameter('max_steering_angle')
        self.declare_parameter('max_straight_speed_mps', 3.0)
        self.declare_parameter('min_speed_mps', 0.8)
        self.declare_parameter('default_speed_mps', 1.2)
        self.declare_parameter('max_accel_mps2', 2.5)
        self.declare_parameter('max_brake_mps2', 3.5)
        self.declare_parameter('speed_response_time_s', 0.30)
        self.declare_parameter('max_lateral_accel_mps2', 4.5)
        self.declare_parameter('horizon_steps', 8)
        self.declare_parameter('dt_s', 0.08)
        self.declare_parameter('path_weight', 4.0)
        self.declare_parameter('heading_weight', 1.5)
        self.declare_parameter('steering_weight', 0.2)
        self.declare_parameter('target_speed_weight', 0.35)
        self.declare_parameter('progress_weight', 0.15)

        path = self.get_parameter('raceline_file').value
        self.raceline = load_raceline(path) if path else []
        if not self.raceline:
            self.get_logger().warn('No raceline loaded; MPC controller will publish stop')

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
        speed_now = msg.twist.twist.linear.x
        best = mpc_command(self.raceline, x, y, yaw, speed_now, self.controller_params())
        self.publish_command(best[0], best[1])

    def controller_params(self):
        return {
            'wheelbase': float(self.get_parameter('wheelbase').value),
            'max_steering_angle': float(self.get_parameter('max_steering_angle').value),
            'max_straight_speed_mps': float(self.get_parameter('max_straight_speed_mps').value),
            'min_speed_mps': float(self.get_parameter('min_speed_mps').value),
            'default_speed_mps': float(self.get_parameter('default_speed_mps').value),
            'max_accel_mps2': float(self.get_parameter('max_accel_mps2').value),
            'max_brake_mps2': float(self.get_parameter('max_brake_mps2').value),
            'speed_response_time_s': float(self.get_parameter('speed_response_time_s').value),
            'max_lateral_accel_mps2': float(self.get_parameter('max_lateral_accel_mps2').value),
            'horizon_steps': int(self.get_parameter('horizon_steps').value),
            'dt_s': float(self.get_parameter('dt_s').value),
            'path_weight': float(self.get_parameter('path_weight').value),
            'heading_weight': float(self.get_parameter('heading_weight').value),
            'steering_weight': float(self.get_parameter('steering_weight').value),
            'target_speed_weight': float(self.get_parameter('target_speed_weight').value),
            'progress_weight': float(self.get_parameter('progress_weight').value),
        }

def main(args=None):
    rclpy.init(args=args)
    node = MpcControllerNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
