"""
Launch file for the full system (server + client).
Starts all nodes for local testing.
"""
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch.substitutions import PythonExpression
from ament_index_python.packages import get_package_share_directory
import os
from pathlib import Path


def _find_workspace_root():
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


def generate_launch_description():
    realtime_backend = PythonExpression(["'", LaunchConfiguration('conversation_backend'), "' == 'openai_realtime'"])

    client_share = get_package_share_directory('conversational_client')
    models_dir = os.path.join(client_share, 'models')
    workspace_root = _find_workspace_root()
    voices_dir = os.path.join(
        str(workspace_root) if workspace_root else os.getcwd(),
        'voices'
    )

    stop_model_path = os.path.join(voices_dir, 'stop_keyword.onnx')
    if not os.path.exists(stop_model_path):
        stop_model_path = os.path.join(models_dir, 'stop_keyword.onnx')
    enrollment_dir = os.path.join(voices_dir, 'enrollment')

    hello_model_path = os.path.join(models_dir, 'hello_robot.onnx')
    stop_model_path_oww = os.path.join(models_dir, 'stop_robot.onnx')
    goodbye_model_path = os.path.join(models_dir, 'goodbye_robot.onnx')

    return LaunchDescription([
        # ========== ARGUMENTE ==========
        DeclareLaunchArgument(
            'conversation_backend',
            default_value='legacy',
            description='Conversation backend (legacy/openai_realtime)'
        ),
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
            'speaker_similarity_threshold',
            default_value='0.45',
            description='Minimum similarity score for speaker identification'
        ),
        DeclareLaunchArgument(
            'speaker_similarity_margin',
            default_value='0.12',
            description='Minimum margin between top-1 and top-2 speaker matches'
        ),
        DeclareLaunchArgument(
            'enrollment_reuse_threshold',
            default_value='0.58',
            description='Minimum similarity score required to reuse an existing speaker label during automatic enrollment'
        ),
        DeclareLaunchArgument(
            'enrollment_reuse_margin',
            default_value='0.16',
            description='Minimum top-1 vs top-2 margin required to reuse an existing speaker label during automatic enrollment'
        ),
        DeclareLaunchArgument(
            'speaker_switch_hits_required',
            default_value='2',
            description='Consecutive positive speaker-ID hits required before switching from one known speaker to another'
        ),
        DeclareLaunchArgument(
            'auto_enroll_unknown_speakers',
            default_value='true',
            description='Allow automatic enrollment when a new person explicitly introduces their name'
        ),
        DeclareLaunchArgument(
            'min_enrollment_segment_seconds',
            default_value='1.2',
            description='Minimum audio length used for automatic enrollment'
        ),
        DeclareLaunchArgument(
            'realtime_local_response_gating',
            default_value='true',
            description='Only create Realtime responses after local pause/attention filtering accepts the transcript'
        ),
        DeclareLaunchArgument(
            'language_switch_hits_required',
            default_value='2',
            description='Consecutive language detections required before switching the active conversation language without an explicit request'
        ),
        DeclareLaunchArgument(
            'realtime_vad_silence_duration_ms',
            default_value='800',
            description='Silence duration before OpenAI Realtime finalizes a user turn'
        ),
        DeclareLaunchArgument(
            'realtime_response_create_delay_ms',
            default_value='100',
            description='Extra local wait before creating a Realtime response after transcript acceptance'
        ),
        DeclareLaunchArgument(
            'realtime_continued_turn_response_delay_ms',
            default_value='700',
            description='Delay before answering a transcript that arrived after the user resumed speaking'
        ),
        DeclareLaunchArgument(
            'vad_min_silence_frames',
            default_value='14',
            description='Consecutive non-speech audio frames required before local VAD ends the user turn'
        ),
        DeclareLaunchArgument(
            'stop_keyword_prob_threshold',
            default_value='0.98',
            description='Minimum stop-keyword probability required to interrupt TTS'
        ),
        DeclareLaunchArgument(
            'stop_keyword_logit_margin',
            default_value='0.6',
            description='Minimum stop-vs-other logit margin required to interrupt TTS'
        ),
        DeclareLaunchArgument(
            'stop_keyword_hits_required',
            default_value='3',
            description='Consecutive stop-keyword detections required before interrupting TTS'
        ),
        DeclareLaunchArgument(
            'stop_keyword_requires_voice_signature',
            default_value='true',
            description='Require the microphone audio to look like real human speech before accepting a stop-keyword hit'
        ),
        
        # ========== SERVER NODES ==========
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
                'local_response_gating': LaunchConfiguration('realtime_local_response_gating'),
                'speaker_switch_hits_required': LaunchConfiguration('speaker_switch_hits_required'),
                'language_switch_hits_required': LaunchConfiguration('language_switch_hits_required'),
                'vad_threshold': 0.82,
                'vad_prefix_padding_ms': 400,
                'vad_silence_duration_ms': LaunchConfiguration('realtime_vad_silence_duration_ms'),
                'response_create_delay_ms': LaunchConfiguration('realtime_response_create_delay_ms'),
                'continued_turn_response_delay_ms': LaunchConfiguration(
                    'realtime_continued_turn_response_delay_ms'
                ),
                'short_transcript_dedupe_window_s': 4.0,
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
                'min_silence_frames': LaunchConfiguration('vad_min_silence_frames'),
            }]
        ),

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
        
        # Audio Playback (difuzor)
        Node(
            package='conversational_client',
            executable='audio_playback_node',
            name='audio_playback_node',
            output='screen',
        ),
        
        # Barge-in (interrupts TTS when the user speaks)
        Node(
            package='conversational_client',
            executable='barge_in_node',
            name='barge_in_node',
            output='screen',
            parameters=[{
                # PyTorch Stop Keyword Detector (rulează DOAR când TTS vorbește)
                'stop_model_path': stop_model_path,
                'stop_enabled': True,  # ACTIVAT - la cererea userului
                'stop_prob_threshold': LaunchConfiguration('stop_keyword_prob_threshold'),
                'stop_logit_margin': LaunchConfiguration('stop_keyword_logit_margin'),
                'stop_hits_required': LaunchConfiguration('stop_keyword_hits_required'),
                'stop_frame_samples': 16000,  # Frame = 1s (impus de model!)
                'stop_hop_samples': 4000,     # Hop = 0.25s = verificare la fiecare 250ms
                'stop_requires_voice_signature': LaunchConfiguration('stop_keyword_requires_voice_signature'),
            }]
        ),

        Node(
            package='conversational_client',
            executable='speaker_id_node',
            name='speaker_id_node',
            output='screen',
            parameters=[{
                'enrollment_dir': enrollment_dir,
                'similarity_threshold': LaunchConfiguration('speaker_similarity_threshold'),
                'similarity_margin': LaunchConfiguration('speaker_similarity_margin'),
                'enrollment_reuse_threshold': LaunchConfiguration('enrollment_reuse_threshold'),
                'enrollment_reuse_margin': LaunchConfiguration('enrollment_reuse_margin'),
            }]
        ),

        Node(
            package='conversational_client',
            executable='attention_manager_node',
            name='attention_manager_node',
            output='screen',
            parameters=[{
                'speaker_switch_hits_required': LaunchConfiguration('speaker_switch_hits_required'),
            }]
        ),

        Node(
            package='conversational_client',
            executable='person_memory_store_node',
            name='person_memory_store_node',
            output='screen',
            parameters=[{
                'auto_enroll_unknown_speakers': LaunchConfiguration('auto_enroll_unknown_speakers'),
                'min_enrollment_segment_seconds': LaunchConfiguration('min_enrollment_segment_seconds'),
                'speaker_switch_hits_required': LaunchConfiguration('speaker_switch_hits_required'),
            }]
        ),

        Node(
            package='conversational_client',
            executable='conversation_control_node',
            name='conversation_control_node',
            output='screen',
            parameters=[{
                'speaker_switch_hits_required': LaunchConfiguration('speaker_switch_hits_required'),
            }]
        ),

        # Wake Word + Stop Keyword (OpenWakeWord unified)
        # Detectează: "hello robot" (wake), "stop robot" (barge_in), "goodbye robot" (stop)
        Node(
            package='conversational_client',
            executable='wake_word_node',
            name='wake_word_node',
            output='screen',
            parameters=[{
                'threshold': 0.5,  # Default threshold
                'cooldown_ms': 1500,
                # Format: "path:kind" - \'wake\' for activation, \'barge_in\' for stopping TTS, \'stop\' for ending session
                'custom_models': ','.join([
                    f'{hello_model_path}:wake',
                    f'{stop_model_path_oww}:barge_in',
                    f'{goodbye_model_path}:stop',
                ]),
                # Threshold-uri individuale per model
                'model_thresholds': 'hello_robot:0.30,stop_robot:0.70,goodbye_robot:0.40',
            }]
        ),

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

        # Stop Keyword Node (DISABLED - using OpenWakeWord in wake_word_node)
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
