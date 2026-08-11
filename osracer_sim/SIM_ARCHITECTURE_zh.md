# OSRacer 仿真架构与能力

`osracer_sim` 提供轻量运动学仿真和 Gazebo Sim 集成入口，用于 ROS 接口验证、
教学演示、SLAM/Nav2 联调以及 `osracer_race` 算法测试。仿真模型不修改底层
固件，也不替代真实车辆验证。

## 架构层级

| 层级 | 主要入口 | 能力 | 适用场景 |
| --- | --- | --- | --- |
| 轻量运动学 | `base_sim.launch.py`、`race_sim.launch.py` | Ackermann 运动、odom、TF、IMU、joint、raycast scan | 快速接口检查、算法回归、教学 |
| SLAM/Nav2 | `slam_sim.launch.py`、`navigation_sim.launch.py` | 仿真定位、建图和导航入口 | 无真车环境下的 ROS 链路联调 |
| Gazebo 场景 | `gazebo.launch.py` | 赛道、简化模型、LiDAR、IMU、clock bridge | 可视化和传感器桥接 |
| Gazebo 控制 | `use_gz_control:=true` | 转向位置和车轮速度 joint command | 控制方向、几何和接口验证 |

## 轻量运动学仿真

轻量模式提供确定性的 Ackermann 运动和矩形赛道 raycast `/scan`。它支持：

- `/ackermann_cmd` 和 `/cmd_vel` 控制输入；
- `/odometry/filtered`、`/imu_filter`、`/tf` 和 `/joint_states`；
- `/scan` 和 `/clock`；
- `front`、`left`、`right`、`off`、`custom` 障碍物预设；
- `eval_output_csv` 评测输出；
- Gap Follow、轨迹录制、Pure Pursuit、Stanley、Vehicle ID 和 MPC 仿真入口。

该模式启动快、结果可复现，适合自动检查和控制算法的初始验证。

## Gazebo 场景与传感器

Gazebo 模式提供：

- `osracer_rect_track.sdf` 矩形赛道；
- `osracer_rect_track_obstacle.sdf` 障碍物赛道；
- `osracer_empty.sdf` 空场景；
- `model://osracer_simple` 简化车辆模型；
- Gazebo LiDAR、IMU 和 `/clock`；
- 可选 `ros_gz_bridge`，对应 `/gazebo/scan` 和 `/gazebo/imu`。

启用 Gazebo 传感器时，应避免与轻量模式的 `/scan` 和 `/clock` 同名发布者同时
运行。

## Gazebo 控制链路

`gazebo_ackermann_bridge_node` 将 `/ackermann_cmd` 转换为左右转向位置和四轮
角速度命令。左右轮速度根据轴距、轮距、轮半径和转向几何分别计算，用于检查：

- 直行时四轮方向一致；
- 左转和右转的内外轮方向正确；
- 转向角和轮速保持在配置范围内；
- Gazebo joint 动画与 ROS 控制方向一致。

## 公共接口

默认接口包括：

- 控制：`/ackermann_cmd`、`/cmd_vel`；
- 状态：`/odometry/filtered`、`/imu_filter`；
- 传感器：`/scan`、`/gazebo/scan`、`/gazebo/imu`；
- 模型：`/tf`、`/joint_states`；
- 时间：`/clock`。

具体 launch 参数和场景命令见 [`README_zh.md`](README_zh.md)。

## 模型边界

当前模型用于验证 ROS 接口和算法链路，不模拟以下实车特性：

- 轮胎侧偏和打滑；
- 电机电流、电池电压和热衰减；
- 机械间隙和舵机负载变化；
- 完整传感器噪声、USB 调度和通信延迟；
- 高速赛道中的抓地力极限。

仿真速度、转弯半径和控制方向可以作为联调依据，但高速参数必须经过真实车辆
低速递进验证。验收指标和禁止进入实车测试的条件见
[`SIM_VALIDATION_zh.md`](SIM_VALIDATION_zh.md)。
