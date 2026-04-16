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
        
        # Declare parameters
        self.declare_parameters(
            namespace='',
            parameters=[
                ('video_device', '/dev/video0'),
                ('fps', 120.0),
                ('frame_width', 640),
                ('frame_height', 480),
                ('topic_name', '/rgb/image_raw'),
                ('mjpg_quality', 100)  # MJPG compression quality
            ]
        )
        
        # Get parameters
        self.video_device = self.get_parameter('video_device').value
        self.fps = self.get_parameter('fps').value
        self.frame_width = self.get_parameter('frame_width').value
        self.frame_height = self.get_parameter('frame_height').value
        self.topic_name = self.get_parameter('topic_name').value
        self.mjpg_quality = self.get_parameter('mjpg_quality').value
        
        # Create publisher
        self.publisher_ = self.create_publisher(Image, self.topic_name, 10)
        
        # Create CvBridge object
        self.bridge = CvBridge()
        
        # Initialize video capture
        self.cap = None
        self.init_camera()
        
        # Create timer for periodic image publishing
        self.timer = self.create_timer(1.0/self.fps, self.timer_callback)
        
        self.get_logger().info(f'Video publisher node started: {self.video_device}')
        self.get_logger().info(f'Publishing to topic: {self.topic_name}, FPS: {self.fps}, Resolution: {self.frame_width}x{self.frame_height}')
        
    def init_camera(self):
        """Initialize camera and set to MJPG format"""
        try:
            # Open camera
            self.cap = cv2.VideoCapture(self.video_device)
            
            if not self.cap.isOpened():
                self.get_logger().error(f'Failed to open video device: {self.video_device}')
                return False
            
            # Set MJPG format
            fourcc = cv2.VideoWriter_fourcc(*'MJPG')
            self.cap.set(cv2.CAP_PROP_FOURCC, fourcc)
            
            # Set resolution
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
            
            # Set FPS
            self.cap.set(cv2.CAP_PROP_FPS, self.fps)
            
            # Verify settings
            actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
            actual_fourcc = int(self.cap.get(cv2.CAP_PROP_FOURCC))
            
            # Decode fourcc to string
            fourcc_str = ''.join([chr((actual_fourcc >> 8 * i) & 0xFF) for i in range(4)])
            
            self.get_logger().info(f'Actual camera settings: {actual_width}x{actual_height}, {actual_fps:.1f}FPS, Encoding: {fourcc_str}')
            
            return True
            
        except Exception as e:
            self.get_logger().error(f'Failed to initialize camera: {e}')
            return False
    
    def timer_callback(self):
        """Timer callback function to capture and publish images"""
        if self.cap is None or not self.cap.isOpened():
            self.get_logger().warning('Camera not opened, attempting to reconnect')
            self.init_camera()
            return
        
        try:
            # Capture frame
            ret, frame = self.cap.read()
            
            if not ret:
                self.get_logger().warning('Failed to capture frame')
                return
            
            # Optional MJPG encoding here
            # OpenCV might decode MJPG by default, so we use BGR image directly
            
            # Convert OpenCV image to ROS Image message
            ros_image = self.bridge.cv2_to_imgmsg(frame, encoding='bgr8')
            
            # Add timestamp
            ros_image.header.stamp = self.get_clock().now().to_msg()
            ros_image.header.frame_id = 'camera_frame'
            
            # Publish image
            self.publisher_.publish(ros_image)
            
        except Exception as e:
            self.get_logger().error(f'Error processing image: {e}')
    
    def destroy_node(self):
        """Cleanup resources"""
        if self.cap is not None:
            self.cap.release()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    
    video_publisher = VideoPublisher()
    
    try:
        rclpy.spin(video_publisher)
    except KeyboardInterrupt:
        video_publisher.get_logger().info('Program interrupted by user')
    finally:
        video_publisher.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()