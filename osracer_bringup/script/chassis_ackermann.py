#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile

from geometry_msgs.msg import Twist
from sensor_msgs.msg import Imu, MagneticField
from nav_msgs.msg import Odometry
from tf2_ros import TransformBroadcaster
from geometry_msgs.msg import TransformStamped
from ackermann_msgs.msg import AckermannDrive
from std_msgs.msg import Int32MultiArray  # Add RC data message type

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
        self.declare_parameter('imu_frame', 'imu_link')
        self.declare_parameter('wheelbase', 0.285)  # Wheelbase, unit: meters
        self.declare_parameter('max_steering_angle_deg', 30.0) # Maximum steering angle, unit: degrees
        self.declare_parameter('cmd_watchdog_timeout_s', 0.5) # Command watchdog timeout
        self.declare_parameter('reconnect_interval_s', 2.0) # Serial reconnect interval
        
        # TF publishing toggle parameters
        self.declare_parameter('publish_tf', True)  # Whether to publish TF transforms
        
        # RC topic parameters
        self.declare_parameter('publish_rc', True)  # Whether to publish RC data
        self.declare_parameter('rc_topic', 'rc_data')  # RC topic name
        
        # Magnetometer topic parameters
        self.declare_parameter('publish_mag', True)  # Whether to publish magnetometer data
        self.declare_parameter('mag_topic', 'magnetometer_data')  # Magnetometer topic name
        self.declare_parameter('mag_frame', 'imu_link')  # Reference frame for magnetometer data

        self.port_name = self.get_parameter('port_name').value
        self.baud_rate = self.get_parameter('baud_rate').value
        self.odom_frame = self.get_parameter('odom_frame').value
        self.base_frame = self.get_parameter('base_frame').value
        self.imu_frame = self.get_parameter('imu_frame').value
        self.wheelbase = self.get_parameter('wheelbase').value
        self.max_steering_angle_deg = self.get_parameter('max_steering_angle_deg').value
        self.cmd_watchdog_timeout = self.get_parameter('cmd_watchdog_timeout_s').value
        self.reconnect_interval_s = self.get_parameter('reconnect_interval_s').value
        
        # TF publication toggle
        self.publish_tf = self.get_parameter('publish_tf').value
        
        # RC topic parameters
        self.publish_rc = self.get_parameter('publish_rc').value
        self.rc_topic = self.get_parameter('rc_topic').value
        
        # Magnetometer topic parameters
        self.publish_mag = self.get_parameter('publish_mag').value
        self.mag_topic = self.get_parameter('mag_topic').value
        self.mag_frame = self.get_parameter('mag_frame').value

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
        
        # New AckermannDrive subscriber
        self.ackermann_cmd_sub = self.create_subscription(
            AckermannDrive,
            'ackermann_cmd',
            self.ackermann_cmd_callback,
            10)
            
        # Use Best Practice QoS
        odom_qos = QoSProfile(depth=10)
        imu_qos = QoSProfile(depth=10)
        mag_qos = QoSProfile(depth=10)
        rc_qos = QoSProfile(depth=10)

        # Existing Publishers
        self.imu_pub = self.create_publisher(Imu, 'imu/data', qos_profile=imu_qos)
        self.odom_pub = self.create_publisher(Odometry, 'odom', qos_profile=odom_qos)
        
        # Create TF Broadcaster based on parameters
        if self.publish_tf:
            self.tf_broadcaster = TransformBroadcaster(self)
            self.get_logger().info("TF publication enabled")
        else:
            self.tf_broadcaster = None
            self.get_logger().info("TF publication disabled")
        
        # Create RC data publisher based on parameters
        if self.publish_rc:
            self.rc_pub = self.create_publisher(Int32MultiArray, self.rc_topic, qos_profile=rc_qos)
            self.get_logger().info(f"RC data publication enabled, topic: {self.rc_topic}")
        else:
            self.rc_pub = None
            self.get_logger().info("RC data publication disabled")
        
        # Create Magnetometer data publisher based on parameters
        if self.publish_mag:
            self.mag_pub = self.create_publisher(MagneticField, self.mag_topic, qos_profile=mag_qos)
            self.get_logger().info(f"Magnetometer data publication enabled, topic: {self.mag_topic}")
        else:
            self.mag_pub = None
            self.get_logger().info("Magnetometer data publication disabled")

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
        # When linear velocity is near zero, set steering to max based on angular velocity (for tight turns)
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
                # IMU Data: i qx qy qz qw ax ay az gx gy gz
                q_x, q_y, q_z, q_w = map(float, parts[1:5])
                a_x, a_y, a_z = map(float, parts[5:8])
                g_x, g_y, g_z = map(float, parts[8:11])

                imu_msg = Imu()
                imu_msg.header.stamp = self.get_clock().now().to_msg()
                imu_msg.header.frame_id = self.imu_frame

                # Quaternion
                imu_msg.orientation.x = q_x
                imu_msg.orientation.y = q_y
                imu_msg.orientation.z = q_z
                imu_msg.orientation.w = q_w

                # Linear Acceleration
                imu_msg.linear_acceleration.x = a_x
                imu_msg.linear_acceleration.y = a_y
                imu_msg.linear_acceleration.z = a_z

                # Angular Velocity
                imu_msg.angular_velocity.x = g_x
                imu_msg.angular_velocity.y = g_y
                imu_msg.angular_velocity.z = g_z

                self.imu_pub.publish(imu_msg)

            elif cmd_type == 'o' and len(parts) == 8:
                # Odometry Data: o px py pz vx vy vz w
                p_x, p_y, p_z = map(float, parts[1:4])
                v_x, v_y, v_z = map(float, parts[4:7])
                yaw = float(parts[7])

                # Create Odometry message
                odom_msg = Odometry()
                odom_msg.header.stamp = self.get_clock().now().to_msg()
                odom_msg.header.frame_id = self.odom_frame
                odom_msg.child_frame_id = self.base_frame

                # Set Position
                odom_msg.pose.pose.position.x = p_x
                odom_msg.pose.pose.position.y = p_y
                odom_msg.pose.pose.position.z = p_z

                # Create Quaternion from Yaw
                q = self.quaternion_from_euler(0, 0, yaw)
                odom_msg.pose.pose.orientation.x = q[0]
                odom_msg.pose.pose.orientation.y = q[1]
                odom_msg.pose.pose.orientation.z = q[2]
                odom_msg.pose.pose.orientation.w = q[3]

                # Set Velocity
                odom_msg.twist.twist.linear.x = v_x
                odom_msg.twist.twist.linear.y = v_y
                odom_msg.twist.twist.linear.z = v_z
                odom_msg.twist.twist.angular.x = 0.0
                odom_msg.twist.twist.angular.y = 0.0
                odom_msg.twist.twist.angular.z = 0.0

                self.odom_pub.publish(odom_msg)

                # Broadcast TF transform based on parameters
                if self.publish_tf and self.tf_broadcaster:
                    t = TransformStamped()
                    t.header.stamp = odom_msg.header.stamp
                    t.header.frame_id = self.odom_frame
                    t.child_frame_id = self.base_frame
                    
                    t.transform.translation.x = p_x
                    t.transform.translation.y = p_y
                    t.transform.translation.z = p_z
                    t.transform.rotation = odom_msg.pose.pose.orientation
                    
                    self.tf_broadcaster.sendTransform(t)
            
            elif cmd_type == 'r' and self.publish_rc and self.rc_pub:
                # RC Data: r ch1 ch2 ch3 ...
                # Convert string values to integers
                int_values = [int(val) for val in parts[1:]]
                
                # Create RC message
                rc_msg = Int32MultiArray()
                rc_msg.data = int_values
                
                # Publish RC data
                self.rc_pub.publish(rc_msg)
                
            elif cmd_type == 'm' and self.publish_mag and self.mag_pub:
                # Magnetometer Data: m x y z
                if len(parts) == 4:
                    x_gauss = float(parts[1])
                    y_gauss = float(parts[2])
                    z_gauss = float(parts[3])
                    
                    # Convert to Tesla (1 Gauss = 1e-4 Tesla)
                    x_tesla = x_gauss * 1e-4
                    y_tesla = y_gauss * 1e-4
                    z_tesla = z_gauss * 1e-4
                    
                    mag_msg = MagneticField()
                    mag_msg.header.stamp = self.get_clock().now().to_msg()
                    mag_msg.header.frame_id = self.mag_frame
                    mag_msg.magnetic_field.x = x_tesla
                    mag_msg.magnetic_field.y = y_tesla
                    mag_msg.magnetic_field.z = z_tesla
                    
                    self.mag_pub.publish(mag_msg)
                    
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
        """Check for command timeout; stop robot and send stop command if timed out"""
        time_since_last_cmd = (self.get_clock().now() - self.last_cmd_time).nanoseconds / 1e9
        if time_since_last_cmd > self.cmd_watchdog_timeout:
            # Send stop command
            command = "v 0.00 0.00\n"
            self.write_serial(command)


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
        node.close_serial()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
