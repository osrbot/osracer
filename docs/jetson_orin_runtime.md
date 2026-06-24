# Jetson Orin Runtime for OSRacer

This document is for the real OSRacer compute platform. Current target hardware is Jetson Orin Nano Super 8GB running JetPack 6.x / Ubuntu 22.04 with ROS 2 Humble.
For the first real-car low-speed test sequence, follow `docs/first_drive_runbook.md`.

## Split of Responsibility

Use a separate workstation for training, simulation, and heavy dataset
preparation. Use Jetson for runtime inference and real-robot control.

```text
External workstation
  IsaacLab / MuJoCo
  train policy
  export TorchScript / optional ONNX
  sim2sim and replay validation

Jetson Orin Nano Super 8GB
  ROS 2 Humble runtime
  policy inference
  TensorRT / TorchScript / ONNX Runtime
  sensor preprocessing
  /ackermann_cmd publishing
```

Do not plan on large-scale RL training on Jetson. The Jetson should be optimized for deterministic, low-latency inference.

## Runtime Targets

Recommended first target:

- Drift policy
- TorchScript `policy.pt`
- 10 Hz inference target
- `max_speed_mps=0.3` for initial tests
- `max_steering_rad=0.488`
- publish `ackermann_msgs/msg/AckermannDrive` to `/ackermann_cmd`

Later targets:

- ONNX export and TensorRT engine build
- Visual policy with camera preprocessing matched to simulation
- MuJoCo sim2sim regression before every real-car test

## Jetson Setup Checklist

1. Install JetPack 6.x / Jetson Linux 36.x.
2. Use NVMe for workspace, logs, and model artifacts when possible.
3. Configure a high-performance power mode before runtime tests.
4. Keep thermal throttling visible with `tegrastats` or `jtop`.
5. Install ROS 2 Humble packages and OSRacer workspace dependencies.
6. Install `ros-humble-ackermann-msgs`.
7. Install Torch for the exact Python used by `ros2 launch`.
8. Run `tools/jetson_preflight.sh`.
9. Review and apply the runtime performance profile before latency-sensitive tests.

Example:

```bash
sudo apt install ros-humble-ackermann-msgs
python3 -m pip install torch
tools/jetson_preflight.sh /path/to/policy.pt
```

Review Jetson power, clocks, CPU governors, swap/zram, and storage without
changing the system:

```bash
tools/jetson_performance_profile.sh
```

After checking the target Jetson's `nvpmodel -q` output, apply a repeatable
runtime profile. `MODE_ID` is board/image specific, so do not hard-code it
without checking the target device first:

```bash
sudo tools/jetson_performance_profile.sh \
  --apply \
  --nvpmodel MODE_ID \
  --jetson-clocks \
  --set-cpu-governor \
  --json-output /tmp/osracer_performance_profile.json
```

Run a structured Jetson environment report and keep it with the first-drive evidence:

```bash
tools/jetson_environment_report.py --output /tmp/osracer_jetson_environment.json
```

Run the optional offline replay smoke when `policy.pt` is available:

```bash
tools/jetson_preflight.sh --policy /path/to/policy.pt --offline-smoke --environment-output /tmp/osracer_jetson_environment.json
```

After starting camera, lidar, chassis, IMU, and odometry drivers, capture the
actual sensor device and ROS topic contract:

```bash
tools/jetson_sensor_preflight.sh \
  --output-dir /tmp/osracer_sensor_preflight \
  --duration 10 \
  --camera-topic /rgb/image_raw \
  --lidar-topic /scan \
  --imu-topic /imu_filter \
  --odom-topic /odometry/filtered
```

Keep the generated `summary.md`, `sensor_summary.json`, and logs with the real-car
measurement JSON. This is the quickest way to prove AR0234 camera visibility,
25m lidar network or USB visibility, topic frame names, and measured topic rates
on Orin Nano. For a gate after collecting logs, run:

```bash
tools/jetson_sensor_summary.py /tmp/osracer_sensor_preflight --strict
```

For the sim2real measurement pack, prefer the combined read-only session. It
captures sensor topic evidence, Jetson environment, serial latency, and one
`CameraInfo` sample for AR0234 intrinsics import:

```bash
tools/jetson_measurement_session.sh \
  --output-dir /tmp/osracer_measurement_session \
  --camera-topic /rgb/image_raw \
  --lidar-topic /scan \
  --imu-topic /imu_filter \
  --odom-topic /odometry/filtered \
  --camera-info-topic /camera_info
```

If the policy came from an `osracer_lab` deployment package, verify the package
first:

```bash
tools/verify_jetson_deployment.py /path/to/osracer_jetson_deployment
# Visual packages fail verification unless measured_overlay.json includes
# CameraInfo-derived camera calibration at the runtime resolution.
tools/jetson_preflight.sh \
  --policy /path/to/osracer_jetson_deployment/policy.pt \
  --offline-smoke
```

The preflight is read-only. It reports Jetson Linux, power-mode tools, memory,
swap/zram, disk space, CPU governor, ROS packages, Python inference packages,
and optional policy replay. Use it before changing power settings so the
baseline is recorded.

Run a read-only real-car readiness check before enabling live policy commands:

```bash
tools/real_car_readiness_check.sh \
  --policy /path/to/policy.pt \
  --observations /tmp/osracer_policy_observations.csv \
  --replay /tmp/osracer_policy_replay.csv
```

On Jetson, prefer NVIDIA-provided or JetPack-compatible Python wheels for acceleration libraries.


## ONNX and TensorRT Path

For the first MLP drift policy, TorchScript is the lowest-risk runtime. For
visual policies or any deployment that starts competing with camera processing
for the 8GB memory budget, export ONNX and build a TensorRT engine on the target
Jetson.

Export ONNX from `osracer_lab`:

```bash
~/rlgpu_ws/IsaacLab/isaaclab.sh -p scripts/export_osracer_policy.py \
  --headless \
  --checkpoint /path/to/model.pt \
  --format onnx \
  --output_dir /tmp/osracer_policy_export
```

Build a TensorRT engine on Jetson after applying the performance profile:

```bash
tools/build_tensorrt_engine.sh \
  --onnx /tmp/osracer_policy_export/policy.onnx \
  --engine /tmp/osracer_policy_export/policy_fp16.engine \
  --fp16 \
  --workspace-mb 1024 \
  --log /tmp/osracer_policy_export/trtexec_build.log
```

Use `--dry-run` first to record the exact `trtexec` command. Keep batch size 1
for live control unless a batch-specific offline benchmark proves otherwise.
Summarize the TensorRT timing log with the same benchmark report format:

```bash
tools/benchmark_policy_inference.py \
  --format trtexec-log \
  --trtexec-log /tmp/osracer_policy_export/trtexec_build.log \
  --output /tmp/osracer_policy_export/trtexec_benchmark.json
```

Deployment packages are format-aware:

```bash
tools/verify_jetson_deployment.py /path/to/package
```

The verifier runs TorchScript load checks for `torchscript`, ONNX checker for
`onnx`, and a structural engine check plus optional `trtexec --loadEngine` for
`tensorrt`.

## Runtime Launch Flow

Build and source the OSRacer workspace:

```bash
source /opt/ros/humble/setup.bash
colcon build --packages-select osracer_bringup
source install/setup.bash
```

Start the chassis bridge:

```bash
ros2 launch osracer_bringup chassis_ackermann.launch.py
```

Start policy inference in safe disabled mode:

```bash
ros2 launch osracer_bringup policy_inference.launch.py \
  policy_path:=/path/to/policy.pt
```

Only enable non-zero commands after manual override, odometry, IMU, and watchdog behavior are verified:

```bash
ros2 launch osracer_bringup policy_inference.launch.py \
  policy_path:=/path/to/policy.pt \
  enabled:=True \
  max_speed_mps:=0.3
```

## Sim2Sim Plan

The purpose of sim2sim is to catch dynamics and observation-contract mistakes before real-car tests.

Minimum useful sim2sim checks:

- Same action contract: `[target_speed_mps, target_steering_rad]`
- Same observation order as `policy_inference.py`
- Same wheelbase `0.285`
- Same steering clamp `0.488 rad`
- Similar actuator delay and steering response
- Similar yaw and odometry conventions

Recommended path:

1. Keep IsaacLab as the high-throughput training simulator.
2. Add a MuJoCo model with the same action and observation contract.
3. Load exported `policy.pt` and run closed-loop MuJoCo rollouts.
4. Compare speed, yaw rate, turn radius, and termination behavior against IsaacLab rollouts.
5. Only move to the real car after both simulators agree on basic behavior.

## Sim2Real Plan

Do not start with full closed-loop policy driving.

Stage 1: passive logs

- Run chassis, odom, IMU, and sensors.
- Record manual driving bags.
- Validate observation builder against recorded `/odom` and `/imu`.

Record the exact drift-policy observation CSV:

```bash
ros2 launch osracer_bringup policy_observation_recorder.launch.py \
  output_path:=/tmp/osracer_policy_observations.csv \
  rate_hz:=10.0
```

Stage 2: offline replay

- Feed recorded observations into `policy.pt`.
- Check action magnitude, steering sign, saturation rate, and non-finite handling.
- Confirm the policy would stay within low-speed limits.

Replay CSV format:

```text
px,py,pz,roll,pitch,yaw,vx,vy,vz,wx,wy,wz,last_speed,last_steering
```

`last_speed` and `last_steering` default to `0` when omitted, which is useful for first-pass manual driving logs. Use `--strict-last-action` when checking policy closed-loop logs.

Example:

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

The output appends:

```text
action_speed_raw,action_steering_raw,speed_cmd,steering_cmd,clamped
```

Use the replay output to check:

- Whether `speed_cmd` stays inside the current test envelope.
- Whether steering sign matches manual driving logs.
- How often the policy saturates at the steering or speed clamp.
- Whether any row fails because odometry, IMU, or preprocessing produced non-finite values.

For Jetson deployment, run the same replay on the Jetson Python environment before enabling live ROS control:

```bash
tools/policy_replay_csv.py --policy /path/to/policy.pt --input /path/to/log.csv --output /tmp/replay.csv
```

Stage 3: low-speed closed loop

- Use `enabled:=True`.
- Keep `max_speed_mps <= 0.3`.
- Keep RC/manual override active.
- Test on blocks before floor tests.

Stage 4: expand envelope

- Increase speed only after watchdog, stale input stop, and manual override are proven.
- Keep logs for every test run.

## Performance Notes

Measure before optimizing:

```bash
tegrastats
ros2 topic hz /ackermann_cmd
ros2 topic hz /odom
ros2 topic hz /imu_filter
```


Run a local policy inference latency benchmark after applying the performance
profile and before enabling live commands:

```bash
tools/benchmark_policy_inference.py \
  --policy /path/to/policy.pt \
  --warmup 50 \
  --iterations 500 \
  --output /tmp/osracer_policy_benchmark.json
```

Use `--max-p95-ms` to turn the benchmark into a gate once a target-device
baseline is recorded. For the 10 Hz first-drive target, the full ROS control loop
has a 100 ms period, but policy inference should stay far below that so sensor
processing and safety checks have headroom.

For a repeatable runtime snapshot, run:

```bash
tools/jetson_runtime_monitor.sh \
  --duration 60 \
  --output-dir /tmp/osracer_runtime_monitor
```

Run it once with policy inference disabled and once with `enabled:=True` on
blocks. Compare topic rates, process RSS/CPU, and tegrastats logs before floor
tests.
The monitor also writes `/tmp/osracer_runtime_monitor/summary_report.log`.
To summarize an existing monitor directory again, run:

```bash
tools/jetson_runtime_summary.py /tmp/osracer_runtime_monitor
```

Before first closed-loop motion, aggregate the deployment package, replay, sensor,
serial, and runtime evidence into one go/no-go report:

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

# For ONNX deployment packages, add this first-drive gate argument:
#   --tensorrt-build-report /tmp/osracer_tensorrt_build_report.json

tools/first_drive_evidence_pack.py \
  --gate-report /tmp/osracer_first_drive_gate.json \
  --output-dir /tmp/osracer_first_drive_evidence_pack \
  --overwrite

tools/verify_first_drive_evidence_pack.py /tmp/osracer_first_drive_evidence_pack --require-pass
```

Recommended Orin Nano Super 8GB runtime posture:

- Run `tools/jetson_performance_profile.sh` before and after applying the profile so the before/after state is recorded.
- Set the high-performance `nvpmodel` profile, run `jetson_clocks`, and keep the CPU governor at `performance` for repeatable latency tests.
- Monitor thermals with `tegrastats`, especially during camera or visual-policy tests.
- Keep heavy training and large simulation sweeps on an external workstation.
- Run only inference, preprocessing, logging, and ROS control on Jetson.
- Use NVMe for logs, replay CSVs, bags, and model artifacts.
- Treat swap/zram as a safety margin, not as normal inference memory.
- Prefer TorchScript for the first drift policy and TensorRT FP16 for visual policies.
- Use INT8 only after calibration data is collected and offline replay proves bounded action differences.

For MLP drift policies, TorchScript may be sufficient. For visual policies, TensorRT should be the default deployment target because camera preprocessing and inference compete for the 8GB memory budget.

Use FP16 first. Consider INT8 only after calibration data is available and action differences are bounded in offline replay.
