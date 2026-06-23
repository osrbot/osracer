# Jetson Orin Runtime Plan for OSRacer

This document is for the real OSRacer compute platform. Current target hardware is Jetson Orin Nano Super 8GB.

## Split of Responsibility

Use the RTX 4080 SUPER server for training and simulation. Use Jetson for runtime inference and real-robot control.

```text
Server
  IsaacLab / MuJoCo
  train policy
  export TorchScript / optional ONNX
  sim2sim and replay validation

Jetson Orin Nano Super 8GB
  ROS 2 runtime
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
5. Install ROS 2 Jazzy packages and OSRacer workspace dependencies.
6. Install `ros-jazzy-ackermann-msgs`.
7. Install Torch for the exact Python used by `ros2 launch`.
8. Run `tools/jetson_preflight.sh`.

Example:

```bash
sudo apt install ros-jazzy-ackermann-msgs
python3 -m pip install torch
tools/jetson_preflight.sh /path/to/policy.pt
```

Run the optional offline replay smoke when `policy.pt` is available:

```bash
tools/jetson_preflight.sh --policy /path/to/policy.pt --offline-smoke
```

On Jetson, prefer NVIDIA-provided or JetPack-compatible Python wheels for acceleration libraries.

## Runtime Launch Flow

Build and source the OSRacer workspace:

```bash
source /opt/ros/jazzy/setup.bash
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

For MLP drift policies, TorchScript may be sufficient. For visual policies, TensorRT should be the default deployment target because camera preprocessing and inference compete for the 8GB memory budget.

Use FP16 first. Consider INT8 only after calibration data is available and action differences are bounded in offline replay.
