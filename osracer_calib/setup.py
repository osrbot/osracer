from setuptools import find_packages, setup

package_name = 'osracer_calib'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', [f'resource/{package_name}']),
        (f'share/{package_name}', ['package.xml']),
        (f'share/{package_name}/launch', ['launch/mag_calibration.launch.py']),
        (f'share/{package_name}/config', ['config/mag_calibration.yaml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='kitso666',
    maintainer_email='kitso666@example.com',
    description='Magnetometer hard-iron and soft-iron calibration using ellipsoid fitting.',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'mag_calibration_node = osracer_calib.mag_calibration_node:main',
        ],
    },
)
