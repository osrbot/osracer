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
ros2 topic pub /ackermann_cmd ackermann_msgs/msg/AckermannDrive \
  "{speed: 0.5, steering_angle: 0.2}"
```

切换简单走廊扫描：

```bash
ros2 launch osracer_sim base_sim.launch.py scan_environment:=hallway
```

在矩形赛道扫描中加入一个固定圆形障碍物：

```bash
ros2 launch osracer_sim base_sim.launch.py \
  obstacle_enabled:=true \
  obstacle_x:=2.0 \
  obstacle_y:=-1.7 \
  obstacle_radius:=0.25
```

## Gazebo 赛道世界

```bash
ros2 launch osracer_sim gazebo.launch.py
```

该入口默认启动 `osracer_rect_track.sdf` 矩形赛道 world、`osracer_simple`
简化车辆模型，并同时启动轻量 kinematic 仿真节点。Gazebo 里的车辆模型目前用于
场景可视化和后续控制插件接入；ROS 侧里程计、TF、joint 动画和 `/scan` 仍由
`ackermann_kinematic_sim_node` 发布。

如果只想加载赛道和 ROS kinematic 仿真，不加载 Gazebo 车辆模型：

```bash
ros2 launch osracer_sim gazebo.launch.py include_model:=false
```

Gazebo 车辆模型内置 Gazebo LiDAR 和 IMU 传感器。默认不启动 `ros_gz_bridge`，
避免和 kinematic 仿真节点同时发布 `/clock` 或 `/scan`。需要检查 Gazebo 原生传感器时：

```bash
ros2 launch osracer_sim gazebo.launch.py \
  use_gz_bridge:=true \
  publish_kinematic_clock:=false
```

桥接 topic：

- Gazebo LiDAR：`/gazebo/scan` -> `sensor_msgs/msg/LaserScan`
- Gazebo IMU：`/gazebo/imu` -> `sensor_msgs/msg/Imu`
- Gazebo clock：`/clock` -> `rosgraph_msgs/msg/Clock`

需要让 `/ackermann_cmd` 同时驱动 Gazebo 简化车模的转向和轮速 joint controller：

```bash
ros2 launch osracer_sim gazebo.launch.py \
  use_gz_bridge:=true \
  use_gz_control:=true \
  publish_kinematic_clock:=false
```

`gazebo_ackermann_bridge_node` 会订阅 `ackermann_msgs/msg/AckermannDrive`
格式的 `/ackermann_cmd`，并发布：

- `/gazebo/left_steering_position`
- `/gazebo/right_steering_position`
- `/model/osracer_simple/joint/*_wheel_joint/cmd_vel`

这条链路用于检查 Gazebo joint controller、转向方向和轮速方向。Gazebo 车体的物理
运动仍取决于简化碰撞、摩擦和 joint controller 参数，不等价于实车高速动力学。

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
ros2 launch osracer_sim race_sim.launch.py \
  stage:=gap_follow \
  eval_output_csv:=/tmp/osracer_sim_eval_gap_follow.csv
```

第二阶段轨迹录制：

```bash
ros2 launch osracer_sim race_sim.launch.py stage:=track_record
```

第二阶段轨迹跟踪：

```bash
ros2 launch osracer_sim race_sim.launch.py \
  stage:=pure_pursuit \
  eval_output_csv:=/tmp/osracer_sim_eval_pure_pursuit.csv
ros2 launch osracer_sim race_sim.launch.py \
  stage:=stanley \
  eval_output_csv:=/tmp/osracer_sim_eval_stanley.csv
```

第三阶段车辆能力辨识：

```bash
ros2 launch osracer_sim race_sim.launch.py stage:=vehicle_id
```

第四阶段 MPC：

```bash
ros2 launch osracer_sim race_sim.launch.py \
  stage:=mpc \
  eval_output_csv:=/tmp/osracer_sim_eval_mpc.csv
```

带障碍物的四阶段仿真示例：

```bash
ros2 launch osracer_sim race_sim.launch.py \
  stage:=gap_follow \
  obstacle_enabled:=true \
  obstacle_x:=2.0 \
  obstacle_y:=-1.7 \
  obstacle_radius:=0.25
```

该障碍物只注入到 kinematic `/scan`，用于验证 TTC safety、Gap Follow 和
`obstacle_overtake_node` 的接口链路；它不是 Gazebo 碰撞体。

对比各阶段评测结果：

```bash
ros2 run osracer_race race_report_tools \
  /tmp/osracer_sim_eval_gap_follow.csv \
  /tmp/osracer_sim_eval_pure_pursuit.csv \
  /tmp/osracer_sim_eval_stanley.csv \
  /tmp/osracer_sim_eval_mpc.csv
```

## 当前限制

- `/scan` 是基于矩形赛道墙体的 2D raycast，不代表真实 LiDAR 噪声和材质反射。
- 可选圆形障碍物只影响 kinematic `/scan`，不会改变 Gazebo 物理场景。
- 没有高保真轮胎侧偏、打滑、差速器、电机电流和电池模型。
- Gazebo world 当前包含场地、墙体和简化 OSRacer 模型；还没有接入轮胎侧偏模型。
  Gazebo LiDAR/IMU 和 joint controller 已提供可选 `ros_gz_bridge` 入口，
  但默认 race/SLAM 仿真仍使用 kinematic 节点发布的
  `/scan`、`/odometry/filtered` 和 `/joint_states`。
- 高速能力、MPC 稳定性和安全边界必须以真实车辆低速逐步验证为准。

## 自检

源码树内运行：

```bash
bash osracer_sim/scripts/check_sim_package.sh
```
