#!/usr/bin/env python3

import math
import threading
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

import serial
from serial import SerialException

from ackermann_msgs.msg import AckermannDrive
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu
from std_msgs.msg import Float32MultiArray, Int32MultiArray
from tf_transformations import quaternion_from_euler

from tf2_ros import TransformBroadcaster


DEFAULT_SERIAL_DEVICE = "/dev/osrbot_base"
DEFAULT_SERIAL_BAUDRATE = 115200
DEFAULT_BASE_FRAME = "base_link"
DEFAULT_ODOM_FRAME = "odom"
DEFAULT_IMU_FRAME = "imu_link"
DEFAULT_PUBLISH_TF = False

SERIAL_READ_SIZE = 512
MAX_LINE_BYTES = 512


class HighPerformanceSerial:
    """Small thread-safe wrapper around pyserial."""

    def __init__(self, port, baudrate, timeout=0.05):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_conn = None
        self.write_lock = threading.Lock()
        self._is_open = False

    def open(self):
        try:
            if self._is_open:
                self.close()

            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
                write_timeout=0.1,
            )
            self._is_open = self.serial_conn.is_open

            if self._is_open:
                self.serial_conn.reset_input_buffer()
                self.serial_conn.reset_output_buffer()

            return self._is_open
        except SerialException as exc:
            print(f"打开串口失败: {exc}")
            self._is_open = False
            return False

    def close(self):
        try:
            if self.serial_conn and self.serial_conn.is_open:
                self.serial_conn.close()
        finally:
            self._is_open = False

    def is_open(self):
        return self._is_open and self.serial_conn and self.serial_conn.is_open

    def read(self, size=SERIAL_READ_SIZE):
        try:
            if self.is_open():
                return self.serial_conn.read(size)
        except Exception as exc:
            print(f"串口读取错误: {exc}")
            self._is_open = False
        return b""

    def write(self, data: bytes):
        with self.write_lock:
            try:
                if self.is_open():
                    sent = self.serial_conn.write(data)
                    self.serial_conn.flush()
                    return sent
            except Exception as exc:
                print(f"串口写入错误: {exc}")
                self._is_open = False
        return 0


class TextLineParser:
    """Line parser for osrcore Arduino-style text protocol.

    Supported frames:
      i qx qy qz qw ax ay az gx gy gz
      o px py pz vx vy vz yaw
      m mx my mz
      r ch0 ch1 ... ch9
    """

    def __init__(self):
        self.buffer = bytearray()

    def add_data(self, data: bytes):
        if not data:
            return
        self.buffer.extend(data)
        if len(self.buffer) > MAX_LINE_BYTES * 4:
            # Keep the latest partial data only; old bytes are unrecoverable noise.
            self.buffer = self.buffer[-MAX_LINE_BYTES:]

    def parse_lines(self):
        lines = []
        while True:
            newline_pos = self.buffer.find(b"\n")
            if newline_pos < 0:
                # Drop overlong partial line instead of letting bad bytes poison parsing.
                if len(self.buffer) > MAX_LINE_BYTES:
                    self.buffer.clear()
                break

            raw_line = bytes(self.buffer[:newline_pos])
            del self.buffer[:newline_pos + 1]

            raw_line = raw_line.strip(b"\r\t ")
            if not raw_line:
                continue

            try:
                line = raw_line.decode("utf-8", errors="strict").strip()
            except UnicodeDecodeError:
                continue

            if line:
                lines.append(line)
        return lines

    def clear(self):
        self.buffer.clear()


class OsrbotCore(Node):
    def __init__(self):
        super().__init__("osrbot_chassis")
        self.init_done = False
        self.running = True

        self.serial_port = self.declare_parameter("serial_port", DEFAULT_SERIAL_DEVICE).value
        self.serial_baudrate = int(self.declare_parameter("serial_baudrate", DEFAULT_SERIAL_BAUDRATE).value)

        self.serial_interface = HighPerformanceSerial(
            self.serial_port,
            self.serial_baudrate,
            timeout=0.05,
        )
        self.line_parser = TextLineParser()

        self.line_count = 0
        self.error_count = 0
        self.last_stat_time = time.time()
        self.last_rx_time = time.time()
        self.communication_timeout = False

        self.managed_timers = []
        self.serial_thread = None

        self.setup_timers()
        self.open_serial()
        self.init_done = True

    def setup_timers(self):
        self.cleanup_timers()
        self.managed_timers.append(self.create_timer(0.5, self.communication_error_callback))
        self.managed_timers.append(self.create_timer(5.0, self.stat_callback))
        self.managed_timers.append(self.create_timer(10.0, self.serial_health_check))

    def cleanup_timers(self):
        for timer in self.managed_timers:
            try:
                timer.cancel()
                timer.destroy()
            except Exception:
                pass
        self.managed_timers.clear()

    def open_serial(self):
        if self.serial_interface.open():
            if self.serial_thread and self.serial_thread.is_alive():
                self.serial_thread.join(timeout=1.0)

            self.serial_thread = threading.Thread(target=self.serial_receive_thread, daemon=True)
            self.serial_thread.start()
            self.get_logger().info(f"串口打开成功: {self.serial_port}@{self.serial_baudrate}")
            self.communication_timeout = False
            self.last_rx_time = time.time()
        else:
            self.get_logger().warning("串口打开失败，2秒后重试")
            if not hasattr(self, "_reconnect_timer"):
                self._reconnect_timer = self.create_timer(2.0, self.reconnect_serial)
                self.managed_timers.append(self._reconnect_timer)

    def reconnect_serial(self):
        if not self.serial_interface.is_open():
            self.get_logger().info("尝试重新连接串口...")
            self.open_serial()

    def serial_health_check(self):
        if not self.serial_interface.is_open():
            self.get_logger().warning("串口连接断开，尝试重连")
            self.open_serial()
            return

        current_time = time.time()
        if current_time - self.last_rx_time > 2.0 and not self.communication_timeout:
            self.get_logger().warning("串口接收超时")
            self.communication_timeout = True

    def serial_receive_thread(self):
        consecutive_errors = 0
        max_consecutive_errors = 5

        while self.running and rclpy.ok():
            try:
                data = self.serial_interface.read(SERIAL_READ_SIZE)
                if data:
                    consecutive_errors = 0
                    self.line_parser.add_data(data)
                    for line in self.line_parser.parse_lines():
                        self.handle_serial_line(line)
                else:
                    time.sleep(0.001)
            except Exception as exc:
                consecutive_errors += 1
                self.error_count += 1
                if consecutive_errors >= max_consecutive_errors:
                    self.get_logger().error(f"串口接收连续错误，尝试重连: {exc}")
                    self.serial_interface.close()
                    break
                time.sleep(0.01)

    def handle_serial_line(self, line: str):
        try:
            if not self.init_done:
                return

            self.line_count += 1
            self.osrbot_data_proc(line)
            self.communication_timeout = False
            self.last_rx_time = time.time()
        except Exception as exc:
            self.error_count += 1
            self.get_logger().warning(f"处理串口行异常: {line!r}, error: {exc}")

    def communication_error_callback(self):
        if self.communication_timeout:
            self.get_logger().warning("串口通信超时，尝试恢复")
            self.reconnect_serial()

    def stat_callback(self):
        now = time.time()
        elapsed = now - self.last_stat_time
        line_rate = self.line_count / elapsed if elapsed > 0 else 0.0
        self.get_logger().info(
            f"串口统计: {self.line_count}行, {line_rate:.1f}行/秒, "
            f"错误: {self.error_count}, 超时: {self.communication_timeout}"
        )
        self.line_count = 0
        self.error_count = 0
        self.last_stat_time = now

    def serial_send(self, data: bytes):
        if not self.serial_interface.is_open():
            return -1
        sent = self.serial_interface.write(data)
        return 0 if sent == len(data) else -1

    def osrbot_data_proc(self, line: str):
        raise NotImplementedError("Subclasses must implement this method")

    def destroy_node(self):
        self.get_logger().info("正在关闭节点...")
        self.running = False

        if self.serial_thread and self.serial_thread.is_alive():
            self.serial_thread.join(timeout=2.0)

        self.serial_interface.close()
        self.cleanup_timers()
        self.line_parser.clear()
        super().destroy_node()


class OsrbotChassis(OsrbotCore):
    def __init__(self):
        super().__init__()

        self.base_frame = self.declare_parameter("base_frame", DEFAULT_BASE_FRAME).value
        self.odom_frame = self.declare_parameter("odom_frame", DEFAULT_ODOM_FRAME).value
        self.imu_frame = self.declare_parameter("imu_frame", DEFAULT_IMU_FRAME).value
        self.publish_tf = bool(self.declare_parameter("publish_tf", DEFAULT_PUBLISH_TF).value)

        self.odom_pub = self.create_publisher(Odometry, "odom", 10)
        self.imu_pub = self.create_publisher(Imu, "imu", 10)
        self.mag_pub = self.create_publisher(Float32MultiArray, "magnetometer_data", 10)
        self.rc_pub = self.create_publisher(Int32MultiArray, "rc_data", 10)

        self.tf_broadcaster = TransformBroadcaster(self)
        self.publisher_init_done = True

    def osrbot_data_proc(self, line: str):
        if not self.publisher_init_done:
            return

        parts = line.split()
        if not parts:
            return

        frame = parts[0].lower()

        try:
            if frame == "i":
                self.handle_imu_values([float(x) for x in parts[1:]])
            elif frame == "o":
                self.handle_odom_values([float(x) for x in parts[1:]])
            elif frame == "m":
                self.handle_mag_values([float(x) for x in parts[1:]])
            elif frame == "r":
                self.handle_rc_values([int(x) for x in parts[1:]])
            elif frame.startswith(("ok", "info", "warn", "status", "pid", "error")):
                self.get_logger().debug(line)
            else:
                self.get_logger().debug(f"忽略未知串口行: {line}")
        except ValueError as exc:
            self.error_count += 1
            self.get_logger().warning(f"串口数据解析失败: {line!r}, error: {exc}")

    def handle_odom_values(self, values):
        if len(values) < 7:
            self.get_logger().warning(f"里程计字段不足: {values}")
            return

        x, y, z, lin_x, lin_y, lin_z, yaw = values[:7]

        odom_msg = Odometry()
        current_time = self.get_clock().now().to_msg()
        odom_msg.header.stamp = current_time
        odom_msg.header.frame_id = self.odom_frame
        odom_msg.child_frame_id = self.base_frame

        odom_msg.pose.pose.position.x = x
        odom_msg.pose.pose.position.y = y
        odom_msg.pose.pose.position.z = z

        quat = quaternion_from_euler(0.0, 0.0, yaw)
        odom_msg.pose.pose.orientation.x = quat[0]
        odom_msg.pose.pose.orientation.y = quat[1]
        odom_msg.pose.pose.orientation.z = quat[2]
        odom_msg.pose.pose.orientation.w = quat[3]

        odom_msg.twist.twist.linear.x = lin_x
        odom_msg.twist.twist.linear.y = lin_y
        odom_msg.twist.twist.linear.z = lin_z
        odom_msg.twist.twist.angular.z = 0.0

        self.odom_pub.publish(odom_msg)

        if self.publish_tf:
            transform = TransformStamped()
            transform.header.stamp = current_time
            transform.header.frame_id = self.odom_frame
            transform.child_frame_id = self.base_frame
            transform.transform.translation.x = x
            transform.transform.translation.y = y
            transform.transform.translation.z = z
            transform.transform.rotation.x = quat[0]
            transform.transform.rotation.y = quat[1]
            transform.transform.rotation.z = quat[2]
            transform.transform.rotation.w = quat[3]
            self.tf_broadcaster.sendTransform(transform)

    def handle_imu_values(self, values):
        if len(values) < 10:
            self.get_logger().warning(f"IMU字段不足: {values}")
            return

        qx, qy, qz, qw, acc_x, acc_y, acc_z, ang_x, ang_y, ang_z = values[:10]

        imu_msg = Imu()
        current_time = self.get_clock().now().to_msg()
        imu_msg.header.stamp = current_time
        imu_msg.header.frame_id = self.imu_frame

        imu_msg.orientation.x = qx
        imu_msg.orientation.y = qy
        imu_msg.orientation.z = qz
        imu_msg.orientation.w = qw

        imu_msg.angular_velocity.x = ang_x
        imu_msg.angular_velocity.y = ang_y
        imu_msg.angular_velocity.z = ang_z

        imu_msg.linear_acceleration.x = acc_x
        imu_msg.linear_acceleration.y = acc_y
        imu_msg.linear_acceleration.z = acc_z

        self.imu_pub.publish(imu_msg)

    def handle_mag_values(self, values):
        if len(values) < 3:
            self.get_logger().warning(f"磁力计字段不足: {values}")
            return

        msg = Float32MultiArray()
        msg.data = [float(values[0]), float(values[1]), float(values[2])]
        self.mag_pub.publish(msg)

    def handle_rc_values(self, values):
        if len(values) < 10:
            self.get_logger().warning(f"RC字段不足: {values}")
            return

        msg = Int32MultiArray()
        msg.data = [int(x) for x in values[:10]]
        self.rc_pub.publish(msg)


class OsrbotAckermann(OsrbotChassis):
    def __init__(self):
        super().__init__()

        self.ackermann_sub = self.create_subscription(
            AckermannDrive,
            "ackermann_cmd",
            self.ackermann_callback,
            10,
        )

        self.init_done = True

    def ackermann_callback(self, msg):
        try:
            speed = float(msg.speed)
            steering_angle = float(msg.steering_angle)
            if not math.isfinite(speed) or not math.isfinite(steering_angle):
                return

            # osrcore text protocol, compatible with the original Arduino version:
            #   v <vx_mps> <steering_deg>\n
            cmd = f"v {speed:.4f} {steering_angle:.4f}\n".encode("utf-8")
            if self.serial_send(cmd) != 0:
                self.get_logger().warning("发送Ackermann字符串命令失败")
        except Exception as exc:
            self.get_logger().warning(f"发送Ackermann命令失败: {exc}")


def main():
    rclpy.init()

    try:
        node = OsrbotAckermann()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except ExternalShutdownException:
        pass
    except Exception as exc:
        print(f"程序异常: {exc}")
    finally:
        if "node" in locals():
            node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
