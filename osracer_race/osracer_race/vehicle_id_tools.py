class VehicleObservation:
    def __init__(self):
        self.max_speed = 0.0
        self.last_speed = None
        self.last_time_s = None
        self.max_accel = 0.0
        self.max_brake = 0.0
        self.max_yaw_rate = 0.0
        self.min_turning_radius = None

    def update(self, speed, yaw_rate, time_s):
        self.max_speed = max(self.max_speed, abs(speed))
        self.max_yaw_rate = max(self.max_yaw_rate, abs(yaw_rate))
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
        self.last_speed = speed
        self.last_time_s = time_s

    def to_ros_parameters_yaml(self, vehicle_params):
        min_turning_radius = (
            f'{self.min_turning_radius:.3f}' if self.min_turning_radius is not None else 'null')
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
            f'    observed_min_turning_radius_m: {min_turning_radius}',
            '    source: odometry_observation',
            '',
        ]
        return '\n'.join(lines)
