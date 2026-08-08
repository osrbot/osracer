cmake_minimum_required(VERSION 3.5)
project(osracer_bringup)

# Add support for C++14
if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 14)
endif()

find_package(ament_cmake REQUIRED)
find_package(std_msgs REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclpy REQUIRED)

ament_python_install_package(script)

# Install Python executables
install(PROGRAMS
  script/led_matrix.py
  script/policy_inference.py
  script/policy_observation_recorder.py
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY launch param script
    DESTINATION share/${PROJECT_NAME}/
)

ament_export_dependencies(ament_cmake)
ament_export_dependencies(std_msgs)
ament_export_dependencies(rclcpp)
ament_export_dependencies(rclpy)
ament_export_include_directories(${INCLUDE_DIRS})

ament_package()
