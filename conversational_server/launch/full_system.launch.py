"""
Minimalist Launch file for the full system (server + client).
Relies on YAML configuration profiles for most parameters.
"""
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from ament_index_python.packages import get_package_share_directory
import os
from pathlib import Path


def _find_workspace_root():
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


def launch_setup(context, *args, **kwargs):
    # Retrieve configuration values
    config_name = LaunchConfiguration('config').perform(context)
    conversation_backend = LaunchConfiguration('conversation_backend').perform(context)
    asr_model_size = LaunchConfiguration('asr_model_size').perform(context)
    audio_device_index = LaunchConfiguration('audio_device_index').perform(context)
    stop_enabled = LaunchConfiguration('stop_enabled').perform(context)
    
    client_share = get_package_share_directory('conversational_client')
    server_share = get_package_share_directory('conversational_server')
    models_dir = os.path.join(client_share, 'models')
    workspace_root = _find_workspace_root()
    voices_dir = os.path.join(str(workspace_root) if workspace_root else os.getcwd(), 'voices')
    
    stop_model_path = os.path.join(voices_dir, 'stop_keyword.onnx')
    if not os.path.exists(stop_model_path):
        stop_model_path = os.path.join(models_dir, 'stop_keyword.onnx')
    
    enrollment_dir = os.path.join(voices_dir, 'enrollment')
    hello_model_path = os.path.join(models_dir, 'hello_robot.onnx')
    goodbye_model_path = os.path.join(models_dir, 'goodbye_robot.onnx')

    config_file_path = os.path.join(server_share, 'config', f'params_{config_name}.yaml')

    # ASR parameters override if specified
    asr_params = [config_file_path]
    if asr_model_size.strip() != '':
        asr_params.append({'model_size': asr_model_size})

    # Audio capture parameters override if specified
    audio_capture_params = [config_file_path]
    if audio_device_index.strip().lower() != 'default':
        audio_capture_params.append({'device_index': int(audio_device_index)})

    # Barge-in parameters override if specified
    barge_in_params = [config_file_path, {'stop_model_path': stop_model_path}]
    if stop_enabled.strip().lower() != 'default':
        barge_in_params.append({'stop_enabled': stop_enabled.lower() == 'true'})

    nodes = [
        # ========== SERVER NODES ==========
        
        Node(
            package='conversational_server',
            executable='backend_manager_node',
            name='backend_manager_node',
            parameters=[config_file_path, {'preferred_backend': conversation_backend}]
        ),
        
        Node(
            package='conversational_server',
            executable='asr_node',
            name='asr_node',
            parameters=asr_params,
            remappings=[('/audio_raw', '/audio_clean')]
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
            condition=IfCondition(PythonExpression(["'", conversation_backend, "' == 'openai_realtime'"])),
            parameters=[config_file_path],
            remappings=[('/audio_raw', '/audio_clean')]
        ),
        
        # ========== CLIENT NODES ==========
        
        Node(
            package='conversational_client',
            executable='audio_capture_node',
            name='audio_capture_node',
            parameters=audio_capture_params
        ),
        
        Node(
            package='conversational_client',
            executable='vad_node',
            name='vad_node',
            parameters=[config_file_path],
            remappings=[('/audio_raw', '/audio_clean')]
        ),

        Node(
            package='conversational_client',
            executable='audio_segment_node',
            name='audio_segment_node',
            parameters=[config_file_path],
            remappings=[('/audio_raw', '/audio_clean')]
        ),
        
        Node(
            package='conversational_client',
            executable='audio_playback_node',
            name='audio_playback_node',
            parameters=[config_file_path]
        ),
        
        Node(
            package='conversational_client',
            executable='barge_in_node',
            name='barge_in_node',
            parameters=barge_in_params,
            remappings=[('/audio_raw', '/audio_clean')]
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
            executable='echo_canceller_node',
            name='echo_canceller_node',
            parameters=[config_file_path]
        ),

        Node(
            package='conversational_client',
            executable='wake_word_node',
            name='wake_word_node',
            parameters=[config_file_path, {
                'custom_models': ','.join([f'{hello_model_path}:wake', f'{goodbye_model_path}:stop']),
            }],
            remappings=[('/audio_raw', '/audio_clean')]
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
    ]

    return nodes


def generate_launch_description():
    return LaunchDescription([
        # ========== LAUNCH ARGUMENTS (CORE OVERRIDES) ==========
        DeclareLaunchArgument('config', default_value='raspberry', description='Profile (raspberry/laptop)'),
        DeclareLaunchArgument('conversation_backend', default_value='legacy', description='Backend (legacy/openai_realtime)'),
        DeclareLaunchArgument('asr_model_size', default_value='', description='ASR model size override (empty to use YAML default)'),
        DeclareLaunchArgument('audio_device_index', default_value='default', description='Audio capture device index (default to YAML default)'),
        DeclareLaunchArgument('stop_enabled', default_value='default', description='PyTorch stop override (default to YAML default)'),
        
        OpaqueFunction(function=launch_setup)
    ])
