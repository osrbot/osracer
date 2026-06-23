#!/usr/bin/env python3

import math
import os
import sys

import rclpy
from ackermann_msgs.msg import AckermannDrive
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import Imu


def quat_to_euler_xyz(q):
    x, y, z, w = q.x, q.y, q.z, q.w
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    sinp = 2.0 * (w * y - z * x)
    pitch = math.copysign(math.pi / 2.0, sinp) if abs(sinp) >= 1.0 else math.asin(sinp)

    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return roll, pitch, yaw


class PolicyInferenceNode(Node):
    def __init__(self):
        super().__init__("osracer_policy_inference")

        self.declare_parameter("policy_path", "")
        self.declare_parameter("enabled", False)
        self.declare_parameter("odom_topic", "/odometry/filtered")
        self.declare_parameter("imu_topic", "/imu_filter")
        self.declare_parameter("ackermann_cmd_topic", "/ackermann_cmd")
        self.declare_parameter("rate_hz", 5.0)
        self.declare_parameter("stale_timeout_s", 0.5)
        self.declare_parameter("max_speed_mps", 0.3)
        self.declare_parameter("max_steering_rad", 0.488)
        self.declare_parameter("publish_zero_when_disabled", True)

        self.enabled = bool(self.get_parameter("enabled").value)
        self.stale_timeout_s = float(self.get_parameter("stale_timeout_s").value)
        self.max_speed_mps = float(self.get_parameter("max_speed_mps").value)
        self.max_steering_rad = float(self.get_parameter("max_steering_rad").value)
        self.publish_zero_when_disabled = bool(self.get_parameter("publish_zero_when_disabled").value)

        self.last_odom = None
        self.last_imu = None
        self.last_odom_time = None
        self.last_imu_time = None
        self.last_action = [0.0, 0.0]
        self.zero_published = False

        policy_path = str(self.get_parameter("policy_path").value)
        self.policy = self._load_policy(policy_path)

        odom_topic = str(self.get_parameter("odom_topic").value)
        imu_topic = str(self.get_parameter("imu_topic").value)
        cmd_topic = str(self.get_parameter("ackermann_cmd_topic").value)

        self.create_subscription(Odometry, odom_topic, self.odom_callback, 10)
        self.create_subscription(Imu, imu_topic, self.imu_callback, 10)
        self.cmd_pub = self.create_publisher(AckermannDrive, cmd_topic, 10)

        rate_hz = max(float(self.get_parameter("rate_hz").value), 0.1)
        self.timer = self.create_timer(1.0 / rate_hz, self.timer_callback)

        self.get_logger().info(
            f"Policy inference node started: enabled={self.enabled}, "
            f"odom_topic={odom_topic}, imu_topic={imu_topic}, cmd_topic={cmd_topic}"
        )

    def _load_policy(self, policy_path):
        if not policy_path:
            self.get_logger().warning("policy_path is empty; node will only publish safe stop commands")
            return None
        if not os.path.exists(policy_path):
            self.get_logger().error(f"policy_path does not exist: {policy_path}")
            return None
        try:
            import torch

            policy = torch.jit.load(policy_path, map_location="cpu")
            policy.eval()
            self.torch = torch
            self.get_logger().info(f"Loaded TorchScript policy: {policy_path}")
            return policy
        except Exception as exc:
            self.get_logger().error(f"Failed to load policy '{policy_path}': {exc}")
            return None

    def odom_callback(self, msg):
        self.last_odom = msg
        self.last_odom_time = self.get_clock().now()

    def imu_callback(self, msg):
        self.last_imu = msg
        self.last_imu_time = self.get_clock().now()

    def timer_callback(self):
        if not self.enabled or self.policy is None:
            if self.publish_zero_when_disabled and not self.zero_published:
                self.publish_command(0.0, 0.0)
            return

        if not self.inputs_fresh():
            self.publish_command(0.0, 0.0)
            return

        obs = self.build_observation()
        if obs is None:
            self.publish_command(0.0, 0.0)
            return

        try:
            with self.torch.inference_mode():
                action = self.policy(obs).squeeze(0).tolist()
        except Exception as exc:
            self.get_logger().error(f"Policy inference failed: {exc}")
            self.publish_command(0.0, 0.0)
            return

        speed = max(0.0, min(float(action[0]), self.max_speed_mps))
        steering = max(-self.max_steering_rad, min(float(action[1]), self.max_steering_rad))
        self.publish_command(speed, steering)

    def inputs_fresh(self):
        if self.last_odom is None or self.last_imu is None:
            return False
        now = self.get_clock().now()
        odom_age = (now - self.last_odom_time).nanoseconds * 1e-9
        imu_age = (now - self.last_imu_time).nanoseconds * 1e-9
        return odom_age <= self.stale_timeout_s and imu_age <= self.stale_timeout_s

    def build_observation(self):
        odom = self.last_odom
        imu = self.last_imu
        pose = odom.pose.pose
        twist = odom.twist.twist
        roll, pitch, yaw = quat_to_euler_xyz(pose.orientation)

        values = [
            pose.position.x,
            pose.position.y,
            pose.position.z,
            roll,
            pitch,
            yaw,
            twist.linear.x,
            twist.linear.y,
            twist.linear.z,
            imu.angular_velocity.x,
            imu.angular_velocity.y,
            imu.angular_velocity.z,
            self.last_action[0],
            self.last_action[1],
        ]

        if not all(math.isfinite(v) for v in values):
            self.get_logger().warning("Non-finite observation; publishing stop command")
            return None
        return self.torch.tensor([values], dtype=self.torch.float32)

    def publish_command(self, speed, steering):
        msg = AckermannDrive()
        msg.speed = float(speed)
        msg.steering_angle = float(steering)
        self.cmd_pub.publish(msg)
        self.last_action = [msg.speed, msg.steering_angle]
        self.zero_published = msg.speed == 0.0 and msg.steering_angle == 0.0


def main(args=None):
    rclpy.init(args=args)
    node = PolicyInferenceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.publish_command(0.0, 0.0)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main(sys.argv)
