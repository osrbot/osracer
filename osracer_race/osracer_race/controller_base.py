from ackermann_msgs.msg import AckermannDrive
from std_msgs.msg import Bool


class RaceControllerMixin:
    def setup_race_controller(self):
        self.declare_parameter('ackermann_topic', '/ackermann_cmd')
        self.declare_parameter('safety_stop_topic', '/race/safety_stop')
        self.safety_stop = False
        self.cmd_pub = self.create_publisher(
            AckermannDrive, self.get_parameter('ackermann_topic').value, 10)
        self.create_subscription(
            Bool, self.get_parameter('safety_stop_topic').value, self.safety_callback, 10)

    def safety_callback(self, msg):
        self.safety_stop = msg.data
        if self.safety_stop:
            self.publish_command(0.0, 0.0)

    def publish_command(self, speed, steering):
        msg = AckermannDrive()
        if self.safety_stop:
            msg.speed = 0.0
            msg.steering_angle = 0.0
        else:
            msg.speed = float(speed)
            msg.steering_angle = float(steering)
        self.cmd_pub.publish(msg)
