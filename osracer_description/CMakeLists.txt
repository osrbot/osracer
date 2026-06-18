cmake_minimum_required(VERSION 3.5)
project(osracer_description)

find_package(ament_cmake REQUIRED)

install(
  DIRECTORY config launch meshes urdf
  DESTINATION share/${PROJECT_NAME}
)

install(PROGRAMS
  scripts/osracer_joint_state_publisher.py
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
