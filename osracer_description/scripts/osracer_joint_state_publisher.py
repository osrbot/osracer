#!/usr/bin/env python3

import math
import xml.etree.ElementTree as ET

import rclpy
from ackermann_msgs.msg import AckermannDrive
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import JointState


class OsracerJointStatePublisher(Node):
    def __init__(self):
        super().__init__("osracer_joint_state_publisher")

        self.declare_parameter("urdf_model", "")
        self.declare_parameter("wheel_radius", 0.0425)
        self.declare_parameter("wheelbase", 0.285)
        self.declare_parameter("track_width", 0.215)
        self.declare_parameter("max_steering_angle_deg", 30.0)
        self.declare_parameter("steering_joint_sign", -1.0)
        self.declare_parameter("odom_topic", "odom")
        self.declare_parameter("cmd_vel_topic", "cmd_vel")
        self.declare_parameter("ackermann_cmd_topic", "ackermann_cmd")
        self.declare_parameter("joint_state_topic", "joint_states")

        self.wheel_radius = self.get_parameter("wheel_radius").value
        self.wheelbase = self.get_parameter("wheelbase").value
        self.track_width = self.get_parameter("track_width").value
        self.max_steering_angle = math.radians(self.get_parameter("max_steering_angle_deg").value)
        self.steering_joint_sign = self.get_parameter("steering_joint_sign").value

        urdf_model = self.get_parameter("urdf_model").value
        model_geometry = self.derive_geometry_from_urdf(urdf_model)
        if model_geometry:
            model_wheelbase, model_track_width = model_geometry
            self.get_logger().info(
                "URDF geometry: wheelbase=%.4fm track_width=%.4fm; using configured %.4fm %.4fm"
                % (model_wheelbase, model_track_width, self.wheelbase, self.track_width)
            )

        self.last_odom_time = None
        self.command_steering = 0.0
        self.wheel_positions = {
            "Left_front_wheel_joint": 0.0,
            "right_front_wheel_joint": 0.0,
            "left_rear_wheel_joint": 0.0,
            "right_rear_wheel_joint": 0.0,
        }

        self.joint_pub = self.create_publisher(
            JointState,
            self.get_parameter("joint_state_topic").value,
            1,
        )
        self.create_subscription(
            Odometry,
            self.get_parameter("odom_topic").value,
            self.odom_callback,
            1,
        )
        self.create_subscription(
            Twist,
            self.get_parameter("cmd_vel_topic").value,
            self.cmd_vel_callback,
            5,
        )
        self.create_subscription(
            AckermannDrive,
            self.get_parameter("ackermann_cmd_topic").value,
            self.ackermann_cmd_callback,
            5,
        )

    def derive_geometry_from_urdf(self, urdf_model):
        if not urdf_model:
            return None
        try:
            root = ET.parse(urdf_model).getroot()
        except (OSError, ET.ParseError) as exc:
            self.get_logger().warning(f"Could not read URDF geometry from '{urdf_model}': {exc}")
            return None

        joints = {}
        for joint in root.findall("joint"):
            parent = joint.find("parent")
            child = joint.find("child")
            origin = joint.find("origin")
            if parent is None or child is None:
                continue
            xyz = [0.0, 0.0, 0.0]
            rpy = [0.0, 0.0, 0.0]
            if origin is not None and origin.get("xyz"):
                xyz = [float(v) for v in origin.get("xyz").split()]
            if origin is not None and origin.get("rpy"):
                rpy = [float(v) for v in origin.get("rpy").split()]
            joints[child.get("link")] = (parent.get("link"), xyz, rpy)

        cache = {"base_link": self.identity_transform()}

        def link_transform(link_name):
            if link_name in cache:
                return cache[link_name]
            if link_name not in joints:
                return self.identity_transform()
            parent_name, xyz, rpy = joints[link_name]
            parent_tf = link_transform(parent_name)
            cache[link_name] = self.compose_transform(parent_tf, (self.rpy_matrix(rpy), xyz))
            return cache[link_name]

        names = [
            "Left_front_wheel_link",
            "right_front_wheel_link",
            "left_rear_wheel_link",
            "right_rear_wheel_link",
        ]
        positions = [link_transform(name)[1] for name in names]
        front_center = [(positions[0][i] + positions[1][i]) * 0.5 for i in range(3)]
        rear_center = [(positions[2][i] + positions[3][i]) * 0.5 for i in range(3)]
        wheelbase = math.hypot(front_center[0] - rear_center[0], front_center[1] - rear_center[1])
        front_track = math.hypot(positions[0][0] - positions[1][0], positions[0][1] - positions[1][1])
        rear_track = math.hypot(positions[2][0] - positions[3][0], positions[2][1] - positions[3][1])
        return wheelbase, (front_track + rear_track) * 0.5

    def identity_transform(self):
        return (
            [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ],
            [0.0, 0.0, 0.0],
        )

    def rpy_matrix(self, rpy):
        roll, pitch, yaw = rpy
        cr, sr = math.cos(roll), math.sin(roll)
        cp, sp = math.cos(pitch), math.sin(pitch)
        cy, sy = math.cos(yaw), math.sin(yaw)
        return [
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ]

    def compose_transform(self, parent_tf, child_tf):
        parent_rot, parent_xyz = parent_tf
        child_rot, child_xyz = child_tf
        rot = self.matrix_multiply(parent_rot, child_rot)
        xyz = self.rotate_vector(parent_rot, child_xyz)
        return rot, [parent_xyz[i] + xyz[i] for i in range(3)]

    def matrix_multiply(self, a, b):
        return [
            [sum(a[row][k] * b[k][col] for k in range(3)) for col in range(3)]
            for row in range(3)
        ]

    def rotate_vector(self, rot, vector):
        return [sum(rot[row][col] * vector[col] for col in range(3)) for row in range(3)]

    def cmd_vel_callback(self, msg):
        linear_x = msg.linear.x
        angular_z = msg.angular.z
        if abs(linear_x) < 0.01:
            steering = 0.0 if angular_z == 0.0 else math.copysign(self.max_steering_angle, angular_z)
        else:
            steering = math.atan(self.wheelbase * angular_z / linear_x)
        self.command_steering = self.clamp_steering(steering)

    def ackermann_cmd_callback(self, msg):
        self.command_steering = self.clamp_steering(msg.steering_angle)

    def clamp_steering(self, steering):
        return max(-self.max_steering_angle, min(self.max_steering_angle, steering))

    def odom_callback(self, msg):
        current_time = Time.from_msg(msg.header.stamp)
        dt = 0.0
        if self.last_odom_time is not None:
            dt = (current_time - self.last_odom_time).nanoseconds / 1e9
        self.last_odom_time = current_time

        wheel_speeds, steering_positions = self.calculate_ackermann_targets(
            msg.twist.twist.linear.x,
            self.command_steering,
        )
        if dt > 0.0 and self.wheel_radius > 0.0:
            for joint_name, linear_speed in wheel_speeds.items():
                self.wheel_positions[joint_name] += linear_speed / self.wheel_radius * dt

        joint_state = JointState()
        joint_state.header.stamp = msg.header.stamp
        joint_state.name = [
            "left_steering_hinge_joint",
            "right_steering_hinge_joint",
            "Left_front_wheel_joint",
            "right_front_wheel_joint",
            "left_rear_wheel_joint",
            "right_rear_wheel_joint",
        ]
        joint_state.position = [
            steering_positions["left_steering_hinge_joint"],
            steering_positions["right_steering_hinge_joint"],
            self.wheel_positions["Left_front_wheel_joint"],
            -self.wheel_positions["right_front_wheel_joint"],
            -self.wheel_positions["left_rear_wheel_joint"],
            self.wheel_positions["right_rear_wheel_joint"],
        ]
        self.joint_pub.publish(joint_state)

    def calculate_ackermann_targets(self, speed, steering):
        wheel_speeds = {
            "Left_front_wheel_joint": speed,
            "right_front_wheel_joint": speed,
            "left_rear_wheel_joint": speed,
            "right_rear_wheel_joint": speed,
        }
        steering_positions = {
            "left_steering_hinge_joint": steering * self.steering_joint_sign,
            "right_steering_hinge_joint": steering * self.steering_joint_sign,
        }
        if abs(steering) < 1e-4 or self.wheelbase <= 0.0 or self.track_width <= 0.0:
            return wheel_speeds, steering_positions

        tan_steer = math.tan(steering)
        left_steering = math.atan2(tan_steer, 1.0 - self.track_width * tan_steer / (2.0 * self.wheelbase))
        right_steering = math.atan2(tan_steer, 1.0 + self.track_width * tan_steer / (2.0 * self.wheelbase))
        steering_positions["left_steering_hinge_joint"] = left_steering * self.steering_joint_sign
        steering_positions["right_steering_hinge_joint"] = right_steering * self.steering_joint_sign

        center_radius = abs(self.wheelbase / tan_steer)
        half_track = self.track_width * 0.5
        angular_speed = speed / center_radius
        turn_sign = math.copysign(1.0, steering)
        inner_radius = max(center_radius - half_track, 1e-3)
        outer_radius = center_radius + half_track
        inner_front_speed = angular_speed * math.hypot(self.wheelbase, inner_radius)
        outer_front_speed = angular_speed * math.hypot(self.wheelbase, outer_radius)
        inner_rear_speed = angular_speed * inner_radius
        outer_rear_speed = angular_speed * outer_radius

        if turn_sign > 0.0:
            wheel_speeds["Left_front_wheel_joint"] = inner_front_speed
            wheel_speeds["right_front_wheel_joint"] = outer_front_speed
            wheel_speeds["left_rear_wheel_joint"] = inner_rear_speed
            wheel_speeds["right_rear_wheel_joint"] = outer_rear_speed
        else:
            wheel_speeds["Left_front_wheel_joint"] = outer_front_speed
            wheel_speeds["right_front_wheel_joint"] = inner_front_speed
            wheel_speeds["left_rear_wheel_joint"] = outer_rear_speed
            wheel_speeds["right_rear_wheel_joint"] = inner_rear_speed
        return wheel_speeds, steering_positions


def main(args=None):
    rclpy.init(args=args)
    node = OsracerJointStatePublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
