# OSRacer 仿真包

`osracer_sim` 提供 OSRacer 的第一版轻量仿真入口。目标是让教学、SLAM/Nav2 和
`osracer_race` 四阶段算法先在无真车环境下跑通接口链路；它不是高保真轮胎、
差速器或电机动力学模型。

## 设计边界

- 不修改底层固件。
- 不替代真实车辆测试。
- 第一版采用 kinematic Ackermann 模型，复用实车几何参数：
  轴距 `0.285m`、轮距 `0.215m`、轮半径 `0.0425m`。
- 同时支持 `/ackermann_cmd` 和 `/cmd_vel` 输入。
- 发布 `/odometry/filtered`、`/tf`、`/joint_states`、`/scan` 和 `/clock`。
- 默认 `/scan` 使用矩形赛道墙体 raycast，适合 Gap Follow、TTC safety 和
  轨迹录制的离线接口验证；也可以切回 `scan_environment:=hallway`。

## 基础仿真

```bash
ros2 launch osracer_sim base_sim.launch.py use_rviz:=true
```

该命令会启动：

- `robot_state_publisher`
- OSRacer 轮子/转向 joint 动画
- `ackermann_kinematic_sim_node`
- 可选 RViz

手动发 Ackermann 命令：

```bash
ros2 topic pub /ackermann_cmd ackermann_msgs/msg/AckermannDriveStamped \
  "{drive: {speed: 0.5, steering_angle: 0.2}}"
```

切换简单走廊扫描：

```bash
ros2 launch osracer_sim base_sim.launch.py scan_environment:=hallway
```

## Gazebo 赛道世界

```bash
ros2 launch osracer_sim gazebo.launch.py
```

该入口默认启动 `osracer_rect_track.sdf` 矩形赛道 world，并同时启动轻量
kinematic 仿真节点。车辆物理模型还没有 spawn 到 Gazebo 里；Gazebo 主要作为后续
传感器、赛道和可视化扩展入口。

如果只需要空地面：

```bash
ros2 launch osracer_sim gazebo.launch.py \
  world:=$(ros2 pkg prefix osracer_sim)/share/osracer_sim/worlds/osracer_empty.sdf
```

## SLAM 仿真

```bash
ros2 launch osracer_sim slam_sim.launch.py use_rviz:=true
```

仿真节点会发布 `/scan` 和 `/odometry/filtered`，用于先检查 SLAM topic、TF 和
launch 链路。真实建图质量仍以车端 LiDAR 和场地测试为准。

## Nav2 仿真

```bash
ros2 launch osracer_sim navigation_sim.launch.py use_rviz:=true
```

该入口使用 TEB 作为默认局部规划器，适合检查 Nav2 启动、TF、odom、scan 和参数链路。

## Race 四阶段仿真

第一阶段 Gap Follow：

```bash
ros2 launch osracer_sim race_sim.launch.py stage:=gap_follow
```

第二阶段轨迹录制：

```bash
ros2 launch osracer_sim race_sim.launch.py stage:=track_record
```

第二阶段轨迹跟踪：

```bash
ros2 launch osracer_sim race_sim.launch.py stage:=pure_pursuit
ros2 launch osracer_sim race_sim.launch.py stage:=stanley
```

第三阶段车辆能力辨识：

```bash
ros2 launch osracer_sim race_sim.launch.py stage:=vehicle_id
```

第四阶段 MPC：

```bash
ros2 launch osracer_sim race_sim.launch.py stage:=mpc
```

## 当前限制

- `/scan` 是基于矩形赛道墙体的 2D raycast，不代表真实 LiDAR 噪声和材质反射。
- 没有高保真轮胎侧偏、打滑、差速器、电机电流和电池模型。
- Gazebo world 当前只有场地和墙体，不是完整物理车辆模型。
- 高速能力、MPC 稳定性和安全边界必须以真实车辆低速逐步验证为准。

## 自检

源码树内运行：

```bash
bash osracer_sim/scripts/check_sim_package.sh
```
