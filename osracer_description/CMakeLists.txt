cmake_minimum_required(VERSION 3.5)
project(osracer_description)

# Add support for C++14
if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 14)
endif()

find_package(ament_cmake REQUIRED)
find_package(std_msgs REQUIRED)
find_package(rclpy REQUIRED)
find_package(rclcpp REQUIRED)
set(INCLUDE_DIRS ${ament_cmake_INCLUDE_DIRS} ${std_msgs_INCLUDE_DIRS}
  ${rclpy_INCLUDE_DIRS} ${rclcpp_INCLUDE_DIRS})
include_directories(${INCLUDE_DIRS})
set(LIBRARY_DIRS ${ament_cmake_LIBRARIES} ${std_msgs_LIBRARIES}
  ${rclpy_LIBRARIES} ${rclcpp_LIBRARIES})
set(LIBS ${ament_cmake_LIBRARIES} ${std_msgs_LIBRARIES} ${rclpy_LIBRARIES}
  ${rclcpp_LIBRARIES})

  install(DIRECTORY launch meshes urdf
    DESTINATION share/${PROJECT_NAME}/
)

ament_export_dependencies(ament_cmake)
ament_export_dependencies(std_msgs)
ament_export_dependencies(rclpy)
ament_export_dependencies(rclcpp)
ament_export_include_directories(${INCLUDE_DIRS})

ament_package()
