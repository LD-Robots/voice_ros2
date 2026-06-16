from setuptools import find_packages, setup
import os
from glob import glob

package_name = 'conversational_client'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.json')),
        # Wake-word and stop-keyword assets must be installed together.
        (os.path.join('share', package_name, 'models'), glob('models/*.onnx')),
        (os.path.join('share', package_name, 'models'), glob('models/*.onnx.data')),
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
            'audio_capture_node = conversational_client.audio_capture_node:main',
            'audio_playback_node = conversational_client.audio_playback_node:main',
            'audio_segment_node = conversational_client.audio_segment_node:main',
            'wake_word_node = conversational_client.wake_word_node:main',
            'vad_node = conversational_client.vad_node:main',
            'barge_in_node = conversational_client.barge_in_node:main',
            'stop_keyword_node = conversational_client.stop_keyword_node:main',
            'speaker_id_node = conversational_client.speaker_id_node:main',
            'session_manager_node = conversational_client.session_manager_node:main',
            'voice_command_node = conversational_client.voice_command_node:main',
            'robot_command_executor_node = conversational_client.robot_command_executor_node:main',
            'attention_manager_node = conversational_client.attention_manager_node:main',
            'person_memory_store_node = conversational_client.person_memory_store_node:main',
            'conversation_control_node = conversational_client.conversation_control_node:main',
            'echo_canceller_node = conversational_client.echo_canceller_node:main',
        ],
    },
)
