"""
Launch file for the server-side pipeline.
Starts ASR, LLM, and TTS nodes.
"""
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument, OpaqueFunction, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration


def _to_bool(value: str, default: bool) -> bool:
    raw = (value or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _launch_setup(context, *args, **kwargs):
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
                'edge_auto_fallback': True,
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
                    'websearch_enabled': defaults['websearch_enabled'],
                }]
            )
        )

    actions.append(
        Node(
            package='conversational_server',
            executable='robot_command_node',
            name='robot_command_node',
            output='screen',
            parameters=[{
                'enabled': True,
                'min_asr_confidence': 0.50,
                'require_session_active': False,
                'stop_ends_session': True,
            }]
        )
    )
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
