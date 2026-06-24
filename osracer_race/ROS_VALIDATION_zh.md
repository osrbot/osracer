# OSRacer Race ROS/实车验证清单

本文档用于在 Ubuntu 22.04 + ROS 2 Humble 机器上验证 `osracer_race`。
推荐在车端 Jetson Orin Nano 或连接真实车辆的 ROS 2 Humble 开发机上执行。开发电脑
可以用于远程终端、RViz、录包、地图和轨迹文件准备；串口、LiDAR、相机和实车运动验证
以车端结果为准。

## 1. 构建验证

```bash
cd ~/osracer_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select osracer_race
source install/setup.bash
ros2 pkg prefix osracer_race
```

通过标准：

- `colcon build` 成功。
- `ros2 pkg prefix osracer_race` 能找到安装路径。
- `ros2 run osracer_race raceline_tools --help` 正常输出帮助。
- `ros2 run osracer_race race_report_tools --help` 正常输出帮助。
- `ros2 launch osracer_race gap_follow.launch.py --show-args` 正常输出参数。
- `ros2 launch osracer_race race_bringup.launch.py --show-args` 正常输出参数。

## 2. 静态自检

```bash
bash $(ros2 pkg prefix osracer_race)/share/osracer_race/scripts/check_race_package.sh
```

通过标准：

- 源码树运行时，完整离线单元测试通过。
- 安装后运行时，已安装测试通过；源码树专属元数据检查会自动跳过。
- XML/YAML 检查通过。
- helper 模块 import smoke test 通过。
- 工具 smoke test 通过。
- 如果当前机器有 ROS 2 Humble 和 `colcon`，脚本会自动执行 `colcon build --symlink-install --packages-select osracer_race`。

也可以运行车端 ROS 验证辅助脚本。该脚本只做构建后入口、launch 参数、
配置路径和可选 topic 可见性检查，不会发布运动命令：

```bash
bash $(ros2 pkg prefix osracer_race)/share/osracer_race/scripts/validate_race_ros.sh
```

## 3. 低速安全验证

启动整车 bringup 和无地图比赛模式：

```bash
ros2 launch osracer_race race_bringup.launch.py controller:=gap_follow
```

`controller` 仅支持 `gap_follow`、`pure_pursuit`、`stanley`、`mpc`。
其他值应在 launch 参数解析阶段被拒绝。

验证项目：

- `/race/safety_stop` 正常发布。
- 遮挡前方 LiDAR 时，`/ackermann_cmd` 速度变为 `0.0`。
- 断开或遮挡到前方 FOV 没有有效 LiDAR 点时，`/race/safety_stop` 变为 `true`。
- 停止 `/scan` 发布超过 `scan_timeout_s` 后，`/race/safety_stop` 变为 `true`。
- 停止上游控制器命令发布超过 `command_timeout_s` 后，`/ackermann_cmd` 速度变为 `0.0`。
- 解除遮挡后，车辆不会突然高速冲出。
- 遥控器/底层急停仍可接管。

建议命令：

```bash
ros2 topic echo /race/safety_stop
ros2 topic echo /ackermann_cmd
ros2 topic hz /scan
ros2 topic hz /odometry/filtered
```

## 4. 第一阶段：Gap Follow

```bash
ros2 launch osracer_race gap_follow.launch.py \
  eval_output_csv:=/tmp/osracer_race_eval_gap_follow.csv
```

通过标准：

- 低速 `race_safe.yaml` 下能绕开静态障碍。
- `speed_profile_node` 输出的 `/ackermann_cmd` 不超过配置限速。
- `/tmp/osracer_race_eval_gap_follow.csv` 持续写入。

## 5. 第二阶段：录制轨迹和跟踪

录制一圈低速轨迹：

```bash
ros2 launch osracer_race track_record.launch.py \
  output_csv:=/tmp/osracer_recorded_track.csv
```

生成 raceline：

```bash
ros2 run osracer_race raceline_tools \
  /tmp/osracer_recorded_track.csv \
  /tmp/osracer_raceline.csv \
  --max-speed 2.0 \
  --min-speed 0.8 \
  --max-lateral-accel 3.0
```

Pure Pursuit：

```bash
ros2 launch osracer_race pure_pursuit.launch.py \
  raceline_file:=/tmp/osracer_raceline.csv \
  eval_output_csv:=/tmp/osracer_race_eval_pure_pursuit.csv
```

Stanley：

```bash
ros2 launch osracer_race stanley.launch.py \
  raceline_file:=/tmp/osracer_raceline.csv \
  eval_output_csv:=/tmp/osracer_race_eval_stanley.csv
```

通过标准：

- 车辆能低速闭环跟踪 raceline。
- 急弯速度会因曲率限速降低。
- 前方近距离障碍触发 `obstacle_overtake_node` 后车辆低速绕行。
- 评测 CSV 可用 `race_report_tools` 汇总。

## 6. 第三阶段：车辆能力辨识

```bash
ros2 launch osracer_race vehicle_id.launch.py \
  output_file:=/tmp/osracer_vehicle_identified.yaml
```

通过标准：

- YAML 文件持续更新。
- 包含 `observed_max_speed_mps`、`observed_max_accel_mps2`、
  `observed_max_brake_mps2`、`observed_max_yaw_rate_rps`、
  `observed_max_lateral_accel_mps2`、`observed_min_turning_radius_m`、
  `observed_motor_response_tau_s`、`observed_steering_response_delay_s`。
- 数据来自保守安全场地，不在人员附近做加速/制动测试。

## 7. 第四阶段：MPC 和对比评测

```bash
ros2 launch osracer_race mpc.launch.py \
  raceline_file:=/tmp/osracer_raceline.csv \
  eval_output_csv:=/tmp/osracer_race_eval_mpc.csv
```

汇总对比：

```bash
ros2 run osracer_race race_report_tools \
  /tmp/osracer_race_eval_gap_follow.csv \
  /tmp/osracer_race_eval_pure_pursuit.csv \
  /tmp/osracer_race_eval_stanley.csv \
  /tmp/osracer_race_eval_mpc.csv
```

通过标准：

- MPC 模式可低速跟踪 raceline。
- `speed_profile_node` 仍是最终输出到 `/ackermann_cmd` 的限幅层。
- 对比报告能显示每种算法的最大速度、平均速度、横向误差和转角峰值。

## 8. 高速前置条件

`race_fast.yaml` 是完整运行参数文件，仍显式包含 topic、安全停车、watchdog、
跟踪、MPC、绕行、评测和圈速参数。切换前必须满足：

- `race_safe.yaml` 下安全停车可靠。
- `/scan` 和 `/odometry/filtered` 频率稳定。
- 遥控器/底层急停确认可用。
- `race_report_tools` 中横向误差没有持续发散。
- 场地无人员近距离进入。

切换示例：

```bash
ros2 launch osracer_race race_bringup.launch.py \
  controller:=pure_pursuit \
  race_config:=$(ros2 pkg prefix osracer_race)/share/osracer_race/config/race_fast.yaml \
  raceline_file:=/tmp/osracer_raceline.csv
```
