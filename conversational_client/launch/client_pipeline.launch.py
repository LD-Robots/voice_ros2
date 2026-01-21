"""
Launch file pentru client-side nodes.
Pornește toate nodurile client pentru conversație vocală.
"""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    pkg_share = get_package_share_directory('conversational_client')
    models_dir = os.path.join(pkg_share, 'models')
    
    # Construim string-ul pentru custom_models
    # Format: path:kind
    hello_path = os.path.join(models_dir, 'hello_robot.onnx')
    stop_path = os.path.join(models_dir, 'stop_robot.onnx')
    goodbye_path = os.path.join(models_dir, 'goodbye_robot.onnx')
    
    # Definim modelele: hello=wake, stop/goodbye=stop
    custom_models = f"{hello_path}:wake,{stop_path}:stop,{goodbye_path}:stop"

    return LaunchDescription([
        
        # Audio Capture (microfon)
        Node(
            package='conversational_client',
            executable='audio_capture_node',
            name='audio_capture_node',
            output='screen',
            parameters=[{
                'device_index': 3,  # Device ID 3 - pulse (folosește microfonul selectat în Settings)
            }]
        ),
        
        # Wake Word (detectare "hello robot")
        Node(
            package='conversational_client',
            executable='wake_word_node',
            name='wake_word_node',
            output='screen',
            parameters=[{
                'threshold': 0.45,  # Mai sensibil pentru testare
                'custom_models': custom_models,
            }]
        ),
        
        # VAD (Voice Activity Detection)
        Node(
            package='conversational_client',
            executable='vad_node',
            name='vad_node',
            output='screen',
            parameters=[{
                'aggressiveness': 2,
                'wake_word_enabled': False,
                'session_timeout': 8.0,
            }]
        ),
        
        # Audio Segment (bufferează audio și trimite segment complet)
        Node(
            package='conversational_client',
            executable='audio_segment_node',
            name='audio_segment_node',
            output='screen',
            parameters=[{
                'min_segment_seconds': 0.5,
                'max_segment_seconds': 30.0,
            }]
        ),
        
        # Barge-in (detectare "stop" keyword)
        Node(
            package='conversational_client',
            executable='barge_in_node',
            name='barge_in_node',
            output='screen',
            parameters=[{
                'prob_threshold': 0.96,
                'hits_required': 2,
            }]
        ),
        
        # Audio Playback (difuzor)
        Node(
            package='conversational_client',
            executable='audio_playback_node',
            name='audio_playback_node',
            output='screen',
        ),
    ])
