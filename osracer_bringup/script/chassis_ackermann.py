#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile

from geometry_msgs.msg import Twist
from sensor_msgs.msg import Imu, MagneticField, BatteryState
from nav_msgs.msg import Odometry
from tf2_ros import TransformBroadcaster
from geometry_msgs.msg import TransformStamped
from ackermann_msgs.msg import AckermannDrive
from std_msgs.msg import Int32MultiArray

import serial
import termios
import math
import threading
import os

class OsrbotCore(Node):
    def __init__(self):
        super().__init__('osracer_chassis_node')

        # --- Declare Parameters ---
        self.declare_parameter('port_name', '/dev/ttyACM0')
        self.declare_parameter('baud_rate', 460800)
        self.declare_parameter('odom_frame', 'odom')
        self.declare_parameter('base_frame', 'base_footprint')
        self.declare_parameter('imu_frame', 'imu_link')
        self.declare_parameter('wheelbase', 0.285)
        self.declare_parameter('max_steering_angle_deg', 30.0)
        self.declare_parameter('cmd_watchdog_timeout_s', 0.5)
        self.declare_parameter('reconnect_interval_s', 2.0)

        self.declare_parameter('publish_tf', True)
        self.declare_parameter('publish_rc', True)
        self.declare_parameter('rc_topic', 'rc_data')
        self.declare_parameter('publish_mag', True)
        self.declare_parameter('mag_topic', 'magnetometer_data')
        self.declare_parameter('mag_frame', 'imu_link')
        self.declare_parameter('imu_orientation_covariance', [0.02, 0.02, 0.05])
        self.declare_parameter('imu_angular_velocity_covariance', [0.01, 0.01, 0.01])
        self.declare_parameter('imu_linear_acceleration_covariance', [0.10, 0.10, 0.10])
        self.declare_parameter('odom_twist_covariance', [0.02, 0.20, 1.0, 1.0, 1.0, 0.30])

        self.declare_parameter('publish_battery', True)
        self.declare_parameter('battery_topic', 'battery_state')
        self.declare_parameter('battery_voltage_min', 10.8)  # 3S LiPo cutoff
        self.declare_parameter('battery_voltage_max', 12.6)  # 3S LiPo full

        # --- Get Parameters ---
        self.port_name = self.get_parameter('port_name').value
        self.baud_rate = self.get_parameter('baud_rate').value
        self.odom_frame = self.get_parameter('odom_frame').value
        self.base_frame = self.get_parameter('base_frame').value
        self.imu_frame = self.get_parameter('imu_frame').value
        self.wheelbase = self.get_parameter('wheelbase').value
        self.max_steering_angle_deg = self.get_parameter('max_steering_angle_deg').value
        self.cmd_watchdog_timeout = self.get_parameter('cmd_watchdog_timeout_s').value
        self.reconnect_interval_s = self.get_parameter('reconnect_interval_s').value

        self.publish_tf = self.get_parameter('publish_tf').value
        self.publish_rc = self.get_parameter('publish_rc').value
        self.rc_topic = self.get_parameter('rc_topic').value
        self.publish_mag = self.get_parameter('publish_mag').value
        self.mag_topic = self.get_parameter('mag_topic').value
        self.mag_frame = self.get_parameter('mag_frame').value
        self.imu_orientation_covariance = self.diagonal_covariance(
            self.get_parameter('imu_orientation_covariance').value)
        self.imu_angular_velocity_covariance = self.diagonal_covariance(
            self.get_parameter('imu_angular_velocity_covariance').value)
        self.imu_linear_acceleration_covariance = self.diagonal_covariance(
            self.get_parameter('imu_linear_acceleration_covariance').value)
        self.odom_twist_covariance = self.diagonal_covariance_6d(
            self.get_parameter('odom_twist_covariance').value)

        self.publish_battery = self.get_parameter('publish_battery').value
        self.battery_topic = self.get_parameter('battery_topic').value
        self.battery_voltage_min = self.get_parameter('battery_voltage_min').value
        self.battery_voltage_max = self.get_parameter('battery_voltage_max').value

        # --- Serial state ---
        self.serial = None
        self.serial_lock = threading.Lock()
        self.read_thread = None

        # --- QoS Profiles ---
        # Real-time for high frequency topics (odom, imu, mag)
        rt_qos = QoSProfile(depth=1)
        # Normal QoS for low frequency topics
        normal_qos = QoSProfile(depth=5)

        # --- Subscribers ---
        self.cmd_vel_sub = self.create_subscription(
            Twist, 'cmd_vel', self.cmd_vel_callback, normal_qos)
        self.ackermann_cmd_sub = self.create_subscription(
            AckermannDrive, 'ackermann_cmd', self.ackermann_cmd_callback, normal_qos)

        # --- Publishers ---
        self.imu_pub = self.create_publisher(Imu, 'imu/data', qos_profile=rt_qos)
        self.odom_pub = self.create_publisher(Odometry, 'odom', qos_profile=rt_qos)

        if self.publish_tf:
            self.tf_broadcaster = TransformBroadcaster(self)
            self.get_logger().info("TF publication enabled")
        else:
            self.tf_broadcaster = None

        if self.publish_rc:
            self.rc_pub = self.create_publisher(Int32MultiArray, self.rc_topic, qos_profile=normal_qos)
            self.get_logger().info(f"RC publication enabled, topic: {self.rc_topic}")
        else:
            self.rc_pub = None

        if self.publish_mag:
            self.mag_pub = self.create_publisher(MagneticField, self.mag_topic, qos_profile=rt_qos)
            self.get_logger().info(f"Magnetometer publication enabled, topic: {self.mag_topic}, QoS: depth=1")
        else:
            self.mag_pub = None

        if self.publish_battery:
            self.battery_pub = self.create_publisher(BatteryState, self.battery_topic, qos_profile=normal_qos)
            self.get_logger().info(f"Battery publication enabled, topic: {self.battery_topic}, range: {self.battery_voltage_min}V - {self.battery_voltage_max}V")
        else:
            self.battery_pub = None

        # --- State Variables ---
        self.last_cmd_time = self.get_clock().now()
        self.reconnect_timer = self.create_timer(self.reconnect_interval_s, self.reconnect_serial)
        self.open_serial()

        self.get_logger().info("Vehicle bridge node started.")

    # ========== Serial Management ==========
    def open_serial(self):
        """Open serial port and start read thread if available."""
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

    # ========== Command Callbacks ==========
    def cmd_vel_callback(self, msg: Twist):
        linear_x = msg.linear.x
        angular_z = msg.angular.z

        if abs(linear_x) < 0.01:
            steering_angle_rad = 0.0 if angular_z == 0.0 else math.copysign(
                self.max_steering_angle_deg * math.pi / 180.0, angular_z)
        else:
            steering_angle_rad = math.atan(self.wheelbase * angular_z / linear_x)

        max_steering_angle_rad = self.max_steering_angle_deg * math.pi / 180.0
        steering_angle_rad = max(-max_steering_angle_rad, min(max_steering_angle_rad, steering_angle_rad))
        steering_angle_deg = math.degrees(steering_angle_rad)

        command = f"v {linear_x:.3f} {steering_angle_deg:.2f}\n"
        if self.write_serial(command):
            self.last_cmd_time = self.get_clock().now()

    def ackermann_cmd_callback(self, msg: AckermannDrive):
        speed = msg.speed
        steering_angle = msg.steering_angle

        max_steering_angle_rad = self.max_steering_angle_deg * math.pi / 180.0
        steering_angle_rad = max(-max_steering_angle_rad, min(max_steering_angle_rad, steering_angle))
        steering_angle_deg = math.degrees(steering_angle_rad)

        command = f"v {speed:.3f} {steering_angle_deg:.2f}\n"
        if self.write_serial(command):
            self.last_cmd_time = self.get_clock().now()

    # ========== Serial Read Loop ==========
    def read_serial_loop(self):
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

    # ========== Parser ==========
    def parse_serial_data(self, line: str):
        try:
            parts = line.split()
            if not parts:
                return

            cmd_type = parts[0]

            # --- s-frame: synchronized data ---
            if cmd_type == 's' and len(parts) == 18:
                px, py, pz = float(parts[1]), float(parts[2]), float(parts[3])
                vx, vy, vz = float(parts[4]), float(parts[5]), float(parts[6])
                # yaw = float(parts[7])  # not used
                qx, qy, qz, qw = float(parts[8]), float(parts[9]), float(parts[10]), float(parts[11])
                ax, ay, az = float(parts[12]), float(parts[13]), float(parts[14])
                gx, gy, gz = float(parts[15]), float(parts[16]), float(parts[17])

                current_time = self.get_clock().now()
                current_stamp = current_time.to_msg()

                # --- Odometry ---
                odom_msg = Odometry()
                odom_msg.header.stamp = current_stamp
                odom_msg.header.frame_id = self.odom_frame
                odom_msg.child_frame_id = self.base_frame
                odom_msg.pose.pose.position.x = px
                odom_msg.pose.pose.position.y = py
                odom_msg.pose.pose.position.z = pz
                odom_msg.pose.pose.orientation.x = qx
                odom_msg.pose.pose.orientation.y = qy
                odom_msg.pose.pose.orientation.z = qz
                odom_msg.pose.pose.orientation.w = qw
                odom_msg.twist.twist.linear.x = vx
                odom_msg.twist.twist.linear.y = vy
                odom_msg.twist.twist.linear.z = vz
                odom_msg.twist.covariance = self.odom_twist_covariance
                self.odom_pub.publish(odom_msg)

                # --- TF ---
                if self.publish_tf and self.tf_broadcaster:
                    t = TransformStamped()
                    t.header.stamp = current_stamp
                    t.header.frame_id = self.odom_frame
                    t.child_frame_id = self.base_frame
                    t.transform.translation.x = px
                    t.transform.translation.y = py
                    t.transform.translation.z = pz
                    t.transform.rotation = odom_msg.pose.pose.orientation
                    self.tf_broadcaster.sendTransform(t)

                # --- IMU ---
                imu_msg = Imu()
                imu_msg.header.stamp = current_stamp
                imu_msg.header.frame_id = self.imu_frame
                imu_msg.orientation.x = qx
                imu_msg.orientation.y = qy
                imu_msg.orientation.z = qz
                imu_msg.orientation.w = qw
                imu_msg.linear_acceleration.x = ax
                imu_msg.linear_acceleration.y = ay
                imu_msg.linear_acceleration.z = az
                imu_msg.angular_velocity.x = gx
                imu_msg.angular_velocity.y = gy
                imu_msg.angular_velocity.z = gz
                imu_msg.orientation_covariance = self.imu_orientation_covariance
                imu_msg.angular_velocity_covariance = self.imu_angular_velocity_covariance
                imu_msg.linear_acceleration_covariance = self.imu_linear_acceleration_covariance
                self.imu_pub.publish(imu_msg)

            # --- r-frame: RC ---
            elif cmd_type == 'r' and self.publish_rc and self.rc_pub:
                int_values = [int(val) for val in parts[1:]]
                rc_msg = Int32MultiArray()
                rc_msg.data = int_values
                self.rc_pub.publish(rc_msg)

            # --- m-frame: Magnetometer ---
            elif cmd_type == 'm' and self.publish_mag and self.mag_pub:
                if len(parts) == 4:
                    x_gauss = float(parts[1])
                    y_gauss = float(parts[2])
                    z_gauss = float(parts[3])
                    # Convert Gauss to Tesla
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

            # --- b-frame: Battery ---
            elif cmd_type == 'b' and self.publish_battery and self.battery_pub:
                if len(parts) == 2:
                    voltage = float(parts[1])
                    percentage = (voltage - self.battery_voltage_min) / (self.battery_voltage_max - self.battery_voltage_min)
                    percentage = max(0.0, min(1.0, percentage))
                    battery_msg = BatteryState()
                    battery_msg.header.stamp = self.get_clock().now().to_msg()
                    battery_msg.voltage = voltage
                    battery_msg.percentage = percentage
                    self.battery_pub.publish(battery_msg)

        except (ValueError, IndexError) as e:
            self.get_logger().warn(f"Error parsing serial data: '{line}', error: {e}")

    # ========== Helpers ==========
    def diagonal_covariance(self, diagonal):
        if len(diagonal) != 3:
            raise ValueError("IMU covariance parameters must contain exactly 3 diagonal values")
        return [
            float(diagonal[0]), 0.0, 0.0,
            0.0, float(diagonal[1]), 0.0,
            0.0, 0.0, float(diagonal[2]),
        ]

    def diagonal_covariance_6d(self, diagonal):
        if len(diagonal) != 6:
            raise ValueError("Odometry twist covariance parameter must contain exactly 6 diagonal values")
        covariance = [0.0] * 36
        for index, value in enumerate(diagonal):
            covariance[index * 6 + index] = float(value)
        return covariance

    def quaternion_from_euler(self, roll, pitch, yaw):
        cy = math.cos(yaw * 0.5)
        sy = math.sin(yaw * 0.5)
        cp = math.cos(pitch * 0.5)
        sp = math.sin(pitch * 0.5)
        cr = math.cos(roll * 0.5)
        sr = math.sin(roll * 0.5)
        q = [0] * 4
        q[0] = sr * cp * cy - cr * sp * sy
        q[1] = cr * sp * cy + sr * cp * sy
        q[2] = cr * cp * sy - sr * sp * cy
        q[3] = cr * cp * cy + sr * sp * sy
        return q

    def watchdog_check(self):
        time_since_last_cmd = (self.get_clock().now() - self.last_cmd_time).nanoseconds / 1e9
        if time_since_last_cmd > self.cmd_watchdog_timeout:
            self.write_serial("v 0.00 0.00\n")

def main(args=None):
    rclpy.init(args=args)
    node = OsrbotCore()
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
