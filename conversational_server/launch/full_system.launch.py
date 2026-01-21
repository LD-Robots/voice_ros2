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
            default_value='small',
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
        
        # ASR Node
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
            }]
        ),
        
        # TTS Node
        Node(
            package='conversational_server',
            executable='tts_node',
            name='tts_node',
            output='screen',
            parameters=[{
                'voice_en': 'en-IE-EmilyNeural',
                'voice_ro': 'ro-RO-AlinaNeural',
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
                'device_index': 3,  # Device ID 3 - pulse (folosește microfonul selectat în Settings)
            }]
        ),
        
        # VAD (Voice Activity Detection)
        Node(
            package='conversational_client',
            executable='vad_node',
            name='vad_node',
            output='screen',
            parameters=[{
                'wake_word_enabled': False,   # Gate audio until wake word
                'session_timeout': 8.0,      # Reset to standby after 8s silence
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
        
        # Wake Word (detectare "Hey robot")
        Node(
            package='conversational_client',
            executable='wake_word_node',
            name='wake_word_node',
            output='screen',
            parameters=[{
                'threshold': 0.5,
                'custom_models': os.path.expanduser('~/ros2_ws/src/voice_ros2/conversational_client/models/hello_robot.onnx:wake'),
            }]
        ),
        
        # Stop Keyword (detectare "stop" în timpul TTS)
        Node(
            package='conversational_client',
            executable='stop_keyword_node',
            name='stop_keyword_node',
            output='screen',
            parameters=[{
                'model_path': os.path.expanduser('~/ros2_ws/src/voice_ros2/voices/stop_keyword.onnx'),
                'enabled': False,  # Disabled - .onnx.data file missing
            }]
        ),
    ])
