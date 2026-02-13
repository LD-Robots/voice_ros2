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
    stop_path = os.path.join(models_dir, 'stop_robot.onnx')  # testăm modelul original
    goodbye_path = os.path.join(models_dir, 'goodbye_robot.onnx')
    
    # Definim modelele: hello=wake, stop_robot_oww=barge_in (doar stop TTS), goodbye=stop (bye bye)
    custom_models = f"{hello_path}:wake,{stop_path}:barge_in,{goodbye_path}:stop"
    
    # Threshold-uri individuale per model (stop_robot_oww mai mic pentru detectare mai bună)
    # stop_robot_oww mai mic (0.25) pentru detectare chiar și când robotul vorbește
    # stop_robot mai mic (0.15) pentru detectare chiar și când robotul vorbește
    model_thresholds = "hello_robot:0.30,stop_robot:0.15,goodbye_robot:0.50"

    return LaunchDescription([
        
        # Audio Capture (microfon)
        Node(
            package='conversational_client',
            executable='audio_capture_node',
            name='audio_capture_node',
            output='screen',
            parameters=[{
                'device_index': -1,  # Auto-detect (use OS default/PulseAudio)
            }]
        ),
        
        # Wake Word (detectare "hello robot") + Stop Keyword ("stop")
        Node(
            package='conversational_client',
            executable='wake_word_node',
            name='wake_word_node',
            output='screen',
            parameters=[{
                'threshold': 0.8,  # Default threshold
                'cooldown_ms': 1500,
                'custom_models': custom_models,
                'model_thresholds': model_thresholds,
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
                'wake_word_enabled': True,
                'session_timeout': 30.0,
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
        
        # Barge-in (detectare voce + PyTorch stop keyword)
        Node(
            package='conversational_client',
            executable='barge_in_node',
            name='barge_in_node',
            output='screen',
            parameters=[{
                # Voice-based barge-in params (original)
                'min_voice_ms': 600,
                # PyTorch stop keyword detector
                'stop_enabled': True,
                'stop_model_path': os.path.expanduser('~/voice_ros2/voices/stop_keyword.onnx'),
                'stop_prob_threshold': 0.8,  # Increased to prevent false positives (was 0.9)
                'stop_logit_margin': 0.2,
                'stop_hits_required': 2,
                'stop_frame_samples': 16000,  # Frame = 1s (impus de model!)
                'stop_hop_samples': 4000,     # Hop = 0.25s = verificare la fiecare 250ms
            }]
        ),
        
        # Audio Playback (difuzor)
        Node(
            package='conversational_client',
            executable='audio_playback_node',
            name='audio_playback_node',
            output='screen',
        ),

        # Speaker Identification (cine vorbește)
        Node(
            package='conversational_client',
            executable='speaker_id_node',
            name='speaker_id_node',
            output='screen',
            parameters=[{
                'enrollment_dir': os.path.expanduser('~/voice_ros2/voices/enrollment/'),
                'similarity_threshold': 0.25,
            }]
        ),
        
        # Session Manager (Goodbye handling)
        Node(
            package='conversational_client',
            executable='session_manager_node',
            name='session_manager_node',
            output='screen',
        ),
    ])
