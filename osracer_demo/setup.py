from glob import glob
from setuptools import find_packages, setup

package_name = 'osracer_demo'

setup(
    name=package_name,
    version='1.2.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', [f'resource/{package_name}']),
        (f'share/{package_name}', ['package.xml', 'README_zh.md']),
        (f'share/{package_name}/scripts', glob('scripts/*')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='osrbot',
    maintainer_email='winter@osrbot.com',
    description='Field demo tools for OSRacer ROS 2 bringup and low-speed motion checks.',
    license='MIT',
    entry_points={
        'console_scripts': [
            'drive_demo = osracer_demo.drive_demo:main',
            'leader_demo = osracer_demo.leader_demo:main',
            'odom_watch = osracer_demo.odom_watch:main',
        ],
    },
)
