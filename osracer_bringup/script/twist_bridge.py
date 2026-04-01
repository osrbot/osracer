#!/usr/bin/env python

# Author: christoph.roesmann@tu-dortmund.de

import sys
import rclpy, math
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from geometry_msgs.msg import Twist
from ackermann_msgs.msg import AckermannDrive


def convert_trans_rot_vel_to_steering_angle(v, omega, wheelbase):
  if omega == 0 or v == 0:
    return 0

  radius = v / omega
  return math.atan(wheelbase / radius)


def cmd_callback(data):
  global wheelbase
  global ackermann_cmd_topic
  global pub
  
  v = data.linear.x
  # v = min(max(v, -0.8), 0.8)
  steering = float(convert_trans_rot_vel_to_steering_angle(v, data.angular.z, wheelbase))
  
  msg = AckermannDrive()
  msg.steering_angle = steering
  msg.speed = v
  
  pub.publish(msg)

if __name__ == '__main__': 
  try:
    rclpy.init(args=sys.argv)
    node = rclpy.create_node('ackermann_msg_bridge')
        
    twist_cmd_topic = node.declare_parameter('~twist_cmd_topic', '/cmd_vel').value 
    ackermann_cmd_topic = node.declare_parameter('~ackermann_cmd_topic', '/ackermann_cmd').value
    wheelbase = node.declare_parameter('~wheelbase', 0.21).value
    
    sub = node.create_subscription(Twist, twist_cmd_topic, cmd_callback, 1)
    pub = node.create_publisher(AckermannDrive, ackermann_cmd_topic, 1)
    
    node.get_logger().info("Node 'ackermann_msg_bridge' started.")
    node.get_logger().info("Listening to %s" %  "/cmd_vel")
    node.get_logger().info("publishing to %s" % ackermann_cmd_topic)
    node.get_logger().info("wheelbase: %f" % wheelbase)
    
    rclpy.spin(node)
    
  except (KeyboardInterrupt, ExternalShutdownException):
      pass

