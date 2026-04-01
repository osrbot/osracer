#!/usr/bin/env python3
"""
磁力计数据转发节点 - 从串口接收磁力计数据并发布为ROS2话题
数据格式：m x y z   (x, y, z 单位为高斯)
"""

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import MagneticField
import serial
import threading
from typing import Optional

class MagnetometerForwarder(Node):
    """磁力计数据转发节点"""
    
    def __init__(self):
        super().__init__('magnetometer_forwarder')
        
        # 参数配置
        self.declare_parameter('serial_port', '/dev/ttyACM0')
        self.declare_parameter('baud_rate', 115200)
        self.declare_parameter('topic_name', 'magnetometer_data')
        self.declare_parameter('frame_id', 'imu_link')  # 磁场数据的参考坐标系
        
        # 获取参数
        serial_port = self.get_parameter('serial_port').get_parameter_value().string_value
        baud_rate = self.get_parameter('baud_rate').get_parameter_value().integer_value
        topic_name = self.get_parameter('topic_name').get_parameter_value().string_value
        self.frame_id = self.get_parameter('frame_id').get_parameter_value().string_value
        
        # 创建发布者
        self.publisher = self.create_publisher(MagneticField, topic_name, 10)
        
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
                        # 检查是否为磁力计数据协议格式
                        if line.startswith('m '):
                            # 解析并发布到ROS话题
                            self.publish_magnetometer(line)
                            
            except UnicodeDecodeError:
                # 忽略解码错误
                pass
            except Exception as e:
                self.get_logger().error(f'串口读取错误: {e}')
                break
    
    def publish_magnetometer(self, data_str: str):
        try:
            parts = data_str.split()
            if len(parts) != 4:
                return
            
            x_gauss = float(parts[1])
            y_gauss = float(parts[2])
            z_gauss = float(parts[3])
            
            # 转换为特斯拉 (1 Gauss = 1e-4 Tesla)
            x_tesla = x_gauss * 1e-4
            y_tesla = y_gauss * 1e-4
            z_tesla = z_gauss * 1e-4
            
            msg = MagneticField()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = self.frame_id
            msg.magnetic_field.x = x_tesla
            msg.magnetic_field.y = y_tesla
            msg.magnetic_field.z = z_tesla
            # msg.magnetic_field_covariance[0] = -1.0  # 未知协方差
            
            self.publisher.publish(msg)
            
        except (ValueError, IndexError) as e:
            self.get_logger().debug(f'数据解析错误: {e}')
    
    def cleanup(self):
        """清理资源"""
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            self.get_logger().info('串口已关闭')

def main(args=None):
    rclpy.init(args=args)
    
    try:
        node = MagnetometerForwarder()
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
