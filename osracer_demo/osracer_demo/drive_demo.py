#!/usr/bin/env python3
"""Beginner-friendly ROS 2 drive demos for OSRacer.

Default output is /cmd_vel because osracer_bringup already starts
twist_bridge.py to convert Twist commands into AckermannDrive commands. Use
--mode ackermann when only the chassis node is running and you want to publish
directly to /ackermann_cmd.
"""

from __future__ import annotations

import argparse
import math
import signal
import sys
import time
from dataclasses import dataclass

try:
    import rclpy
    from ackermann_msgs.msg import AckermannDrive
    from geometry_msgs.msg import Twist
    from rclpy.node import Node
except ModuleNotFoundError:
    rclpy = None
    AckermannDrive = None
    Twist = None
    Node = object


MAX_SPEED = 0.90
MAX_STEERING_DEG = 22.0
DEFAULT_SPEED = 0.55
DEFAULT_DURATION = 6.0
CENTER_HOLD_SEC = 1.2


@dataclass(frozen=True)
class Step:
    speed: float
    angular_z: float
    steering_deg: float
    duration: float
    label: str


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_plan(name: str, speed: float, duration: float, loops: int) -> list[Step]:
    speed = clamp(speed, -MAX_SPEED, MAX_SPEED)
    duration = max(0.2, duration)
    loops = max(1, loops)

    if name == "stop":
        return []
    if name == "straight":
        return [Step(speed, 0.0, 0.0, duration, "straight")]
    if name == "left":
        return [Step(speed, 0.70, 12.0, duration, "gentle left")]
    if name == "right":
        return [Step(speed, -0.70, -12.0, duration, "gentle right")]
    if name == "figure8":
        plan: list[Step] = []
        for i in range(loops):
            plan.append(Step(speed, 0.72, 14.0, duration, f"figure8 {i + 1} left circle"))
            plan.append(Step(speed, -0.72, -14.0, duration, f"figure8 {i + 1} right circle"))
        return plan
    if name == "circle":
        return [Step(0.45, 1.05, 20.0, 3600.0, "continuous tight circle")]
    if name == "patrol":
        plan = []
        for i in range(loops):
            plan.extend([
                Step(speed, 0.0, 0.0, duration, f"patrol {i + 1} straight out"),
                Step(speed, 0.75, 14.0, duration * 0.8, f"patrol {i + 1} turn left"),
                Step(speed, 0.0, 0.0, duration, f"patrol {i + 1} straight back"),
                Step(speed, -0.75, -14.0, duration * 0.8, f"patrol {i + 1} turn right"),
            ])
        return plan
    if name == "warmup":
        return [
            Step(0.38, 0.0, 0.0, 2.0, "warmup straight"),
            Step(0.45, 0.42, 8.0, 2.0, "warmup left"),
            Step(0.45, -0.42, -8.0, 2.0, "warmup right"),
            Step(0.38, 0.0, 0.0, 1.5, "warmup straight"),
        ]
    if name == "showcase":
        return [
            Step(0.55, 0.0, 0.0, 3.0, "straight start"),
            Step(0.60, 0.48, 9.0, 4.5, "s-curve left"),
            Step(0.60, -0.48, -9.0, 4.5, "s-curve right"),
            Step(0.58, 0.30, 6.0, 3.0, "gentle arc left"),
            Step(0.58, -0.30, -6.0, 3.0, "gentle arc right"),
            Step(0.50, 0.0, 0.0, 2.0, "finish straight"),
        ]
    raise SystemExit(f"unknown demo: {name}")


class DriveDemo(Node):
    def __init__(self, mode: str, cmd_vel_topic: str, ackermann_topic: str, rate_hz: float):
        super().__init__("osracer_drive_demo")
        self.mode = mode
        self.period = 1.0 / rate_hz
        self.stop_requested = False
        if mode == "twist":
            self.pub = self.create_publisher(Twist, cmd_vel_topic, 10)
            self.get_logger().info(f"publishing Twist to {cmd_vel_topic}")
        else:
            self.pub = self.create_publisher(AckermannDrive, ackermann_topic, 10)
            self.get_logger().info(f"publishing AckermannDrive to {ackermann_topic}")

    def publish_cmd(self, speed: float, angular_z: float, steering_deg: float) -> None:
        speed = clamp(speed, -MAX_SPEED, MAX_SPEED)
        steering_deg = clamp(steering_deg, -MAX_STEERING_DEG, MAX_STEERING_DEG)
        if self.mode == "twist":
            msg = Twist()
            msg.linear.x = speed
            msg.angular.z = angular_z
        else:
            msg = AckermannDrive()
            msg.speed = speed
            msg.steering_angle = math.radians(steering_deg)
        self.pub.publish(msg)

    def stop(self, repeats: int = 8) -> None:
        for _ in range(repeats):
            self.publish_cmd(0.0, 0.0, 0.0)
            rclpy.spin_once(self, timeout_sec=0.01)
            time.sleep(0.04)

    def center_steering(self, duration: float = CENTER_HOLD_SEC) -> None:
        self.get_logger().info(f"centering steering for {duration:.1f}s")
        end_time = time.monotonic() + duration
        while time.monotonic() < end_time and rclpy.ok() and not self.stop_requested:
            self.publish_cmd(0.0, 0.0, 0.0)
            rclpy.spin_once(self, timeout_sec=0.0)
            time.sleep(self.period)

    def run_plan(self, plan: list[Step]) -> None:
        if not plan:
            self.stop()
            return
        self.center_steering()
        for step in plan:
            self.get_logger().info(
                f"{step.label}: speed={step.speed:.2f}m/s "
                f"angular_z={step.angular_z:.2f}rad/s steering={step.steering_deg:.1f}deg "
                f"duration={step.duration:.1f}s"
            )
            end_time = time.monotonic() + step.duration
            while time.monotonic() < end_time and rclpy.ok() and not self.stop_requested:
                self.publish_cmd(step.speed, step.angular_z, step.steering_deg)
                rclpy.spin_once(self, timeout_sec=0.0)
                time.sleep(self.period)
            if self.stop_requested:
                break


def print_plan(plan: list[Step], mode: str) -> None:
    if not plan:
        print("stop only")
        return
    print(f"Mode: {mode}")
    for step in plan:
        if mode == "twist":
            print(f"- {step.label}: /cmd_vel linear.x={step.speed:.2f}, angular.z={step.angular_z:.2f}, {step.duration:.1f}s")
        else:
            print(f"- {step.label}: /ackermann_cmd speed={step.speed:.2f}, steering={step.steering_deg:.1f}deg, {step.duration:.1f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run low-speed OSRacer ROS 2 demos")
    parser.add_argument("demo", choices=("stop", "warmup", "straight", "left", "right", "figure8", "patrol", "showcase", "circle"))
    parser.add_argument("--mode", choices=("twist", "ackermann"), default="twist")
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION)
    parser.add_argument("--loops", type=int, default=2)
    parser.add_argument("--rate", type=float, default=20.0)
    parser.add_argument("--cmd-vel-topic", default="/cmd_vel")
    parser.add_argument("--ackermann-topic", default="/ackermann_cmd")
    parser.add_argument("--yes", action="store_true", help="start without confirmation")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without publishing")
    args = parser.parse_args()

    plan = build_plan(args.demo, args.speed, args.duration, args.loops)
    print_plan(plan, args.mode)
    if args.dry_run:
        return 0
    if rclpy is None:
        raise SystemExit(
            "ERROR: ROS 2 Python packages not found. "
            "Run `source /opt/ros/humble/setup.bash` and source the osracer workspace first."
        )

    if not args.yes and args.demo != "stop":
        print("\nPut the car on a clear floor or lift it safely.")
        print("Press Enter to start. Press Ctrl-C anytime to stop.")
        input()

    rclpy.init(args=None)
    node = DriveDemo(args.mode, args.cmd_vel_topic, args.ackermann_topic, args.rate)

    stop_requested = False

    def handle_sigint(signum, frame):
        nonlocal stop_requested
        stop_requested = True
        node.stop_requested = True
        node.get_logger().warning("stop requested")

    old_int_handler = signal.signal(signal.SIGINT, handle_sigint)
    old_term_handler = signal.signal(signal.SIGTERM, handle_sigint)
    try:
        if stop_requested:
            return 130
        node.run_plan(plan)
    finally:
        node.get_logger().info("sending stop")
        node.stop()
        signal.signal(signal.SIGINT, old_int_handler)
        signal.signal(signal.SIGTERM, old_term_handler)
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
