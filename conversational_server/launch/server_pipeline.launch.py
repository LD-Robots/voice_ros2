"""
Launch file for the server-side pipeline.
Starts ASR, LLM, and TTS nodes.
"""
import os
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch.substitutions import PythonExpression
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    realtime_backend = PythonExpression(["'", LaunchConfiguration('conversation_backend'), "' == 'openai_realtime'"])

    return LaunchDescription([
        # Declare arguments
        DeclareLaunchArgument(
            'conversation_backend',
            default_value='legacy',
            description='Conversation backend (legacy/openai_realtime)'
        ),
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
        DeclareLaunchArgument(
            'realtime_model',
            default_value='gpt-realtime-mini',
            description='OpenAI Realtime model name'
        ),
        DeclareLaunchArgument(
            'realtime_voice',
            default_value='cedar',
            description='OpenAI Realtime voice'
        ),
        DeclareLaunchArgument(
            'realtime_capture_during_playback',
            default_value='false',
            description='Stream microphone audio to OpenAI while robot playback is active'
        ),
        DeclareLaunchArgument(
            'realtime_vad_silence_duration_ms',
            default_value='1000',
            description='Silence duration before OpenAI Realtime finalizes a user turn'
        ),
        DeclareLaunchArgument(
            'realtime_response_create_delay_ms',
            default_value='450',
            description='Extra local wait before creating a Realtime response after transcript acceptance'
        ),
        
        # ASR Node
        Node(
            package='conversational_server',
            executable='backend_manager_node',
            name='backend_manager_node',
            output='screen',
            parameters=[{
                'preferred_backend': LaunchConfiguration('conversation_backend'),
                'fallback_backend': 'legacy',
                'offline_timeout_s': 6.0,
                'auto_return_to_preferred': True,
            }]
        ),

        Node(
            package='conversational_server',
            executable='asr_node',
            name='asr_node',
            output='screen',
            parameters=[{
                'model_size': LaunchConfiguration('asr_model_size'),
                'device': 'cpu',
                'compute_type': 'int8',
                'language': 'ro_en',  # Force EN/RO detection only
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
                'voice_en': 'en-GB-RyanNeural',
                'voice_ro': 'ro-RO-EmilNeural',
                'piper_model_en': os.path.join(
                    get_package_share_directory('conversational_server'),
                    'models', 'piper', 'en_US-amy-medium.onnx'),
                'piper_model_ro': os.path.join(
                    get_package_share_directory('conversational_server'),
                    'models', 'piper', 'ro_RO-mihai-medium.onnx'),
            }]
        ),

        Node(
            package='conversational_server',
            executable='openai_realtime_node',
            name='openai_realtime_node',
            output='screen',
            condition=IfCondition(realtime_backend),
            parameters=[{
                'model': LaunchConfiguration('realtime_model'),
                'voice': LaunchConfiguration('realtime_voice'),
                'capture_during_playback': LaunchConfiguration('realtime_capture_during_playback'),
                'vad_threshold': 0.65,
                'vad_prefix_padding_ms': 400,
                'vad_silence_duration_ms': LaunchConfiguration('realtime_vad_silence_duration_ms'),
                'response_create_delay_ms': LaunchConfiguration('realtime_response_create_delay_ms'),
                'short_transcript_dedupe_window_s': 4.0,
            }]
        ),
    ])
