"""
Launch file for client-side nodes.
Starts all client nodes for voice conversation.
"""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from pathlib import Path


def _find_workspace_root():
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


def generate_launch_description():
    pkg_share = get_package_share_directory('conversational_client')
    server_share = get_package_share_directory('conversational_server')
    models_dir = os.path.join(pkg_share, 'models')
    workspace_root = _find_workspace_root()
    voices_dir = os.path.join(
        str(workspace_root) if workspace_root else os.getcwd(),
        'voices'
    )
    
    # Build the string for custom_models
    # Format: path:kind
    hello_path = os.path.join(models_dir, 'hello_robot.onnx')
    stop_path = os.path.join(models_dir, 'stop_robot.onnx')  # test the original model
    goodbye_path = os.path.join(models_dir, 'goodbye_robot.onnx')
    
    # Define models: hello=wake, stop_robot_oww=barge_in (stop TTS only), goodbye=stop (bye bye)
    custom_models = f"{hello_path}:wake,{stop_path}:barge_in,{goodbye_path}:stop"
    
    model_thresholds = "hello_robot:0.10,stop_robot:0.70,goodbye_robot:0.50"
    stop_keyword_path = os.path.join(voices_dir, 'stop_keyword.onnx')
    if not os.path.exists(stop_keyword_path):
        stop_keyword_path = os.path.join(models_dir, 'stop_keyword.onnx')
    enrollment_dir = os.path.join(voices_dir, 'enrollment')

    # YAML configuration profile resolution
    config_name = LaunchConfiguration('config')
    config_file_path = [server_share, '/config/params_', config_name, '.yaml']

    # Debug recording path
    ws_root_str = str(workspace_root) if workspace_root else str(Path.home())
    debug_mic_wav = os.path.join(ws_root_str, 'gemini_debug_mic.wav')

    return LaunchDescription([
        DeclareLaunchArgument(
            'config',
            default_value='raspberry',
            description='Profile (raspberry/laptop)'
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
            'allow_known_speaker_switch_without_address',
            default_value='true',
            description='Allow a recognized known speaker to take over the conversation without explicitly saying robot'
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
            'vad_min_silence_frames',
            default_value='14',
            description='Consecutive non-speech audio frames required before local VAD ends the user turn'
        ),
        DeclareLaunchArgument(
            'stop_keyword_prob_threshold',
            default_value='0.80',
            description='Minimum stop-keyword probability required to interrupt TTS'
        ),
        DeclareLaunchArgument(
            'stop_keyword_logit_margin',
            default_value='0.5',
            description='Minimum stop-vs-other logit margin required to interrupt TTS'
        ),
        DeclareLaunchArgument(
            'stop_keyword_hits_required',
            default_value='1',
            description='Consecutive stop-keyword detections required before interrupting TTS'
        ),
        DeclareLaunchArgument(
            'stop_keyword_requires_voice_signature',
            default_value='false',
            description='Require the microphone audio to look like real human speech before accepting a stop-keyword hit'
        ),
        DeclareLaunchArgument(
            'barge_in_voice_enabled',
            default_value='true',
            description='Enable voice-based barge-in'
        ),
        DeclareLaunchArgument(
            'barge_in_stop_enabled',
            default_value='true',
            description='Enable stop-keyword based barge-in'
        ),
        
        # Audio Capture (microphone)
        Node(
            package='conversational_client',
            executable='audio_capture_node',
            name='audio_capture_node',
            output='screen',
            parameters=[config_file_path, {
                'debug_wav_path': debug_mic_wav,
            }]
        ),
        
        # Wake Word (detect "hello robot") + Stop Keyword ("stop")
        Node(
            package='conversational_client',
            executable='wake_word_node',
            name='wake_word_node',
            output='screen',
            parameters=[config_file_path, {
                'custom_models': custom_models,
            }],
            arguments=['--ros-args', '--log-level', 'wake_word_node:=DEBUG']
        ),
        
        # VAD (Voice Activity Detection)
        Node(
            package='conversational_client',
            executable='vad_node',
            name='vad_node',
            output='screen',
            parameters=[config_file_path]
        ),
        
        # Audio Segment (buffers audio and sends complete segment)
        Node(
            package='conversational_client',
            executable='audio_segment_node',
            name='audio_segment_node',
            output='screen',
            arguments=['--ros-args', '--log-level', 'audio_segment_node:=DEBUG'],
            parameters=[config_file_path]
        ),
        
        # Barge-in (detectare voce + PyTorch stop keyword)
        Node(
            package='conversational_client',
            executable='barge_in_node',
            name='barge_in_node',
            output='screen',
            arguments=['--ros-args', '--log-level', 'barge_in_node:=DEBUG'],
            parameters=[config_file_path, {
                'stop_model_path': stop_keyword_path,
            }]
        ),
        
        # Audio Playback (speaker)
        Node(
            package='conversational_client',
            executable='audio_playback_node',
            name='audio_playback_node',
            output='screen',
            parameters=[config_file_path]
        ),

        # Speaker Identification (who is speaking)
        Node(
            package='conversational_client',
            executable='speaker_id_node',
            name='speaker_id_node',
            output='screen',
            parameters=[config_file_path, {
                'enrollment_dir': enrollment_dir,
            }]
        ),

        Node(
            package='conversational_client',
            executable='attention_manager_node',
            name='attention_manager_node',
            output='screen',
            parameters=[config_file_path]
        ),

        Node(
            package='conversational_client',
            executable='person_memory_store_node',
            name='person_memory_store_node',
            output='screen',
            parameters=[config_file_path]
        ),

        Node(
            package='conversational_client',
            executable='conversation_control_node',
            name='conversation_control_node',
            output='screen',
            parameters=[config_file_path]
        ),
        
        # Session Manager (Goodbye handling)
        Node(
            package='conversational_client',
            executable='session_manager_node',
            name='session_manager_node',
            output='screen',
            parameters=[config_file_path]
        ),

        # Voice Command Intent (raise hands / move / dance)
        Node(
            package='conversational_client',
            executable='voice_command_node',
            name='voice_command_node',
            output='screen',
            parameters=[config_file_path]
        ),

        # Robot Command Executor (bridges voice intents to controllers)
        Node(
            package='conversational_client',
            executable='robot_command_executor_node',
            name='robot_command_executor_node',
            output='screen',
            parameters=[config_file_path]
        ),
    ])
