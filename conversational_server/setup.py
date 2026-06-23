from setuptools import find_packages, setup
import os
from glob import glob

package_name = 'conversational_server'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='voice_ros2 contributors',
    maintainer_email='',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'asr_node = conversational_server.asr_node:main',
            'llm_node = conversational_server.llm_node:main',
            'tts_node = conversational_server.tts_node:main',
            'gemini_live_node = conversational_server.gemini_live_node:main',
            'backend_manager_node = conversational_server.backend_manager_node:main',
        ],
    },
)
