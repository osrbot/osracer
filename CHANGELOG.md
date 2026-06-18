# Changelog

## Unreleased

### ROS interface and TF

- No firmware serial protocol changes. The chassis node still consumes the
  current osrcore `stream sync` telemetry:
  `s px py pz vx vy vz yaw qx qy qz qw ax ay az gx gy gz`.
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
