#!/usr/bin/env python3

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
import struct
import threading
import serial
from serial import SerialException
import math
import time
from collections import deque

from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu
from ackermann_msgs.msg import AckermannDrive
from tf_transformations import quaternion_from_euler

import tf2_ros
from tf2_ros import TransformBroadcaster

# Protocol Constants
PROTOCOL_HEAD = 0xAA55

# Packet Types
PACK_TYPE_HEART_BEAT = 0x0000
PACK_TYPE_CMD_VEL = 0x0001
PACK_TYPE_ACKMAN_VEL = 0x0002
PACK_TYPE_SET_ROVER_MOTION_MODE = 0x0003
PACK_TYPE_ODOM_RESPONSE = 0x8000
PACK_TYPE_HEART_BEAT_RESPONSE = 0x8002
PACK_TYPE_IMU_REPONSE = 0x8003

# Default Parameters
DEFAULT_SERIAL_DEVICE = "/dev/ACM0"
DEFAULT_SERIAL_BAUDRATE = 115200
DEFAULT_BASE_FRAME = "base_link"
DEFAULT_ODOM_FRAME = "odom"
DEFAULT_IMU_FRAME = "imu_link"
DEFAULT_PUBLISH_TF = False

# Performance Optimization Parameters
SERIAL_READ_BUFFER_SIZE = 4096
SERIAL_WRITE_BUFFER_SIZE = 1024
MAX_PACKET_SIZE = 1024
RECV_BUFFER_SIZE = 8192

def build_cmd(cmd_type, data):
    """Build command packet"""
    buf = bytearray()
    
    # Packet header (Little-endian: 0x55 0xAA)
    buf.append(PROTOCOL_HEAD & 0xFF)        # 0x55
    buf.append((PROTOCOL_HEAD >> 8) & 0xFF) # 0xAA
    
    # Length (Data length + 2 bytes packet type)
    length = len(data) + 2
    buf.append(length & 0xFF)               # Length LSB
    buf.append((length >> 8) & 0xFF)        # Length MSB
    
    # Packet type (Little-endian)
    buf.append(cmd_type & 0xFF)             # Type LSB
    buf.append((cmd_type >> 8) & 0xFF)      # Type MSB
    
    # Data
    buf.extend(data)
    
    # BCC Checksum - From index 4 (length field) to the end of data
    bcc = 0
    for i in range(4, len(buf)):
        bcc ^= buf[i]
    
    buf.append(bcc)
    
    return buf

class HighPerformanceSerial:
    """High-performance serial wrapper class"""
    def __init__(self, port, baudrate, timeout=0.1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_conn = None
        self.write_lock = threading.Lock()
        self._is_open = False
        
    def open(self):
        """Open serial port"""
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
                write_timeout=0.1
            )
            
            self._is_open = self.serial_conn.is_open
            
            if self._is_open:
                self.serial_conn.reset_input_buffer()
                self.serial_conn.reset_output_buffer()
                
            return self._is_open
            
        except SerialException as e:
            print(f"Failed to open serial port: {e}")
            self._is_open = False
            return False
    
    def close(self):
        """Close serial port"""
        try:
            if self.serial_conn and self.serial_conn.is_open:
                self.serial_conn.close()
            self._is_open = False
        except Exception:
            pass
    
    def is_open(self):
        """Check if serial port is open"""
        return self._is_open and self.serial_conn and self.serial_conn.is_open
    
    def read(self, size=1024):
        """Read data"""
        try:
            if self.is_open():
                return self.serial_conn.read(size)
        except Exception as e:
            print(f"Serial read error: {e}")
            self._is_open = False
        return None
    
    def write(self, data):
        """Write data"""
        with self.write_lock:
            try:
                if self.is_open():
                    sent = self.serial_conn.write(data)
                    self.serial_conn.flush()
                    return sent
            except Exception as e:
                print(f"Serial write error: {e}")
                self._is_open = False
            return 0

class ProtocolParser:
    """High-performance protocol parser"""
    def __init__(self):
        self.buffer = bytearray()
        self.max_buffer_size = RECV_BUFFER_SIZE
        
    def add_data(self, data):
        """Add new data to buffer"""
        self.buffer.extend(data)
        
        # Prevent infinite buffer growth
        if len(self.buffer) > self.max_buffer_size:
            # Keep the most recent data
            keep_size = min(self.max_buffer_size // 2, len(self.buffer))
            self.buffer = self.buffer[-keep_size:]
    
    def parse_packets(self):
        """Parse complete packets"""
        packets = []
        
        while len(self.buffer) >= 7:  # Minimum packet length
            # Search for packet header
            head_pos = -1
            for i in range(len(self.buffer) - 1):
                if self.buffer[i] == (PROTOCOL_HEAD & 0xFF) and \
                   self.buffer[i+1] == ((PROTOCOL_HEAD >> 8) & 0xFF):
                    head_pos = i
                    break
            
            if head_pos == -1:
                # Header not found, clear buffer
                self.buffer.clear()
                break
            
            if head_pos > 0:
                # Remove invalid data before header
                del self.buffer[:head_pos]
                continue
            
            # Check if length is sufficient
            if len(self.buffer) < 6:
                break
                
            # Parse length field
            data_len = self.buffer[2] + (self.buffer[3] << 8)
            total_packet_len = 4 + data_len + 1  # Header 4 + data length + checksum byte
            
            if total_packet_len > MAX_PACKET_SIZE:
                # Invalid packet length, skip byte and continue search
                del self.buffer[0]
                continue
                
            if len(self.buffer) < total_packet_len:
                # Incomplete data, wait for more
                break
            
            # Extract complete packet
            packet = bytes(self.buffer[:total_packet_len])
            
            # BCC verification
            if self.verify_bcc(packet):
                packets.append(packet)
                del self.buffer[:total_packet_len]
            else:
                # Verification failed, skip byte and continue search
                del self.buffer[0]
        
        return packets
    
    def verify_bcc(self, packet):
        """BCC verification"""
        if len(packet) < 5:
            return False
            
        bcc = 0
        for i in range(4, len(packet) - 1):
            bcc ^= packet[i]
        
        return bcc == packet[-1]
    
    def clear(self):
        """Clear buffer"""
        self.buffer.clear()

class OsrbotCore(Node):
    def __init__(self):
        super().__init__('osrbot_chassis')
        self.init_done = False
        self.running = True
        
        # Parameters
        self.serial_port = self.declare_parameter("serial_port", DEFAULT_SERIAL_DEVICE).value
        self.serial_baudrate = self.declare_parameter("serial_baudrate", DEFAULT_SERIAL_BAUDRATE).value
        
        # Performance optimization components
        self.serial_interface = HighPerformanceSerial(
            self.serial_port, 
            self.serial_baudrate,
            timeout=0.05
        )
        self.protocol_parser = ProtocolParser()
        
        # Statistics information
        self.packet_count = 0
        self.error_count = 0
        self.heartbeat_count = 0
        self.last_stat_time = time.time()
        self.last_heartbeat_time = time.time()
        
        # Timer management - use different property names to avoid conflicts
        self.managed_timers = []
        
        # Serial thread
        self.serial_thread = None
        self.communication_timeout = False
        
        # Initialize serial and timers
        self.setup_timers()
        self.open_serial()
        
        self.init_done = True
        
    def setup_timers(self):
        """Setup timers - unified management"""
        # Cleanup existing timers
        self.cleanup_timers()
        
        # Heartbeat timer - fixed interval sending
        heartbeat_timer = self.create_timer(0.2, self.heart_callback)
        self.managed_timers.append(heartbeat_timer)
        
        # Communication timeout detection timer
        comm_timer = self.create_timer(0.5, self.communication_error_callback)
        self.managed_timers.append(comm_timer)
        
        # Statistics timer
        stat_timer = self.create_timer(5.0, self.stat_callback)
        self.managed_timers.append(stat_timer)
        
        # Serial health check timer
        health_timer = self.create_timer(10.0, self.serial_health_check)
        self.managed_timers.append(health_timer)
    
    def cleanup_timers(self):
        """Cleanup all timers"""
        for timer in self.managed_timers:
            try:
                timer.cancel()
                timer.destroy()
            except Exception:
                pass
        self.managed_timers.clear()
    
    def open_serial(self):
        """Open serial connection"""
        if self.serial_interface.open():
            # Start receive thread
            if self.serial_thread and self.serial_thread.is_alive():
                # Wait for old thread to end
                self.serial_thread.join(timeout=1.0)
                
            self.serial_thread = threading.Thread(target=self.serial_receive_thread)
            self.serial_thread.daemon = True
            self.serial_thread.start()
            self.get_logger().info("Serial port opened successfully")
            self.communication_timeout = False
            self.last_heartbeat_time = time.time()
        else:
            self.get_logger().warning("Failed to open serial port, retrying in 2 seconds")
            # Use one-shot timer for reconnection
            def delayed_reconnect():
                self.reconnect_serial()
                # Remove this one-shot timer from managed list
                if hasattr(self, '_reconnect_timer') and self._reconnect_timer in self.managed_timers:
                    self.managed_timers.remove(self._reconnect_timer)
            
            self._reconnect_timer = self.create_timer(2.0, delayed_reconnect, oneshot=True)
            self.managed_timers.append(self._reconnect_timer)
    
    def reconnect_serial(self):
        """Reconnect serial port"""
        if not self.serial_interface.is_open():
            self.get_logger().info("Attempting to reconnect serial port...")
            self.open_serial()
    
    def serial_health_check(self):
        """Serial health check"""
        if not self.serial_interface.is_open():
            self.get_logger().warning("Serial connection lost, attempting reconnect")
            self.open_serial()
        
        # Check if heartbeat is normal
        current_time = time.time()
        if current_time - self.last_heartbeat_time > 2.0:  # 2 seconds without heartbeat response
            if not self.communication_timeout:
                self.get_logger().warning("Heartbeat communication timeout")
                self.communication_timeout = True
    
    def serial_receive_thread(self):
        """High-performance serial receive thread"""
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while self.running and rclpy.ok():
            try:
                # Batch read data
                data = self.serial_interface.read(SERIAL_READ_BUFFER_SIZE)
                if data:
                    consecutive_errors = 0  # Reset error count
                    
                    # Add to parser
                    self.protocol_parser.add_data(data)
                    
                    # Batch parse packets
                    packets = self.protocol_parser.parse_packets()
                    for packet in packets:
                        self.handle_serial_packet(packet)
                else:
                    # No data, short sleep
                    time.sleep(0.001)
                    
            except Exception as e:
                consecutive_errors += 1
                self.error_count += 1
                if consecutive_errors >= max_consecutive_errors:
                    self.get_logger().error(f"Consecutive serial receive errors, attempting reconnect: {e}")
                    time.sleep(0.1)
                    break  # Exit thread, wait for reconnect
                time.sleep(0.01)
    
    def handle_serial_packet(self, packet):
        """Handle a single serial packet"""
        try:
            if self.init_done:
                # Update statistics
                self.packet_count += 1
                
                # Process packet
                self.osrbot_data_proc(packet)
                
                # Reset communication timeout flag
                self.communication_timeout = False
                self.last_heartbeat_time = time.time()
                
        except Exception as e:
            self.error_count += 1
            self.get_logger().warning(f"Exception handling packet: {e}")
    
    def communication_error_callback(self):
        """Communication error callback"""
        if self.communication_timeout:
            self.get_logger().warning("Serial communication timeout, attempting recovery")
            self.reconnect_serial()
    
    def stat_callback(self):
        """Statistics callback"""
        current_time = time.time()
        elapsed = current_time - self.last_stat_time
        packet_rate = self.packet_count / elapsed if elapsed > 0 else 0
        
        self.get_logger().info(
            f"Serial Stats: {self.packet_count} packets, {packet_rate:.1f} packets/sec, "
            f"Heartbeats: {self.heartbeat_count}, Errors: {self.error_count}, "
            f"Timeout: {self.communication_timeout}"
        )
        
        # Reset statistics
        self.packet_count = 0
        self.heartbeat_count = 0
        self.error_count = 0
        self.last_stat_time = current_time
    
    def heart_callback(self):
        """Heartbeat callback - fixed version"""
        try:
            if not self.serial_interface.is_open():
                self.reconnect_serial()
                return
                
            # Send heartbeat packet
            dummy = 0
            data = struct.pack('<H', dummy)
            buf = build_cmd(PACK_TYPE_HEART_BEAT, data)
            sent = self.serial_interface.write(buf)
            
            if sent == len(buf):
                self.heartbeat_count += 1
            else:
                self.get_logger().warning("Failed to send heartbeat packet")
                self.communication_timeout = True
                
        except Exception as e:
            self.get_logger().warning(f"Exception sending heartbeat packet: {e}")
            self.communication_timeout = True
    
    def serial_send(self, data):
        """Send serial data"""
        try:
            if not self.serial_interface.is_open():
                return -1
                
            sent = self.serial_interface.write(data)
            return 0 if sent == len(data) else -1
        except Exception:
            return -1
    
    def osrbot_data_proc(self, buf):
        """Process received packets (subclass implementation)"""
        raise NotImplementedError("Subclasses must implement this method")
    
    def destroy_node(self):
        """Destroy node - resource cleanup"""
        self.get_logger().info("Closing node...")
        self.running = False
        
        # Wait for serial thread to end
        if self.serial_thread and self.serial_thread.is_alive():
            self.serial_thread.join(timeout=2.0)
        
        # Close serial port
        self.serial_interface.close()
        
        # Cleanup timers
        self.cleanup_timers()
        
        # Clear protocol parser buffer
        self.protocol_parser.clear()
        
        super().destroy_node()

# OsrbotChassis and OsrbotAckermann classes remain unchanged
class OsrbotChassis(OsrbotCore):
    def __init__(self):
        super().__init__()
        
        # Parameters
        self.base_frame = self.declare_parameter("base_frame", DEFAULT_BASE_FRAME).value
        self.odom_frame = self.declare_parameter("odom_frame", DEFAULT_ODOM_FRAME).value
        self.imu_frame = self.declare_parameter("imu_frame", DEFAULT_IMU_FRAME).value
        self.publish_tf = self.declare_parameter("publish_tf", DEFAULT_PUBLISH_TF).value
        
        # Publishers
        self.odom_pub = self.create_publisher(Odometry, "odom", 10)
        self.imu_pub = self.create_publisher(Imu, "imu", 10)
        
        # TF Broadcaster
        self.tf_broadcaster = TransformBroadcaster(self)
        
        self.publisher_init_done = True
    
    def osrbot_data_proc(self, buf):
        """Process received packets"""
        if not self.publisher_init_done:
            return
            
        # Parse protocol packet
        if len(buf) < 6:
            return
            
        # Packet type at offset 4 (Little-endian)
        pack_type = struct.unpack_from('<H', buf, 4)[0]
        
        if pack_type == PACK_TYPE_ODOM_RESPONSE:
            self.handle_odom_data(buf)
        elif pack_type == PACK_TYPE_IMU_REPONSE:
            self.handle_imu_data(buf)
        elif pack_type == PACK_TYPE_HEART_BEAT_RESPONSE:
            # Heartbeat response, update last heartbeat time
            self.last_heartbeat_time = time.time()
            self.communication_timeout = False
    
    def handle_odom_data(self, buf):
        """Handle odometry data"""
        data_start = 6
        data_len = len(buf) - data_start - 1
        
        try:
            if data_len >= 40:
                data = buf[data_start:data_start + 40]
                odom_data = struct.unpack('<ffffffffff', data)
                x, y, z, yaw, lin_x, lin_y, lin_z, ang_x, ang_y, ang_z = odom_data
                
                # Publish Odometry message
                odom_msg = Odometry()
                current_time = self.get_clock().now().to_msg()
                odom_msg.header.stamp = current_time
                odom_msg.header.frame_id = self.odom_frame
                
                # Position
                odom_msg.pose.pose.position.x = x
                odom_msg.pose.pose.position.y = y
                odom_msg.pose.pose.position.z = z
                
                # Direction
                quat = quaternion_from_euler(0, 0, yaw)
                odom_msg.pose.pose.orientation.x = quat[0]
                odom_msg.pose.pose.orientation.y = quat[1]
                odom_msg.pose.pose.orientation.z = quat[2]
                odom_msg.pose.pose.orientation.w = quat[3]
                
                # Velocity
                odom_msg.child_frame_id = self.base_frame
                odom_msg.twist.twist.linear.x = lin_x
                odom_msg.twist.twist.linear.y = lin_y
                odom_msg.twist.twist.linear.z = lin_z
                odom_msg.twist.twist.angular.x = ang_x
                odom_msg.twist.twist.angular.y = ang_y
                odom_msg.twist.twist.angular.z = ang_z
                
                self.odom_pub.publish(odom_msg)
                
                # Publish TF
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
                
        except Exception as e:
            self.get_logger().warning(f"Failed to process odometry data: {e}")
    
    def handle_imu_data(self, buf):
        """Handle IMU data"""
        data_start = 6
        data_len = len(buf) - data_start - 1
        
        try:
            if data_len >= 40:
                data = buf[data_start:data_start + 40]
                imu_data = struct.unpack('<10f', data)
                qx, qy, qz, qw, acc_x, acc_y, acc_z, ang_x, ang_y, ang_z = imu_data
                
                imu_msg = Imu()
                current_time = self.get_clock().now().to_msg()
                imu_msg.header.stamp = current_time
                imu_msg.header.frame_id = self.imu_frame
                
                # Direction
                imu_msg.orientation.x = qx
                imu_msg.orientation.y = qy
                imu_msg.orientation.z = qz
                imu_msg.orientation.w = qw
                
                # Angular Velocity
                imu_msg.angular_velocity.x = ang_x
                imu_msg.angular_velocity.y = ang_y
                imu_msg.angular_velocity.z = ang_z
                
                # Linear Acceleration
                imu_msg.linear_acceleration.x = acc_x
                imu_msg.linear_acceleration.y = acc_y
                imu_msg.linear_acceleration.z = acc_z
                
                # Covariance - may impact Cartographer if these are enabled
                # imu_msg.orientation_covariance[0] = -1
                # imu_msg.angular_velocity_covariance[0] = -1
                # imu_msg.linear_acceleration_covariance[0] = -1
                
                self.imu_pub.publish(imu_msg)
                
        except Exception as e:
            self.get_logger().warning(f"Failed to process IMU data: {e}")

class OsrbotAckermann(OsrbotChassis):
    def __init__(self):
        super().__init__()
        
        # Subscribe to Ackermann control commands
        self.ackermann_sub = self.create_subscription(
            AckermannDrive, 
            "ackermann_cmd",
            self.ackermann_callback,
            10
        )
        
        self.init_done = True
    
    def ackermann_callback(self, msg):
        """Ackermann control command callback"""
        try:
            steering_angle = msg.steering_angle
            speed = msg.speed
            
            data = struct.pack('<ff', steering_angle, speed)
            buf = build_cmd(PACK_TYPE_ACKMAN_VEL, data)
            
            self.serial_send(buf)
                
        except Exception as e:
            self.get_logger().warning(f"Failed to send Ackermann command: {e}")

def main():
    rclpy.init()
    
    try:
        node = OsrbotAckermann()
        rclpy.spin(node)
            
    except KeyboardInterrupt:
        pass
    except ExternalShutdownException:
        pass
    except Exception as e:
        print(f"Exception in program: {e}")
    finally:
        if 'node' in locals():
            node.destroy_node()
        rclpy.try_shutdown()

if __name__ == "__main__":
    main()
