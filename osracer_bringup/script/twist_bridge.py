#!/usr/bin/env python3

# Author: christoph.roesmann@tu-dortmund.de

import math
import sys

import rclpy
from ackermann_msgs.msg import AckermannDrive
from geometry_msgs.msg import Twist
from rclpy.executors import ExternalShutdownException


def convert_trans_rot_vel_to_steering_angle(v, omega, wheelbase):
    if omega == 0 or v == 0:
        return 0
    radius = v / omega
    return math.atan(wheelbase / radius)


def cmd_callback(data):
    global wheelbase, pub

    v = data.linear.x
    steering = float(convert_trans_rot_vel_to_steering_angle(v, data.angular.z, wheelbase))

    msg = AckermannDrive()
    msg.steering_angle = steering
    msg.speed = v

    pub.publish(msg)


if __name__ == '__main__':
    try:
        rclpy.init(args=sys.argv)
        node = rclpy.create_node('ackermann_msg_bridge')

        twist_cmd_topic = node.declare_parameter('twist_cmd_topic', '/cmd_vel').value
        ackermann_cmd_topic = node.declare_parameter('ackermann_cmd_topic', '/ackermann_cmd').value
        wheelbase = node.declare_parameter('wheelbase', 0.285).value

        sub = node.create_subscription(Twist, twist_cmd_topic, cmd_callback, 1)
        pub = node.create_publisher(AckermannDrive, ackermann_cmd_topic, 1)

        node.get_logger().info("Node 'ackermann_msg_bridge' started.")
        node.get_logger().info(f"Listening to {twist_cmd_topic}")
        node.get_logger().info(f"Publishing to {ackermann_cmd_topic}")
        node.get_logger().info(f"Wheelbase: {wheelbase}")

        rclpy.spin(node)

    except (KeyboardInterrupt, ExternalShutdownException):
        pass
