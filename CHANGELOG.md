# Changelog

## Unreleased

### ROS interface and TF

- No firmware serial protocol changes. The chassis node still consumes the
  current osrcore `stream sync` telemetry:
  `s px py pz vx vy vz yaw qx qy qz qw ax ay az gx gy gz`.
- Limited OSRacer front steering joints in the URDF to the measured steering
  range (`±0.5236 rad`) while keeping wheel rotation joints continuous.
- Added non-zero default covariance values to the ROS IMU messages published by
  `osracer_bringup/chassis_ackermann.py` so EKF consumers do not interpret IMU
  orientation, angular velocity, or linear acceleration as perfect readings.
- The `s` frame quaternion is treated as the current osrcore yaw-zero-relative
  Madgwick full attitude, preserving roll/pitch while keeping yaw relative to
  the latest firmware `odom reset`.
- Moved wheel and steering joint state animation out of
  `osracer_bringup/chassis_ackermann.py` and into `osracer_description`, keeping
  the chassis driver focused on serial protocol, odometry, IMU, battery, RC, and
  chassis TF.
- Added `osracer_joint_state_publisher.py` in `osracer_description` to publish
  model-only wheel and steering `/joint_states` from `/odom` data and current
  command steering.
- The model joint publisher stamps `/joint_states` with the incoming odometry
  timestamp so `odom -> base_footprint` and `base_link -> wheel` transforms stay
  in the same TF time domain.
- Updated model animation defaults to measured OSRacer geometry:
  `wheel_radius=0.0425`, `track_width=0.215`, `wheelbase=0.285`,
  `max_steering_angle_deg=30.0`.

### Demo and RViz

- Added missing runtime dependencies for `osracer_demo` advanced scripts:
  `osracer_debug`, `osracer_navigation`, and `osracer_slam`.
- Enabled robot model display in odometry, mapping, and navigation RViz configs
  so the model and wheel/steering joint animation are visible during demos.
- Removed stale RViz TF frame names from older robot configs and aligned them
  with the current OSRacer URDF frame names, including `laser` and wheel link
  frames.

### Navigation and SLAM

- Changed AMCL from the omnidirectional motion model to the differential motion
  model in both Nav2 parameter sets, matching the non-holonomic OSRacer chassis
  more closely for localization.
- Updated TEB car-like model parameters to the measured OSRacer wheelbase
  (`0.285 m`) and a steering-limited minimum turning radius (`0.50 m`).
- Normalized Nav2 parameter files to use real-time clocks by default
  (`use_sim_time: False`) for true robot navigation.
- Fixed the standalone `osracer_slam` SLAM Toolbox launch file so it loads the
  installed mapper parameters from `param/mapper_params_online_async.yaml`.

### Package metadata

- Aligned ROS package license metadata with the root MIT license and removed the
  remaining `TODO` license marker. The `osracer_navigation` package also keeps
  `Apache-2.0` in its metadata for the Nav2-derived launch files that retain
  their upstream Apache-2.0 headers.
