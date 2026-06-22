FROM osrf/ros:humble-desktop

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-c"]

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      git \
      libsuitesparse-dev \
      python3-colcon-common-extensions \
      python3-numpy \
      python3-pip \
      python3-serial \
      python3-tk \
      python3-yaml \
      ros-humble-ackermann-msgs \
      ros-humble-cartographer-ros \
      ros-humble-cartographer-rviz \
      ros-humble-cv-bridge \
      ros-humble-imu-tools \
      ros-humble-joint-state-publisher \
      ros-humble-joint-state-publisher-gui \
      ros-humble-libg2o \
      ros-humble-nav2-bringup \
      ros-humble-robot-localization \
      ros-humble-rqt-tf-tree \
      ros-humble-slam-toolbox \
      ros-humble-tf-transformations \
      ros-humble-usb-cam && \
    rm -rf /var/lib/apt/lists/*

COPY check_ros_humble_workspace.sh /usr/local/bin/check_ros_humble_workspace.sh
RUN chmod +x /usr/local/bin/check_ros_humble_workspace.sh

WORKDIR /tmp/osracer_ws
ENTRYPOINT ["/usr/local/bin/check_ros_humble_workspace.sh"]
