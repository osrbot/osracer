# OSRacer 仿真开发计划

## 结论

仿真有必要做，但不建议一开始追求高保真 Gazebo 动力学。当前更适合采用
`kinematic -> Gazebo 场景 -> Gazebo 控制 -> 车辆动力学校准` 的路线：
先保证教学、SLAM/Nav2 和 race 四阶段算法链路能稳定复现，再逐步加入 Gazebo
传感器、关节控制和真实车辆参数标定。

## 为什么需要仿真

- 教学演示：不依赖真车和场地即可演示 TF、SLAM、Nav2、Gap Follow 和轨迹跟踪。
- 算法回归：每次改 `osracer_race` 后，可以先用固定赛道、固定障碍物和 CSV 输出对比。
- 安全验证：TTC safety、限速、急停和命令超时可以先在无风险环境中检查接口链路。
- Gazebo 接入准备：先验证 world、model、sensor bridge 和 joint command 方向，再做更重的动力学。

## 不建议现在做的内容

- 不在第一阶段做高保真轮胎侧偏、打滑、差速器、电池电压和电机电流模型。
- 不把 Gazebo 结果等同于实车高速性能。
- 不为了仿真修改底层固件协议。
- 不在 ROS 包内放固件、原理图或 PDF 交付文件。

## 四阶段开发路线

### 阶段一：轻量 kinematic 仿真

目标：让算法接口、TF、odom、joint、scan 和 `/ackermann_cmd` 能快速自检。

已具备：

- `base_sim.launch.py`
- `slam_sim.launch.py`
- `navigation_sim.launch.py`
- `race_sim.launch.py`
- 矩形赛道 raycast `/scan`
- `front`、`left`、`right`、`off`、`custom` 障碍物预设
- `eval_output_csv` race 评测输出

验收标准：

- `bash osracer_sim/scripts/check_sim_package.sh` 通过。
- `validate_sim_ros.sh` 在安装态能检查所有 launch 参数。
- race 四阶段至少能通过 `--show-args` 和离线单元测试。

### 阶段二：Gazebo 场景和传感器桥接

目标：提供可视化赛道、简化车模、Gazebo LiDAR/IMU 和 clock bridge，服务教学和调试。

已具备：

- `gazebo.launch.py`
- `osracer_rect_track.sdf`
- `osracer_empty.sdf`
- `model://osracer_simple`
- `/gazebo/scan`
- `/gazebo/imu`
- `/clock`

下一步：

- 根据真实场地补充更接近比赛的 world。
- 增加静态障碍物 world，和 kinematic `obstacle_preset` 做对应。
- 记录 RViz/Gazebo 推荐显示配置，减少教学部署成本。

验收标准：

- Gazebo world 能启动。
- `ros_gz_bridge` 参数能在安装态通过 `--show-args`。
- Gazebo topic 不和 kinematic `/scan`、`/clock` 默认冲突。

### 阶段三：Gazebo 控制链路

目标：让 `/ackermann_cmd` 能驱动 Gazebo 简化模型的转向和轮速 joint controller，
用于验证方向、限幅和控制接口。

已具备：

- `gazebo_ackermann_bridge_node`
- `/gazebo/left_steering_position`
- `/gazebo/right_steering_position`
- `/model/osracer_simple/joint/*_wheel_joint/cmd_vel`

下一步：

- 用 Gazebo launch 做短时运行 smoke test，记录 joint topic 是否有输出。
- 加入左右轮差速角速度计算，匹配阿克曼转弯几何。
- 校核 steering joint 正方向和 RViz joint 动画一致。

验收标准：

- 直行时四轮角速度方向一致。
- 左转/右转时左右转向角方向正确。
- 不影响默认 kinematic race/SLAM 仿真。

### 阶段四：动力学和实车标定

目标：只在前三阶段稳定后，再加入更接近赛车场景的模型。

建议加入：

- 轮胎摩擦和侧偏近似参数。
- 速度/转向一阶响应。
- 传感器噪声和延迟。
- 低速、中速、高速三组参数 profile。
- 与实车日志对齐的速度、转向、横摆角速度误差评估。

验收标准：

- 仿真速度、转弯半径和 yaw rate 与实车日志同量级。
- 参数变化有文档和 CSV 证据。
- 高速 race 参数必须经过真车低速逐步放大验证。

## 推荐开发顺序

1. 继续完善 kinematic 场景，保证教学和 race 四阶段可复现。
2. 补 Gazebo 静态障碍物 world 和 RViz/Gazebo 教程。
3. 完成 Gazebo joint 控制方向和轮速差速验证。
4. 用实车日志反推速度响应、转向响应和传感器噪声。
5. 最后再做高保真动力学，不把它作为第一版交付门槛。

## 当前判断

当前包里的仿真方向是必要且合理的：先提供稳定的轻量仿真和 Gazebo 入口，再逐步增强
Gazebo 物理控制。这样可以服务教学科研和 RoboRacer 算法开发，同时避免过早把时间花在
难以标定、也无法替代实车测试的高保真模型上。
