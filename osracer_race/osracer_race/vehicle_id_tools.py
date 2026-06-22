class VehicleObservation:
    def __init__(self):
        self.max_speed = 0.0
        self.last_speed = None
        self.last_time_s = None
        self.last_yaw_rate = None
        self.last_command_speed = None
        self.last_command_steering = None
        self.max_accel = 0.0
        self.max_brake = 0.0
        self.max_yaw_rate = 0.0
        self.max_lateral_accel = 0.0
        self.min_turning_radius = None
        self.motor_response_tau_s = None
        self.steering_response_delay_s = None
        self.pending_speed_step = None
        self.pending_steering_step = None

    def update(self, speed, yaw_rate, time_s):
        self.max_speed = max(self.max_speed, abs(speed))
        self.max_yaw_rate = max(self.max_yaw_rate, abs(yaw_rate))
        self.max_lateral_accel = max(self.max_lateral_accel, abs(speed * yaw_rate))
        if abs(speed) > 0.2 and abs(yaw_rate) > 0.05:
            radius = abs(speed / yaw_rate)
            if self.min_turning_radius is None or radius < self.min_turning_radius:
                self.min_turning_radius = radius
        if self.last_time_s is not None:
            dt = time_s - self.last_time_s
            if dt > 0.0:
                accel = (speed - self.last_speed) / dt
                self.max_accel = max(self.max_accel, accel)
                self.max_brake = min(self.max_brake, accel)
        self.update_response_estimates(speed, yaw_rate, time_s)
        self.last_speed = speed
        self.last_yaw_rate = yaw_rate
        self.last_time_s = time_s

    def update_command(self, speed, steering, time_s):
        if self.last_command_speed is not None:
            speed_step = speed - self.last_command_speed
            start_speed = self.last_speed if self.last_speed is not None else 0.0
            speed_delta = speed - start_speed
            if speed_step > 0.30 and speed_delta > 0.30:
                self.pending_speed_step = {
                    'time_s': time_s,
                    'start_speed': start_speed,
                    'target_speed': speed,
                }

        if self.last_command_steering is not None:
            steering_step = steering - self.last_command_steering
            moving = self.last_speed is not None and abs(self.last_speed) > 0.3
            if moving and abs(steering_step) > 0.08:
                self.pending_steering_step = {
                    'time_s': time_s,
                    'baseline_yaw_rate': self.last_yaw_rate if self.last_yaw_rate is not None else 0.0,
                }

        self.last_command_speed = speed
        self.last_command_steering = steering

    def update_response_estimates(self, speed, yaw_rate, time_s):
        if self.motor_response_tau_s is None and self.pending_speed_step is not None:
            step = self.pending_speed_step
            target = step['target_speed']
            start = step['start_speed']
            threshold = start + 0.632 * (target - start)
            if speed >= threshold:
                self.motor_response_tau_s = max(time_s - step['time_s'], 0.0)
                self.pending_speed_step = None

        if self.steering_response_delay_s is None and self.pending_steering_step is not None:
            step = self.pending_steering_step
            if abs(yaw_rate - step['baseline_yaw_rate']) >= 0.15:
                self.steering_response_delay_s = max(time_s - step['time_s'], 0.0)
                self.pending_steering_step = None

    def to_ros_parameters_yaml(self, vehicle_params):
        min_turning_radius = (
            f'{self.min_turning_radius:.3f}' if self.min_turning_radius is not None else 'null')
        motor_response_tau = (
            f'{self.motor_response_tau_s:.3f}' if self.motor_response_tau_s is not None else 'null')
        steering_response_delay = (
            f'{self.steering_response_delay_s:.3f}'
            if self.steering_response_delay_s is not None else 'null')
        lines = [
            'vehicle_identified:',
            '  ros__parameters:',
            f'    wheel_radius: {vehicle_params["wheel_radius"]}',
            f'    wheelbase: {vehicle_params["wheelbase"]}',
            f'    track_width: {vehicle_params["track_width"]}',
            f'    gear_ratio: {vehicle_params["gear_ratio"]}',
            f'    mass_kg: {vehicle_params["mass_kg"]}',
            f'    max_steering_angle_deg: {vehicle_params["max_steering_angle_deg"]}',
            f'    observed_max_speed_mps: {self.max_speed:.3f}',
            f'    observed_max_accel_mps2: {self.max_accel:.3f}',
            f'    observed_max_brake_mps2: {abs(self.max_brake):.3f}',
            f'    observed_max_yaw_rate_rps: {self.max_yaw_rate:.3f}',
            f'    observed_max_lateral_accel_mps2: {self.max_lateral_accel:.3f}',
            f'    observed_min_turning_radius_m: {min_turning_radius}',
            f'    observed_motor_response_tau_s: {motor_response_tau}',
            f'    observed_steering_response_delay_s: {steering_response_delay}',
            '    source: odometry_observation',
            '',
        ]
        return '\n'.join(lines)
