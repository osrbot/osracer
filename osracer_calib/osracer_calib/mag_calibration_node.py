#!/usr/bin/env python3
"""Magnetometer calibration node (hard-iron + soft-iron via ellipsoid fitting).

Subscribes to raw magnetometer data, collects samples on demand, fits an
ellipsoid, and publishes the calibration result as a latched MagneticField
message compatible with magnetometer_pipeline/bias_remover.

The chassis node (chassis_ackermann.py) publishes magnetometer data as:
  topic : magnetometer_data  (configurable via mag_topic parameter)
  frame : imu_link
  units : Tesla (converted from Gauss in the chassis driver)
  format: sensor_msgs/MagneticField

Calibration result is encoded in a single MagneticField message:
  magnetic_field            → hard-iron offset b  [T]
  magnetic_field_covariance → soft-iron matrix A  (3×3, row-major, 9 values)

Correction: B_calibrated = A * (B_raw - b)

Services
--------
~/start_calibration  (std_srvs/Trigger)  begin sample collection
~/stop_calibration   (std_srvs/Trigger)  compute and publish result

Published topics
----------------
mag_bias_topic  (sensor_msgs/MagneticField, latched)  calibration result
~/status        (std_msgs/String)                      human-readable status

Subscribed topics
-----------------
mag_topic  (sensor_msgs/MagneticField)  raw magnetometer data
"""

import os

from ament_index_python.packages import get_package_share_directory
import numpy as np
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from sensor_msgs.msg import MagneticField
from std_msgs.msg import String
from std_srvs.srv import Trigger
import yaml

from osracer_calib.ellipsoid_fit import compute_calibration


def _default_calib_file() -> str:
    # mag_calibration.yaml in the install tree is a symlink to the source tree.
    # Resolving it gives us the real source config/ directory, so the result
    # file is written directly into the package source regardless of where
    # the workspace is installed.
    share = get_package_share_directory('osracer_calib')
    anchor = os.path.realpath(os.path.join(share, 'config', 'mag_calibration.yaml'))
    return os.path.join(os.path.dirname(anchor), 'result.yaml')


class MagCalibrationNode(Node):

    def __init__(self):
        super().__init__('mag_calibration_node')

        self.declare_parameters(namespace='', parameters=[
            ('mag_topic', 'magnetometer_data'),
            ('mag_bias_topic', 'mag_bias'),
            ('mag_frame', 'imu_link'),
            ('min_samples', 200),
            ('calibration_file', ''),
            ('load_calib_on_start', True),
            ('save_calib_on_stop', True),
        ])

        self._min_samples: int = self.get_parameter('min_samples').value
        raw_path: str = self.get_parameter('calibration_file').value
        self._calib_file: str = raw_path if raw_path else _default_calib_file()
        self._save_on_stop: bool = self.get_parameter('save_calib_on_stop').value
        self._mag_frame: str = self.get_parameter('mag_frame').value

        self._collecting: bool = False
        self._samples: list = []
        self._hard_iron: np.ndarray | None = None
        self._soft_iron: np.ndarray | None = None

        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)

        self._bias_pub = self.create_publisher(
            MagneticField, self.get_parameter('mag_bias_topic').value, latched)
        self._status_pub = self.create_publisher(String, '~/status', 10)

        self.create_subscription(
            MagneticField,
            self.get_parameter('mag_topic').value,
            self._on_mag,
            100,
        )
        self.create_service(Trigger, '~/start_calibration', self._on_start)
        self.create_service(Trigger, '~/stop_calibration', self._on_stop)

        if self.get_parameter('load_calib_on_start').value:
            self._load()

        self._print_usage()

    def _print_usage(self):
        node = self.get_name()
        has_result = os.path.exists(self._calib_file)
        load_on_start = self.get_parameter('load_calib_on_start').value

        lines = [
            '─' * 60,
            'Magnetometer Calibration Node Ready',
            '─' * 60,
        ]

        if has_result and not load_on_start:
            lines += [
                f'  Existing calibration found: {self._calib_file}',
                '  load_calib_on_start is false — previous result NOT loaded.',
                '  To reuse it, restart with:',
                f'    ros2 param set /{node} load_calib_on_start true',
                '  or set load_calib_on_start: true in config/mag_calibration.yaml.',
                '',
            ]
        elif has_result and load_on_start:
            lines += [
                f'  Calibration loaded from: {self._calib_file}',
                '  To recalibrate, follow the steps below.',
                '',
            ]
        else:
            lines += [
                '  No existing calibration found — please run calibration.',
                '',
            ]

        lines += [
            'Calibration steps:',
            '  1. Start the chassis node so magnetometer_data is publishing.',
            '  2. Begin sample collection:',
            f'       ros2 service call /{node}/start_calibration std_srvs/srv/Trigger',
            f'  3. Slowly rotate the robot to cover all orientations',
            f'     (roll, pitch, yaw) until >= {self._min_samples} samples are collected.',
            '  4. Stop collection and compute result:',
            f'       ros2 service call /{node}/stop_calibration std_srvs/srv/Trigger',
            f'  5. Result is saved to: {self._calib_file}',
            '     and published on: ' + self.get_parameter('mag_bias_topic').value,
            '─' * 60,
        ]

        for line in lines:
            self.get_logger().info(line)

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------

    def _on_start(self, _req, resp):
        if self._collecting:
            resp.success = False
            resp.message = 'Calibration already running'
            return resp

        self._samples = []
        self._collecting = True
        msg = (
            f'Collecting magnetometer samples — rotate the sensor in all '
            f'orientations, then call ~/stop_calibration '
            f'(need >= {self._min_samples} samples).'
        )
        self.get_logger().info(msg)
        self._pub_status(msg)
        resp.success = True
        resp.message = msg
        return resp

    def _on_stop(self, _req, resp):
        if not self._collecting:
            resp.success = False
            resp.message = 'No calibration in progress'
            return resp

        self._collecting = False
        n = len(self._samples)

        if n < self._min_samples:
            msg = f'Too few samples ({n}/{self._min_samples}) — calibration aborted.'
            self.get_logger().warning(msg)
            self._pub_status(msg)
            resp.success = False
            resp.message = msg
            return resp

        self.get_logger().info(f'Fitting ellipsoid to {n} samples...')
        try:
            hard_iron, soft_iron = compute_calibration(np.array(self._samples))
        except (ValueError, np.linalg.LinAlgError) as exc:
            msg = f'Ellipsoid fitting failed: {exc}'
            self.get_logger().error(msg)
            self._pub_status(msg)
            resp.success = False
            resp.message = msg
            return resp

        self._hard_iron = hard_iron
        self._soft_iron = soft_iron
        self._log_result(n)
        self._publish_bias()
        if self._save_on_stop:
            self._save()

        resp.success = True
        resp.message = f'Calibration complete ({n} samples).'
        return resp

    # ------------------------------------------------------------------
    # Subscriber
    # ------------------------------------------------------------------

    def _on_mag(self, msg: MagneticField):
        if msg.header.frame_id:
            self._mag_frame = msg.header.frame_id
        if self._collecting:
            f = msg.magnetic_field
            self._samples.append([f.x, f.y, f.z])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _publish_bias(self):
        b = self._hard_iron
        A = self._soft_iron
        msg = MagneticField()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self._mag_frame
        msg.magnetic_field.x = float(b[0])
        msg.magnetic_field.y = float(b[1])
        msg.magnetic_field.z = float(b[2])
        msg.magnetic_field_covariance = [float(A[r, c]) for r in range(3) for c in range(3)]
        self._bias_pub.publish(msg)

    def _pub_status(self, text: str):
        msg = String()
        msg.data = text
        self._status_pub.publish(msg)

    def _log_result(self, n: int):
        b, A = self._hard_iron, self._soft_iron
        mc_cmd = (
            f'mc set '
            f'{b[0]:.6f} {b[1]:.6f} {b[2]:.6f}  '
            f'{A[0, 0]:.6f} {A[0, 1]:.6f} {A[0, 2]:.6f}  '
            f'{A[1, 0]:.6f} {A[1, 1]:.6f} {A[1, 2]:.6f}  '
            f'{A[2, 0]:.6f} {A[2, 1]:.6f} {A[2, 2]:.6f}'
        )
        self.get_logger().info(
            f'Calibration result ({n} samples)\n'
            f' hard-iron [T]: [{b[0]:.6f}, {b[1]:.6f}, {b[2]:.6f}]\n'
            f' soft-iron matrix:\n'
            f' {A[0, 0]:.6f} {A[0, 1]:.6f} {A[0, 2]:.6f}\n'
            f' {A[1, 0]:.6f} {A[1, 1]:.6f} {A[1, 2]:.6f}\n'
            f' {A[2, 0]:.6f} {A[2, 1]:.6f} {A[2, 2]:.6f}\n'
            f' copy-paste command:\n'
            f' {mc_cmd}'
        )
        self._pub_status(f'Done. hard-iron={b.tolist()}')

    def _save(self):
        b, A = self._hard_iron, self._soft_iron
        os.makedirs(os.path.dirname(self._calib_file), exist_ok=True)
        payload = {
            'hard_iron': [float(b[0]), float(b[1]), float(b[2])],
            'soft_iron_matrix': [float(v) for v in A.flatten()],
        }
        try:
            with open(self._calib_file, 'w') as f:
                yaml.safe_dump(payload, f)
            self.get_logger().info(f'Calibration saved → {self._calib_file}')
        except OSError as exc:
            self.get_logger().error(f'Failed to save calibration: {exc}')

    def _load(self):
        if not os.path.exists(self._calib_file):
            return
        try:
            with open(self._calib_file) as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                self.get_logger().warning(
                    f'Calibration file is empty or invalid, skipping: {self._calib_file}')
                return
            self._hard_iron = np.array(data['hard_iron'])
            self._soft_iron = np.array(data['soft_iron_matrix']).reshape(3, 3)
            self.get_logger().info(f'Loaded calibration ← {self._calib_file}')
            self._publish_bias()
        except Exception as exc:
            self.get_logger().warning(f'Could not load calibration: {exc}')


def main(args=None):
    rclpy.init(args=args)
    try:
        rclpy.spin(MagCalibrationNode())
    except (ExternalShutdownException, KeyboardInterrupt):
        pass
    finally:
        rclpy.try_shutdown()
