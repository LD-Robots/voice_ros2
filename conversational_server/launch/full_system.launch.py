"""
Minimalist Launch file for the full system (server + client).
Relies on YAML configuration profiles for most parameters.
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction, SetEnvironmentVariable
from launch.conditions import IfCondition
from launch.substitutions import EnvironmentVariable, LaunchConfiguration, PythonExpression
from launch_ros.actions import Node, PushRosNamespace
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
    non_gemini_backend = PythonExpression(["'", LaunchConfiguration('conversation_backend'), "' != 'gemini_live'"])
    
    client_share = get_package_share_directory('conversational_client')
    server_share = get_package_share_directory('conversational_server')
    models_dir = os.path.join(client_share, 'models')
    workspace_root = _find_workspace_root()
    ws_root_str = str(workspace_root) if workspace_root else str(Path.home())
    voices_dir = os.path.join(ws_root_str, 'voices')

    # Debug recording paths — resolved at launch time so no user-specific paths in YAML
    debug_mic_wav   = os.path.join(ws_root_str, 'gemini_debug_mic.wav')
    aec_raw_wav     = os.path.join(ws_root_str, 'gemini_aec_raw.wav')
    aec_ref_wav     = os.path.join(ws_root_str, 'gemini_aec_reference.wav')
    aec_clean_wav   = os.path.join(ws_root_str, 'gemini_aec_cleaned.wav')
    
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
        DeclareLaunchArgument('conversation_backend', default_value='gemini_live', description='Backend (legacy/gemini_live)'),
        DeclareLaunchArgument('asr_model_size', default_value='medium', description='ASR model size override'),
        DeclareLaunchArgument('audio_device_index', default_value='-1', description='Audio capture device index (-1 = OS default via Pipewire/Pulse)'),
        DeclareLaunchArgument('stop_enabled', default_value='false', description='PyTorch stop override'),
        DeclareLaunchArgument('namespace', default_value='voice', description='ROS namespace for all nodes (default voice)'),

        # Every node runs under `namespace` (default /voice). Node topic names are
        # relative, so they resolve under this namespace too (e.g. /voice/robot_commands).
        GroupAction([
            PushRosNamespace(LaunchConfiguration('namespace')),

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
            executable='gemini_live_node',
            name='gemini_live_node',
            condition=IfCondition(PythonExpression(["'", LaunchConfiguration('conversation_backend'), "' == 'gemini_live'"])),
            parameters=[config_file_path],
            remappings=[('audio_raw', 'audio_clean')]
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
        
        # barge_in_node is only needed for the legacy backend.
        # When using gemini_live, Gemini handles barge-in natively via its
        # own automaticActivityDetection VAD — the local node would interfere.
        Node(
            package='conversational_client',
            executable='barge_in_node',
            name='barge_in_node',
            condition=IfCondition(non_gemini_backend),
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
            executable='robot_command_gate_node',
            name='robot_command_gate_node',
            parameters=[config_file_path]
        ),
        ]),  # end GroupAction(namespace)
    ])
