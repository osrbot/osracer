from glob import glob
from setuptools import find_packages, setup

package_name = 'osracer_calib'

setup(
    name=package_name,
    version='1.3.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', [f'resource/{package_name}']),
        (f'share/{package_name}', ['package.xml']),
        (f'share/{package_name}/launch', glob('launch/*.launch.py')),
        (f'share/{package_name}/config', glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='osrbot',
    maintainer_email='winter@osrbot.com',
    description='Magnetometer hard-iron and soft-iron calibration using ellipsoid fitting.',
    license='MIT',
    entry_points={
        'console_scripts': [
            'mag_calibration_node = osracer_calib.mag_calibration_node:main',
        ],
    },
)
