cmake_minimum_required(VERSION 3.5)
project(osracer_navigation)

# Add support for C++14
if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 14)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclpy REQUIRED)
find_package(tf2 REQUIRED)
find_package(std_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(visualization_msgs REQUIRED)

install(DIRECTORY launch params maps
    DESTINATION share/${PROJECT_NAME}/
)

ament_export_dependencies(ament_cmake)
ament_export_dependencies(rclcpp)
ament_export_dependencies(rclpy)
ament_export_dependencies(tf2)
ament_export_dependencies(std_msgs)
ament_export_dependencies(geometry_msgs)
ament_export_dependencies(visualization_msgs)
ament_export_include_directories(${INCLUDE_DIRS})

ament_package()
