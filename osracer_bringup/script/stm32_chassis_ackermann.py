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

# 协议常量
PROTOCOL_HEAD = 0xAA55

# 包类型
PACK_TYPE_HEART_BEAT = 0x0000
PACK_TYPE_CMD_VEL = 0x0001
PACK_TYPE_ACKMAN_VEL = 0x0002
PACK_TYPE_SET_ROVER_MOTION_MODE = 0x0003
PACK_TYPE_ODOM_RESPONSE = 0x8000
PACK_TYPE_HEART_BEAT_RESPONSE = 0x8002
PACK_TYPE_IMU_REPONSE = 0x8003

# 默认参数
DEFAULT_SERIAL_DEVICE = "/dev/ACM0"
DEFAULT_SERIAL_BAUDRATE = 115200
DEFAULT_BASE_FRAME = "base_link"
DEFAULT_ODOM_FRAME = "odom"
DEFAULT_IMU_FRAME = "imu_link"
DEFAULT_PUBLISH_TF = False

# 性能优化参数
SERIAL_READ_BUFFER_SIZE = 4096
SERIAL_WRITE_BUFFER_SIZE = 1024
MAX_PACKET_SIZE = 1024
RECV_BUFFER_SIZE = 8192

def build_cmd(cmd_type, data):
    """构建命令包"""
    buf = bytearray()
    
    # 包头 (小端序: 0x55 0xAA)
    buf.append(PROTOCOL_HEAD & 0xFF)        # 0x55
    buf.append((PROTOCOL_HEAD >> 8) & 0xFF) # 0xAA
    
    # 长度 (数据长度 + 2字节包类型)
    length = len(data) + 2
    buf.append(length & 0xFF)               # 长度低字节
    buf.append((length >> 8) & 0xFF)        # 长度高字节
    
    # 包类型 (小端序)
    buf.append(cmd_type & 0xFF)             # 包类型低字节
    buf.append((cmd_type >> 8) & 0xFF)      # 包类型高字节
    
    # 数据
    buf.extend(data)
    
    # BCC校验 - 从索引4(长度字段)开始到数据结束
    bcc = 0
    for i in range(4, len(buf)):
        bcc ^= buf[i]
    
    buf.append(bcc)
    
    return buf

class HighPerformanceSerial:
    """高性能串口包装类"""
    def __init__(self, port, baudrate, timeout=0.1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_conn = None
        self.write_lock = threading.Lock()
        self._is_open = False
        
    def open(self):
        """打开串口"""
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
            print(f"打开串口失败: {e}")
            self._is_open = False
            return False
    
    def close(self):
        """关闭串口"""
        try:
            if self.serial_conn and self.serial_conn.is_open:
                self.serial_conn.close()
            self._is_open = False
        except Exception:
            pass
    
    def is_open(self):
        """检查串口是否打开"""
        return self._is_open and self.serial_conn and self.serial_conn.is_open
    
    def read(self, size=1024):
        """读取数据"""
        try:
            if self.is_open():
                return self.serial_conn.read(size)
        except Exception as e:
            print(f"串口读取错误: {e}")
            self._is_open = False
        return None
    
    def write(self, data):
        """写入数据"""
        with self.write_lock:
            try:
                if self.is_open():
                    sent = self.serial_conn.write(data)
                    self.serial_conn.flush()
                    return sent
            except Exception as e:
                print(f"串口写入错误: {e}")
                self._is_open = False
            return 0

class ProtocolParser:
    """高性能协议解析器"""
    def __init__(self):
        self.buffer = bytearray()
        self.max_buffer_size = RECV_BUFFER_SIZE
        
    def add_data(self, data):
        """添加新数据到缓冲区"""
        self.buffer.extend(data)
        
        # 防止缓冲区无限增长
        if len(self.buffer) > self.max_buffer_size:
            # 保留最近的数据
            keep_size = min(self.max_buffer_size // 2, len(self.buffer))
            self.buffer = self.buffer[-keep_size:]
    
    def parse_packets(self):
        """解析完整数据包"""
        packets = []
        
        while len(self.buffer) >= 7:  # 最小包长度
            # 查找包头
            head_pos = -1
            for i in range(len(self.buffer) - 1):
                if self.buffer[i] == (PROTOCOL_HEAD & 0xFF) and \
                   self.buffer[i+1] == ((PROTOCOL_HEAD >> 8) & 0xFF):
                    head_pos = i
                    break
            
            if head_pos == -1:
                # 没有找到包头，清空缓冲区
                self.buffer.clear()
                break
            
            if head_pos > 0:
                # 移除包头前的无效数据
                del self.buffer[:head_pos]
                continue
            
            # 检查长度是否足够
            if len(self.buffer) < 6:
                break
                
            # 解析长度字段
            data_len = self.buffer[2] + (self.buffer[3] << 8)
            total_packet_len = 4 + data_len + 1  # 包头4 + 数据长度 + 校验位
            
            if total_packet_len > MAX_PACKET_SIZE:
                # 包长度异常，跳过这个字节继续查找
                del self.buffer[0]
                continue
                
            if len(self.buffer) < total_packet_len:
                # 数据不完整，等待更多数据
                break
            
            # 提取完整数据包
            packet = bytes(self.buffer[:total_packet_len])
            
            # BCC校验
            if self.verify_bcc(packet):
                packets.append(packet)
                del self.buffer[:total_packet_len]
            else:
                # 校验失败，跳过这个字节继续查找
                del self.buffer[0]
        
        return packets
    
    def verify_bcc(self, packet):
        """BCC校验"""
        if len(packet) < 5:
            return False
            
        bcc = 0
        for i in range(4, len(packet) - 1):
            bcc ^= packet[i]
        
        return bcc == packet[-1]
    
    def clear(self):
        """清空缓冲区"""
        self.buffer.clear()

class OsrbotCore(Node):
    def __init__(self):
        super().__init__('osrbot_chassis')
        self.init_done = False
        self.running = True
        
        # 参数
        self.serial_port = self.declare_parameter("serial_port", DEFAULT_SERIAL_DEVICE).value
        self.serial_baudrate = self.declare_parameter("serial_baudrate", DEFAULT_SERIAL_BAUDRATE).value
        
        # 性能优化组件
        self.serial_interface = HighPerformanceSerial(
            self.serial_port, 
            self.serial_baudrate,
            timeout=0.05
        )
        self.protocol_parser = ProtocolParser()
        
        # 统计信息
        self.packet_count = 0
        self.error_count = 0
        self.heartbeat_count = 0
        self.last_stat_time = time.time()
        self.last_heartbeat_time = time.time()
        
        # 定时器管理 - 使用不同的属性名避免冲突
        self.managed_timers = []
        
        # 串口线程
        self.serial_thread = None
        self.communication_timeout = False
        
        # 初始化串口和定时器
        self.setup_timers()
        self.open_serial()
        
        self.init_done = True
        
    def setup_timers(self):
        """设置定时器 - 统一管理"""
        # 清理现有定时器
        self.cleanup_timers()
        
        # 心跳定时器 - 固定间隔发送
        heartbeat_timer = self.create_timer(0.2, self.heart_callback)
        self.managed_timers.append(heartbeat_timer)
        
        # 通信超时检测定时器
        comm_timer = self.create_timer(0.5, self.communication_error_callback)
        self.managed_timers.append(comm_timer)
        
        # 统计定时器
        stat_timer = self.create_timer(5.0, self.stat_callback)
        self.managed_timers.append(stat_timer)
        
        # 串口健康检查定时器
        health_timer = self.create_timer(10.0, self.serial_health_check)
        self.managed_timers.append(health_timer)
    
    def cleanup_timers(self):
        """清理所有定时器"""
        for timer in self.managed_timers:
            try:
                timer.cancel()
                timer.destroy()
            except Exception:
                pass
        self.managed_timers.clear()
    
    def open_serial(self):
        """打开串口连接"""
        if self.serial_interface.open():
            # 启动接收线程
            if self.serial_thread and self.serial_thread.is_alive():
                # 等待旧线程结束
                self.serial_thread.join(timeout=1.0)
                
            self.serial_thread = threading.Thread(target=self.serial_receive_thread)
            self.serial_thread.daemon = True
            self.serial_thread.start()
            self.get_logger().info("串口打开成功")
            self.communication_timeout = False
            self.last_heartbeat_time = time.time()
        else:
            self.get_logger().warning("串口打开失败，2秒后重试")
            # 使用单次定时器进行重连
            def delayed_reconnect():
                self.reconnect_serial()
                # 从管理列表中移除这个一次性定时器
                if hasattr(self, '_reconnect_timer') and self._reconnect_timer in self.managed_timers:
                    self.managed_timers.remove(self._reconnect_timer)
            
            self._reconnect_timer = self.create_timer(2.0, delayed_reconnect, oneshot=True)
            self.managed_timers.append(self._reconnect_timer)
    
    def reconnect_serial(self):
        """重新连接串口"""
        if not self.serial_interface.is_open():
            self.get_logger().info("尝试重新连接串口...")
            self.open_serial()
    
    def serial_health_check(self):
        """串口健康检查"""
        if not self.serial_interface.is_open():
            self.get_logger().warning("串口连接断开，尝试重连")
            self.open_serial()
        
        # 检查心跳是否正常
        current_time = time.time()
        if current_time - self.last_heartbeat_time > 2.0:  # 2秒没有心跳响应
            if not self.communication_timeout:
                self.get_logger().warning("心跳通信超时")
                self.communication_timeout = True
    
    def serial_receive_thread(self):
        """高性能串口接收线程"""
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while self.running and rclpy.ok():
            try:
                # 批量读取数据
                data = self.serial_interface.read(SERIAL_READ_BUFFER_SIZE)
                if data:
                    consecutive_errors = 0  # 重置错误计数
                    
                    # 添加到解析器
                    self.protocol_parser.add_data(data)
                    
                    # 批量解析数据包
                    packets = self.protocol_parser.parse_packets()
                    for packet in packets:
                        self.handle_serial_packet(packet)
                else:
                    # 没有数据，短暂休眠
                    time.sleep(0.001)
                    
            except Exception as e:
                consecutive_errors += 1
                self.error_count += 1
                if consecutive_errors >= max_consecutive_errors:
                    self.get_logger().error(f"串口接收连续错误，尝试重连: {e}")
                    time.sleep(0.1)
                    break  # 退出线程，等待重连
                time.sleep(0.01)
    
    def handle_serial_packet(self, packet):
        """处理单个串口数据包"""
        try:
            if self.init_done:
                # 更新统计
                self.packet_count += 1
                
                # 处理数据包
                self.osrbot_data_proc(packet)
                
                # 重置通信超时标志
                self.communication_timeout = False
                self.last_heartbeat_time = time.time()
                
        except Exception as e:
            self.error_count += 1
            self.get_logger().warning(f"处理数据包异常: {e}")
    
    def communication_error_callback(self):
        """通信错误回调"""
        if self.communication_timeout:
            self.get_logger().warning("串口通信超时，尝试恢复")
            self.reconnect_serial()
    
    def stat_callback(self):
        """统计信息回调"""
        current_time = time.time()
        elapsed = current_time - self.last_stat_time
        packet_rate = self.packet_count / elapsed if elapsed > 0 else 0
        
        self.get_logger().info(
            f"串口统计: {self.packet_count}包, {packet_rate:.1f}包/秒, "
            f"心跳: {self.heartbeat_count}, 错误: {self.error_count}, "
            f"超时: {self.communication_timeout}"
        )
        
        # 重置统计
        self.packet_count = 0
        self.heartbeat_count = 0
        self.error_count = 0
        self.last_stat_time = current_time
    
    def heart_callback(self):
        """心跳回调 - 修复版本"""
        try:
            if not self.serial_interface.is_open():
                self.reconnect_serial()
                return
                
            # 发送心跳包
            dummy = 0
            data = struct.pack('<H', dummy)
            buf = build_cmd(PACK_TYPE_HEART_BEAT, data)
            sent = self.serial_interface.write(buf)
            
            if sent == len(buf):
                self.heartbeat_count += 1
            else:
                self.get_logger().warning("心跳包发送失败")
                self.communication_timeout = True
                
        except Exception as e:
            self.get_logger().warning(f"心跳包发送异常: {e}")
            self.communication_timeout = True
    
    def serial_send(self, data):
        """发送串口数据"""
        try:
            if not self.serial_interface.is_open():
                return -1
                
            sent = self.serial_interface.write(data)
            return 0 if sent == len(data) else -1
        except Exception:
            return -1
    
    def osrbot_data_proc(self, buf):
        """处理接收到的数据包（子类实现）"""
        raise NotImplementedError("Subclasses must implement this method")
    
    def destroy_node(self):
        """销毁节点 - 修复资源清理"""
        self.get_logger().info("正在关闭节点...")
        self.running = False
        
        # 等待串口线程结束
        if self.serial_thread and self.serial_thread.is_alive():
            self.serial_thread.join(timeout=2.0)
        
        # 关闭串口
        self.serial_interface.close()
        
        # 清理定时器
        self.cleanup_timers()
        
        # 清空协议解析器缓冲区
        self.protocol_parser.clear()
        
        super().destroy_node()

# OsrbotChassis 和 OsrbotAckermann 类保持不变
class OsrbotChassis(OsrbotCore):
    def __init__(self):
        super().__init__()
        
        # 参数
        self.base_frame = self.declare_parameter("base_frame", DEFAULT_BASE_FRAME).value
        self.odom_frame = self.declare_parameter("odom_frame", DEFAULT_ODOM_FRAME).value
        self.imu_frame = self.declare_parameter("imu_frame", DEFAULT_IMU_FRAME).value
        self.publish_tf = self.declare_parameter("publish_tf", DEFAULT_PUBLISH_TF).value
        
        # 发布器
        self.odom_pub = self.create_publisher(Odometry, "odom", 10)
        self.imu_pub = self.create_publisher(Imu, "imu", 10)
        
        # TF广播器
        self.tf_broadcaster = TransformBroadcaster(self)
        
        self.publisher_init_done = True
    
    def osrbot_data_proc(self, buf):
        """处理接收到的数据包"""
        if not self.publisher_init_done:
            return
            
        # 解析协议包
        if len(buf) < 6:
            return
            
        # 包类型在偏移4的位置 (小端序)
        pack_type = struct.unpack_from('<H', buf, 4)[0]
        
        if pack_type == PACK_TYPE_ODOM_RESPONSE:
            self.handle_odom_data(buf)
        elif pack_type == PACK_TYPE_IMU_REPONSE:
            self.handle_imu_data(buf)
        elif pack_type == PACK_TYPE_HEART_BEAT_RESPONSE:
            # 心跳响应，更新最后心跳时间
            self.last_heartbeat_time = time.time()
            self.communication_timeout = False
    
    def handle_odom_data(self, buf):
        """处理里程计数据"""
        data_start = 6
        data_len = len(buf) - data_start - 1
        
        try:
            if data_len >= 40:
                data = buf[data_start:data_start + 40]
                odom_data = struct.unpack('<ffffffffff', data)
                x, y, z, yaw, lin_x, lin_y, lin_z, ang_x, ang_y, ang_z = odom_data
                
                # 发布Odometry消息
                odom_msg = Odometry()
                current_time = self.get_clock().now().to_msg()
                odom_msg.header.stamp = current_time
                odom_msg.header.frame_id = self.odom_frame
                
                # 位置
                odom_msg.pose.pose.position.x = x
                odom_msg.pose.pose.position.y = y
                odom_msg.pose.pose.position.z = z
                
                # 方向
                quat = quaternion_from_euler(0, 0, yaw)
                odom_msg.pose.pose.orientation.x = quat[0]
                odom_msg.pose.pose.orientation.y = quat[1]
                odom_msg.pose.pose.orientation.z = quat[2]
                odom_msg.pose.pose.orientation.w = quat[3]
                
                # 速度
                odom_msg.child_frame_id = self.base_frame
                odom_msg.twist.twist.linear.x = lin_x
                odom_msg.twist.twist.linear.y = lin_y
                odom_msg.twist.twist.linear.z = lin_z
                odom_msg.twist.twist.angular.x = ang_x
                odom_msg.twist.twist.angular.y = ang_y
                odom_msg.twist.twist.angular.z = ang_z
                
                self.odom_pub.publish(odom_msg)
                
                # 发布TF
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
            self.get_logger().warning(f"处理里程计数据失败: {e}")
    
    def handle_imu_data(self, buf):
        """处理IMU数据"""
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
                
                # 方向
                imu_msg.orientation.x = qx
                imu_msg.orientation.y = qy
                imu_msg.orientation.z = qz
                imu_msg.orientation.w = qw
                
                # 角速度
                imu_msg.angular_velocity.x = ang_x
                imu_msg.angular_velocity.y = ang_y
                imu_msg.angular_velocity.z = ang_z
                
                # 线性加速度
                imu_msg.linear_acceleration.x = acc_x
                imu_msg.linear_acceleration.y = acc_y
                imu_msg.linear_acceleration.z = acc_z
                
                # 协方差   have a impact for cartographer account for enable the follow lines
                # imu_msg.orientation_covariance[0] = -1
                # imu_msg.angular_velocity_covariance[0] = -1
                # imu_msg.linear_acceleration_covariance[0] = -1
                
                self.imu_pub.publish(imu_msg)
                
        except Exception as e:
            self.get_logger().warning(f"处理IMU数据失败: {e}")

class OsrbotAckermann(OsrbotChassis):
    def __init__(self):
        super().__init__()
        
        # 订阅Ackermann控制命令
        self.ackermann_sub = self.create_subscription(
            AckermannDrive, 
            "ackermann_cmd",
            self.ackermann_callback,
            10
        )
        
        self.init_done = True
    
    def ackermann_callback(self, msg):
        """Ackermann控制命令回调"""
        try:
            steering_angle = msg.steering_angle
            speed = msg.speed
            
            data = struct.pack('<ff', steering_angle, speed)
            buf = build_cmd(PACK_TYPE_ACKMAN_VEL, data)
            
            self.serial_send(buf)
                
        except Exception as e:
            self.get_logger().warning(f"发送Ackermann命令失败: {e}")

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
        print(f"程序异常: {e}")
    finally:
        if 'node' in locals():
            node.destroy_node()
        rclpy.try_shutdown()

if __name__ == "__main__":
    main()
