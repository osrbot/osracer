# OSRacer Race

`osracer_race` 是 OSRacer 的比赛和科研算法包，独立于 `osracer_demo`
和 `osracer_navigation`。它面向 RoboRacer/F1TENTH 类 1/10 阿克曼赛车，
通过最终限幅层输出 `/ackermann_cmd`，不修改底层固件和普通 Nav2 导航参数。

## 车辆参数

当前车辆参数已经确定。运行时轴距、转角和速度限制由随工作空间导入的
OSRacer Base 配置提供；Race 配置保存竞速算法需要的机械与模型参数：

| 参数 | 当前值 | 用途 |
| --- | ---: | --- |
| 比例 | 1/10 阿克曼赛车 | 产品类别 |
| 轴距 | `0.285 m` | Ackermann 运动学 |
| 最大转向角 | `30 deg` | ROS 软件限幅 |
| 车辆速度上限 | `4.64 m/s` | 配置上限，不是首次实车测试速度 |
| 轮径 / 轮半径 | `85 mm / 0.0425 m` | 车辆运动模型 |
| 轮距 | `0.215 m` | 当前 ROS 车型几何值 |
| 质量 | `3.2 kg` | 当前整车模型值 |
| 差速器 / 主传动 | `40T/13T`、`48T/14T` | 当前机械配置 |
| 总减速比 | `10.55:1` | 车辆运动模型 |
| 电机空载转速 | `11000 RPM` | 当前电机参数 |
| 编码器 | `1024` 线、单倍频 | 车辆观测模型 |
| 最小转弯半径 | `0.50 m` | 当前 Ackermann 模型值 |

`race_safe.yaml` 和 `race_fast.yaml` 是比赛算法配置，不是首次上车验收限速。
只有更换车辆几何、轮胎、传动、电机、编码器或转向机构时，才需要重新测量并
同步相关配置。

参数文件：

```bash
osracer_race/config/vehicle.yaml
osracer_race/config/race_safe.yaml
osracer_race/config/race_fast.yaml
```

四阶段覆盖清单见：

```bash
osracer_race/PHASES_zh.md
```

ROS/实车验证清单见：

```bash
osracer_race/ROS_VALIDATION_zh.md
```

## 推荐上手顺序

建议按下面顺序使用，不要一开始直接切高速参数：

1. 在 ROS 机器上构建并运行自检。
2. 使用 `race_safe.yaml` 验证 LiDAR 急停、命令超时停车和遥控器/底层急停。
3. 用 Gap Follow 做低速无地图绕障，确认 `/ackermann_cmd` 限幅正常。
4. 低速录制赛道，生成 `raceline.csv`。
5. 分别跑 Pure Pursuit、Stanley、MPC，并用 CSV 报告对比。
6. 只在前面步骤稳定后，再切 `race_fast.yaml` 做逐步提速。

## 安装和自检

在 Ubuntu 22.04 + ROS 2 Humble 工作空间中：

```bash
cd ~/osracer_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select osracer_race
source install/setup.bash
```

确认安装路径：

```bash
ros2 pkg prefix osracer_race
```

运行安装后的包自检：

```bash
bash $(ros2 pkg prefix osracer_race)/share/osracer_race/scripts/check_race_package.sh
```

运行车端 ROS 入口验证。该脚本只检查安装资源、命令行入口、launch 参数和可选 topic
可见性，不会发布行驶命令：

```bash
bash $(ros2 pkg prefix osracer_race)/share/osracer_race/scripts/validate_race_ros.sh
```

如果只是在源码树做离线检查：

```bash
bash osracer_race/scripts/check_race_package.sh
```

运行前必须先初始化 `osracer_dependency` submodule：

```bash
git submodule update --init --recursive
```

车端 Jetson Orin Nano 负责运行底盘、雷达、相机、SLAM、Nav2 和 race 控制
节点；开发电脑可用于编辑代码、远程终端、RViz、录包、地图和轨迹文件准备。
实车运动验证必须在车端或连接真实车辆的 ROS 2 Humble 环境完成。

race 包本地自检：

```bash
bash osracer_race/scripts/check_race_package.sh
```

构建后检查 ROS 入口：

```bash
bash $(ros2 pkg prefix osracer_race)/share/osracer_race/scripts/validate_race_ros.sh
```

## Topic 链路和安全边界

比赛包不直接修改底层串口驱动。普通底盘节点继续提供 `/odom` 或
`/odometry/filtered`，并接收最终 `/ackermann_cmd`。

无地图链路：

```text
/scan + /odometry/filtered
  -> safety_node
  -> gap_follow_node
  -> /race/raw_ackermann_cmd
  -> speed_profile_node
  -> /ackermann_cmd
```

有地图链路：

```text
/odometry/filtered + raceline.csv
  -> Pure Pursuit / Stanley / MPC
  -> /race/tracking_ackermann_cmd
  -> obstacle_overtake_node
  -> /race/raw_ackermann_cmd
  -> speed_profile_node
  -> /ackermann_cmd
```

`speed_profile_node` 是最后一层上位机限幅，负责速度、制动、转向、横向加速度、
上游命令超时和 safety stop 后门停车。底层遥控器/固件急停仍必须保留，比赛包不能替代
硬件级安全措施。

## 参数文件说明

- OSRacer Base 车辆配置：提供运行时轴距、车辆速度上限和最大转角。
- `vehicle.yaml`：保存轮距、传动、电机、重量和竞速算法使用的 Ackermann
  模型参数，不重复运行时限幅参数。
- `race_safe.yaml`：默认低速参数，首次上车必须使用。
- `race_fast.yaml`：完整高速运行参数文件，只有在低速安全验证通过后使用。

常调参数：

- `max_straight_speed_mps`：直道最高速度。
- `min_speed_mps`：控制器允许的最低前进速度。
- `max_lateral_accel_mps2`：弯道曲率限速的横向加速度上限。
- `max_accel_mps2` / `max_brake_mps2`：加速和制动斜率限制。
- `speed_response_time_s`：MPC 估算可达速度候选时使用的速度响应时间。
- `target_speed_weight` / `progress_weight`：MPC 的 raceline 速度跟踪和路径前进奖励权重。
- `max_steering_angle`：由 OSRacer Base 配置提供的弧度制转角限幅。
- `ttc_threshold_s` / `emergency_distance_m`：LiDAR 急停阈值。
- `command_timeout_s` / `scan_timeout_s`：上游控制命令和 `/scan` 断流停车时间。

## 第一阶段：安全无地图跑圈

启动底盘、雷达后运行：

```bash
ros2 launch osracer_race gap_follow.launch.py
```

如果希望一条命令同时启动底盘、模型、雷达和比赛节点：

```bash
ros2 launch osracer_race race_bringup.launch.py controller:=gap_follow
```

低速验证时建议同时观察：

```bash
ros2 topic echo /race/safety_stop
ros2 topic echo /ackermann_cmd
ros2 topic hz /scan
ros2 topic hz /odometry/filtered
```

`race_bringup.launch.py` 的 `controller` 仅支持 `gap_follow`、`pure_pursuit`、
`stanley`、`mpc`；其他值会在 launch 参数解析阶段被拒绝。

包含：

- `safety_node`：基于 LiDAR 和车速的 TTC 急停
- `gap_follow_node`：Follow-the-Gap 无地图避障跑圈
- `lap_timer_node`：基于里程计起点区域的圈速统计

所有竞速控制器都会监听 `/race/safety_stop`。安全节点触发后，控制器本身也只会发布
零速度/零转角，避免继续覆盖安全停车命令。
默认 `stop_on_no_front_scan: true`，当前方 FOV 内没有有效 LiDAR 点时也会停车，
避免传感器异常时继续输出行驶命令。`scan_timeout_s` 还会在 `/scan` 整体断流时
触发 safety stop。

第一阶段通过标准：

- 遮挡前方 LiDAR 时 `/race/safety_stop` 变为 `true`。
- `/scan` 断流超过 `scan_timeout_s` 后停车。
- 上游控制器停止发布超过 `command_timeout_s` 后停车。
- 清空障碍后不会突然高速冲出。
- `/ackermann_cmd` 的速度和转角不超过配置限制。

控制器默认发布到 `/race/raw_ackermann_cmd`，最终由 `speed_profile_node` 输出
`/ackermann_cmd`。该节点统一处理：

- 直道最高速度限制
- 曲率/横向加速度限速
- 最大加速度限制
- 最大制动减速度限制
- 最大转向角限制
- safety stop 后门停车
- 上游控制器命令超时停车

有地图算法还会经过 `obstacle_overtake_node`：

```text
Pure Pursuit / Stanley / MPC
  -> /race/tracking_ackermann_cmd
  -> obstacle_overtake_node
  -> /race/raw_ackermann_cmd
  -> speed_profile_node
  -> /ackermann_cmd
```

当正前方检测到近距离障碍时，该节点会临时选择左右更空的一侧，以低速绕行；
障碍清除后恢复原轨迹跟踪命令。

默认使用保守 `race_safe.yaml`。确认场地和急停行为后，可以切到更激进参数。
`race_fast.yaml` 是完整运行参数文件，仍显式包含 topic、安全停车、watchdog
跟踪、MPC、绕行、评测和圈速参数，不只是速度覆盖片段：

```bash
ros2 launch osracer_race gap_follow.launch.py \
  race_config:=$(ros2 pkg prefix osracer_race)/share/osracer_race/config/race_fast.yaml
```

单独指定评测输出：

```bash
ros2 launch osracer_race gap_follow.launch.py \
  eval_output_csv:=/tmp/osracer_race_eval_gap_follow.csv
```

## 第二阶段：有地图轨迹跟踪

先用遥控器或人工低速跑一圈，记录轨迹：

```bash
ros2 launch osracer_race track_record.launch.py \
  output_csv:=/tmp/osracer_recorded_track.csv
```

可先从手工或离线生成的 `x,y,speed` CSV 生成带曲率的速度剖面：

```bash
ros2 run osracer_race raceline_tools \
  /tmp/osracer_recorded_track.csv \
  /tmp/osracer_raceline.csv \
  --max-speed 3.0 \
  --min-speed 0.8 \
  --max-lateral-accel 4.5
```

Pure Pursuit：

```bash
ros2 launch osracer_race pure_pursuit.launch.py \
  raceline_file:=/path/to/raceline.csv \
  eval_output_csv:=/tmp/osracer_race_eval_pure_pursuit.csv
```

一键 bringup：

```bash
ros2 launch osracer_race race_bringup.launch.py \
  controller:=pure_pursuit \
  raceline_file:=/path/to/raceline.csv
```

Stanley：

```bash
ros2 launch osracer_race stanley.launch.py \
  raceline_file:=/path/to/raceline.csv \
  eval_output_csv:=/tmp/osracer_race_eval_stanley.csv
```

`raceline.csv` 格式：

```text
x,y,speed,curvature
0.0,0.0,1.2,0.000000
1.0,0.0,1.6,0.320000
```

第二阶段调试顺序：

1. 先用 `track_record.launch.py` 低速录一圈，确认 CSV 中点位连续。
2. 用 `raceline_tools` 生成带曲率的速度剖面，先把 `--max-speed` 控制在低速。
3. Pure Pursuit 先跑通闭环，再用 Stanley 对比横向误差。
4. 观察 `/race/raw_ackermann_cmd` 和 `/ackermann_cmd`，确认限幅层没有被绕过。
5. 用 `race_report_tools` 对比每次测试的速度、转角和轨迹误差。

第二阶段通过标准：

- 车辆能稳定跟踪同一条 raceline。
- 急弯速度会明显低于直道速度。
- 近距离前方障碍触发低速绕行，障碍清除后恢复跟踪。
- 评测 CSV 能稳定写入并被报告工具读取。

## 第三阶段：车辆能力标定

```bash
ros2 launch osracer_race vehicle_id.launch.py \
  output_file:=/tmp/osracer_vehicle_identified.yaml
```

当前记录观测到的最高速度、最大加速度、最大制动减速度、最大 yaw rate、
最大横向加速度、最小转弯半径、速度响应时间常数和转向响应延迟，并持续写出
`vehicle_identified.yaml`。后续可把速度、制动、转向能力和转向延迟等参数从估计值
替换为实测值。

第三阶段建议在封闭场地执行：

- 先保持低速直线，确认速度估计平稳。
- 再做小幅加速和制动，观察 `observed_max_accel_mps2` 和
  `observed_max_brake_mps2`、`observed_motor_response_tau_s`。
- 最后做固定转角低速转弯，观察 `observed_max_yaw_rate_rps`、
  `observed_max_lateral_accel_mps2`、`observed_min_turning_radius_m` 和
  `observed_steering_response_delay_s`。

生成的 YAML 不会自动覆盖运行参数。建议人工审查后，将轴距、车辆速度上限和
最大转角更新到使用中的 OSRacer Base 车辆配置；其余赛道参考参数保留在
`vehicle.yaml`。

## 第四阶段：高级控制

```bash
ros2 launch osracer_race mpc.launch.py \
  raceline_file:=/path/to/raceline.csv \
  eval_output_csv:=/tmp/osracer_race_eval_mpc.csv
```

一键 bringup：

```bash
ros2 launch osracer_race race_bringup.launch.py \
  controller:=mpc \
  raceline_file:=/path/to/raceline.csv
```

当前 MPC 是轻量 kinematic shooting controller，不依赖外部优化库。它在候选速度
和转角中滚动预测，按路径误差、航向误差和转向代价选择 `/ackermann_cmd`。
速度候选会根据 `max_accel_mps2`、`max_brake_mps2` 和 `speed_response_time_s`
裁剪到当前车速附近的可达范围，避免 MPC 输出超出实车响应能力的速度跳变。
代价函数同时考虑 raceline 目标速度和沿路径前进距离，使直线段能主动提速，
弯道仍由曲率、横向加速度和响应窗限制。
后续可以替换为 LTV-MPC 或非线性 MPC。

第四阶段调试顺序：

1. 使用和第二阶段相同的 raceline，先保持 `race_safe.yaml`。
2. 跑 Pure Pursuit、Stanley、MPC 三组 CSV。
3. 用 `race_report_tools` 比较平均速度、最大速度、横向误差和转角峰值。
4. 如果 MPC 跟踪误差更大，先调低速度，再根据第三阶段辨识结果调整
   `speed_response_time_s`、`path_weight`、`heading_weight` 和 `steering_weight`。
   如果直线速度偏保守，再小幅提高 `target_speed_weight` 或 `progress_weight`。
5. 只有低速对比稳定后，才逐步提高 `max_straight_speed_mps` 和横向加速度上限。

## 教学科研评测

`pure_pursuit.launch.py`、`stanley.launch.py` 和 `mpc.launch.py` 会同时启动
`race_evaluator_node`，默认把评测数据写到：

```bash
/tmp/osracer_race_eval.csv
```

CSV 包含：

- 位置、航向、当前速度
- 命令速度和命令转角
- 相对 raceline 的横向误差
- 相对 raceline 的航向误差

这可以用于同一赛道对比 Pure Pursuit、Stanley 和 MPC 的圈速、跟踪误差和速度曲线。

汇总评测结果：

```bash
ros2 run osracer_race race_report_tools /tmp/osracer_race_eval.csv
```

如果评测时没有加载 raceline，报告中的 track/heading error 会显示 `N/A`；
这表示没有有效轨迹误差样本，不代表误差为 0。

可以同时传入多个 CSV，方便同一赛道对比不同算法：

```bash
ros2 run osracer_race race_report_tools \
  /tmp/osracer_race_eval_pure_pursuit.csv \
  /tmp/osracer_race_eval_stanley.csv \
  /tmp/osracer_race_eval_mpc.csv
```

离线测试：

```bash
PYTHONPATH=./osracer_race python3 osracer_race/test/test_race_math.py
```

完整自检：

```bash
bash osracer_race/scripts/check_race_package.sh
```

## 车端验证

车端完整验证以 `ROS_VALIDATION_zh.md` 为准。最小检查顺序：

```bash
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select osracer_race
source install/setup.bash
bash $(ros2 pkg prefix osracer_race)/share/osracer_race/scripts/validate_race_ros.sh
```

源码自检：

```bash
bash osracer_race/scripts/check_race_package.sh
git diff --check
```

## 安全要求

- 首次运行必须使用 `race_safe.yaml`。
- 确认 TTC 急停生效前，不要使用高速参数。
- 比赛模式只负责上位机命令，底层遥控器/固件急停仍应保留。
- 直道速度和弯道速度分开调，急弯必须依靠曲率限速。

## 常见问题

### 启动后没有行驶命令

先看 `/race/safety_stop`。如果为 `true`，检查 `/scan` 是否有数据、前方 FOV
是否有有效点、前方是否被遮挡，以及 `/odometry/filtered` 是否正常。

### 有地图控制器不跟踪

检查 `raceline_file` 是否存在，CSV 是否包含 `x,y,speed,curvature` 四列，
并确认当前位置离 raceline 起点不太远。首次测试建议从记录轨迹的起点附近开始。

### 车辆在弯道明显太快

优先降低 `max_lateral_accel_mps2`，再降低 `max_straight_speed_mps`。
弯道速度应该由曲率和横向加速度限制共同决定。

### 报告中轨迹误差为空

这通常表示评测节点没有加载 raceline，或者 CSV 中没有有效轨迹误差样本。
无地图 Gap Follow 可以记录速度和转角，但不会产生相对 raceline 的误差。
