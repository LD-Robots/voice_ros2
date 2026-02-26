"""
Launch file for client-side nodes.
Starts all client nodes for voice conversation.
"""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node
from pathlib import Path


def _find_workspace_root():
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


def generate_launch_description():
    pkg_share = get_package_share_directory('conversational_client')
    models_dir = os.path.join(pkg_share, 'models')
    workspace_root = _find_workspace_root()
    voices_dir = os.path.join(
        str(workspace_root) if workspace_root else os.getcwd(),
        'voices'
    )
    
    # Build the string for custom_models
    # Format: path:kind
    hello_path = os.path.join(models_dir, 'hello_robot.onnx')
    stop_path = os.path.join(models_dir, 'stop_robot.onnx')  # testăm modelul original
    goodbye_path = os.path.join(models_dir, 'goodbye_robot.onnx')
    
    # Definim modelele: hello=wake, stop_robot_oww=barge_in (doar stop TTS), goodbye=stop (bye bye)
    custom_models = f"{hello_path}:wake,{stop_path}:barge_in,{goodbye_path}:stop"
    
    # Individual thresholds per model (stop_robot_oww lower for better detection)
    # stop_robot_oww lower (0.25) for detection even when the robot is speaking
    # stop_robot lower (0.15) for detection even when the robot is speaking
    model_thresholds = "hello_robot:0.10,goodbye_robot:0.50"
    stop_keyword_path = os.path.join(voices_dir, 'stop_keyword.onnx')
    if not os.path.exists(stop_keyword_path):
        stop_keyword_path = os.path.join(models_dir, 'stop_keyword.onnx')
    enrollment_dir = os.path.join(voices_dir, 'enrollment')

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
                'threshold': 0.5,  # Default threshold
                'cooldown_ms': 1500,
                'custom_models': custom_models,
                'model_thresholds': model_thresholds,
            }],
            arguments=['--ros-args', '--log-level', 'wake_word_node:=DEBUG']
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
        
        # Audio Segment (buffers audio and sends complete segment)
        Node(
            package='conversational_client',
            executable='audio_segment_node',
            name='audio_segment_node',
            output='screen',
            arguments=['--ros-args', '--log-level', 'audio_segment_node:=DEBUG'],
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
                'stop_model_path': stop_keyword_path,
                'stop_prob_threshold': 0.95,  # Increased to prevent false positives
                'stop_logit_margin': 0.5,
                'stop_hits_required': 2,      # Remote suggests 2, safer
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
                'enrollment_dir': enrollment_dir,
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

        # Voice Command Intent (raise hands / move / dance)
        Node(
            package='conversational_client',
            executable='voice_command_node',
            name='voice_command_node',
            output='screen',
            parameters=[{
                'min_transcription_confidence': 0.45,
                'default_steps': 1,
                'max_steps': 20,
                'enable_tts_ack': False,
            }]
        ),

        # Robot Command Executor (bridges voice intents to controllers)
        Node(
            package='conversational_client',
            executable='robot_command_executor_node',
            name='robot_command_executor_node',
            output='screen',
            parameters=[{
                'execution_enabled': True,
                'move_mode': 'twist',
                'cmd_vel_topic': '/cmd_vel',
                'behavior_mode': 'topic',
                'behavior_topic': '/robot_behavior_command',
                'raise_hands_service': '/raise_hands',
                'dance_service': '/dance',
                'preempt_on_new_command': True,
                'enable_voice_cancel': True,
                'enable_risky_confirmation': True,
                'confirmation_timeout_s': 12.0,
                'risky_steps_threshold': 5,
                'risky_backward_steps_threshold': 3,
            }]
        ),
    ])
