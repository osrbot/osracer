#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import cv2
import numpy as np

class VideoPublisher(Node):
    def __init__(self):
        super().__init__('video_publisher')
        
        # 声明参数
        self.declare_parameters(
            namespace='',
            parameters=[
                ('video_device', '/dev/video0'),
                ('fps', 120.0),
                ('frame_width', 640),
                ('frame_height', 480),
                ('topic_name', '/rgb/image_raw'),
                ('mjpg_quality', 100)  # MJPG压缩质量
            ]
        )
        
        # 获取参数
        self.video_device = self.get_parameter('video_device').value
        self.fps = self.get_parameter('fps').value
        self.frame_width = self.get_parameter('frame_width').value
        self.frame_height = self.get_parameter('frame_height').value
        self.topic_name = self.get_parameter('topic_name').value
        self.mjpg_quality = self.get_parameter('mjpg_quality').value
        
        # 创建发布者
        self.publisher_ = self.create_publisher(Image, self.topic_name, 10)
        
        # 创建CvBridge对象
        self.bridge = CvBridge()
        
        # 初始化视频捕获
        self.cap = None
        self.init_camera()
        
        # 创建定时器用于定时发布图像
        self.timer = self.create_timer(1.0/self.fps, self.timer_callback)
        
        self.get_logger().info(f'视频发布节点已启动: {self.video_device}')
        self.get_logger().info(f'发布话题: {self.topic_name}, FPS: {self.fps}, 分辨率: {self.frame_width}x{self.frame_height}')
        
    def init_camera(self):
        """初始化摄像头并设置为MJPG格式"""
        try:
            # 打开摄像头
            self.cap = cv2.VideoCapture(self.video_device)
            
            if not self.cap.isOpened():
                self.get_logger().error(f'无法打开视频设备: {self.video_device}')
                return False
            
            # 设置MJPG格式
            fourcc = cv2.VideoWriter_fourcc(*'MJPG')
            self.cap.set(cv2.CAP_PROP_FOURCC, fourcc)
            
            # 设置分辨率
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
            
            # 设置FPS
            self.cap.set(cv2.CAP_PROP_FPS, self.fps)
            
            # 验证设置
            actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
            actual_fourcc = int(self.cap.get(cv2.CAP_PROP_FOURCC))
            
            # 将fourcc解码为字符串
            fourcc_str = ''.join([chr((actual_fourcc >> 8 * i) & 0xFF) for i in range(4)])
            
            self.get_logger().info(f'摄像头实际设置: {actual_width}x{actual_height}, {actual_fps:.1f}FPS, 编码: {fourcc_str}')
            
            return True
            
        except Exception as e:
            self.get_logger().error(f'初始化摄像头失败: {e}')
            return False
    
    def timer_callback(self):
        """定时器回调函数，捕获并发布图像"""
        if self.cap is None or not self.cap.isOpened():
            self.get_logger().warn('摄像头未打开，尝试重新连接')
            self.init_camera()
            return
        
        try:
            # 捕获帧
            ret, frame = self.cap.read()
            
            if not ret:
                self.get_logger().warn('无法捕获图像帧')
                return
            
            # 如果需要，可以在这里进行MJPG编码
            # 但OpenCV默认可能会解码MJPG，所以我们直接使用BGR图像
            
            # 将OpenCV图像转换为ROS Image消息
            ros_image = self.bridge.cv2_to_imgmsg(frame, encoding='bgr8')
            
            # 添加时间戳
            ros_image.header.stamp = self.get_clock().now().to_msg()
            ros_image.header.frame_id = 'camera_frame'
            
            # 发布图像
            self.publisher_.publish(ros_image)
            
        except Exception as e:
            self.get_logger().error(f'处理图像时出错: {e}')
    
    def destroy_node(self):
        """清理资源"""
        if self.cap is not None:
            self.cap.release()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    
    video_publisher = VideoPublisher()
    
    try:
        rclpy.spin(video_publisher)
    except KeyboardInterrupt:
        video_publisher.get_logger().info('用户中断程序')
    finally:
        video_publisher.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()