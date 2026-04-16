#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile

from geometry_msgs.msg import Twist
from sensor_msgs.msg import Imu
from nav_msgs.msg import Odometry
from tf2_ros import TransformBroadcaster
from geometry_msgs.msg import TransformStamped
from ackermann_msgs.msg import AckermannDrive  # Import AckermannDrive message type

import serial
import math
import threading
import time

class OsrbotCore(Node):
    def __init__(self):
        super().__init__('osracer_chassis_node')

        # --- Declare and Get Parameters ---
        self.declare_parameter('port_name', '/dev/ttyACM0')
        self.declare_parameter('baud_rate', 115200)
        self.declare_parameter('odom_frame', 'odom')
        self.declare_parameter('base_frame', 'base_footprint')
        self.declare_parameter('imu_frame', 'imu_link')  # New IMU frame parameter
        self.declare_parameter('wheelbase', 0.285)  # Wheelbase, unit: meters
        self.declare_parameter('max_steering_angle_deg', 30.0) # Maximum steering angle, unit: degrees
        self.declare_parameter('cmd_watchdog_timeout_s', 0.5) # Command watchdog timeout

        self.port_name = self.get_parameter('port_name').value
        self.baud_rate = self.get_parameter('baud_rate').value
        self.odom_frame = self.get_parameter('odom_frame').value
        self.base_frame = self.get_parameter('base_frame').value
        self.imu_frame = self.get_parameter('imu_frame').value  # Get IMU frame parameter
        self.wheelbase = self.get_parameter('wheelbase').value
        self.max_steering_angle_deg = self.get_parameter('max_steering_angle_deg').value
        self.cmd_watchdog_timeout = self.get_parameter('cmd_watchdog_timeout_s').value

        # --- Initialize Serial Port ---
        try:
            self.serial = serial.Serial(self.port_name, self.baud_rate, timeout=0.1)
            self.get_logger().info(f"Successfully opened serial port: {self.port_name}")
        except serial.SerialException as e:
            self.get_logger().fatal(f"Could not open serial port '{self.port_name}': {e}")
            rclpy.shutdown()
            return

        # --- Initialize ROS2 Publishers, Subscribers and TF Broadcaster ---
        self.cmd_vel_sub = self.create_subscription(
            Twist,
            'cmd_vel',
            self.cmd_vel_callback,
            10)
        
        # New AckermannDrive Subscriber
        self.ackermann_cmd_sub = self.create_subscription(
            AckermannDrive,
            'ackermann_cmd',
            self.ackermann_cmd_callback,
            10)
            
        # Use Best Practice QoS
        odom_qos = QoSProfile(depth=10)
        imu_qos = QoSProfile(depth=10)

        self.imu_pub = self.create_publisher(Imu, 'imu/data', qos_profile=imu_qos)
        self.odom_pub = self.create_publisher(Odometry, 'odom', qos_profile=odom_qos)
        
        self.tf_broadcaster = TransformBroadcaster(self)

        # --- State Variables ---
        self.last_cmd_time = self.get_clock().now()
        self.serial_lock = threading.Lock()

        # --- Start Serial Reading Thread ---
        self.read_thread = threading.Thread(target=self.read_serial_loop, daemon=True)
        self.read_thread.start()
        
        self.get_logger().info("Vehicle bridge node started.")

    def cmd_vel_callback(self, msg: Twist):
        """Convert Twist message from /cmd_vel to serial command"""
        linear_x = msg.linear.x
        angular_z = msg.angular.z

        # Convert angular velocity to steering angle
        # Formula: steering_angle = atan(wheelbase * angular_z / linear_x)
        # When linear velocity is near zero, set steering to max for on-the-spot turning
        if abs(linear_x) < 0.01:
            steering_angle_rad = math.copysign(self.max_steering_angle_deg * math.pi / 180.0, angular_z)
        else:
            steering_angle_rad = math.atan(self.wheelbase * angular_z / linear_x)

        # Limit steering angle range
        max_steering_angle_rad = self.max_steering_angle_deg * math.pi / 180.0
        steering_angle_rad = max(-max_steering_angle_rad, min(max_steering_angle_rad, steering_angle_rad))

        # Convert to degrees
        steering_angle_deg = math.degrees(steering_angle_rad)

        # Format and send command string
        command = f"v {linear_x:.3f} {steering_angle_deg:.2f}\n"
        
        with self.serial_lock:
            try:
                self.serial.write(command.encode('utf-8'))
            except serial.SerialException as e:
                self.get_logger().error(f"Failed to write to serial: {e}")

        self.last_cmd_time = self.get_clock().now()

    def ackermann_cmd_callback(self, msg: AckermannDrive):
        """Convert AckermannDrive message from /ackermann_cmd to serial command"""
        speed = msg.speed
        steering_angle = msg.steering_angle
        
        # Limit steering angle range
        max_steering_angle_rad = self.max_steering_angle_deg * math.pi / 180.0
        steering_angle_rad = max(-max_steering_angle_rad, min(max_steering_angle_rad, steering_angle))
        
        # Convert to degrees
        steering_angle_deg = math.degrees(steering_angle_rad)
        
        # Format and send command string
        command = f"v {speed:.3f} {steering_angle_deg:.2f}\n"
        
        with self.serial_lock:
            try:
                self.serial.write(command.encode('utf-8'))
            except serial.SerialException as e:
                self.get_logger().error(f"Failed to write to serial: {e}")
                
        self.last_cmd_time = self.get_clock().now()

    def read_serial_loop(self):
        """Continuously read serial data in a separate thread"""
        buffer = ""
        while rclpy.ok():
            if self.serial.in_waiting > 0:
                try:
                    # Read a line of data
                    line = self.serial.readline().decode('utf-8').strip()
                    if line:
                        self.parse_serial_data(line)
                except serial.SerialException:
                    self.get_logger().error("Serial read error, connection might be lost.")
                    break
                except UnicodeDecodeError:
                    self.get_logger().warning("Unable to decode serial data.")
            else:
                time.sleep(0.01) # Short sleep to avoid high CPU usage

    def parse_serial_data(self, line: str):
        """Parse a single line of data from ESP32"""
        try:
            parts = line.split()
            if not parts:
                return

            cmd_type = parts[0]

            if cmd_type == 'i' and len(parts) == 11:
                # New Protocol: i qx qy qz qw ax ay az gx gy gz
                q_x, q_y, q_z, q_w = map(float, parts[1:5])
                a_x, a_y, a_z = map(float, parts[5:8])
                g_x, g_y, g_z = map(float, parts[8:11])

                imu_msg = Imu()
                imu_msg.header.stamp = self.get_clock().now().to_msg()
                imu_msg.header.frame_id = self.imu_frame  # Use parameterized IMU frame

                # Quaternion (Note ROS2 uses x, y, z, w)
                imu_msg.orientation.x = q_x
                imu_msg.orientation.y = q_y
                imu_msg.orientation.z = q_z
                imu_msg.orientation.w = q_w

                # Linear acceleration (m/s2)
                imu_msg.linear_acceleration.x = a_x
                imu_msg.linear_acceleration.y = a_y
                imu_msg.linear_acceleration.z = a_z

                # Angular velocity (rad/s)
                imu_msg.angular_velocity.x = g_x
                imu_msg.angular_velocity.y = g_y
                imu_msg.angular_velocity.z = g_z

                # Optional: set covariance (if not provided, can be set to -1 for unknown)
                # Set to 0 or -1 for simplicity here
                # imu_msg.orientation_covariance = [-1.0] * 9
                # imu_msg.angular_velocity_covariance = [-1.0] * 9
                # imu_msg.linear_acceleration_covariance = [-1.0] * 9

                self.imu_pub.publish(imu_msg)

            elif cmd_type == 'o' and len(parts) == 8:
                # Parse odometry data: o px py pz vx vy vz w
                p_x, p_y, p_z = map(float, parts[1:4])
                v_x, v_y, v_z = map(float, parts[4:7])
                yaw = float(parts[7])

                # Create odometry message
                odom_msg = Odometry()
                odom_msg.header.stamp = self.get_clock().now().to_msg()
                odom_msg.header.frame_id = self.odom_frame
                odom_msg.child_frame_id = self.base_frame

                # Set position
                odom_msg.pose.pose.position.x = p_x
                odom_msg.pose.pose.position.y = p_y
                odom_msg.pose.pose.position.z = p_z

                # Create quaternion from yaw angle
                q = self.quaternion_from_euler(0, 0, yaw)
                odom_msg.pose.pose.orientation.x = q[0]
                odom_msg.pose.pose.orientation.y = q[1]
                odom_msg.pose.pose.orientation.z = q[2]
                odom_msg.pose.pose.orientation.w = q[3]

                # Set velocity
                odom_msg.twist.twist.linear.x = v_x
                odom_msg.twist.twist.linear.y = v_y
                odom_msg.twist.twist.linear.z = v_z
                # Angular velocity can be estimated from steering angle and linear speed, 
                # but ESP32 does not provide it, left blank here
                odom_msg.twist.twist.angular.x = 0.0
                odom_msg.twist.twist.angular.y = 0.0
                odom_msg.twist.twist.angular.z = 0.0 # Not provided by ESP32, needs model estimation

                self.odom_pub.publish(odom_msg)

                # Broadcast TF transform
                # t = TransformStamped()
                # t.header.stamp = odom_msg.header.stamp
                # t.header.frame_id = self.odom_frame
                # t.child_frame_id = self.base_frame
                
                # t.transform.translation.x = p_x
                # t.transform.translation.y = p_y
                # t.transform.translation.z = p_z
                # t.transform.rotation = odom_msg.pose.pose.orientation
                
                # self.tf_broadcaster.sendTransform(t)
        except (ValueError, IndexError) as e:
            self.get_logger().warn(f"Error parsing serial data: '{line}', error: {e}")

    def quaternion_from_euler(self, roll, pitch, yaw):
        """Create quaternion from Euler angles"""
        cy = math.cos(yaw * 0.5)
        sy = math.sin(yaw * 0.5)
        cp = math.cos(pitch * 0.5)
        sp = math.sin(pitch * 0.5)
        cr = math.cos(roll * 0.5)
        sr = math.sin(roll * 0.5)

        q = [0] * 4
        q[0] = sr * cp * cy - cr * sp * sy # x
        q[1] = cr * sp * cy + sr * cp * sy # y
        q[2] = cr * cp * sy - sr * sp * cy # z
        q[3] = cr * cp * cy + sr * sp * sy # w
        return q

    def watchdog_check(self):
        """Check command timeout; if timed out, stop sending commands and let ESP32 take over"""
        time_since_last_cmd = (self.get_clock().now() - self.last_cmd_time).nanoseconds / 1e9
        if time_since_last_cmd > self.cmd_watchdog_timeout:
            # Send nothing on timeout, ESP32 SERIAL_TIMEOUT will trigger
            # This is safer than sending stop command as it allows ESP32 to revert to SBUS control
            pass


def main(args=None):
    rclpy.init(args=args)
    node = OsrbotCore()
    
    # Create a timer to perform watchdog checks
    watchdog_timer = node.create_timer(0.1, node.watchdog_check)

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
    
