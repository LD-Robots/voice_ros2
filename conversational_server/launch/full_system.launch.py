"""
Launch file for the full system (server + client).
Starts all nodes for local testing.
"""
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument, OpaqueFunction, SetEnvironmentVariable
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


def _to_bool(value: str, default: bool) -> bool:
    raw = (value or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _launch_setup(context, *args, **kwargs):
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

    runtime_mode = LaunchConfiguration('runtime_mode').perform(context).strip().lower() or 'offline'
    if runtime_mode not in ('offline', 'hybrid', 'online'):
        runtime_mode = 'offline'

    mode_defaults = {
        'offline': {'asr_model_size': 'small', 'tts_backend': 'pyttsx3', 'enable_llm': False, 'websearch_enabled': False},
        'hybrid': {'asr_model_size': 'small', 'tts_backend': 'pyttsx3', 'enable_llm': True, 'websearch_enabled': True},
        'online': {'asr_model_size': 'medium', 'tts_backend': 'edge', 'enable_llm': True, 'websearch_enabled': True},
    }
    defaults = mode_defaults[runtime_mode]

    asr_override = LaunchConfiguration('asr_model_size').perform(context).strip().lower()
    tts_override = LaunchConfiguration('tts_backend').perform(context).strip().lower()
    llm_override = LaunchConfiguration('enable_llm').perform(context).strip().lower()
    llm_provider = LaunchConfiguration('llm_provider').perform(context).strip() or 'groq'
    llm_model = LaunchConfiguration('llm_model').perform(context).strip() or 'llama-3.1-8b-instant'
    ros_localhost_only = LaunchConfiguration('ros_localhost_only').perform(context).strip() or '1'

    asr_model_size = defaults['asr_model_size'] if asr_override in ('', 'auto') else asr_override
    tts_backend = defaults['tts_backend'] if tts_override in ('', 'auto') else tts_override
    enable_llm = defaults['enable_llm'] if llm_override in ('', 'auto') else _to_bool(llm_override, defaults['enable_llm'])

    actions = [
        SetEnvironmentVariable('ROS_LOCALHOST_ONLY', ros_localhost_only),
        Node(
            package='conversational_server',
            executable='asr_node',
            name='asr_node',
            output='screen',
            parameters=[{
                'model_size': asr_model_size,
                'model_fallbacks': 'small,base,tiny',
                'device': 'cpu',
                'compute_type': 'int8',
                'language': 'ro_en',
                'beam_size': 8,
                'initial_prompt': 'A bilingual conversation in Romanian and English. O conversație bilingvă.',
            }]
        ),
        Node(
            package='conversational_server',
            executable='tts_node',
            name='tts_node',
            output='screen',
            parameters=[{
                'backend': tts_backend,
                'voice_en': 'en-GB-RyanNeural',
                'voice_ro': 'ro-RO-EmilNeural',
                'buffer_size': 1,
                'edge_auto_fallback': True,
            }]
        ),
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
    ]

    if enable_llm:
        actions.append(
            Node(
                package='conversational_server',
                executable='llm_node',
                name='llm_node',
                output='screen',
                parameters=[{
                    'provider': llm_provider,
                    'model': llm_model,
                    'max_tokens': 150,
                    'temperature': 0.7,
                    'min_chunk_chars': 20,
                    'websearch_enabled': defaults['websearch_enabled'],
                }]
            )
        )

    actions.extend([
        Node(
            package='conversational_client',
            executable='audio_capture_node',
            name='audio_capture_node',
            output='screen',
            parameters=[{
                'device_index': -1,
            }]
        ),
        Node(
            package='conversational_client',
            executable='vad_node',
            name='vad_node',
            output='screen',
            parameters=[{
                'wake_word_enabled': True,
                'session_timeout': 30.0,
            }]
        ),
        Node(
            package='conversational_client',
            executable='audio_playback_node',
            name='audio_playback_node',
            output='screen',
        ),
        Node(
            package='conversational_client',
            executable='barge_in_node',
            name='barge_in_node',
            output='screen',
            parameters=[{
                'stop_model_path': stop_model_path,
                'stop_enabled': True,
                'stop_prob_threshold': 0.95,
                'stop_logit_margin': 0.3,
                'stop_hits_required': 2,
                'stop_frame_samples': 16000,
                'stop_hop_samples': 4000,
            }]
        ),
        Node(
            package='conversational_client',
            executable='wake_word_node',
            name='wake_word_node',
            output='screen',
            parameters=[{
                'threshold': 0.5,
                'cooldown_ms': 1500,
                'custom_models': ','.join([
                    f'{hello_model_path}:wake',
                    f'{stop_model_path_oww}:barge_in',
                    f'{goodbye_model_path}:stop',
                ]),
                'model_thresholds': 'hello_robot:0.30,stop_robot:0.25,goodbye_robot:0.40',
            }]
        ),
    ])
    return actions


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'runtime_mode',
            default_value='offline',
            description='offline/hybrid/online profile for robust deployment'
        ),
        DeclareLaunchArgument(
            'asr_model_size',
            default_value='auto',
            description='Whisper model override (auto/tiny/base/small/medium/large)'
        ),
        DeclareLaunchArgument(
            'tts_backend',
            default_value='auto',
            description='TTS backend override (auto/edge/pyttsx3)'
        ),
        DeclareLaunchArgument(
            'enable_llm',
            default_value='auto',
            description='Enable LLM node (auto/true/false)'
        ),
        DeclareLaunchArgument(
            'ros_localhost_only',
            default_value='1',
            description='Set ROS_LOCALHOST_ONLY (1 for single-machine, 0 for LAN)'
        ),
        DeclareLaunchArgument(
            'llm_provider',
            default_value='groq',
            description='LLM provider (groq)'
        ),
        DeclareLaunchArgument(
            'llm_model',
            default_value='llama-3.1-8b-instant',
            description='LLM model name'
        ),
        OpaqueFunction(function=_launch_setup),
    ])
