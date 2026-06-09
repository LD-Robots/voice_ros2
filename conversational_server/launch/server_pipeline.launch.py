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
            description='Whisper model size when asr_provider:=whisper'
        ),
        DeclareLaunchArgument(
            'asr_provider',
            default_value='elevenlabs',
            description='ASR provider (elevenlabs/whisper)'
        ),
        DeclareLaunchArgument(
            'eleven_stt_model',
            default_value='scribe_v2',
            description='ElevenLabs STT model'
        ),
        DeclareLaunchArgument(
            'llm_provider',
            default_value='openai',
            description='LLM provider (openai)'
        ),
        DeclareLaunchArgument(
            'llm_model',
            default_value='gpt-5.5',
            description='LLM model name'
        ),
        DeclareLaunchArgument(
            'tts_provider',
            default_value='elevenlabs',
            description='TTS provider (elevenlabs/edge)'
        ),
        DeclareLaunchArgument(
            'eleven_tts_model',
            default_value='eleven_v3',
            description='ElevenLabs TTS model'
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
                'provider': LaunchConfiguration('asr_provider'),
                'model_size': LaunchConfiguration('asr_model_size'),
                'device': 'cpu',
                'compute_type': 'int8',
                'language': 'ro_en',  # Force EN/RO detection only
                'eleven_model_id': LaunchConfiguration('eleven_stt_model'),
                'eleven_diarize': True,
                'eleven_diarization_threshold': 0.18,
                'eleven_prefer_raw_pcm': True,
                'eleven_min_diarized_words': 2,
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
                'reasoning_effort': 'low',
                'max_tokens': 150,
                'temperature': 0.7,
                'websearch_enabled': True,
                'websearch_provider': 'brave',
            }]
        ),
        
        # TTS Node
        Node(
            package='conversational_server',
            executable='tts_node',
            name='tts_node',
            output='screen',
            parameters=[{
                'provider': LaunchConfiguration('tts_provider'),
                'eleven_model_id': LaunchConfiguration('eleven_tts_model'),
                'eleven_voice_id_en': 'vBKc2FfBKJfcZNyEt1n6',
                'eleven_voice_id_ro': 'vBKc2FfBKJfcZNyEt1n6',
                'eleven_output_format': 'pcm_16000',
                'eleven_latency_optimization': -1,
                'eleven_stream_pcm_chunks': True,
                'eleven_stream_chunk_ms': 120,
                'eleven_stability': 0.42,
                'eleven_similarity_boost': 0.78,
                'eleven_style': 0.38,
                'eleven_use_speaker_boost': True,
                'fallback_to_edge': True,
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
