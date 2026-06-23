# OSRacer 仿真验收指标

## 结论

仿真验收不以“看起来能跑”为准，而以 topic 链路、TF/odom 稳定性、控制输出、CSV
评测和 Gazebo 资源完整性为准。当前阶段的目标是证明算法接口和教学演示稳定，不证明
实车高速性能。

## 通用通过条件

- `base_sim.launch.py` 能启动并发布 `/scan`、`/odometry/filtered`、`/tf`、
  `/joint_states` 和 `/clock`。
- `race_sim.launch.py` 六个 stage 的 launch 参数能解析。
- `gazebo.launch.py` 能加载默认赛道、障碍物赛道和可选 bridge 参数。
- `eval_output_csv` 能生成 CSV，并可由 `race_report_tools` 汇总。
- 仿真过程中不出现 TF 断裂、odom 大跳变、命令 NaN 或持续安全急停误触发。

## 四阶段验收矩阵

| 阶段 | 推荐场景 | 重点观察 | 最低通过条件 |
| --- | --- | --- | --- |
| 基础链路 | `base_sim.launch.py` | `/scan`、TF、joint 动画、odom | topic 存在，TF 连续，轮子和转向 joint 有变化 |
| Gap Follow | `stage:=gap_follow obstacle_preset:=front` | safety stop、避障方向、速度限制 | 障碍物进入视野后不向障碍物持续加速 |
| 轨迹录制 | `stage:=track_record` | 轨迹点、时间戳、odom 连续性 | CSV/轨迹文件有连续样本，无明显时间倒退 |
| Pure Pursuit | `stage:=pure_pursuit` | 跟踪误差、转向方向 | `race_report_tools` 能输出 track error 样本 |
| Stanley | `stage:=stanley` | 横向误差、航向误差 | heading error 和 steering 输出非 NaN |
| Vehicle ID | `stage:=vehicle_id` | 速度响应、转向响应 | 输出样本可用于后续参数估计 |
| MPC | `stage:=mpc` | 轨迹跟踪、约束、求解稳定性 | CSV 有样本，命令速度/转向有限，未持续触发安全停 |
| Gazebo world | `osracer_rect_track_obstacle.sdf` | world、模型、障碍物位置 | Gazebo launch 参数解析通过，障碍物位置对齐 kinematic front preset |
| Gazebo bridge | `use_gz_bridge:=true use_gz_control:=true` | `/gazebo/scan`、IMU、joint command | bridge 参数解析通过，joint command topic 名称完整 |

## CSV 评估方式

仿真命令加入 `eval_output_csv`：

```bash
ros2 launch osracer_sim race_sim.launch.py \
  stage:=pure_pursuit \
  eval_output_csv:=/tmp/osracer_sim_eval_pure_pursuit.csv
```

汇总：

```bash
ros2 run osracer_race race_report_tools /tmp/osracer_sim_eval_pure_pursuit.csv
```

重点字段：

- `samples`：样本数，必须大于 0。
- `max_speed_mps`：实际速度上限，用于发现异常速度尖峰。
- `max_command_speed_mps`：控制命令速度上限，用于检查限速配置。
- `mean_abs_track_error_m` / `max_abs_track_error_m`：轨迹跟踪误差。
- `mean_abs_heading_error_rad`：航向误差。
- `max_abs_steering_rad`：转向命令幅度，应符合 OSRacer 转向限位。

## 不能进入真车测试的情况

- 仿真中出现 NaN、Inf 或空 CSV。
- `/tf` 或 `/odometry/filtered` 有明显跳变。
- Gazebo bridge topic 名称缺失，或左右转向方向与 RViz joint 动画相反。
- Gap Follow / TTC safety 在前方障碍物场景中仍持续给出高速度。
- MPC 或轨迹跟踪阶段持续输出超过实车安全速度的命令。

## 当前限制

- 这些指标只证明 ROS 接口和算法链路，不证明轮胎侧偏、抓地力、电机电流或电池模型。
- 高速参数必须用实车低速逐步放大验证。
- Gazebo 物理运动仍是简化模型，不能替代真实 RoboRacer 赛道测试。
