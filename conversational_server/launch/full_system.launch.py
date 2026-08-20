"""
Minimalist Launch file for the full system (server + client).
Relies on YAML configuration profiles for most parameters.
"""
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    GroupAction,
    OpaqueFunction,
    SetEnvironmentVariable,
)
from launch.conditions import IfCondition
from launch.substitutions import EnvironmentVariable, LaunchConfiguration, PythonExpression
from launch_ros.actions import Node, PushRosNamespace
from ament_index_python.packages import get_package_share_directory
import os
from pathlib import Path


# The speech-to-speech node and the legacy pipeline are selected by exact string
# match on conversation_backend. A typo would therefore start NEITHER: the
# realtime node is skipped, and tts_node only speaks when the backend is exactly
# 'legacy' — so the robot comes up completely mute with no error anywhere.
# validate_conversation_backend() turns that silent failure into a loud one.
VALID_BACKENDS = ('openai_realtime', 'legacy')


def validate_conversation_backend(context, *args, **kwargs):
    """Fail fast on an unknown conversation_backend instead of booting mute."""
    value = LaunchConfiguration('conversation_backend').perform(context)
    if value not in VALID_BACKENDS:
        raise RuntimeError(
            f"Unknown conversation_backend '{value}'. "
            f"Valid values: {', '.join(VALID_BACKENDS)}. "
            "(The OpenAI speech-to-speech backend is called 'openai_realtime', "
            "not 'openai_live'.)"
        )
    return []


try:
    from conversational_client.workspace_paths import find_workspace_root
except ImportError:  # launch may run before the package is on PYTHONPATH
    def find_workspace_root():
        """Fallback: identify the workspace by the packages it contains."""
        markers = (
            Path('conversational_client') / 'package.xml',
            Path('conversational_server') / 'package.xml',
        )
        override = os.environ.get('VOICE_ROS2_WS', '').strip()
        if override and Path(override).expanduser().is_dir():
            return Path(override).expanduser().resolve()
        for base in (Path(__file__).resolve(), Path.cwd().resolve()):
            for parent in [base] + list(base.parents):
                if all((parent / marker).is_file() for marker in markers):
                    return parent
        return None


def generate_launch_description():
    # ========== SHARED CONTEXT ==========
    backend = LaunchConfiguration('conversation_backend')
    realtime_backend = PythonExpression(["'", backend, "' == 'openai_realtime'"])
    legacy_backend = PythonExpression(["'", backend, "' != 'openai_realtime'"])

    client_share = get_package_share_directory('conversational_client')
    server_share = get_package_share_directory('conversational_server')
    models_dir = os.path.join(client_share, 'models')
    workspace_root = find_workspace_root()
    ws_root_str = str(workspace_root) if workspace_root else str(Path.home())
    voices_dir = os.path.join(ws_root_str, 'voices')

    # Debug recording paths — resolved at launch time so no user-specific paths in YAML
    debug_mic_wav   = os.path.join(ws_root_str, 'openai_debug_mic.wav')
    aec_raw_wav     = os.path.join(ws_root_str, 'openai_aec_raw.wav')
    aec_ref_wav     = os.path.join(ws_root_str, 'openai_aec_reference.wav')
    aec_clean_wav   = os.path.join(ws_root_str, 'openai_aec_cleaned.wav')

    stop_model_path = os.path.join(voices_dir, 'stop_keyword.onnx')
    if not os.path.exists(stop_model_path):
        stop_model_path = os.path.join(models_dir, 'stop_keyword.onnx')

    enrollment_dir = os.path.join(voices_dir, 'enrollment')
    fillers_dir = os.path.join(voices_dir, 'fillers')
    hello_model_path = os.path.join(models_dir, 'hello_robot.onnx')
    goodbye_model_path = os.path.join(models_dir, 'goodbye_robot.onnx')

    # ========== CONFIGURATION SELECTION ==========
    config_name = LaunchConfiguration('config')
    config_file_path = [server_share, '/config/params_', config_name, '.yaml']

    return LaunchDescription([
        # ========== LAUNCH ARGUMENTS (CORE OVERRIDES) ==========
        # ROS_DOMAIN_ID isolates this system on the DDS network. Defaults to 11,
        # but respects an already-exported ROS_DOMAIN_ID and can be overridden with
        # `ros_domain_id:=<N>`. Set before any node so every process shares the domain.
        DeclareLaunchArgument(
            'ros_domain_id',
            default_value=EnvironmentVariable('ROS_DOMAIN_ID', default_value='11'),
            description='DDS domain id shared by all nodes (default 11)'
        ),
        SetEnvironmentVariable('ROS_DOMAIN_ID', LaunchConfiguration('ros_domain_id')),

        DeclareLaunchArgument('config', default_value='raspberry', description='Profile (raspberry/laptop)'),
        DeclareLaunchArgument('conversation_backend', default_value='openai_realtime', description='Backend (legacy/openai_realtime)'),
        OpaqueFunction(function=validate_conversation_backend),
        DeclareLaunchArgument('asr_model_size', default_value='medium', description='ASR model size override'),
        DeclareLaunchArgument('audio_device_index', default_value='-1', description='Audio capture device index (-1 = auto-detect ReSpeaker, else OS default)'),
        DeclareLaunchArgument('stop_enabled', default_value='false', description='PyTorch stop override'),
        DeclareLaunchArgument('namespace', default_value='voice', description='ROS namespace for all nodes (default voice)'),

        # ---------- OpenAI Realtime overrides ----------
        DeclareLaunchArgument('realtime_model', default_value='gpt-realtime-2.1', description='OpenAI Realtime model (gpt-realtime-2.1 / gpt-realtime-2.1-mini)'),
        DeclareLaunchArgument('realtime_voice', default_value='cedar', description='OpenAI Realtime voice'),
        DeclareLaunchArgument('realtime_reasoning_effort', default_value='medium', description='OpenAI Realtime reasoning effort'),
        DeclareLaunchArgument('realtime_capture_during_playback', default_value='true', description='Stream microphone audio to OpenAI while robot playback is active'),
        DeclareLaunchArgument('realtime_web_search_enabled', default_value='true', description='Allow OpenAI Realtime to call web_search'),
        DeclareLaunchArgument('realtime_web_search_model', default_value='gpt-4.1-mini', description='Responses API model used for web_search'),
        DeclareLaunchArgument('realtime_web_search_context_size', default_value='medium', description='OpenAI web-search context size'),
        DeclareLaunchArgument('realtime_vad_silence_duration_ms', default_value='700', description='Silence duration before OpenAI Realtime finalizes a user turn'),
        DeclareLaunchArgument('realtime_response_create_delay_ms', default_value='100', description='Delay before creating a Realtime response'),
        DeclareLaunchArgument('realtime_continued_turn_response_delay_ms', default_value='450', description='Delay before answering after the user resumes speaking'),
        DeclareLaunchArgument('realtime_local_fillers', default_value='false', description='Play locally cached filler audio to mask response latency'),

        # Every node runs under `namespace` (default /voice). ALL topic, action and
        # service names are relative, so everything resolves under this namespace —
        # including the motion-team interfaces (/voice/humanoid_command,
        # /voice/humanoid_motion, /voice/cmd_vel and the behaviour services).
        GroupAction([
            PushRosNamespace(LaunchConfiguration('namespace')),

        # ========== SERVER NODES ==========

        Node(
            package='conversational_server',
            executable='backend_manager_node',
            name='backend_manager_node',
            parameters=[config_file_path, {'preferred_backend': backend}]
        ),

        Node(
            package='conversational_server',
            executable='asr_node',
            name='asr_node',
            parameters=[config_file_path, {'model_size': LaunchConfiguration('asr_model_size')}]
        ),

        Node(
            package='conversational_server',
            executable='llm_node',
            name='llm_node',
            parameters=[config_file_path]
        ),

        Node(
            package='conversational_server',
            executable='tts_node',
            name='tts_node',
            parameters=[config_file_path]
        ),

        Node(
            package='conversational_server',
            executable='openai_realtime_node',
            name='openai_realtime_node',
            condition=IfCondition(realtime_backend),
            parameters=[config_file_path, {
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
                'vad_silence_duration_ms': LaunchConfiguration('realtime_vad_silence_duration_ms'),
                'response_create_delay_ms': LaunchConfiguration('realtime_response_create_delay_ms'),
                'continued_turn_response_delay_ms': LaunchConfiguration(
                    'realtime_continued_turn_response_delay_ms'
                ),
                'enable_local_fillers': LaunchConfiguration('realtime_local_fillers'),
                'fillers_dir': fillers_dir,
            }],
            remappings=[('audio_raw', 'audio_clean')]
        ),

        Node(
            package='conversational_server',
            executable='diarization_assist_node',
            name='diarization_assist_node',
            condition=IfCondition(realtime_backend),
            parameters=[config_file_path, {'enrollment_dir': enrollment_dir}]
        ),

        # ========== CLIENT NODES ==========

        Node(
            package='conversational_client',
            executable='audio_capture_node',
            name='audio_capture_node',
            parameters=[config_file_path, {
                'device_index': LaunchConfiguration('audio_device_index'),
                'debug_wav_path': debug_mic_wav,
            }]
        ),

        Node(
            package='conversational_client',
            executable='vad_node',
            name='vad_node',
            parameters=[config_file_path],
            remappings=[('audio_raw', 'audio_clean')]
        ),

        Node(
            package='conversational_client',
            executable='audio_segment_node',
            name='audio_segment_node',
            parameters=[config_file_path],
            remappings=[('audio_raw', 'audio_clean')]
        ),

        Node(
            package='conversational_client',
            executable='audio_playback_node',
            name='audio_playback_node',
            parameters=[config_file_path]
        ),

        # barge_in_node is only needed for the legacy backend. The realtime
        # backend handles barge-in natively via its own server-side VAD, so the
        # local node would interfere.
        Node(
            package='conversational_client',
            executable='barge_in_node',
            name='barge_in_node',
            condition=IfCondition(legacy_backend),
            parameters=[config_file_path, {
                'stop_model_path': stop_model_path,
                'stop_enabled': LaunchConfiguration('stop_enabled')
            }]
        ),

        Node(
            package='conversational_client',
            executable='speaker_id_node',
            name='speaker_id_node',
            parameters=[config_file_path, {'enrollment_dir': enrollment_dir}]
        ),

        Node(
            package='conversational_client',
            executable='attention_manager_node',
            name='attention_manager_node',
            parameters=[config_file_path]
        ),

        Node(
            package='conversational_client',
            executable='person_memory_store_node',
            name='person_memory_store_node',
            parameters=[config_file_path]
        ),

        Node(
            package='conversational_client',
            executable='conversation_control_node',
            name='conversation_control_node',
            parameters=[config_file_path]
        ),

        Node(
            package='conversational_client',
            executable='acoustic_monitor_node',
            name='acoustic_monitor_node',
            parameters=[config_file_path]
        ),

        Node(
            package='conversational_client',
            executable='echo_canceller_node',
            name='echo_canceller_node',
            parameters=[config_file_path, {
                'raw_wav_path':   aec_raw_wav,
                'ref_wav_path':   aec_ref_wav,
                'clean_wav_path': aec_clean_wav,
            }]
        ),

        Node(
            package='conversational_client',
            executable='wake_word_node',
            name='wake_word_node',
            parameters=[config_file_path, {
                'custom_models': ','.join([f'{hello_model_path}:wake', f'{goodbye_model_path}:stop']),
            }],
            remappings=[('audio_raw', 'audio_clean')]
        ),

        Node(
            package='conversational_client',
            executable='session_manager_node',
            name='session_manager_node',
            parameters=[config_file_path]
        ),

        Node(
            package='conversational_client',
            executable='voice_command_node',
            name='voice_command_node',
            parameters=[config_file_path]
        ),

        Node(
            package='conversational_client',
            executable='robot_command_executor_node',
            name='robot_command_executor_node',
            parameters=[config_file_path]
        ),

        Node(
            package='conversational_client',
            executable='respeaker_doa_node',
            name='respeaker_doa_node',
            parameters=[config_file_path]
        ),
        ]),  # end GroupAction(namespace)
    ])
