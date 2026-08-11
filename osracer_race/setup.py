from glob import glob
from setuptools import find_packages, setup

package_name = 'osracer_race'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', [f'resource/{package_name}']),
        (f'share/{package_name}', [
            'package.xml',
            'README_zh.md',
            'PHASES_zh.md',
            'ROS_VALIDATION_zh.md',
        ]),
        (f'share/{package_name}/config', glob('config/*.yaml')),
        (f'share/{package_name}/config/tracks', glob('config/tracks/*')),
        (f'share/{package_name}/launch', glob('launch/*.launch.py')),
        (f'share/{package_name}/scripts', glob('scripts/*')),
        (f'share/{package_name}/test', glob('test/*.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='osrbot',
    maintainer_email='winter@osrbot.com',
    description='Race-mode safety, planning, and control algorithms for OSRacer.',
    license='MIT',
    entry_points={
        'console_scripts': [
            'gap_follow_node = osracer_race.gap_follow_node:main',
            'lap_timer_node = osracer_race.lap_timer_node:main',
            'mpc_controller_node = osracer_race.mpc_controller_node:main',
            'obstacle_overtake_node = osracer_race.obstacle_overtake_node:main',
            'pure_pursuit_node = osracer_race.pure_pursuit_node:main',
            'race_evaluator_node = osracer_race.race_evaluator_node:main',
            'race_report_tools = osracer_race.race_report_tools:main',
            'raceline_tools = osracer_race.raceline_tools:main',
            'safety_node = osracer_race.safety_node:main',
            'speed_profile_node = osracer_race.speed_profile_node:main',
            'stanley_node = osracer_race.stanley_node:main',
            'track_recorder_node = osracer_race.track_recorder_node:main',
            'vehicle_id_node = osracer_race.vehicle_id_node:main',
        ],
    },
)
