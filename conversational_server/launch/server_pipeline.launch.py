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
            default_value='gpt-realtime-2',
            description='OpenAI Realtime model name'
        ),
        DeclareLaunchArgument(
            'realtime_reasoning_effort',
            default_value='medium',
            description='OpenAI Realtime reasoning effort (minimal/low/medium/high/xhigh)'
        ),
        DeclareLaunchArgument(
            'realtime_voice',
            default_value='cedar',
            description='OpenAI Realtime voice'
        ),
        DeclareLaunchArgument(
            'realtime_capture_during_playback',
            default_value='true',
            description='Stream microphone audio to OpenAI while robot playback is active'
        ),
        DeclareLaunchArgument(
            'realtime_web_search_enabled',
            default_value='true',
            description='Allow OpenAI Realtime to call a web-search tool via the Responses API'
        ),
        DeclareLaunchArgument(
            'realtime_web_search_model',
            default_value='gpt-4.1-mini',
            description='Responses API model used to execute web search tool calls'
        ),
        DeclareLaunchArgument(
            'realtime_web_search_context_size',
            default_value='medium',
            description='OpenAI web-search context size (low/medium/high)'
        ),
        DeclareLaunchArgument(
            'realtime_vad_threshold',
            default_value='0.82',
            description='Sensitivity threshold for OpenAI VAD (higher means less sensitive to noise)'
        ),
        DeclareLaunchArgument(
            'realtime_vad_silence_duration_ms',
            default_value='550',
            description='Silence duration before OpenAI Realtime finalizes a user turn'
        ),
        DeclareLaunchArgument(
            'realtime_response_create_delay_ms',
            default_value='100',
            description='Extra local wait before creating a Realtime response after transcript acceptance'
        ),
        DeclareLaunchArgument(
            'realtime_continued_turn_response_delay_ms',
            default_value='450',
            description='Delay before answering a transcript that arrived after the user resumed speaking'
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
                'reasoning_enabled': PythonExpression([
                    "'", LaunchConfiguration('realtime_model'), "'.startswith('gpt-realtime-2')"
                ]),
                'reasoning_effort': LaunchConfiguration('realtime_reasoning_effort'),
                'wait_for_user_tool_enabled': True,
                'capture_during_playback': LaunchConfiguration('realtime_capture_during_playback'),
                'web_search_enabled': LaunchConfiguration('realtime_web_search_enabled'),
                'web_search_model': LaunchConfiguration('realtime_web_search_model'),
                'web_search_context_size': LaunchConfiguration('realtime_web_search_context_size'),
                'vad_threshold': LaunchConfiguration('realtime_vad_threshold'),
                'vad_prefix_padding_ms': 400,
                'vad_silence_duration_ms': LaunchConfiguration('realtime_vad_silence_duration_ms'),
                'response_create_delay_ms': LaunchConfiguration('realtime_response_create_delay_ms'),
                'continued_turn_response_delay_ms': LaunchConfiguration(
                    'realtime_continued_turn_response_delay_ms'
                ),
                'short_transcript_dedupe_window_s': 4.0,
            }]
        ),
    ])
