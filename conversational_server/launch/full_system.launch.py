"""
Launch file pentru sistemul complet (server + client).
Pornește toate nodurile pentru testare locală.
"""
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
import os


def generate_launch_description():
    return LaunchDescription([
        # ========== ARGUMENTE ==========
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
                'voice_ro': 'ro-RO-AlinaNeural',
                'buffer_size': 1,  # Start playback immediately (was 2)
            }]
        ),
        
        # ========== CLIENT NODES ==========
        
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
        
        # Audio Playback (difuzor)
        Node(
            package='conversational_client',
            executable='audio_playback_node',
            name='audio_playback_node',
            output='screen',
        ),
        
        # Barge-in (întrerupe TTS când vorbește userul)
        Node(
            package='conversational_client',
            executable='barge_in_node',
            name='barge_in_node',
            output='screen',
        ),
        
        # Wake Word + Stop Keyword (OpenWakeWord unified)
        # Detectează: "hello robot" (wake) + "stop" (stop)
        Node(
            package='conversational_client',
            executable='wake_word_node',
            name='wake_word_node',
            output='screen',
            parameters=[{
                'threshold': 0.5,  # Default threshold
                'cooldown_ms': 1500,
                # Format: "path:kind" - 'wake' pentru activare, 'stop' pentru oprire
                'custom_models': ','.join([
                    os.path.expanduser('~/ros2_ws/src/voice_ros2/conversational_client/models/hello_robot.onnx:wake'),
                    os.path.expanduser('~/ros2_ws/src/voice_ros2/conversational_client/models/stop.onnx:stop'),
                ]),
                # Threshold-uri individuale per model
                'model_thresholds': 'hello_robot:0.30,stop:0.5',
            }]
        ),
        
        # Stop Keyword Node (DISABLED - folosim OpenWakeWord în wake_word_node)
        # Node(
        #     package='conversational_client',
        #     executable='stop_keyword_node',
        #     name='stop_keyword_node',
        #     output='screen',
        #     parameters=[{
        #         'model_path': os.path.expanduser('~/ros2_ws/src/voice_ros2/voices/stop_keyword.onnx'),
        #         'enabled': False,
        #     }]
        # ),
    ])
