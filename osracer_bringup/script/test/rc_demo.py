#!/usr/bin/env python3
"""
RC data forwarder node - Receive data from serial and forward to ROS2 topic
Only keep receive and forward functions
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Int32MultiArray
import serial
import threading
from typing import Optional

class RCForwarder(Node):
    """RC data forwarder node"""
    
    def __init__(self):
        super().__init__('rc_forwarder')
        
        # Parameter configuration
        self.declare_parameter('serial_port', '/dev/ttyACM0')
        self.declare_parameter('baud_rate', 115200)
        self.declare_parameter('topic_name', 'rc_data_raw')
        
        # Get parameters
        serial_port = self.get_parameter('serial_port').get_parameter_value().string_value
        baud_rate = self.get_parameter('baud_rate').get_parameter_value().integer_value
        topic_name = self.get_parameter('topic_name').get_parameter_value().string_value
        
        # Create publisher
        self.publisher = self.create_publisher(Int32MultiArray, topic_name, 10)
        
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
                        if line.startswith('r '):
                            # Parse and forward to ROS topic
                            self.forward_to_ros(line)
                            
            except UnicodeDecodeError:
                # Ignore decoding errors
                pass
            except Exception as e:
                self.get_logger().error(f'Serial read error: {e}')
                break
    
    def forward_to_ros(self, data_str: str):
        """Parse data and forward to ROS topic"""
        # Strip prefix and split data
        try:
            # Format: "r value1 value2 value3 ..."
            parts = data_str.split()
            if len(parts) < 2:
                return
                
            # Convert string values to integers
            int_values = [int(val) for val in parts[1:]]
            
            # Create message
            msg = Int32MultiArray()
            msg.data = int_values
            
            # Publish message
            self.publisher.publish(msg)
            
        except (ValueError, IndexError) as e:
            # Ignore data format errors
            pass
    
    def cleanup(self):
        """Cleanup resources"""
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            self.get_logger().info('Serial port closed')

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