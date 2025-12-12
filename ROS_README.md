## Preparation

- install [ros humble](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html)

### rustdesk

```bash
wget https://gh-proxy.com/https://github.com/rustdesk/rustdesk/releases/download/1.4.3/rustdesk-1.4.3-aarch64.deb
sudo dpkg -i rustdesk-1.4.3-aarch64.deb
sudo apt install --fix-broken
rustdesk
```

### UDEV

```bash
sudo usermod -aG dialout $USER

sudo udevadm control --reload-rules
sudo service udev restart
sudo udevadm trigger

lsb_release -a
sudo apt install python3-pip
sudo apt install python3
sudo passwd root
```


### ROS1 NOETIC

- https://wiki.ros.org/noetic/Installation/Ubuntu
- https://mirrors.ustc.edu.cn/help/ros.html

```bash
sudo apt update && sudo apt install curl -y

sudo curl -sSL https://gh-proxy.com/https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
sudo sh -c 'echo "deb [signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] https://mirrors.ustc.edu.cn/ros/ubuntu $(lsb_release -sc) main" > /etc/apt/sources.list.d/ros-latest.list'

export ROS_APT_SOURCE_VERSION=$(curl -s https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest | grep -F "tag_name" | awk -F\" '{print $4}')
curl -L -o /tmp/ros1-apt-source.deb "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros-apt-source_${ROS_APT_SOURCE_VERSION}.$(. /etc/os-release && echo ${UBUNTU_CODENAME:-${VERSION_CODENAME}})_all.deb"
sudo dpkg -i /tmp/ros1-apt-source.deb

sudo apt update
sudo apt install ros-noetic-desktop-full

echo "source /opt/ros/noetic/setup.bash" >> ~/.bashrc
```

```bash
sudo apt install ros-noetic- -y
sudo apt install ros-noetic-imu-tools -y
sudo apt install ros-noetic-robot-localization -y
sudo apt install ros-noetic-joint-state-publisher -y
sudo apt install ros-noetic-joint-state-publisher-gui -y
sudo apt install ros-noetic-usb-cam -y
sudo apt install ros-noetic-ackermann_msgs -y
sudo apt install ros-noetic-rqt-tf-tree  -y
sudo apt install ros-noetic-ackermann-msgs ros-noetic-move-base -y
sudo apt install ros-noetic-map-server  ros-noetic-amcl  -y
sudo apt install ros-noetic-ros-control ros-noetic-ros-controllers  -y
sudo apt install ros-noetic-teb-local-planner  -y
```

sudo dpkg -i --force-overwrite /var/cache/apt/archives/ffmpeg_7%3a4.2.7-nvidia_arm64.deb  #https://www.cnblogs.com/chenyujie/p/17875572.html

### ROS2 HUMBLE
```bash
locale  # check for UTF-8
sudo apt update && sudo apt install locales
sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
export LANG=en_US.UTF-8
sudo apt update && sudo apt install locales

sudo apt install software-properties-common
sudo add-apt-repository universe

sudo apt update && sudo apt install curl -y
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
sudo curl -sSL https://gh-proxy.com/https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://mirrors.ustc.edu.cn/ros2/ubuntu $(lsb_release -sc) main" | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

sudo apt update && sudo apt install curl -y
export ROS_APT_SOURCE_VERSION=$(curl -s https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest | grep -F "tag_name" | awk -F\" '{print $4}')
curl -L -o /tmp/ros2-apt-source.deb "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.$(. /etc/os-release && echo ${UBUNTU_CODENAME:-${VERSION_CODENAME}})_all.deb"
sudo dpkg -i /tmp/ros2-apt-source.deb

sudo apt upgrade
sudo apt install ros-humble-desktop-full
sudo apt autoremove
sudo apt install ros-dev-tools
echo "source /opt/ros/humble/setup.bash" >>  ~/.bashrc
```

```bash
sudo apt install ros-humble-nav2-bringup
sudo apt install ros-humble-libg2o
sudo apt install ros-humble-imu-tools
sudo apt install ros-humble-robot-localization
sudo apt install ros-humble-joint-state-publisher
sudo apt install ros-humble-joint-state-publisher-gui
sudo apt install ros-humble-usb-cam
sudo apt install ros-humble-cartographer-ros
sudo apt install ros-humble-cartographer-rviz
sudo apt install ros-humble-rqt-tf-tree
sudo apt install ros-humble-ackermann_msgs -y
```