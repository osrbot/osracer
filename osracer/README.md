## Usage

### Bringup

```bash
ros2 launch osracer_bringup bringup.launch.py
```

### View Snesor
```bash
ros2 launch osracer_debug debug_odom.launch.py
ros2 launch osracer_debug debug_lidar.launch.py 
ros2 launch osracer_debug debug_imu.launch.py 
ros2 launch osracer_debug debug_image.launch.py
```

### Base

```bash
ros2 launch osracer_bringup chassis_ackeramn.launch.py
```

### Lidar

```bash
ros2 launch osracer_bringup lidar.launch.py. 
```

### USB Camera

```bash
ros2 launch osracer_bringup usb_cam.launch.py
```

## Mapping

### GMapping

```bash
ros2 launch osracer_slam gmapping.launch.py
```

```bash
ros2 launch osracer_debug debug_mapping.launch.py 
```

### Cartographer

```bash
ros2 launch osracer_slam cartographer.launch.py
```

```bash
ros2 launch osracer_debug debug_cartographer.launch.py 
```

### Save the Map

```bash
ros2 launch osracer_slam map_save.launch.xml
```

```bash
ros2 launch osracer_slam map_save_cartographer.launch.xml
```

## Navigation

### NAVFN + TEB
```bash
ros2 launch osracer_navigation nav2.launch.py use_map:=map use_planner:=teb
```

### NAVFN + DWB
```bash
ros2 launch osracer_navigation nav2.launch.py use_map:=map use_planner:=dwb
```