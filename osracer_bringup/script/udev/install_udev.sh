#! /bin/bash

sudo usermod -aG dialout $USER
sudo usermod -aG video $USER
sudo cp -f 99-osrbot-*.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo service udev restart
sudo udevadm trigger