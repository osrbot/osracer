from glob import glob
from setuptools import find_packages, setup

package_name = 'osracer_demo'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', [f'resource/{package_name}']),
        (f'share/{package_name}', ['package.xml', 'README_zh.md']),
        (f'share/{package_name}/scripts', glob('scripts/*')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='kitso666',
    maintainer_email='kitso666@example.com',
    description='Field demo tools for OSRacer ROS 2 bringup and low-speed motion checks.',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'drive_demo = osracer_demo.drive_demo:main',
            'leader_demo = osracer_demo.leader_demo:main',
            'odom_watch = osracer_demo.odom_watch:main',
        ],
    },
)
