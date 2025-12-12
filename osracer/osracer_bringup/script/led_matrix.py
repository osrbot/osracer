#!/usr/bin/env python3

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import String
import serial
import serial.tools.list_ports
import sys

class LedMatrixNode(Node):
    def __init__(self):
        super().__init__('led_matrix_node')
        # 读取参数（私有命名空间）
        self.port = self.declare_parameter('~port', '/dev/osrbot_led_matrix').value
        self.baud = self.declare_parameter('~baudrate', 115200).value
        self.topic = self.declare_parameter('~input_topic', 'led_matrix/command').value
        self.append_newline = self.declare_parameter('~append_newline', True).value
        
        # 打开串口
        if not self.open_port():
            self.get_logger().error("Failed to open serial port %s" % self.port)
            raise RuntimeError("open serial failed")
        
        # 创建订阅者
        self.sub = self.create_subscription(String, self.topic, self.cb_write, 1)
        
        self.get_logger().info("LedMatrix ready: port=%s baud=%d topic=%s", 
                     self.port, self.baud, self.topic)
    
    def open_port(self):
        """打开并配置串口"""
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,  # 读取超时时间
                xonxoff=False,
                rtscts=False
            )
            
            # 清空缓冲区
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            
            return True
            
        except serial.SerialException as e:
            self.get_logger().error("Serial open failed: %s" % str(e))
            return False
        except Exception as e:
            self.get_logger().error("Unexpected error: %s"% str(e))
            return False
    
    def cb_write(self, msg):
        """回调函数：向串口写入数据"""
        try:
            data = msg.data
            if self.append_newline and (not data or data[-1] != '\n'):
                data += '\n'
            
            self.ser.write(data.encode('utf-8'))
            
        except serial.SerialException as e:
            self.get_logger().error("Serial write failed: %s", str(e))
        except Exception as e:
            self.get_logger().error("Unexpected error in cb_write: %s", str(e))
    
    def close(self):
        """关闭串口"""
        if hasattr(self, 'ser') and self.ser and self.ser.is_open:
            self.ser.close()

def main(args=None):
    rclpy.init(args=args)
    try:
        node = LedMatrixNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException, Exception) as e:
        print("\nException: %s" % str(e))
        return 1
    finally:
        # 确保串口被正确关闭
        if 'node' in locals():
            node.close()
        pass
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
