"""
Launch file for the full system (server + client).
Starts all nodes for local testing.
"""
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from ament_index_python.packages import get_package_share_directory
import os
from pathlib import Path


def _find_workspace_root() -> Path | None:
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


def generate_launch_description():
    client_share = get_package_share_directory('conversational_client')
    models_dir = os.path.join(client_share, 'models')
    workspace_root = _find_workspace_root()
    voices_dir = os.path.join(
        str(workspace_root) if workspace_root else os.getcwd(),
        'voices'
    )

    stop_model_path = os.path.join(voices_dir, 'stop_keyword.onnx')
    hello_model_path = os.path.join(models_dir, 'hello_robot.onnx')
    stop_model_path_oww = os.path.join(models_dir, 'stop_robot.onnx')
    goodbye_model_path = os.path.join(models_dir, 'goodbye_robot.onnx')

    return LaunchDescription([
        # ========== ARGUMENTS ==========
        DeclareLaunchArgument(
            'asr_model_size',
            default_value='medium',  # Upgraded from 'small' for better accuracy
            description='Whisper model size'
        ),
        DeclareLaunchArgument(
            'llm_provider',
            default_value='groq',
            description='LLM provider (groq/ollama)'
        ),
        DeclareLaunchArgument(
            'llm_model',
            default_value='llama-3.1-8b-instant',
            description='LLM model name'
        ),
        
        # ========== SERVER NODES ==========
        
        Node(
            package='conversational_server',
            executable='asr_node',
            name='asr_node',
            output='screen',
            parameters=[{
                'model_size': LaunchConfiguration('asr_model_size'),
                'device': 'cpu',
                'compute_type': 'int8',
                'language': 'ro_en',  # Enable Romanian/English detection
                'beam_size': 8,  # Higher = more accurate (default was 5)
                'initial_prompt': 'A bilingual conversation in Romanian and English. O conversație bilingvă.',
            }]
        ),
        
        # LLM Node
        Node(
            package='conversational_server',
            executable='llm_node',
            name='llm_node',
            output='screen',
            parameters=[{
                'provider': LaunchConfiguration('llm_provider'),
                'model': LaunchConfiguration('llm_model'),
                'max_tokens': 150,
                'temperature': 0.7,
                'min_chunk_chars': 20,  # Smaller chunks = faster initial response
            }]
        ),
        
        # TTS Node
        Node(
            package='conversational_server',
            executable='tts_node',
            name='tts_node',
            output='screen',
            parameters=[{
                'voice_en': 'en-GB-RyanNeural',  # British male voice (Ryan)
                'voice_ro': 'ro-RO-EmilNeural',
                'buffer_size': 1,  # Start playback immediately (was 2)
            }]
        ),

        # Robot command extraction from transcription
        Node(
            package='conversational_server',
            executable='robot_command_node',
            name='robot_command_node',
            output='screen',
            parameters=[{
                'enabled': True,
                'min_asr_confidence': 0.50,
                'require_session_active': True,
                'stop_ends_session': True,
            }]
        ),
        
        # ========== CLIENT NODES ==========
        
        # Audio Capture (microphone)
        Node(
            package='conversational_client',
            executable='audio_capture_node',
            name='audio_capture_node',
            output='screen',
            parameters=[{
                'device_index': -1,  # Auto-detect (use OS default/PulseAudio)
            }]
        ),
        
        # VAD (Voice Activity Detection)
        Node(
            package='conversational_client',
            executable='vad_node',
            name='vad_node',
            output='screen',
            parameters=[{
                'wake_word_enabled': True,   # Gate audio until wake word
                'session_timeout': 30.0,     # Reset to standby after 30s silence (was 8s)
            }]
        ),
        
        # Audio Playback (speaker)
        Node(
            package='conversational_client',
            executable='audio_playback_node',
            name='audio_playback_node',
            output='screen',
        ),
        
        # Barge-in (interrupt TTS when the user speaks)
        Node(
            package='conversational_client',
            executable='barge_in_node',
            name='barge_in_node',
            output='screen',
            parameters=[{
                # PyTorch Stop Keyword Detector (runs ONLY when TTS is speaking)
                'stop_model_path': stop_model_path,
                'stop_enabled': True,  # ENABLED - per user request
                'stop_prob_threshold': 0.95,
                'stop_logit_margin': 0.3,
                'stop_hits_required': 2,      # 2 consecutive detections
                'stop_frame_samples': 16000,  # Frame = 1s (required by the model)
                'stop_hop_samples': 4000,     # Hop = 0.25s = check every 250ms
            }]
        ),
        
        # Wake Word + Stop Keyword (OpenWakeWord unified)
        # Detects: "hello robot" (wake), "stop robot" (barge_in), "goodbye robot" (stop)
        Node(
            package='conversational_client',
            executable='wake_word_node',
            name='wake_word_node',
            output='screen',
            parameters=[{
                'threshold': 0.5,  # Default threshold
                'cooldown_ms': 1500,
                # Format: "path:kind" - 'wake' to activate, 'barge_in' to stop TTS, 'stop' to end session
                'custom_models': ','.join([
                    f'{hello_model_path}:wake',
                    f'{stop_model_path_oww}:barge_in',
                    f'{goodbye_model_path}:stop',
                ]),
                # Per-model thresholds
                'model_thresholds': 'hello_robot:0.30,stop_robot:0.25,goodbye_robot:0.40',
            }]
        ),
        
        # Stop Keyword Node (DISABLED - using OpenWakeWord in wake_word_node)
        # Node(
        #     package='conversational_client',
        #     executable='stop_keyword_node',
        #     name='stop_keyword_node',
        #     output='screen',
        #     parameters=[{
        #         'model_path': os.path.join(voices_dir, 'stop_keyword.onnx'),
        #         'enabled': False,
        #     }]
        # ),
    ])
