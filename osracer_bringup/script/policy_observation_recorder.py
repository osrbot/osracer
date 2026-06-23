#!/usr/bin/env python3

import csv
import math
import os
import sys

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import Imu

try:
    from ackermann_msgs.msg import AckermannDrive
except ImportError:
    AckermannDrive = None


CSV_FIELDS = [
    "stamp_s",
    "px",
    "py",
    "pz",
    "roll",
    "pitch",
    "yaw",
    "vx",
    "vy",
    "vz",
    "wx",
    "wy",
    "wz",
    "last_speed",
    "last_steering",
]


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


class PolicyObservationRecorder(Node):
    def __init__(self):
        super().__init__("osracer_policy_observation_recorder")

        self.declare_parameter("output_path", "/tmp/osracer_policy_observations.csv")
        self.declare_parameter("odom_topic", "/odometry/filtered")
        self.declare_parameter("imu_topic", "/imu_filter")
        self.declare_parameter("ackermann_cmd_topic", "/ackermann_cmd")
        self.declare_parameter("rate_hz", 10.0)
        self.declare_parameter("stale_timeout_s", 0.5)
        self.declare_parameter("flush_every_rows", 10)

        self.output_path = str(self.get_parameter("output_path").value)
        self.stale_timeout_s = float(self.get_parameter("stale_timeout_s").value)
        self.flush_every_rows = max(int(self.get_parameter("flush_every_rows").value), 1)

        self.last_odom = None
        self.last_imu = None
        self.last_odom_time = None
        self.last_imu_time = None
        self.last_action = [0.0, 0.0]
        self.rows_written = 0

        output_dir = os.path.dirname(os.path.abspath(self.output_path))
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        self.output_file = open(self.output_path, "w", newline="")
        self.writer = csv.DictWriter(self.output_file, fieldnames=CSV_FIELDS, lineterminator="\n")
        self.writer.writeheader()

        odom_topic = str(self.get_parameter("odom_topic").value)
        imu_topic = str(self.get_parameter("imu_topic").value)
        cmd_topic = str(self.get_parameter("ackermann_cmd_topic").value)

        self.create_subscription(Odometry, odom_topic, self.odom_callback, 10)
        self.create_subscription(Imu, imu_topic, self.imu_callback, 10)
        if AckermannDrive is None:
            self.get_logger().warning("ackermann_msgs is not available; last_action columns will remain zero")
        else:
            self.create_subscription(AckermannDrive, cmd_topic, self.cmd_callback, 10)

        rate_hz = max(float(self.get_parameter("rate_hz").value), 0.1)
        self.timer = self.create_timer(1.0 / rate_hz, self.timer_callback)

        self.get_logger().info(
            f"Recording policy observations to {self.output_path}: "
            f"odom_topic={odom_topic}, imu_topic={imu_topic}, cmd_topic={cmd_topic}"
        )

    def odom_callback(self, msg):
        self.last_odom = msg
        self.last_odom_time = self.get_clock().now()

    def imu_callback(self, msg):
        self.last_imu = msg
        self.last_imu_time = self.get_clock().now()

    def cmd_callback(self, msg):
        self.last_action = [float(msg.speed), float(msg.steering_angle)]

    def inputs_fresh(self):
        if self.last_odom is None or self.last_imu is None:
            return False
        now = self.get_clock().now()
        odom_age = (now - self.last_odom_time).nanoseconds * 1e-9
        imu_age = (now - self.last_imu_time).nanoseconds * 1e-9
        return odom_age <= self.stale_timeout_s and imu_age <= self.stale_timeout_s

    def build_row(self):
        odom = self.last_odom
        imu = self.last_imu
        pose = odom.pose.pose
        twist = odom.twist.twist
        roll, pitch, yaw = quat_to_euler_xyz(pose.orientation)
        stamp_s = self.get_clock().now().nanoseconds * 1e-9

        values = {
            "stamp_s": stamp_s,
            "px": pose.position.x,
            "py": pose.position.y,
            "pz": pose.position.z,
            "roll": roll,
            "pitch": pitch,
            "yaw": yaw,
            "vx": twist.linear.x,
            "vy": twist.linear.y,
            "vz": twist.linear.z,
            "wx": imu.angular_velocity.x,
            "wy": imu.angular_velocity.y,
            "wz": imu.angular_velocity.z,
            "last_speed": self.last_action[0],
            "last_steering": self.last_action[1],
        }

        if not all(math.isfinite(float(v)) for v in values.values()):
            return None
        return {key: f"{float(value):.9g}" for key, value in values.items()}

    def timer_callback(self):
        if not self.inputs_fresh():
            return
        row = self.build_row()
        if row is None:
            self.get_logger().warning("Skipping non-finite policy observation row")
            return
        self.writer.writerow(row)
        self.rows_written += 1
        if self.rows_written % self.flush_every_rows == 0:
            self.output_file.flush()

    def close(self):
        if not self.output_file.closed:
            self.output_file.flush()
            self.output_file.close()


def main(args=None):
    rclpy.init(args=args)
    node = PolicyObservationRecorder()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main(sys.argv)
