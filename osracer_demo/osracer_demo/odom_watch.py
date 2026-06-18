#!/usr/bin/env python3
"""Print compact odometry updates for beginner demos."""

from __future__ import annotations

import argparse
import math
import time

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node


def yaw_from_quat(x: float, y: float, z: float, w: float) -> float:
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


class OdomWatch(Node):
    def __init__(self, topic: str, interval: float):
        super().__init__("osracer_odom_watch")
        self.interval = interval
        self.last_print = 0.0
        self.sub = self.create_subscription(Odometry, topic, self.on_odom, 10)
        self.get_logger().info(f"watching {topic}")

    def on_odom(self, msg: Odometry) -> None:
        now = time.monotonic()
        if now - self.last_print < self.interval:
            return
        self.last_print = now
        pos = msg.pose.pose.position
        q = msg.pose.pose.orientation
        yaw_deg = math.degrees(yaw_from_quat(q.x, q.y, q.z, q.w))
        lin = msg.twist.twist.linear.x
        ang = msg.twist.twist.angular.z
        dist = math.hypot(pos.x, pos.y)
        print(
            f"x={pos.x:+.3f}m y={pos.y:+.3f}m dist={dist:.3f}m "
            f"yaw={yaw_deg:+.1f}deg vx={lin:+.3f}m/s wz={ang:+.3f}rad/s",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch OSRacer odometry")
    parser.add_argument("--topic", default="/odometry/filtered")
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()

    rclpy.init()
    node = OdomWatch(args.topic, args.interval)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
