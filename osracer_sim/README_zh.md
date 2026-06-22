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

## Gazebo 空世界

```bash
ros2 launch osracer_sim gazebo.launch.py
```

该入口启动现代 Gazebo Sim 的空地面 world，并同时启动轻量 kinematic 仿真节点。
第一版没有把车辆物理模型 spawn 到 Gazebo 里；Gazebo 主要作为后续传感器、赛道
和可视化扩展入口。

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

- `/scan` 是简单合成环境，不代表真实赛道。
- 没有高保真轮胎侧偏、打滑、差速器、电机电流和电池模型。
- Gazebo world 当前是扩展入口，不是完整物理车辆模型。
- 高速能力、MPC 稳定性和安全边界必须以真实车辆低速逐步验证为准。

## 自检

源码树内运行：

```bash
bash osracer_sim/scripts/check_sim_package.sh
```
