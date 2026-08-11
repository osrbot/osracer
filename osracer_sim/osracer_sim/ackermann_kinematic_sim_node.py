#!/usr/bin/env python3
"""Lightweight kinematic OSRacer simulator.

This node is intentionally not a high-fidelity vehicle dynamics model. It keeps
the ROS interfaces stable for teaching, SLAM/Nav2 smoke tests, and race
controller regression checks when real hardware is unavailable.
"""

from __future__ import annotations

import math
import time
from typing import Iterable

import rclpy
from ackermann_msgs.msg import AckermannDrive, AckermannDriveStamped
from geometry_msgs.msg import TransformStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import Imu, JointState, LaserScan
from tf2_ros import TransformBroadcaster

from osracer_sim.kinematics import (
    ackermann_front_angles,
    clamp,
    obstacle_preset,
    rectangular_track_segments,
    steering_from_twist,
    synthetic_scan,
    synthetic_track_scan,
    yaw_to_quat,
)


class AckermannKinematicSim(Node):
    def __init__(self) -> None:
        super().__init__('osracer_ackermann_kinematic_sim')

        self.declare_parameter('wheelbase')
        self.declare_parameter('track_width', 0.215)
        self.declare_parameter('wheel_radius', 0.0425)
        self.declare_parameter('max_speed')
        self.declare_parameter('max_steering_angle')
        self.declare_parameter('update_rate_hz', 100.0)
        self.declare_parameter('cmd_timeout_s', 0.5)
        self.declare_parameter('ackermann_topic', '/ackermann_cmd')
        self.declare_parameter('ackermann_stamped_topic', '/ackermann_cmd_stamped')
        self.declare_parameter('cmd_vel_topic', '/cmd_vel')
        self.declare_parameter('odom_topic', '/odometry/filtered')
        self.declare_parameter('imu_topic', '/imu_filter')
        self.declare_parameter('scan_topic', '/scan')
        self.declare_parameter('odom_frame', 'odom')
        self.declare_parameter('base_frame', 'base_footprint')
        self.declare_parameter('imu_frame', 'imu_link')
        self.declare_parameter('laser_frame', 'laser')
        self.declare_parameter('publish_tf', True)
        self.declare_parameter('publish_imu', True)
        self.declare_parameter('publish_scan', True)
        self.declare_parameter('publish_clock', True)
        self.declare_parameter('imu_orientation_covariance', [0.02, 0.02, 0.05])
        self.declare_parameter('imu_angular_velocity_covariance', [0.01, 0.01, 0.01])
        self.declare_parameter('imu_linear_acceleration_covariance', [0.10, 0.10, 0.10])
        self.declare_parameter('scan_rate_hz', 20.0)
        self.declare_parameter('scan_range_m', 8.0)
        self.declare_parameter('scan_fov_deg', 270.0)
        self.declare_parameter('scan_points', 541)
        self.declare_parameter('scan_environment', 'track')
        self.declare_parameter('track_outer_length_m', 7.0)
        self.declare_parameter('track_outer_width_m', 4.5)
        self.declare_parameter('track_lane_width_m', 1.1)
        self.declare_parameter('obstacle_preset', 'custom')
        self.declare_parameter('obstacle_enabled', False)
        self.declare_parameter('obstacle_x', 2.0)
        self.declare_parameter('obstacle_y', -1.7)
        self.declare_parameter('obstacle_radius', 0.25)
        self.declare_parameter('initial_x', 0.0)
        self.declare_parameter('initial_y', -1.7)
        self.declare_parameter('initial_yaw_deg', 0.0)

        self.wheelbase = float(self.get_parameter('wheelbase').value)
        self.track_width = float(self.get_parameter('track_width').value)
        self.wheel_radius = float(self.get_parameter('wheel_radius').value)
        self.max_speed = float(self.get_parameter('max_speed').value)
        self.max_steering = float(self.get_parameter('max_steering_angle').value)
        self.cmd_timeout = float(self.get_parameter('cmd_timeout_s').value)
        self.odom_frame = str(self.get_parameter('odom_frame').value)
        self.base_frame = str(self.get_parameter('base_frame').value)
        self.imu_frame = str(self.get_parameter('imu_frame').value)
        self.laser_frame = str(self.get_parameter('laser_frame').value)
        self.publish_tf = bool(self.get_parameter('publish_tf').value)
        self.publish_imu = bool(self.get_parameter('publish_imu').value)
        self.publish_scan = bool(self.get_parameter('publish_scan').value)
        self.publish_clock = bool(self.get_parameter('publish_clock').value)
        self.imu_orientation_covariance = diagonal_covariance(
            self.get_parameter('imu_orientation_covariance').value)
        self.imu_angular_velocity_covariance = diagonal_covariance(
            self.get_parameter('imu_angular_velocity_covariance').value)
        self.imu_linear_acceleration_covariance = diagonal_covariance(
            self.get_parameter('imu_linear_acceleration_covariance').value)

        self.scan_environment = str(self.get_parameter('scan_environment').value)
        self.track_segments = rectangular_track_segments(
            float(self.get_parameter('track_outer_length_m').value),
            float(self.get_parameter('track_outer_width_m').value),
            float(self.get_parameter('track_lane_width_m').value),
        )

        self.x = float(self.get_parameter('initial_x').value)
        self.y = float(self.get_parameter('initial_y').value)
        self.yaw = math.radians(float(self.get_parameter('initial_yaw_deg').value))
        self.speed = 0.0
        self.last_integrated_speed = 0.0
        self.steering = 0.0
        self.wheel_position = 0.0
        self.last_cmd_time = time.monotonic()
        self.last_update_time = time.monotonic()
        self.last_scan_time = 0.0
        self.sim_time_s = 0.0

        self.odom_pub = self.create_publisher(Odometry, self.get_parameter('odom_topic').value, 10)
        self.imu_pub = self.create_publisher(Imu, self.get_parameter('imu_topic').value, 10)
        self.joint_pub = self.create_publisher(JointState, '/joint_states', 10)
        self.scan_pub = self.create_publisher(LaserScan, self.get_parameter('scan_topic').value, 10)
        self.clock_pub = self.create_publisher(Clock, '/clock', 10)
        self.tf_broadcaster = TransformBroadcaster(self) if self.publish_tf else None

        self.create_subscription(
            AckermannDrive,
            self.get_parameter('ackermann_topic').value,
            self.on_ackermann,
            10,
        )
        self.create_subscription(
            AckermannDriveStamped,
            self.get_parameter('ackermann_stamped_topic').value,
            self.on_ackermann_stamped,
            10,
        )
        self.create_subscription(Twist, self.get_parameter('cmd_vel_topic').value, self.on_twist, 10)

        update_rate = max(float(self.get_parameter('update_rate_hz').value), 1.0)
        self.timer = self.create_timer(1.0 / update_rate, self.on_timer)
        self.get_logger().info(
            'OSRacer kinematic sim ready: wheelbase=%.3fm track=%.3fm wheel_radius=%.4fm scan=%s'
            % (self.wheelbase, self.track_width, self.wheel_radius, self.scan_environment)
        )

    def on_ackermann(self, msg: AckermannDrive) -> None:
        self.apply_ackermann(float(msg.speed), float(msg.steering_angle))

    def on_ackermann_stamped(self, msg: AckermannDriveStamped) -> None:
        self.apply_ackermann(float(msg.drive.speed), float(msg.drive.steering_angle))

    def apply_ackermann(self, speed: float, steering: float) -> None:
        self.speed = clamp(speed, -self.max_speed, self.max_speed)
        self.steering = clamp(steering, -self.max_steering, self.max_steering)
        self.last_cmd_time = time.monotonic()

    def on_twist(self, msg: Twist) -> None:
        self.speed = clamp(float(msg.linear.x), -self.max_speed, self.max_speed)
        steering = steering_from_twist(self.speed, float(msg.angular.z), self.wheelbase)
        self.steering = clamp(steering, -self.max_steering, self.max_steering)
        self.last_cmd_time = time.monotonic()

    def on_timer(self) -> None:
        now = time.monotonic()
        dt = clamp(now - self.last_update_time, 0.0, 0.1)
        self.last_update_time = now
        self.sim_time_s += dt

        if now - self.last_cmd_time > self.cmd_timeout:
            self.speed = 0.0
            self.steering = 0.0

        yaw_rate = 0.0
        if abs(self.steering) > 1e-5 and abs(self.speed) > 1e-5:
            yaw_rate = self.speed * math.tan(self.steering) / self.wheelbase
        longitudinal_accel = 0.0
        if dt > 1e-6:
            longitudinal_accel = (self.speed - self.last_integrated_speed) / dt
        lateral_accel = self.speed * yaw_rate
        self.x += self.speed * math.cos(self.yaw) * dt
        self.y += self.speed * math.sin(self.yaw) * dt
        self.yaw = math.atan2(math.sin(self.yaw + yaw_rate * dt), math.cos(self.yaw + yaw_rate * dt))
        if self.wheel_radius > 1e-5:
            self.wheel_position += self.speed * dt / self.wheel_radius

        stamp = self.sim_stamp()
        self.publish_odom(stamp, yaw_rate)
        if self.publish_imu:
            self.publish_imu_msg(stamp, yaw_rate, longitudinal_accel, lateral_accel)
        self.publish_joints(stamp)
        if self.publish_tf:
            self.publish_transform(stamp)
        if self.publish_clock:
            self.publish_clock_msg()
        scan_rate = max(float(self.get_parameter('scan_rate_hz').value), 1.0)
        if self.publish_scan and now - self.last_scan_time >= 1.0 / scan_rate:
            self.last_scan_time = now
            self.publish_scan_msg(stamp)
        self.last_integrated_speed = self.speed

    def publish_odom(self, stamp, yaw_rate: float) -> None:
        msg = Odometry()
        msg.header.stamp = stamp
        msg.header.frame_id = self.odom_frame
        msg.child_frame_id = self.base_frame
        msg.pose.pose.position.x = self.x
        msg.pose.pose.position.y = self.y
        qx, qy, qz, qw = yaw_to_quat(self.yaw)
        msg.pose.pose.orientation.x = qx
        msg.pose.pose.orientation.y = qy
        msg.pose.pose.orientation.z = qz
        msg.pose.pose.orientation.w = qw
        msg.twist.twist.linear.x = self.speed
        msg.twist.twist.angular.z = yaw_rate
        msg.pose.covariance = covariance(0.02, 1e3, 0.05)
        msg.twist.covariance = covariance(0.05, 1e3, 0.08)
        self.odom_pub.publish(msg)

    def publish_imu_msg(
            self,
            stamp,
            yaw_rate: float,
            longitudinal_accel: float,
            lateral_accel: float) -> None:
        msg = Imu()
        msg.header.stamp = stamp
        msg.header.frame_id = self.imu_frame
        qx, qy, qz, qw = yaw_to_quat(self.yaw)
        msg.orientation.x = qx
        msg.orientation.y = qy
        msg.orientation.z = qz
        msg.orientation.w = qw
        msg.angular_velocity.x = 0.0
        msg.angular_velocity.y = 0.0
        msg.angular_velocity.z = yaw_rate
        msg.linear_acceleration.x = longitudinal_accel
        msg.linear_acceleration.y = lateral_accel
        msg.linear_acceleration.z = 0.0
        msg.orientation_covariance = self.imu_orientation_covariance
        msg.angular_velocity_covariance = self.imu_angular_velocity_covariance
        msg.linear_acceleration_covariance = self.imu_linear_acceleration_covariance
        self.imu_pub.publish(msg)

    def publish_transform(self, stamp) -> None:
        transform = TransformStamped()
        transform.header.stamp = stamp
        transform.header.frame_id = self.odom_frame
        transform.child_frame_id = self.base_frame
        transform.transform.translation.x = self.x
        transform.transform.translation.y = self.y
        transform.transform.translation.z = 0.0
        qx, qy, qz, qw = yaw_to_quat(self.yaw)
        transform.transform.rotation.x = qx
        transform.transform.rotation.y = qy
        transform.transform.rotation.z = qz
        transform.transform.rotation.w = qw
        self.tf_broadcaster.sendTransform(transform)

    def publish_joints(self, stamp) -> None:
        left_steering, right_steering = ackermann_front_angles(
            self.steering, self.wheelbase, self.track_width)
        msg = JointState()
        msg.header.stamp = stamp
        msg.name = [
            'left_steering_hinge_joint',
            'right_steering_hinge_joint',
            'Left_front_wheel_joint',
            'right_front_wheel_joint',
            'left_rear_wheel_joint',
            'right_rear_wheel_joint',
        ]
        msg.position = [
            -left_steering,
            -right_steering,
            self.wheel_position,
            -self.wheel_position,
            self.wheel_position,
            -self.wheel_position,
        ]
        self.joint_pub.publish(msg)

    def publish_scan_msg(self, stamp) -> None:
        points = max(int(self.get_parameter('scan_points').value), 3)
        fov = math.radians(float(self.get_parameter('scan_fov_deg').value))
        range_m = float(self.get_parameter('scan_range_m').value)
        msg = LaserScan()
        msg.header.stamp = stamp
        msg.header.frame_id = self.laser_frame
        msg.angle_min = -0.5 * fov
        msg.angle_max = 0.5 * fov
        msg.angle_increment = fov / float(points - 1)
        msg.time_increment = 0.0
        msg.scan_time = 1.0 / max(float(self.get_parameter('scan_rate_hz').value), 1.0)
        msg.range_min = 0.12
        msg.range_max = max(range_m, msg.range_min + 0.1)
        if self.scan_environment == 'track':
            msg.ranges = synthetic_track_scan(
                self.x,
                self.y,
                self.yaw,
                points,
                msg.angle_min,
                msg.angle_increment,
                msg.range_max,
                self.track_segments,
                self.scan_obstacles(),
            )
        else:
            msg.ranges = synthetic_scan(points, msg.angle_min, msg.angle_increment, msg.range_max)
        self.scan_pub.publish(msg)

    def scan_obstacles(self) -> list[tuple[float, float, float]]:
        preset = str(self.get_parameter('obstacle_preset').value)
        preset_obstacles = obstacle_preset(preset)
        if preset != 'custom':
            return preset_obstacles
        if not bool(self.get_parameter('obstacle_enabled').value):
            return []
        return [(
            float(self.get_parameter('obstacle_x').value),
            float(self.get_parameter('obstacle_y').value),
            max(float(self.get_parameter('obstacle_radius').value), 0.0),
        )]

    def publish_clock_msg(self) -> None:
        msg = Clock()
        msg.clock = self.sim_stamp()
        self.clock_pub.publish(msg)

    def sim_stamp(self):
        if not self.publish_clock:
            return self.get_clock().now().to_msg()
        seconds = int(self.sim_time_s)
        stamp = Clock().clock
        stamp.sec = seconds
        stamp.nanosec = int((self.sim_time_s - seconds) * 1e9)
        return stamp


def covariance(xy: float, z_and_tilt: float, yaw: float) -> list[float]:
    values = [0.0] * 36
    for index, value in ((0, xy), (7, xy), (14, z_and_tilt), (21, z_and_tilt), (28, z_and_tilt), (35, yaw)):
        values[index] = value
    return values


def diagonal_covariance(values) -> list[float]:
    diagonal = [float(value) for value in values]
    if len(diagonal) != 3:
        raise ValueError('IMU covariance diagonal must contain exactly 3 values')
    covariance_values = [0.0] * 9
    for index, value in enumerate(diagonal):
        covariance_values[index * 4] = value
    return covariance_values


def main(args: Iterable[str] | None = None) -> int:
    rclpy.init(args=args)
    node = AckermannKinematicSim()
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
