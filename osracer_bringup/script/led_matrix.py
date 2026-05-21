#!/usr/bin/env python3

import sys
import os

import rclpy
import serial
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import String, UInt8MultiArray


class LedMatrixNode(Node):
    def __init__(self):
        super().__init__('led_matrix_node')

        self.port = self.declare_parameter('serial_port', '/dev/ttyACM1').value
        self.baud = self.declare_parameter('serial_baudrate', 115200).value
        self.topic = self.declare_parameter('input_topic', 'led_matrix/command').value
        self.columns_topic = self.declare_parameter('columns_topic', 'led_matrix/columns').value
        self.append_newline = self.declare_parameter('append_newline', True).value
        self.num_matrices = self.declare_parameter('num_matrices', 2).value
        self.auto_newline = self.declare_parameter('auto_newline', True).value
        self.debug = self.declare_parameter('debug', False).value
        self.reconnect_interval_s = self.declare_parameter('reconnect_interval_s', 2.0).value

        self.num_matrices = max(1, min(4, self.num_matrices))
        self.total_columns = self.num_matrices * 8
        self.ser = None
        self.serial_connected = False

        if self.open_port():
            self.init_matrix_config()
        else:
            self.get_logger().warning(
                f"Serial port {self.port} is not available; reconnecting every "
                f"{self.reconnect_interval_s:.1f}s"
            )

        self.sub = self.create_subscription(String, self.topic, self.cb_command, 1)
        self.columns_sub = self.create_subscription(UInt8MultiArray, self.columns_topic, self.cb_columns, 1)
        self.reconnect_timer = self.create_timer(self.reconnect_interval_s, self.reconnect)

        self.get_logger().info(
            f"LedMatrix ready: port={self.port} baud={self.baud} "
            f"cmd_topic={self.topic} columns_topic={self.columns_topic} "
            f"matrices={self.num_matrices} total_columns={self.total_columns}"
        )
        self.get_logger().info("Note: bit7 = bottom row, bit0 = top row (MSB is lowest row)")

    def open_port(self):
        if self.ser and self.ser.is_open and self.port_available():
            return True
        if self.ser and not self.port_available():
            self.mark_disconnected(f"Serial port disappeared: {self.port}")

        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,
                write_timeout=0.1,
                xonxoff=False,
                rtscts=False,
            )
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            self.serial_connected = True
            self.get_logger().info(f"Serial port opened: {self.port}")
            return True
        except (serial.SerialException, OSError, ValueError) as e:
            self.ser = None
            self.serial_connected = False
            self.get_logger().error(f"Serial open failed: {e}")
            return False
        except Exception as e:
            self.ser = None
            self.serial_connected = False
            self.get_logger().error(f"Unexpected serial open error: {e}")
            return False

    def port_available(self):
        if not self.port.startswith('/'):
            return True
        return os.path.exists(self.port)

    def reconnect(self):
        if self.ser and self.ser.is_open and self.port_available():
            return

        if self.ser and not self.port_available():
            self.mark_disconnected(f"Serial port disappeared: {self.port}")

        if not self.port_available():
            return

        if self.open_port():
            self.init_matrix_config()

    def mark_disconnected(self, reason=None, log=True):
        was_connected = self.serial_connected
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.serial_connected = False
        if log and was_connected:
            if reason:
                self.get_logger().warning(f"Serial port disconnected: {reason}")
            else:
                self.get_logger().warning("Serial port disconnected")

    def send_command(self, cmd):
        if not self.ser or not self.ser.is_open:
            self.get_logger().warning("Serial port is disconnected; attempting reconnect")
            self.reconnect()
            if not self.ser or not self.ser.is_open:
                self.get_logger().warning("Reconnect failed; dropping LED command")
                return False

        try:
            self.ser.write(cmd.encode('utf-8'))
            self.ser.flush()
            if self.debug:
                self.get_logger().debug(f"Sent: {cmd.strip()}")
            return True
        except (serial.SerialException, OSError, ValueError) as e:
            self.get_logger().error(f"Serial write failed: {e}")
            self.mark_disconnected(f"write failed: {e}")
        except Exception as e:
            self.get_logger().error(f"Unexpected error in send_command: {e}")
            self.mark_disconnected(f"unexpected write error: {e}")
        return False

    def init_matrix_config(self):
        cmd = f"N {self.num_matrices}#\n"
        if self.send_command(cmd):
            self.get_logger().info(
                f"Matrix configured: {self.num_matrices} modules, "
                f"{self.total_columns} columns"
            )

    def cb_command(self, msg):
        try:
            data = msg.data
            if self.append_newline and (not data or data[-1] != '\n'):
                data += '\n'
            self.send_command(data)
        except Exception as e:
            self.get_logger().error(f"Error in cb_command: {e}")

    def cb_columns(self, msg):
        try:
            columns = list(msg.data)

            if len(columns) > self.total_columns:
                columns = columns[:self.total_columns]
                if self.debug:
                    self.get_logger().debug(f"Truncated to {self.total_columns} columns")
            elif len(columns) < self.total_columns:
                columns.extend([0] * (self.total_columns - len(columns)))
                if self.debug:
                    self.get_logger().debug(f"Padded to {self.total_columns} columns")

            cmd = self.build_p_command_compact(columns)
            if self.auto_newline:
                cmd += '\n'
            self.send_command(cmd)

            if self.debug:
                preview = ', '.join([f"0x{col:02X}" for col in columns[:8]])
                if len(columns) > 8:
                    preview += "..."
                self.get_logger().debug(f"Columns sent: [{preview}]")
                self.get_logger().debug(f"Total columns: {len(columns)}")
        except Exception as e:
            self.get_logger().error(f"Error in cb_columns: {e}")

    def build_p_command_compact(self, columns):
        hex_string = ''.join([f"{col:02X}" for col in columns])
        return f"P{hex_string}#"

    def set_brightness(self, brightness):
        if 0 <= brightness <= 15:
            cmd = f"L {brightness}#\n"
            self.send_command(cmd)
            self.get_logger().info(f"Brightness set to {brightness}")
        else:
            self.get_logger().warning(f"Invalid brightness: {brightness}, must be 0-15")

    def set_scroll_speed(self, speed_ms):
        if 10 <= speed_ms <= 1000:
            cmd = f"S {speed_ms}#\n"
            self.send_command(cmd)
            self.get_logger().info(f"Scroll speed set to {speed_ms}ms")
        else:
            self.get_logger().warning(f"Invalid speed: {speed_ms}, must be 10-1000")

    def show_text(self, text):
        cmd = f"{text}\n"
        self.send_command(cmd)
        self.get_logger().info(f"Display text: {text}")

    def clear_display(self):
        columns = [0] * self.total_columns
        cmd = self.build_p_command_compact(columns) + "\n"
        self.send_command(cmd)
        self.get_logger().info("Display cleared")

    def close(self):
        self.mark_disconnected(log=False)
        self.get_logger().info("Serial port closed")


def main(args=None):
    rclpy.init(args=args)
    try:
        node = LedMatrixNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as e:
        print(f"\nException: {e}")
        return 1
    finally:
        if 'node' in locals():
            node.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
