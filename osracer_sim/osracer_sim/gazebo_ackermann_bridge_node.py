#!/usr/bin/env python3
"""Bridge OSRacer Ackermann commands to Gazebo joint controller topics."""

from __future__ import annotations

from typing import Iterable, Optional

import rclpy
from ackermann_msgs.msg import AckermannDrive
from rclpy.node import Node
from std_msgs.msg import Float64

from osracer_sim.kinematics import ackermann_gazebo_commands, clamp


class GazeboAckermannBridge(Node):
    def __init__(self) -> None:
        super().__init__('osracer_gazebo_ackermann_bridge')

        self.declare_parameter('ackermann_topic', '/ackermann_cmd')
        self.declare_parameter('wheelbase')
        self.declare_parameter('track_width', 0.215)
        self.declare_parameter('wheel_radius', 0.0425)
        self.declare_parameter('max_speed')
        self.declare_parameter('max_steering_angle')
        self.declare_parameter('left_steering_topic', '/gazebo/left_steering_position')
        self.declare_parameter('right_steering_topic', '/gazebo/right_steering_position')
        self.declare_parameter('left_front_wheel_topic', '/model/osracer_simple/joint/Left_front_wheel_joint/cmd_vel')
        self.declare_parameter('right_front_wheel_topic', '/model/osracer_simple/joint/right_front_wheel_joint/cmd_vel')
        self.declare_parameter('left_rear_wheel_topic', '/model/osracer_simple/joint/left_rear_wheel_joint/cmd_vel')
        self.declare_parameter('right_rear_wheel_topic', '/model/osracer_simple/joint/right_rear_wheel_joint/cmd_vel')

        self.wheelbase = float(self.get_parameter('wheelbase').value)
        self.track_width = float(self.get_parameter('track_width').value)
        self.wheel_radius = float(self.get_parameter('wheel_radius').value)
        self.max_speed = float(self.get_parameter('max_speed').value)
        self.max_steering = float(self.get_parameter('max_steering_angle').value)

        self.left_steering_pub = self.create_publisher(
            Float64, self.get_parameter('left_steering_topic').value, 10)
        self.right_steering_pub = self.create_publisher(
            Float64, self.get_parameter('right_steering_topic').value, 10)
        self.wheel_pubs = [
            self.create_publisher(Float64, self.get_parameter('left_front_wheel_topic').value, 10),
            self.create_publisher(Float64, self.get_parameter('right_front_wheel_topic').value, 10),
            self.create_publisher(Float64, self.get_parameter('left_rear_wheel_topic').value, 10),
            self.create_publisher(Float64, self.get_parameter('right_rear_wheel_topic').value, 10),
        ]
        self.create_subscription(
            AckermannDrive,
            self.get_parameter('ackermann_topic').value,
            self.command_callback,
            10,
        )
        self.get_logger().info('Gazebo Ackermann bridge ready')

    def command_callback(self, msg: AckermannDrive) -> None:
        speed = clamp(float(msg.speed), -self.max_speed, self.max_speed)
        steering = clamp(float(msg.steering_angle), -self.max_steering, self.max_steering)
        left_steering, right_steering, wheel_velocities = ackermann_gazebo_commands(
            speed,
            steering,
            self.wheelbase,
            self.track_width,
            self.wheel_radius,
        )
        self.left_steering_pub.publish(float_msg(left_steering))
        self.right_steering_pub.publish(float_msg(right_steering))
        for publisher, wheel_velocity in zip(self.wheel_pubs, wheel_velocities):
            publisher.publish(float_msg(wheel_velocity))


def float_msg(value: float) -> Float64:
    msg = Float64()
    msg.data = float(value)
    return msg


def main(args: Optional[Iterable[str]] = None) -> int:
    rclpy.init(args=args)
    node = GazeboAckermannBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
