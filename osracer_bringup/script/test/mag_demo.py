#!/usr/bin/env python3
"""
Magnetometer data forwarder node - Receives magnetometer data from serial and publishes as a ROS2 topic
Data format: m x y z (x, y, z in Gauss)
"""

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import MagneticField
import serial
import threading
from typing import Optional

class MagnetometerForwarder(Node):
    """Magnetometer data forwarder node"""
    
    def __init__(self):
        super().__init__('magnetometer_forwarder')
        
        # Parameter configuration
        self.declare_parameter('serial_port', '/dev/ttyACM0')
        self.declare_parameter('baud_rate', 115200)
        self.declare_parameter('topic_name', 'magnetometer_data')
        self.declare_parameter('frame_id', 'imu_link')  # Reference coordinate frame for mag data
        
        # Get parameters
        serial_port = self.get_parameter('serial_port').get_parameter_value().string_value
        baud_rate = self.get_parameter('baud_rate').get_parameter_value().integer_value
        topic_name = self.get_parameter('topic_name').get_parameter_value().string_value
        self.frame_id = self.get_parameter('frame_id').get_parameter_value().string_value
        
        # Create publisher
        self.publisher = self.create_publisher(MagneticField, topic_name, 10)
        
        # Initialize serial port
        self.serial_port: Optional[serial.Serial] = None
        
        try:
            # Open serial port
            self.serial_port = serial.Serial(
                port=serial_port,
                baudrate=baud_rate,
                timeout=1.0  # Read timeout 1 second
            )
            
            self.get_logger().info(f'Connected to serial port: {serial_port}, baud rate: {baud_rate}')
            
            # Start receive thread
            self.receive_thread = threading.Thread(target=self.receive_data, daemon=True)
            self.receive_thread.start()
            
        except Exception as e:
            self.get_logger().error(f'Failed to open serial port {serial_port}: {e}')
    
    def receive_data(self):
        """Receive serial data"""
        if self.serial_port is None:
            return
            
        while rclpy.ok():
            try:
                # Read serial data
                if self.serial_port.in_waiting > 0:
                    # Read a line of data
                    line = self.serial_port.readline().decode('utf-8', errors='ignore').strip()
                    
                    if line:
                        # Check packet format
                        if line.startswith('m '):
                            # Parse and publish to ROS topic
                            self.publish_magnetometer(line)
                            
            except UnicodeDecodeError:
                # Ignore decoding errors
                pass
            except Exception as e:
                self.get_logger().error(f'Serial read error: {e}')
                break
    
    def publish_magnetometer(self, data_str: str):
        try:
            parts = data_str.split()
            if len(parts) != 4:
                return
            
            x_gauss = float(parts[1])
            y_gauss = float(parts[2])
            z_gauss = float(parts[3])
            
            # Convert to Tesla (1 Gauss = 1e-4 Tesla)
            x_tesla = x_gauss * 1e-4
            y_tesla = y_gauss * 1e-4
            z_tesla = z_gauss * 1e-4
            
            msg = MagneticField()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = self.frame_id
            msg.magnetic_field.x = x_tesla
            msg.magnetic_field.y = y_tesla
            msg.magnetic_field.z = z_tesla
            # msg.magnetic_field_covariance[0] = -1.0  # Unknown covariance
            
            self.publisher.publish(msg)
            
        except (ValueError, IndexError) as e:
            self.get_logger().debug(f'Data parsing error: {e}')
    
    def cleanup(self):
        """Cleanup resources"""
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            self.get_logger().info('Serial port closed')

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
