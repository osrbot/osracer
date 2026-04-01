#! /bin/bash

sudo rm -f 99-osrbot-*.rules
sudo udevadm control --reload-rules
sudo service udev restart
sudo udevadm trigger