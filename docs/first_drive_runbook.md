# OSRacer First-Drive Runbook

This runbook is for the first low-speed real-car policy test on Jetson Orin Nano Super 8GB.
Target runtime is JetPack 6.x / Ubuntu 22.04 with ROS 2 Humble.
It is intentionally conservative. Do not skip gates after a failure.

## Preconditions

- Manual override is available and tested.
- Wheels can be lifted off the ground for block tests.
- Battery, steering linkage, and wheel fasteners are checked.
- `policy.pt` has already passed offline export validation.
- The car workspace is built and sourced.
- `osracer_base` was imported from `osracer.repos` and still resolves to the
  current profile-aligned mainline commit.

```bash
source /opt/ros/humble/setup.bash
test "$(git -C src/osracer_base rev-parse HEAD)" = \
  "f2c89dc300c407adb95b8b00bd1d828b6e95dbad"
colcon build --packages-up-to osracer_bringup
source install/setup.bash
```

## Stage 0: Environment Preflight

Run:

```bash
nvpmodel -q

sudo tools/jetson_performance_profile.sh \
  --apply \
  --nvpmodel MODE_ID \
  --jetson-clocks \
  --set-cpu-governor \
  --json-output /tmp/osracer_performance_profile.json

tools/jetson_preflight.sh --policy /path/to/policy.pt --offline-smoke
```

Pass condition:

- `MODE_ID` is selected from this Jetson's `nvpmodel -q` output, not copied from another board.
- Jetson performance profile evidence is saved to `/tmp/osracer_performance_profile.json`.
- ROS setup is found.
- `ackermann_msgs`, `nav_msgs`, `sensor_msgs`, and `geometry_msgs` are available.
- TorchScript load/run passes in the same Python environment used by ROS launch.
- Offline replay smoke completes.

For ONNX deployment packages, build the TensorRT engine on the Jetson after Stage 0:

```bash
tools/build_tensorrt_engine.sh \
  --onnx /path/to/osracer_jetson_deployment/policy.onnx \
  --engine /path/to/osracer_jetson_deployment/policy_fp16.engine \
  --fp16 \
  --workspace-mb 1024 \
  --log /tmp/osracer_trtexec_build.log \
  --report /tmp/osracer_tensorrt_build_report.json
```

Stop if:

- `ackermann_msgs` is missing.
- `torch` is missing from the ROS launch Python environment.
- `policy.pt` cannot load.
- `nvpmodel -q` does not show a known high-performance mode for this Jetson image.

## Stage 1: Start Sensors And Chassis

Start the chassis bridge:

```bash
ros2 launch osracer_bringup chassis_ackermann.launch.py
```

Start sensors as needed:

```bash
ros2 launch osracer_bringup lidar.launch.py
ros2 launch osracer_bringup usb_cam.launch.py
```

Capture the sensor device and ROS topic contract:

```bash
tools/jetson_sensor_preflight.sh \
  --output-dir /tmp/osracer_sensor_preflight \
  --duration 10 \
  --camera-topic /rgb/image_raw \
  --lidar-topic /scan \
  --imu-topic /imu_filter \
  --odom-topic /odometry/filtered
```

Summarize the sensor capture:

```bash
tools/jetson_sensor_summary.py /tmp/osracer_sensor_preflight --strict
```

Run the read-only readiness check:

```bash
tools/real_car_readiness_check.sh \
  --policy /path/to/policy.pt \
  --require-topics
```

Pass condition:

- `/odometry/filtered`, `/imu_filter`, and `/ackermann_cmd` are present.
- One `/odometry/filtered` message and one `/imu_filter` message are received.

Stop if:

- Odom or IMU is stale.
- Topic names do not match the policy node defaults.

## Stage 2: Passive Observation Recording

Drive manually or move the car without enabling policy control:

```bash
ros2 launch osracer_bringup policy_observation_recorder.launch.py \
  output_path:=/tmp/osracer_policy_observations.csv \
  rate_hz:=10.0
```

Record enough data for a representative low-speed pass. Then stop the recorder.

Pass condition:

- `/tmp/osracer_policy_observations.csv` exists.
- The CSV has the policy observation columns:

```text
px,py,pz,roll,pitch,yaw,vx,vy,vz,wx,wy,wz,last_speed,last_steering
```

## Stage 3: Offline Policy Replay

Run:

```bash
tools/policy_replay_csv.py \
  --policy /path/to/policy.pt \
  --input /tmp/osracer_policy_observations.csv \
  --output /tmp/osracer_policy_replay.csv \
  --max-speed-mps 0.3 \
  --max-steering-rad 0.488

tools/policy_replay_summary.py /tmp/osracer_policy_replay.csv \
  --min-rows 100 \
  --max-speed-cmd 0.3 \
  --max-abs-steering-cmd 0.488
```

Pass condition:

- Replay finishes without skipped rows.
- `speed_cmd_max <= 0.3`.
- `abs_steering_cmd_max <= 0.488`.
- Clamp ratio is acceptable for the test; high clamp ratio means the policy is trying to exceed the low-speed envelope.

Stop if:

- Any non-finite row appears.
- Steering sign is opposite of manual logs.
- The summary gate fails.

## Stage 4: MuJoCo Kinematic Replay

This stage depends on the separate `osracer_lab` project; its scripts are not
part of this repository or imported by `osracer.repos`. Run it only from a
known, separately versioned `osracer_lab` checkout. If that project is not
available, stop this policy-validation path instead of treating the missing
script as an OSRacer runtime failure.

From `osracer_lab`:

```bash
OSRACER_MUJOCO_PYTHON=/tmp/osracer_mujoco_venv/bin/python \
python3 scripts/run_sim2real_replay_pipeline.py \
  --observations /tmp/osracer_policy_observations.csv \
  --policy /path/to/policy.pt \
  --output-dir /tmp/osracer_sim2real_replay \
  --min-rows 100 \
  --max-clamped-ratio 0.5
```

Pass condition:

- The pipeline completes.
- Summary gate passes.
- MuJoCo action replay reports plausible travel direction and no unexpected saturation.

Stop if:

- The pipeline fails before MuJoCo.
- MuJoCo replay direction or yaw behavior disagrees with expectations.

## Stage 5: Wheels-Off Closed Loop

Lift the car so wheels cannot touch the ground.

Start policy inference in disabled safe mode first:

```bash
ros2 launch osracer_bringup policy_inference.launch.py \
  policy_path:=/path/to/policy.pt \
  enabled:=False \
  max_speed_mps:=0.3
```

Confirm the node starts and publishes safe stop behavior. Then run enabled with wheels still lifted:

```bash
ros2 launch osracer_bringup policy_inference.launch.py \
  policy_path:=/path/to/policy.pt \
  enabled:=True \
  max_speed_mps:=0.3
```

Pass condition:

- Manual override remains active.
- Watchdog stops stale commands.
- Steering direction is correct.
- No unexpected full-throttle or full-steering behavior appears.

Stop if:

- Any command is non-finite.
- Steering sign is wrong.
- Commands continue after odom/IMU is stopped.

## Stage 6: Ground Low-Speed Test

Only after all prior stages pass, test on the floor:

```bash
ros2 launch osracer_bringup policy_inference.launch.py \
  policy_path:=/path/to/policy.pt \
  enabled:=True \
  max_speed_mps:=0.3
```

Rules:

- Keep manual override active.
- Keep the first run short.
- Save logs for every run.
- Do not raise `max_speed_mps` until offline replay and real behavior agree.

Before enabling closed-loop motion, save a final go/no-go report:

```bash
tools/benchmark_policy_inference.py \
  --policy /path/to/osracer_jetson_deployment/policy.pt \
  --device cuda:0 \
  --output /tmp/osracer_policy_benchmark.json \
  --max-p95-ms 10.0

tools/first_drive_gate.py \
  --package-dir /path/to/osracer_jetson_deployment \
  --policy-replay /tmp/osracer_policy_replay.csv \
  --sensor-summary /tmp/osracer_sensor_preflight/sensor_summary.json \
  --environment-report /tmp/osracer_jetson_environment.json \
  --serial-latency /tmp/osracer_serial_latency.json \
  --policy-benchmark /tmp/osracer_policy_benchmark.json \
  --performance-profile /tmp/osracer_performance_profile.json \
  --runtime-dir /tmp/osracer_runtime_monitor \
  --output /tmp/osracer_first_drive_gate.json

# For ONNX deployment packages, add:
#   --tensorrt-build-report /tmp/osracer_tensorrt_build_report.json

tools/first_drive_evidence_pack.py \
  --gate-report /tmp/osracer_first_drive_gate.json \
  --output-dir /tmp/osracer_first_drive_evidence_pack \
  --overwrite

tools/verify_first_drive_evidence_pack.py /tmp/osracer_first_drive_evidence_pack --require-pass
```

For visual policy packages, the evidence verifier also rechecks the archived
deployment package for CameraInfo-derived camera calibration and confirms the
first-drive gate log contains the deployment verifier's camera-calibration OK
line.
The first-drive gate report itself includes a separate
`camera_calibration_overlay` check for visual packages. The evidence verifier
also rechecks the archived Jetson performance profile and TensorRT build report
when those reports are required.

Stop immediately if:

- The car turns opposite the expected direction.
- Odom or IMU drops out.
- The watchdog does not stop stale commands.
- Manual override fails.
