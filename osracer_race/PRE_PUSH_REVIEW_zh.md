# OSRacer Race 推送前审查记录

本文档记录 `osracer_race` 当前可证明的推送前状态。它不替代
`ROS_VALIDATION_zh.md` 的 ROS/实车验证。

## 范围

本次新增的是 ROS 2 上位机比赛/科研算法包：

- 不修改底层固件。
- 不加入原理图、PDF、分区表或固件配置。
- 仅同步与实车阿克曼几何直接相关的 Nav2/TEB 参数。
- 通过 `/ackermann_cmd` 接入现有 OSRacer 底盘命令链路。

## 已完成静态验证

已通过 `scripts/check_race_package.sh`：

- shell 脚本语法检查。
- Python 语法编译。
- `package.xml` XML 解析。
- YAML 参数解析。
- 离线单元测试。
- helper 模块 import smoke test。
- `raceline_tools` 和 `race_report_tools` smoke test。
- `validate_race_ros.sh` 车端验证脚本语法和安装可执行性检查。
- `colcon build --symlink-install --packages-select osracer_race` 需在车端或
  ROS 2 Humble 开发环境执行。

已通过推送前清理检查：

- `git diff --check`。
- 未发现 `.DS_Store`、`*.pyc`、PDF、`sdkconfig*`、`partitions*.csv`。
- 未发现真实隐私路径或敏感凭据关键词。
- 未发现旧品牌关键词残留。

## 已完成代码审查点

- 所有 race 控制器最终通过 `speed_profile_node` 输出 `/ackermann_cmd`。
- `speed_profile_node` 提供最终速度、制动、转角、横向加速度、watchdog 和 safety stop 限制。
  safety stop 激活期间 watchdog 会持续发布停车命令。
- `safety_node` 对前方无有效 LiDAR 点或 `/scan` 断流默认 fail-safe 停车。
- 有地图控制器先输出 `/race/tracking_ackermann_cmd`，再经 `obstacle_overtake_node`
  和 `speed_profile_node`。
- `race_safe.yaml` 和 `race_fast.yaml` 都是完整运行参数文件。
- `vehicle.yaml` 使用当前实车参数：轮半径 `0.0425m`、轴距 `0.285m`、轮距 `0.215m`、
  编码器 `1024` 单倍频、总减速比 `10.55:1`。
- `vehicle_id_node.py` 已扩展第三阶段车辆辨识输出：最大横向加速度、速度阶跃响应时间常数、
  转向响应延迟；未观测到时 YAML 字段保持 `null`，不自动覆盖运行参数。
- `mpc_controller_node.py` 已扩展第四阶段 MPC：速度候选受实车加减速和速度响应时间约束，
  代价函数包含 raceline 目标速度和路径前进奖励。
- 已按清理范围删除未引用的 bringup 测试脚本、旧 STM32 底盘链路和 no-RC/no-mag
  底盘变体；保留 `robot_description_tf.launch.py`、`osracer.csv`、磁力计
  `result.yaml` 和 `osracer_slam/maps`。
- 已删除重复的 `osracer_navigation/maps`，并将导航默认地图改为
  `osracer_slam/maps/map.yaml`。
- `teb_nav2_params.yaml` 的 `max_vel_theta` 已按 `max_vel_x / min_turning_radius`
  对齐到 `6.0rad/s`，与当前 `3.0m/s` 最大速度和 `0.50m` 最小转弯半径一致。
- `twist_bridge.py` 默认轴距已对齐实车 `0.285m`；`chassis_ackermann.py`
  发布 odom twist covariance，供 EKF 融合使用，未修改下位机串口协议。
- `package.xml` 与 `setup.py` 使用 MIT，和仓库根目录 `LICENSE` 一致。
- `README_zh.md` 已补充详细使用教程，覆盖安装自检、topic 链路、四阶段运行、
  车端验证和常见问题；根 `README.md` 已链接比赛包文档并给出上手顺序。
- `osracer_dependency` 作为锁定版本的第三方依赖 submodule 保留，用于固定
  Lakibeam、gmapping、camera calibration、TEB 和 `costmap_converter` 等依赖。

## 已完成 ROS 包静态验证

验证覆盖：

- Python/launch 编译。
- `package.xml` XML 解析。
- YAML 参数解析。
- helper 模块 import smoke test。
- `raceline_tools` 和 `race_report_tools` 离线实跑。
- 离线单元测试。
- 安装布局模拟检查，覆盖安装后的 share 资源、文档、launch、脚本和测试。
- `scripts/check_race_package.sh` 自检入口。
- `scripts/validate_race_ros.sh` 车端 ROS 验证入口。

注意：`costmap_converter` 的 Humble/OpenCV 兼容修复位于 `osracer_dependency`
子模块内，推送时必须先提交并推送子模块，再更新主仓库 submodule 指针。

## 未完成验证

仍未完成的是需要真实设备或车端运行态的项目：

- ROS topic 运行态检查。
- LiDAR、相机、串口和 RViz 真实设备检查。
- 真车低速安全验证。
- 真车轨迹跟踪、车辆辨识和 MPC 低速验证。

这些步骤必须在 Jetson Orin Nano 车端或连接实际硬件的 ROS 2 Humble 开发机上按
`ROS_VALIDATION_zh.md` 执行。
