#!/usr/bin/env python3
"""
RC数据转发节点 - 从串口接收数据并转发到ROS2话题
仅保留接收和转发功能
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Int32MultiArray
import serial
import threading
from typing import Optional

class RCForwarder(Node):
    """RC数据转发节点"""
    
    def __init__(self):
        super().__init__('rc_forwarder')
        
        # 参数配置
        self.declare_parameter('serial_port', '/dev/ttyACM0')
        self.declare_parameter('baud_rate', 115200)
        self.declare_parameter('topic_name', 'rc_data_raw')
        
        # 获取参数
        serial_port = self.get_parameter('serial_port').get_parameter_value().string_value
        baud_rate = self.get_parameter('baud_rate').get_parameter_value().integer_value
        topic_name = self.get_parameter('topic_name').get_parameter_value().string_value
        
        # 创建发布者
        self.publisher = self.create_publisher(Int32MultiArray, topic_name, 10)
        
        # 初始化串口
        self.serial_port: Optional[serial.Serial] = None
        
        try:
            # 打开串口
            self.serial_port = serial.Serial(
                port=serial_port,
                baudrate=baud_rate,
                timeout=1.0  # 读取超时1秒
            )
            
            self.get_logger().info(f'已连接到串口: {serial_port}, 波特率: {baud_rate}')
            
            # 启动接收线程
            self.receive_thread = threading.Thread(target=self.receive_data, daemon=True)
            self.receive_thread.start()
            
        except Exception as e:
            self.get_logger().error(f'无法打开串口 {serial_port}: {e}')
    
    def receive_data(self):
        """接收串口数据"""
        if self.serial_port is None:
            return
            
        while rclpy.ok():
            try:
                # 读取串口数据
                if self.serial_port.in_waiting > 0:
                    # 读取一行数据
                    line = self.serial_port.readline().decode('utf-8', errors='ignore').strip()
                    
                    if line:
                        # 检查是否为RC数据协议格式
                        if line.startswith('r '):
                            # 解析并转发到ROS话题
                            self.forward_to_ros(line)
                            
            except UnicodeDecodeError:
                # 忽略解码错误
                pass
            except Exception as e:
                self.get_logger().error(f'串口读取错误: {e}')
                break
    
    def forward_to_ros(self, data_str: str):
        """解析数据并转发到ROS话题"""
        # 去除前缀并分割数据
        try:
            # 格式: "r value1 value2 value3 ..."
            parts = data_str.split()
            if len(parts) < 2:
                return
                
            # 将字符串值转换为整数
            int_values = [int(val) for val in parts[1:]]
            
            # 创建消息
            msg = Int32MultiArray()
            msg.data = int_values
            
            # 发布消息
            self.publisher.publish(msg)
            
        except (ValueError, IndexError) as e:
            # 忽略数据格式错误
            pass
    
    def cleanup(self):
        """清理资源"""
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            self.get_logger().info('串口已关闭')

def main(args=None):
    rclpy.init(args=args)
    
    try:
        node = RCForwarder()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if 'node' in locals():
            node.cleanup()
            node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()