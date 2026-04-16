#!/usr/bin/env python3

import sys

import rclpy
import serial
import serial.tools.list_ports
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import String


class LedMatrixNode(Node):
    def __init__(self):
        super().__init__('led_matrix_node')
        self.port = self.declare_parameter('port', '/dev/ttyACM0').value
        self.baud = self.declare_parameter('baudrate', 115200).value
        self.topic = self.declare_parameter('input_topic', 'led_matrix/command').value
        self.append_newline = self.declare_parameter('append_newline', True).value

        if not self.open_port():
            self.get_logger().error(f"Failed to open serial port {self.port}")
            raise RuntimeError("open serial failed")

        self.sub = self.create_subscription(String, self.topic, self.cb_write, 1)
        self.get_logger().info(
            f"LedMatrix ready: port={self.port} baud={self.baud} topic={self.topic}"
        )

    def open_port(self):
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,
                xonxoff=False,
                rtscts=False,
            )
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            return True
        except serial.SerialException as e:
            self.get_logger().error(f"Serial open failed: {e}")
            return False
        except Exception as e:
            self.get_logger().error(f"Unexpected error: {e}")
            return False

    def cb_write(self, msg):
        try:
            data = msg.data
            if self.append_newline and (not data or data[-1] != '\n'):
                data += '\n'
            self.ser.write(data.encode('utf-8'))
        except serial.SerialException as e:
            self.get_logger().error(f"Serial write failed: {e}")
        except Exception as e:
            self.get_logger().error(f"Unexpected error in cb_write: {e}")

    def close(self):
        if hasattr(self, 'ser') and self.ser and self.ser.is_open:
            self.ser.close()


def main(args=None):
    rclpy.init(args=args)
    try:
        node = LedMatrixNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException, Exception) as e:
        print(f"\nException: {e}")
        return 1
    finally:
        if 'node' in locals():
            node.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
