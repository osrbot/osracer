# OSRacer 现场演示包

这个包只包含 ROS 2 上位机演示工具，不包含 `osrcore` 底层固件源码、硬件 PDF、烧录分区表或本地交付配置。

## 前提

- Ubuntu 22.04 + ROS 2 Humble
- 当前 workspace 已编译并 source
- 底盘串口默认 `/dev/osrbot_base`
- 底盘串口波特率默认 `460800`
- 底盘节点启动时会记录固件版本信息
- 支持的固件会自动维护 ROS 主机连接状态

## 常用命令

检查环境和串口：

```bash
ros2 run osracer_demo leader_demo
```

安装桌面图标：

```bash
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/install_desktop_shortcuts.sh
```

安装后桌面会出现 `OSRacer Demo`，双击即可打开图形控制台。

图形控制台默认使用英文界面，便于公开 demo 和现场交付统一。

如果双击后没有窗口，查看启动日志：

```bash
tail -n 120 ~/osracer_demo/logs/osracer-demo-launch.log
```

如需临时改到其它目录，可设置 `OSRACER_DEMO_LOG_DIR=/path/to/logs` 后重新运行安装桌面图标命令。

如果工作区不在 `~/osracer_ws`、`~/osracer` 或 `~/Desktop/osracer/osracer`，
设置 `OSRACER_WS=/path/to/workspace` 后重新运行安装桌面图标命令。

编译工作区：

```bash
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/build_workspace.sh ~/osracer_ws
```

命令行启动底盘链路：

```bash
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/start_chassis.sh /dev/osrbot_base
```

低速暖机：

```bash
ros2 run osracer_demo drive_demo warmup --yes
```

观察里程计：

```bash
ros2 run osracer_demo odom_watch
```

高级演示也可以直接用脚本启动：

```bash
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/start_basic_demo.sh
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/open_odom_rviz.sh
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/start_mapping_demo.sh
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/start_navigation_demo.sh
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/start_active_mapping_demo.sh gmapping
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/start_active_mapping_demo.sh cartographer
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/start_slam_navigation_demo.sh
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/save_map_demo.sh default
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/save_map_demo.sh cartographer
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/stop_all_demo.sh
```

导航和边建图边导航脚本会自动生成演示用低速 TEB 参数到 `~/osracer_demo/logs/runtime/teb_slow_nav2_params.yaml`，不修改 `osracer_navigation` 包内的正式参数。运行日志、后台 PID 状态和临时参数都集中在 `~/osracer_demo/logs/` 下，方便现场排查和清理。保存地图时默认使用 `osracer_slam` 包现有 map-save launch 文件里的默认 maps 目录。

低速动作：

```bash
ros2 run osracer_demo drive_demo straight
ros2 run osracer_demo drive_demo left
ros2 run osracer_demo drive_demo right
ros2 run osracer_demo drive_demo figure8 --loops 1
ros2 run osracer_demo drive_demo stop --yes
```

## 安全约束

- 默认通过 `/cmd_vel` 控制，走当前 `osracer_bringup` 链路。
- 动作开始前默认需要确认，`--yes` 才会跳过确认。
- `Ctrl-C` 或退出时会重复发布停车命令。
- `stop_all_demo.sh` 会重复向 `/cmd_vel` 和 `/ackermann_cmd` 发布停车，再清理 demo 相关 Nav2、SLAM、RViz、底盘和传感器节点；不会按全局 `ros` 关键字杀掉其它 ROS 任务。
- 如果清理后仍有 demo 相关进程残留，`stop_all_demo.sh` 会在日志中打印匹配到的 PID 和命令，便于现场继续排查。
- 图形控制台提供 `Save Map` / `Save Cartographer Map`，并把边走边建图拆成 GMapping 和 Cartographer 两个入口。
- 保存地图前会检查 `/map`，没有建图数据时会直接提示先启动建图。
- 高级功能运行时会禁用其它模式切换按钮，保留保存地图、急停和停止高级节点。
- 停止时状态栏会显示发布停车、停止跟踪节点、清理 ROS 节点等阶段，完成后回到 `Idle`。
- 高级功能会在启动底盘、TF、雷达、SLAM、Nav2、RViz 前先检查必需 ROS 包和导航地图，缺失时直接报错退出。
- 遥控器仍保留底层急停/接管价值。
- 更新 demo 后建议重新运行桌面图标安装脚本，刷新双击入口。

## 建议演示流程

1. 运行 `ros2 run osracer_demo leader_demo` 打开图形控制台。
2. 先点“状态检查”，确认串口、ROS 包和 Topic 正常。
3. 悬空点“启动车辆链路”和“暖机小动作”，确认轮子方向、转向方向、停车正常。
4. 落地后依次测试直线、左缓弯、右缓弯和 8 字演示。
5. 建图、导航等高级演示只负责启动 ROS 节点和 RViz，不自动下发目标点；现场由操作者在 RViz 中设置初始位姿和近距离目标。

## 环境变量

```bash
export OSRACER_PORT=/dev/osrbot_base
export OSRACER_BAUD=460800
export OSRACER_WS=~/osracer_ws
```
