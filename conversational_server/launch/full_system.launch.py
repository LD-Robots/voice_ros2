"""
Minimalist Launch file for the full system (server + client).
Relies on YAML configuration profiles for most parameters.
"""
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
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


def generate_launch_description():
    # ========== SHARED CONTEXT ==========
    realtime_backend = PythonExpression(["'", LaunchConfiguration('conversation_backend'), "' == 'openai_realtime'"])
    
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

    # ========== CONFIGURATION SELECTION ==========
    config_name = LaunchConfiguration('config')
    config_file_path = [server_share, '/config/params_', config_name, '.yaml']

    return LaunchDescription([
        # ========== LAUNCH ARGUMENTS (CORE OVERRIDES) ==========
        DeclareLaunchArgument('config', default_value='raspberry', description='Profile (raspberry/laptop)'),
        DeclareLaunchArgument('conversation_backend', default_value='mistral_realtime', description='Backend (legacy/mistral_realtime/openai_realtime)'),
        DeclareLaunchArgument('asr_model_size', default_value='medium', description='ASR model size override'),
        DeclareLaunchArgument('audio_device_index', default_value='-1', description='Audio device override'),
        DeclareLaunchArgument('stop_enabled', default_value='true', description='PyTorch stop override'),
        
        # ========== SERVER NODES ==========
        
        Node(
            package='conversational_server',
            executable='backend_manager_node',
            name='backend_manager_node',
            parameters=[config_file_path, {'preferred_backend': LaunchConfiguration('conversation_backend')}]
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
            parameters=[config_file_path]
        ),
        
        # ========== CLIENT NODES ==========
        
        Node(
            package='conversational_client',
            executable='audio_capture_node',
            name='audio_capture_node',
            parameters=[config_file_path, {'device_index': LaunchConfiguration('audio_device_index')}]
        ),
        
        Node(
            package='conversational_client',
            executable='vad_node',
            name='vad_node',
            parameters=[config_file_path]
        ),

        Node(
            package='conversational_client',
            executable='audio_segment_node',
            name='audio_segment_node',
            parameters=[config_file_path]
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
            executable='wake_word_node',
            name='wake_word_node',
            parameters=[config_file_path, {
                'custom_models': ','.join([f'{hello_model_path}:wake', f'{goodbye_model_path}:stop']),
            }]
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
    ])
