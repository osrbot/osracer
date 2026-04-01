#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile

from geometry_msgs.msg import Twist
from sensor_msgs.msg import Imu
from nav_msgs.msg import Odometry
from tf2_ros import TransformBroadcaster
from geometry_msgs.msg import TransformStamped
from ackermann_msgs.msg import AckermannDrive  # 添加AckermannDrive消息导入

import serial
import math
import threading
import time

class OsrbotCore(Node):
    def __init__(self):
        super().__init__('osracer_chassis_node')

        # --- 声明并获取参数 ---
        self.declare_parameter('port_name', '/dev/ttyACM0')
        self.declare_parameter('baud_rate', 115200)
        self.declare_parameter('odom_frame', 'odom')
        self.declare_parameter('base_frame', 'base_footprint')
        self.declare_parameter('imu_frame', 'imu_link')  # 新增IMU frame参数
        self.declare_parameter('wheelbase', 0.285)  # 轴距，单位：米
        self.declare_parameter('max_steering_angle_deg', 30.0) # 最大转向角，单位：度
        self.declare_parameter('cmd_watchdog_timeout_s', 0.5) # 命令看门狗超时时间

        self.port_name = self.get_parameter('port_name').value
        self.baud_rate = self.get_parameter('baud_rate').value
        self.odom_frame = self.get_parameter('odom_frame').value
        self.base_frame = self.get_parameter('base_frame').value
        self.imu_frame = self.get_parameter('imu_frame').value  # 获取IMU frame参数
        self.wheelbase = self.get_parameter('wheelbase').value
        self.max_steering_angle_deg = self.get_parameter('max_steering_angle_deg').value
        self.cmd_watchdog_timeout = self.get_parameter('cmd_watchdog_timeout_s').value

        # --- 初始化串口 ---
        try:
            self.serial = serial.Serial(self.port_name, self.baud_rate, timeout=0.1)
            self.get_logger().info(f"成功打开串口: {self.port_name}")
        except serial.SerialException as e:
            self.get_logger().fatal(f"无法打开串口 '{self.port_name}': {e}")
            rclpy.shutdown()
            return

        # --- 初始化ROS2发布者、订阅者和TF广播器 ---
        self.cmd_vel_sub = self.create_subscription(
            Twist,
            'cmd_vel',
            self.cmd_vel_callback,
            10)
        
        # 新增AckermannDrive订阅者
        self.ackermann_cmd_sub = self.create_subscription(
            AckermannDrive,
            'ackermann_cmd',
            self.ackermann_cmd_callback,
            10)
            
        # 使用最佳实践QoS
        odom_qos = QoSProfile(depth=10)
        imu_qos = QoSProfile(depth=10)

        self.imu_pub = self.create_publisher(Imu, 'imu/data', qos_profile=imu_qos)
        self.odom_pub = self.create_publisher(Odometry, 'odom', qos_profile=odom_qos)
        
        self.tf_broadcaster = TransformBroadcaster(self)

        # --- 状态变量 ---
        self.last_cmd_time = self.get_clock().now()
        self.serial_lock = threading.Lock()

        # --- 启动串口读取线程 ---
        self.read_thread = threading.Thread(target=self.read_serial_loop, daemon=True)
        self.read_thread.start()
        
        self.get_logger().info("车辆桥接节点已启动。")

    def cmd_vel_callback(self, msg: Twist):
        """将 /cmd_vel 的 Twist 消息转换为串口命令"""
        linear_x = msg.linear.x
        angular_z = msg.angular.z

        # 将角速度 转换为转向角度
        # 公式: steering_angle = atan(wheelbase * angular_z / linear_x)
        # 当线速度接近0时，转向角设为最大值以实现原地转向
        if abs(linear_x) < 0.01:
            steering_angle_rad = math.copysign(self.max_steering_angle_deg * math.pi / 180.0, angular_z)
        else:
            steering_angle_rad = math.atan(self.wheelbase * angular_z / linear_x)

        # 限制转向角度范围
        max_steering_angle_rad = self.max_steering_angle_deg * math.pi / 180.0
        steering_angle_rad = max(-max_steering_angle_rad, min(max_steering_angle_rad, steering_angle_rad))

        # 转换为度
        steering_angle_deg = math.degrees(steering_angle_rad)

        # 格式化命令字符串并发送
        command = f"v {linear_x:.3f} {steering_angle_deg:.2f}\n"
        
        with self.serial_lock:
            try:
                self.serial.write(command.encode('utf-8'))
            except serial.SerialException as e:
                self.get_logger().error(f"写入串口失败: {e}")

        self.last_cmd_time = self.get_clock().now()

    def ackermann_cmd_callback(self, msg: AckermannDrive):
        """将 /ackermann_cmd 的 AckermannDrive 消息转换为串口命令"""
        speed = msg.speed
        steering_angle = msg.steering_angle
        
        # 限制转向角度范围
        max_steering_angle_rad = self.max_steering_angle_deg * math.pi / 180.0
        steering_angle_rad = max(-max_steering_angle_rad, min(max_steering_angle_rad, steering_angle))
        
        # 转换为度
        steering_angle_deg = math.degrees(steering_angle_rad)
        
        # 格式化命令字符串并发送
        command = f"v {speed:.3f} {steering_angle_deg:.2f}\n"
        
        with self.serial_lock:
            try:
                self.serial.write(command.encode('utf-8'))
            except serial.SerialException as e:
                self.get_logger().error(f"写入串口失败: {e}")
                
        self.last_cmd_time = self.get_clock().now()

    def read_serial_loop(self):
        """在独立线程中持续读取串口数据"""
        buffer = ""
        while rclpy.ok():
            if self.serial.in_waiting > 0:
                try:
                    # 读取一行数据
                    line = self.serial.readline().decode('utf-8').strip()
                    if line:
                        self.parse_serial_data(line)
                except serial.SerialException:
                    self.get_logger().error("串口读取错误，连接可能已断开。")
                    break
                except UnicodeDecodeError:
                    self.get_logger().warn("无法解码串口数据。")
            else:
                time.sleep(0.01) # 短暂休眠，避免CPU占用过高

    def parse_serial_data(self, line: str):
        """解析来自ESP32的单行数据"""
        try:
            parts = line.split()
            if not parts:
                return

            cmd_type = parts[0]

            if cmd_type == 'i' and len(parts) == 11:
                # 新协议: i qx qy qz qw ax ay az gx gy gz
                q_x, q_y, q_z, q_w = map(float, parts[1:5])
                a_x, a_y, a_z = map(float, parts[5:8])
                g_x, g_y, g_z = map(float, parts[8:11])

                imu_msg = Imu()
                imu_msg.header.stamp = self.get_clock().now().to_msg()
                imu_msg.header.frame_id = self.imu_frame  # 使用参数化的IMU frame

                # 四元数（注意 ROS2 使用 x, y, z, w）
                imu_msg.orientation.x = q_x
                imu_msg.orientation.y = q_y
                imu_msg.orientation.z = q_z
                imu_msg.orientation.w = q_w

                # 线性加速度 (m/s²)
                imu_msg.linear_acceleration.x = a_x
                imu_msg.linear_acceleration.y = a_y
                imu_msg.linear_acceleration.z = a_z

                # 角速度 (rad/s)
                imu_msg.angular_velocity.x = g_x
                imu_msg.angular_velocity.y = g_y
                imu_msg.angular_velocity.z = g_z

                # 可选：设置协方差（若未提供，可设为 -1 表示未知）
                # 这里为简化，全部设为 0 或 -1
                # imu_msg.orientation_covariance = [-1.0] * 9
                # imu_msg.angular_velocity_covariance = [-1.0] * 9
                # imu_msg.linear_acceleration_covariance = [-1.0] * 9

                self.imu_pub.publish(imu_msg)

            elif cmd_type == 'o' and len(parts) == 8:
                # 解析里程计数据: o px py pz vx vy vz w
                p_x, p_y, p_z = map(float, parts[1:4])
                v_x, v_y, v_z = map(float, parts[4:7])
                yaw = float(parts[7])

                # 创建里程计消息
                odom_msg = Odometry()
                odom_msg.header.stamp = self.get_clock().now().to_msg()
                odom_msg.header.frame_id = self.odom_frame
                odom_msg.child_frame_id = self.base_frame

                # 设置位置
                odom_msg.pose.pose.position.x = p_x
                odom_msg.pose.pose.position.y = p_y
                odom_msg.pose.pose.position.z = p_z

                # 从偏航角创建四元数
                q = self.quaternion_from_euler(0, 0, yaw)
                odom_msg.pose.pose.orientation.x = q[0]
                odom_msg.pose.pose.orientation.y = q[1]
                odom_msg.pose.pose.orientation.z = q[2]
                odom_msg.pose.pose.orientation.w = q[3]

                # 设置速度
                odom_msg.twist.twist.linear.x = v_x
                odom_msg.twist.twist.linear.y = v_y
                odom_msg.twist.twist.linear.z = v_z
                # 角速度可以由转向角和线速度估算，但ESP32未提供，这里留空
                odom_msg.twist.twist.angular.x = 0.0
                odom_msg.twist.twist.angular.y = 0.0
                odom_msg.twist.twist.angular.z = 0.0 # ESP32未提供，需要根据模型估算

                self.odom_pub.publish(odom_msg)

                # 发布TF变换
                # t = TransformStamped()
                # t.header.stamp = odom_msg.header.stamp
                # t.header.frame_id = self.odom_frame
                # t.child_frame_id = self.base_frame
                
                # t.transform.translation.x = p_x
                # t.transform.translation.y = p_y
                # t.transform.translation.z = p_z
                # t.transform.rotation = odom_msg.pose.pose.orientation
                
                # self.tf_broadcaster.sendTransform(t)
        except (ValueError, IndexError) as e:
            self.get_logger().warn(f"解析串口数据时出错: '{line}', 错误: {e}")

    def quaternion_from_euler(self, roll, pitch, yaw):
        """从欧拉角创建四元数"""
        cy = math.cos(yaw * 0.5)
        sy = math.sin(yaw * 0.5)
        cp = math.cos(pitch * 0.5)
        sp = math.sin(pitch * 0.5)
        cr = math.cos(roll * 0.5)
        sr = math.sin(roll * 0.5)

        q = [0] * 4
        q[0] = sr * cp * cy - cr * sp * sy # x
        q[1] = cr * sp * cy + sr * cp * sy # y
        q[2] = cr * cp * sy - sr * sp * cy # z
        q[3] = cr * cp * cy + sr * sp * sy # w
        return q

    def watchdog_check(self):
        """检查命令超时，如果超时则停止发送命令，让ESP32接管"""
        time_since_last_cmd = (self.get_clock().now() - self.last_cmd_time).nanoseconds / 1e9
        if time_since_last_cmd > self.cmd_watchdog_timeout:
            # 超时后不发送任何东西，ESP32的SERIAL_TIMEOUT会触发
            # 这比发送停止命令更安全，因为它会让ESP32恢复到SBUS控制
            pass


def main(args=None):
    rclpy.init(args=args)
    node = OsrbotCore()
    
    # 创建一个定时器来执行看门狗检查
    watchdog_timer = node.create_timer(0.1, node.watchdog_check)

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
    
