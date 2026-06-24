# OSRacer Race 四阶段开发清单

本文档用于审查 `osracer_race` 的阶段覆盖情况。比赛包不修改底层固件；导航包仅同步
与实车阿克曼几何直接相关的参数。

## 第一阶段：安全无地图跑圈

目标：低速自主跑圈，前方危险时停车。

已实现：

- `safety_node.py`：LiDAR + 当前速度 TTC 急停。
- `gap_follow_node.py`：Follow-the-Gap 无地图跑圈。
- `speed_profile_node.py`：最终命令限幅，包含速度、制动、转角、横向加速度和 safety stop 限制。
  上游控制器命令超时后会主动发布停车命令。
- `lap_timer_node.py`：基于里程计起点区域的圈速统计。
- `gap_follow.launch.py`：启动安全、Gap Follow、限幅和圈速节点。
- `race_bringup.launch.py controller:=gap_follow`：一键启动整车 bringup 和无地图比赛模式。

## 第二阶段：有地图轨迹跟踪

目标：基于 raceline 稳定跟踪，支持速度剖面。

已实现：

- `raceline_tools.py`：从 CSV 轨迹生成曲率和速度剖面。
- `track_recorder_node.py`：从 `/odometry/filtered` 录制 `x,y,speed` 轨迹 CSV。
- `pure_pursuit_node.py`：Pure Pursuit 跟踪。
- `stanley_node.py`：Stanley 跟踪。
- `obstacle_overtake_node.py`：有地图控制器前方近距离障碍绕行中间层。
- `track_record.launch.py`：启动轨迹录制。
- `pure_pursuit.launch.py`、`stanley.launch.py`：启动安全、跟踪、绕行、限幅、圈速和评测。
- `config/tracks/example_raceline.csv`：示例 raceline。

## 第三阶段：车辆能力标定

目标：把实车能力从估计值转为观测值。

已实现：

- `vehicle_id_node.py`：观测最高速度、最大加速度、最大制动减速度、
  最大 yaw rate、最大横向加速度、最小转弯半径、速度响应时间常数和
  转向响应延迟。
- `vehicle_id.launch.py`：启动车辆辨识节点。
- 默认输出 `/tmp/osracer_vehicle_identified.yaml`。

待实车继续增强：

- 多组速度阶跃下的电机响应曲线拟合。
- 多组转向阶跃下的舵机响应曲线拟合。
- 打滑阈值辨识。

## 第四阶段：高级比赛/科研算法

目标：支持算法对比、绕行和早期 MPC 实验。

已实现：

- `mpc_controller_node.py`：轻量 kinematic shooting controller，按实车加减速和
  速度响应时间裁剪可达速度候选，并结合 raceline 目标速度和路径进度选择命令。
- `mpc.launch.py`：启动安全、MPC、绕行、限幅、圈速和评测。
- `race_evaluator_node.py`：记录速度、命令、横向误差和航向误差 CSV。
- `race_report_tools.py`：汇总一个或多个评测 CSV，便于算法对比。
- `race_bringup.launch.py controller:=mpc`：一键启动整车 bringup 和 MPC 模式。

待实车继续增强：

- LTV-MPC 或非线性 MPC 替换当前轻量 shooting controller。
- 对手车识别和多车超车策略。
- 与真实赛道地图/轨迹优化器联动。

## 当前验证状态

本机已完成：

- Python/launch 编译。
- `package.xml` XML 解析。
- YAML 参数解析。
- helper 模块 import smoke test。
- `raceline_tools` 和 `race_report_tools` 离线实跑。
- 离线单元测试。
- `scripts/check_race_package.sh` 自检脚本；在 ROS/colcon 存在时会自动构建 `osracer_race`。
- `scripts/validate_race_ros.sh` 车端 ROS 验证脚本；构建后检查资源、CLI、launch 参数和可选 topic。
- 安装布局模拟检查，覆盖安装后的 share 资源、文档、launch、脚本和测试。
- `git diff --check`。
- 缓存、PDF、固件、分区表、隐私关键词扫描。
- `PRE_PUSH_REVIEW_zh.md` 推送前审查记录。

本机未完成：

- 连接真实硬件后的 ROS topic 运行态检查。
- 实车测试。

未完成原因：串口、LiDAR、相机、RViz 和实车运动验证必须在车端 Jetson Orin Nano
或连接真实车辆的 ROS 2 Humble 环境执行。

ROS/实车验证步骤见 `ROS_VALIDATION_zh.md`。
