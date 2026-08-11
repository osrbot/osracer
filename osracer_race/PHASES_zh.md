# OSRacer Race 功能阶段与适用范围

`osracer_race` 按四个功能阶段组织无地图行驶、轨迹跟踪、车辆参数观测和高级
控制算法。各阶段共用最终速度/转角限制与安全停车链路，不修改底层固件。

## 第一阶段：安全无地图行驶

适用于低速避障、基础赛道演示和安全链路验证。

主要功能：

- `safety_node.py`：根据 LiDAR 和当前速度执行 TTC 安全停车；
- `gap_follow_node.py`：执行 Follow-the-Gap 无地图控制；
- `speed_profile_node.py`：限制速度、制动、转角和横向加速度，并处理安全停车
  与上游命令超时；
- `lap_timer_node.py`：基于里程计起点区域统计圈速；
- `gap_follow.launch.py`：启动安全、Gap Follow、限幅和圈速节点；
- `race_bringup.launch.py controller:=gap_follow`：同时启动整车 bringup 和
  无地图控制链路。

## 第二阶段：轨迹录制与跟踪

适用于已知赛道的 raceline 录制、速度剖面生成和闭环轨迹跟踪。

主要功能：

- `track_recorder_node.py`：将 `/odometry/filtered` 记录为轨迹 CSV；
- `raceline_tools.py`：根据轨迹生成曲率和目标速度；
- `track_record.launch.py`：启动轨迹录制；
- `pure_pursuit_node.py`：执行 Pure Pursuit 跟踪；
- `stanley_node.py`：执行 Stanley 跟踪；
- `obstacle_overtake_node.py`：在轨迹控制链路中提供低速障碍物绕行；
- `pure_pursuit.launch.py` 和 `stanley.launch.py`：组合安全、跟踪、绕行、
  限幅、圈速与评测节点；
- `config/tracks/example_raceline.csv`：提供轨迹文件格式示例。

## 第三阶段：车辆能力观测

适用于在封闭安全场地记录车辆响应数据，为控制参数调整提供依据。

`vehicle_id_node.py` 可观测以下指标：

- 最高速度；
- 最大加速度和制动减速度；
- 最大横摆角速度和横向加速度；
- 最小转弯半径；
- 速度响应时间常数；
- 转向响应延迟。

`vehicle_id.launch.py` 用于启动观测节点，默认结果写入
`/tmp/osracer_vehicle_identified.yaml`。观测结果取决于测试场地、车辆状态和
输入动作，不应直接作为其他车辆的通用参数。

## 第四阶段：高级控制与算法比较

适用于教学、研究和低速算法评估。

主要功能：

- `mpc_controller_node.py`：轻量 kinematic shooting controller；
- `mpc.launch.py`：组合安全、MPC、绕行、限幅、圈速和评测节点；
- `race_evaluator_node.py`：记录速度、命令、横向误差和航向误差；
- `race_report_tools.py`：汇总一个或多个评测 CSV；
- `race_bringup.launch.py controller:=mpc`：启动整车 bringup 和 MPC
  控制链路。

当前 MPC 和障碍物绕行属于实验性功能，不能替代最终安全层、遥控接管或底层
急停。高速使用前必须完成低速递进验证。

## 功能成熟度

| 功能 | 状态 | 使用边界 |
| --- | --- | --- |
| TTC 安全停车与 Gap Follow | 可用 | 从 `race_safe.yaml` 和低速场地开始 |
| 轨迹录制、Pure Pursuit、Stanley | 可用 | 需要有效 odom、raceline 和安全限幅 |
| 车辆能力观测 | 可用 | 输出仅适用于被测车辆和测试条件 |
| 轻量 MPC 与障碍物绕行 | 实验性 | 仅用于研究和低速验证 |

## 使用与验证

安装、参数和启动命令见 [`README_zh.md`](README_zh.md)。ROS 入口检查、低速
安全验证、轨迹录制和控制器验证步骤见
[`ROS_VALIDATION_zh.md`](ROS_VALIDATION_zh.md)。
