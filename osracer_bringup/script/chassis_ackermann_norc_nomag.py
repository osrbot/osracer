#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile

from geometry_msgs.msg import Twist
from sensor_msgs.msg import Imu
from nav_msgs.msg import Odometry
from ackermann_msgs.msg import AckermannDrive  # Import AckermannDrive message type

import serial
import termios
import math
import threading
import os

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
        self.declare_parameter('reconnect_interval_s', 2.0) # Serial reconnect interval

        self.port_name = self.get_parameter('port_name').value
        self.baud_rate = self.get_parameter('baud_rate').value
        self.odom_frame = self.get_parameter('odom_frame').value
        self.base_frame = self.get_parameter('base_frame').value
        self.imu_frame = self.get_parameter('imu_frame').value  # Get IMU frame parameter
        self.wheelbase = self.get_parameter('wheelbase').value
        self.max_steering_angle_deg = self.get_parameter('max_steering_angle_deg').value
        self.cmd_watchdog_timeout = self.get_parameter('cmd_watchdog_timeout_s').value
        self.reconnect_interval_s = self.get_parameter('reconnect_interval_s').value

        # --- Serial state ---
        self.serial = None
        self.serial_lock = threading.Lock()
        self.read_thread = None

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

        # --- State Variables ---
        self.last_cmd_time = self.get_clock().now()
        self.reconnect_timer = self.create_timer(self.reconnect_interval_s, self.reconnect_serial)
        self.open_serial()

        self.get_logger().info("Vehicle bridge node started.")

    def open_serial(self):
        """Open the serial port and start the read thread when available."""
        stale_conn = None
        with self.serial_lock:
            if self.serial and self.serial.is_open and self.port_available():
                return True
            if self.serial and not self.port_available():
                stale_conn = self.serial
                self.serial = None

        try:
            if stale_conn and stale_conn.is_open:
                stale_conn.close()
        except Exception:
            pass

        with self.serial_lock:
            try:
                self.serial = serial.Serial(
                    self.port_name,
                    self.baud_rate,
                    timeout=0.1,
                    write_timeout=0.1
                )
                self.serial.reset_input_buffer()
                self.serial.reset_output_buffer()
            except (serial.SerialException, OSError, ValueError) as e:
                self.serial = None
                self.get_logger().warning(
                    f"Could not open serial port '{self.port_name}': {e}; "
                    f"retrying in {self.reconnect_interval_s:.1f}s"
                )
                return False

        self.get_logger().info(f"Successfully opened serial port: {self.port_name}")
        self.start_read_thread()
        return True

    def port_available(self):
        if not self.port_name.startswith('/'):
            return True
        return os.path.exists(self.port_name)

    def start_read_thread(self):
        if self.read_thread and self.read_thread.is_alive():
            return
        self.read_thread = threading.Thread(target=self.read_serial_loop, daemon=True)
        self.read_thread.start()

    def close_serial(self, expected_conn=None):
        with self.serial_lock:
            if expected_conn is not None and self.serial is not expected_conn:
                serial_conn = expected_conn
            else:
                serial_conn = self.serial
                self.serial = None

        try:
            if serial_conn and serial_conn.is_open:
                serial_conn.close()
        except Exception:
            pass

    def mark_serial_failed(self, failed_conn):
        with self.serial_lock:
            if self.serial is failed_conn:
                self.serial = None

        try:
            if failed_conn and failed_conn.is_open:
                failed_conn.close()
        except Exception:
            pass

    def reconnect_serial(self):
        with self.serial_lock:
            current_serial = self.serial
            connected = current_serial is not None and current_serial.is_open and self.port_available()
        if current_serial and not self.port_available():
            self.close_serial(current_serial)
            connected = False
        if connected:
            self.start_read_thread()
        else:
            self.open_serial()

    def write_serial(self, command: str):
        failed_conn = None
        with self.serial_lock:
            if not self.serial or not self.serial.is_open:
                return False
            try:
                self.serial.write(command.encode('utf-8'))
                self.serial.flush()
                return True
            except (serial.SerialException, OSError, ValueError, TypeError, termios.error) as e:
                failed_conn = self.serial
                self.get_logger().error(f"Failed to write to serial: {e}")

        self.mark_serial_failed(failed_conn)
        return False

    def cmd_vel_callback(self, msg: Twist):
        """Convert Twist message from /cmd_vel to serial command"""
        linear_x = msg.linear.x
        angular_z = msg.angular.z

        # Convert angular velocity to steering angle
        # Formula: steering_angle = atan(wheelbase * angular_z / linear_x)
        # When linear velocity is near zero, set steering to max for on-the-spot turning
        if abs(linear_x) < 0.01:
            steering_angle_rad = 0.0 if angular_z == 0.0 else math.copysign(self.max_steering_angle_deg * math.pi / 180.0, angular_z)
        else:
            steering_angle_rad = math.atan(self.wheelbase * angular_z / linear_x)

        # Limit steering angle range
        max_steering_angle_rad = self.max_steering_angle_deg * math.pi / 180.0
        steering_angle_rad = max(-max_steering_angle_rad, min(max_steering_angle_rad, steering_angle_rad))

        # Convert to degrees
        steering_angle_deg = math.degrees(steering_angle_rad)

        # Format and send command string
        command = f"v {linear_x:.3f} {steering_angle_deg:.2f}\n"
        
        if self.write_serial(command):
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
        
        if self.write_serial(command):
            self.last_cmd_time = self.get_clock().now()

    def read_serial_loop(self):
        """Continuously read serial data in a separate thread"""
        current_thread = threading.current_thread()
        try:
            while rclpy.ok():
                with self.serial_lock:
                    serial_conn = self.serial

                if not serial_conn or not serial_conn.is_open:
                    break

                try:
                    line = serial_conn.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        self.parse_serial_data(line)
                except (serial.SerialException, OSError, ValueError, TypeError, termios.error) as e:
                    self.get_logger().error(f"Serial read error, connection might be lost: {e}")
                    self.close_serial(serial_conn)
                    break
        finally:
            with self.serial_lock:
                if self.read_thread is current_thread:
                    self.read_thread = None

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
        """Intentional no-op: ESP32 handles serial timeout and reverts to SBUS control."""
        pass


def main(args=None):
    rclpy.init(args=args)
    node = OsrbotCore()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.close_serial()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
    
