# Changelog

## Unreleased

### Simulation

- Added `osracer_sim` with a lightweight kinematic Ackermann simulator, modern
  Gazebo Sim world entry, SLAM/Nav2 simulation launch files, and race-stage
  simulation launch coverage for the four-stage OSRacer race workflow.
- Added a rectangular-track raycast `/scan` environment and matching Gazebo
  world so Gap Follow, TTC safety, raceline recording, and controller smoke
  tests are not limited to a static hallway scan.
- Added an installable `model://osracer_simple` Gazebo model with measured
  OSRacer wheelbase, track width, wheel radius, and Ackermann joint names as the
  next step toward Gazebo control-plugin integration.
- Added Gazebo-native LiDAR/IMU sensors on the simplified model and an optional
  `ros_gz_bridge` launch path for `/gazebo/scan`, `/gazebo/imu`, and `/clock`.
- Added `gazebo_ackermann_bridge_node` to translate `/ackermann_cmd` into
  Gazebo steering-position and wheel-velocity joint controller topics.
- Added optional circular obstacle injection for the kinematic `/scan` so race
  safety, Gap Follow, and overtaking flows can be smoke-tested without hardware.
- Added `eval_output_csv` passthrough in `race_sim.launch.py` for comparable
  simulation evaluation logs across Gap Follow, Pure Pursuit, Stanley, and MPC.
- Added `validate_sim_ros.sh` and Docker coverage for installed simulation
  launch entries, race-stage arguments, obstacle scenarios, and Gazebo control
  arguments.

### Navigation

- Aligned the TEB `max_vel_theta` limit with the measured OSRacer
  `max_vel_x` and `min_turning_radius` so Ackermann navigation can plan turns
  consistent with the configured `0.50m` minimum turning radius.

### Race mode

- Added `osracer_race` as a standalone ROS 2 race package for RoboRacer-style
  Ackermann racing, separate from demo and Nav2 navigation flows.
- Added measured 1/10 OSRacer vehicle parameters, safe/fast race parameter
  presets, and an example raceline CSV.
- Implemented first-stage race nodes for TTC safety stop, Follow-the-Gap
  driving, and lap timing.
- Added raceline speed-profile tooling, Pure Pursuit, Stanley, speed-profile,
  vehicle-identification YAML export, and a lightweight kinematic MPC controller
  for the staged race development roadmap.
- Added race evaluation CSV logging and offline unit tests for the core raceline
  and vehicle-geometry math.
- Added a shared race-controller safety gate so Pure Pursuit, Stanley, and MPC
  stop publishing motion commands when `/race/safety_stop` is active.
- Routed race controllers through `/race/raw_ackermann_cmd` and upgraded
  `speed_profile_node` into a final command limiter for speed, braking,
  steering, lateral-acceleration, and safety-stop constraints.
- Added `race_report_tools` to summarize race evaluation CSV logs for
  teaching/research comparisons across controllers.
- Added `race_bringup.launch.py` to start OSRacer bringup and the selected race
  controller from one command.
- Added `obstacle_overtake_node` as a raceline-controller middle layer for
  low-speed side selection around close front obstacles before final command
  limiting.
- Added `PHASES_zh.md` to document the four-stage race development coverage and
  current verification status.
- Added `scripts/check_race_package.sh` as a repeatable local/ROS-machine
  validation entry point for the race package.
- Added `track_recorder_node` and `track_record.launch.py` to record odometry
  paths as raceline CSV input for stage-two trajectory tracking.
- Added `eval_output_csv` launch arguments across race controllers and enabled
  CSV evaluation logging for `gap_follow.launch.py`.
- Added `ROS_VALIDATION_zh.md` with ROS/vehicle validation steps for all four
  race-development phases.
- Updated `check_race_package.sh` to run `colcon build --packages-select
  osracer_race` automatically when ROS 2 Humble and colcon are available.
- Made raceline CSV loading accept plain `x,y,speed,curvature` headers in
  addition to commented headers, matching the documented examples.
- Extended race vehicle identification output with observed max yaw rate,
  maximum lateral acceleration, and minimum turning radius for steering-limit
  calibration.
- Added basic speed-step motor response time constant and steering-step response
  delay observation to the race vehicle identification node.
- Constrained MPC speed candidates with vehicle acceleration, braking, and
  speed-response limits so the shooting controller stays within reachable speed
  changes.
- Added raceline target-speed tracking and path-progress reward terms to the
  MPC shooting cost.
- Fixed the Docker ROS Humble stable profile so it builds the TEB /
  `costmap_converter` chain required by the default navigation planner.
- Removed unused bringup demo/test scripts and old STM32/no-RC chassis variants
  from the ROS package surface.
- Removed duplicate `osracer_navigation/maps` files and pointed the navigation
  default map to the retained `osracer_slam/maps` example map.
- Hardened race evaluation summaries so empty, `nan`, and `inf` log fields do
  not produce misleading zero or `nan` comparison metrics.
- Added fail-safe race safety behavior for front-LiDAR dropout: when no valid
  front scan points are available, `/race/safety_stop` defaults to active.
- Added `scan_timeout_s` to `safety_node` so full `/scan` stream loss also
  activates `/race/safety_stop` and publishes stop commands.
- Added a command watchdog in `speed_profile_node` so stale upstream race
  commands are converted to a stop command after `command_timeout_s`.
- Trimmed unused `osracer_race` package dependencies and added metadata checks
  for the race package install surface.
- Made `vehicle_id.launch.py` accept the shared `race_config` argument and
  documented the installed self-check command with an explicit `bash` wrapper.
- Aligned race README self-check usage with the installed validation command
  and added tests that documented launch/run commands match package entries.
- Linked the top-level README race section to the dedicated race usage,
  four-stage roadmap, and ROS/vehicle validation documents.
- Expanded the race usage README into a detailed tutorial covering setup,
  self-checks, topic flow, staged operation, vehicle-side validation, and
  troubleshooting.
- Added a Docker-based Ubuntu 22.04 + ROS 2 Humble compile-check environment
  for macOS pre-push validation.
- Added stable/full Docker build profiles so pinned third-party dependencies can
  be checked separately from the stable deployment path.
- Added ROS Humble Docker dependencies for serial, Tk demo GUI,
  `tf_transformations`, `libg2o`, and suitesparse so the full TEB and
  `costmap_converter` dependency chain builds in the macOS pre-push check.
- Made package-limited Docker checks skip `osracer_race` installed entry checks
  when that package was not part of the selected build.
- Updated package manifests for bringup, navigation, SLAM, calibration, debug,
  and demo packages to declare their actual ROS 2 runtime dependencies.
- Replaced legacy placeholder and personal maintainer metadata with the OSRBot
  maintainer address across package manifests.
- Aligned `twist_bridge.py` with the measured `0.285m` OSRacer wheelbase and
  added configurable odometry twist covariance for EKF fusion.
- Added `PRE_PUSH_REVIEW_zh.md` to record verified static checks, cleanup scope,
  package boundaries, and remaining ROS/vehicle validation items before pushing.
- Reused signed-curvature-safe speed limiting in Pure Pursuit so externally
  generated racelines with negative curvature still reduce corner speed.
- Applied the same raceline curvature speed limiting to Stanley tracking
  commands before obstacle handling and final command limiting.
- Fixed obstacle overtake speed handling so zero or reverse tracking commands
  are not converted into forward overtake motion.
- Sanitized non-finite upstream race commands in `speed_profile_node` so `nan`
  or `inf` speed/steering inputs become zero-speed, zero-steering requests
  before final limiting.
- Replaced temporary `osracer_race` maintainer metadata and fixed the top-level
  workspace path typo in the git update example.
- Removed an unused race helper function from `common.py` after the controller
  implementations settled on direct scan handling.
- Added a regression check that `race_bringup.launch.py` still includes the
  existing `osracer_bringup/launch/bringup.launch.py` entry point.
- Made `race_fast.yaml` explicitly declare the same key runtime topics, safety
  stop topic, and lap-timer parameters as `race_safe.yaml`.
- Documented `race_fast.yaml` as a full runtime parameter file rather than a
  partial speed-only override.
- Expanded `race_safe.yaml` and `race_fast.yaml` to explicitly include tracking,
  MPC, overtake, recorder, evaluator, and watchdog runtime parameters.
- Added ROS-side launch `--show-args` checks to the race validation checklist so
  installed launch files are verified before vehicle tests.
- Added package discovery checks for `osracer_race` resource marker and
  console-script install paths.
- Moved Gap Follow gap selection into an offline-tested helper and changed the
  target from the gap edge to the gap center so clear scans do not induce
  unnecessary steering and minimum-speed output.
- Moved Pure Pursuit and Stanley tracking geometry into offline-tested helpers
  covering straight-line steering, cross-track steering sign, and curvature
  speed limiting.
- Moved lightweight MPC rollout, cost, and command search math into
  offline-tested helpers covering straight-line prediction, corner speed
  limiting, and straight-path steering selection.
- Moved obstacle-overtake scan summary, hysteresis, and side-selection logic into
  offline-tested helpers covering trigger/clear behavior, stop-command passthrough,
  and steering toward the wider side.
- Moved race safety front-scan and TTC calculations into offline-tested helpers
  covering front-FOV filtering, emergency-distance stop, TTC stop, and reversing
  without false TTC triggers.
- Moved final speed-profile limiting into an offline-tested helper covering
  non-finite command sanitization, steering clamp, acceleration limiting,
  braking limiting, and curvature speed limiting.
- Moved race evaluation and track-recording formatting into offline-tested
  helpers covering stable CSV headers, track-error sign, point spacing, and
  default recorded speed handling.
- Split vehicle-identification observation math into a pure helper with offline
  tests for speed, acceleration, braking, yaw-rate, and turning-radius outputs.
- Updated `check_race_package.sh` to support both source-tree checks and
  installed `share/osracer_race` checks.
- Added helper-module import smoke tests to `check_race_package.sh` so source
  and installed layouts both verify the shared race math modules are importable.
- Aligned raceline launch argument descriptions and validation documentation with
  the supported `x,y,speed,curvature` CSV format and helper import smoke checks.
- Added static checks that every source-tree `*_node.py` module defines `main()`
  and has a matching `console_scripts` entry.
- Added a race package license consistency check tying `package.xml` and
  `setup.py` MIT metadata to the root MIT `LICENSE`.
- Made race unit tests skip source-tree-only metadata checks when running from
  an installed package layout.
- Declared `python3-yaml` as an `osracer_race` runtime dependency because the
  installed self-check validates YAML configuration files.
- Documented and tested the supported `race_bringup.launch.py controller` values:
  `gap_follow`, `pure_pursuit`, `stanley`, and `mpc`.
- Added a shell syntax self-check to `check_race_package.sh`.
- Added `ros2 launch ... --show-args` checks to the race self-check when ROS 2
  is available.
- Added `validate_race_ros.sh` as a vehicle-side non-motion validation helper
  for installed resources, CLI tools, launch arguments, and optional topic
  visibility.
- Expanded ROS-side self-checks to cover every `osracer_race` launch file with
  `--show-args`.
- Restricted `race_bringup.launch.py controller` with launch choices and limited
  obstacle-overtake startup to the raceline controllers.

### ROS interface and TF

- No firmware serial protocol changes. The chassis node still consumes the
  current osrcore `stream sync` telemetry:
  `s px py pz vx vy vz yaw qx qy qz qw ax ay az gx gy gz`.
- Added configurable odometry twist covariance as ROS `nav_msgs/Odometry`
  metadata only; this does not change the osrcore serial frame.
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

- Merged the ROS-only parts of the local `osracer_demo` tree into the
  `osracer_demo` package, excluding firmware sources, hardware PDFs, partition
  tables, and local machine configuration.
- Added installed helper scripts for workspace build, odometry RViz, and
  demo-only low-speed Nav2 parameter generation.
- Navigation demo scripts now use generated low-speed TEB parameters for field
  demos without modifying the formal `osracer_navigation` parameter files.
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
