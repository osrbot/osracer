# OSRacer 现场演示包

这个包只包含 ROS 2 上位机演示工具，不包含 `osrcore` 底层固件源码、硬件 PDF、烧录分区表或本地交付配置。

## 前提

- Ubuntu 22.04 + ROS 2 Humble
- 当前 workspace 已编译并 source
- 底盘串口默认 `/dev/osrbot_base`
- 当前 `osrcore` 串口波特率 `460800`
- 固件默认周期遥测为 `stream sync`，输出 `s/m/r/b`

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
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/start_active_mapping_demo.sh
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/start_slam_navigation_demo.sh
$(ros2 pkg prefix osracer_demo)/share/osracer_demo/scripts/stop_all_demo.sh
```

导航和边建图边导航脚本会自动生成演示用低速 TEB 参数到 `/tmp/osracer_demo_teb_slow_nav2_params.yaml`，不修改 `osracer_navigation` 包内的正式参数。

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
- `stop_all_demo.sh` 会同时向 `/cmd_vel` 和 `/ackermann_cmd` 发布停车，并停止常见 Nav2、SLAM、RViz 和底盘演示进程。
- 遥控器仍保留底层急停/接管价值。

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
