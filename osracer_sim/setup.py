from glob import glob
from os import walk
from os.path import join
from setuptools import find_packages, setup

package_name = 'osracer_sim'


def model_data_files():
    data = []
    for root, _, files in walk('models'):
        if files:
            data.append((join('share', package_name, root), [join(root, name) for name in files]))
    return data

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', [f'resource/{package_name}']),
        (f'share/{package_name}', ['package.xml', 'README_zh.md']),
        (f'share/{package_name}/launch', glob('launch/*.launch.py')),
        (f'share/{package_name}/worlds', glob('worlds/*')),
        (f'share/{package_name}/scripts', glob('scripts/*')),
        (f'share/{package_name}/test', glob('test/*.py')),
    ] + model_data_files(),
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='osrbot',
    maintainer_email='osrbot@osrbot.com',
    description='Lightweight OSRacer simulation launch files and kinematic Ackermann simulator.',
    license='MIT',
    entry_points={
        'console_scripts': [
            'ackermann_kinematic_sim_node = osracer_sim.ackermann_kinematic_sim_node:main',
        ],
    },
)
