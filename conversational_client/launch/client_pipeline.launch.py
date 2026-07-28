"""
Launch file for client-side nodes.
Starts all client nodes for voice conversation.
"""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction, SetEnvironmentVariable
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch_ros.actions import Node, PushRosNamespace
from pathlib import Path


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
    pkg_share = get_package_share_directory('conversational_client')
    models_dir = os.path.join(pkg_share, 'models')
    workspace_root = find_workspace_root()
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

    return LaunchDescription([
        # ROS_DOMAIN_ID isolates this system on the DDS network. Defaults to 11,
        # respects an already-exported ROS_DOMAIN_ID, overridable with ros_domain_id:=<N>.
        DeclareLaunchArgument(
            'ros_domain_id',
            default_value=EnvironmentVariable('ROS_DOMAIN_ID', default_value='11'),
            description='DDS domain id shared by all nodes (default 11)'
        ),
        SetEnvironmentVariable('ROS_DOMAIN_ID', LaunchConfiguration('ros_domain_id')),

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
        DeclareLaunchArgument('namespace', default_value='voice', description='ROS namespace for all nodes (default voice)'),

        # Every node runs under `namespace` (default /voice); node topic names are relative.
        GroupAction([
            PushRosNamespace(LaunchConfiguration('namespace')),

        # Audio Capture (microphone)
        Node(
            package='conversational_client',
            executable='audio_capture_node',
            name='audio_capture_node',
            output='screen',
            parameters=[{
                'device_index': 3,  # Explicit PulseAudio to capture the 6 ReSpeaker channels
                'respeaker_mode': True,
                'respeaker_channel': 5, # Processed AEC (Tests showed 5 is better)
                'gain': 3.0,            # Digital gain to avoid clipping
            }]
        ),
        
        # Wake Word (detect "hello robot") + Stop Keyword ("stop")
        Node(
            package='conversational_client',
            executable='wake_word_node',
            name='wake_word_node',
            output='screen',
            parameters=[{
                'threshold': 0.5,  # Default threshold
                'cooldown_ms': 1500,
                'custom_models': custom_models,
                'model_thresholds': model_thresholds,
            }],
            arguments=['--ros-args', '--log-level', 'wake_word_node:=DEBUG']
        ),
        
        # VAD (Voice Activity Detection)
        Node(
            package='conversational_client',
            executable='vad_node',
            name='vad_node',
            output='screen',
            parameters=[{
                'aggressiveness': 2,
                'wake_word_enabled': True,
                'session_timeout': 30.0,
                'min_silence_frames': LaunchConfiguration('vad_min_silence_frames'),
                'capture_during_playback': False, # Disable server-side capture during playback to avoid feedback loop
            }]
        ),
        
        # Audio Segment (buffers audio and sends complete segment)
        Node(
            package='conversational_client',
            executable='audio_segment_node',
            name='audio_segment_node',
            output='screen',
            arguments=['--ros-args', '--log-level', 'audio_segment_node:=DEBUG'],
            parameters=[{
                'min_segment_seconds': 0.5,
                'max_segment_seconds': 30.0,
                'capture_during_playback': False, # Revert to Half-Duplex for stability
            }]
        ),
        
        # Barge-in (detectare voce + PyTorch stop keyword)
        Node(
            package='conversational_client',
            executable='barge_in_node',
            name='barge_in_node',
            output='screen',
            arguments=['--ros-args', '--log-level', 'barge_in_node:=DEBUG'],
            parameters=[{
                # Voice barge-in DISABLED – Gemini Live has its own server-side VAD
                # that handles interruptions natively (sends "interrupted" event).
                # barge_in_node stays active only for the PyTorch stop-keyword detector.
                'voice_enabled': False,
                'min_voice_ms': 400,
                'leak_margin_db': 12.0,
                # PyTorch stop keyword detector
                'stop_enabled': LaunchConfiguration('barge_in_stop_enabled'),
                'stop_model_path': stop_keyword_path,
                'stop_prob_threshold': LaunchConfiguration('stop_keyword_prob_threshold'),
                'stop_logit_margin': LaunchConfiguration('stop_keyword_logit_margin'),
                'stop_hits_required': LaunchConfiguration('stop_keyword_hits_required'),
                'stop_frame_samples': 16000,  # Frame = 1s (imposed by model!)
                'stop_hop_samples': 4000,     # Hop = 0.25s = check every 250ms
                'stop_requires_voice_signature': LaunchConfiguration('stop_keyword_requires_voice_signature'),
            }]
        ),
        
        # Audio Playback (speaker)
        Node(
            package='conversational_client',
            executable='audio_playback_node',
            name='audio_playback_node',
            output='screen',
        ),

        # Acoustic Monitor (Noise / Environment adaptation)
        Node(
            package='conversational_client',
            executable='acoustic_monitor_node',
            name='acoustic_monitor_node',
            output='screen',
        ),

        # Speaker Identification (who is speaking)
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
                'allow_known_speaker_switch_without_address': LaunchConfiguration(
                    'allow_known_speaker_switch_without_address'
                ),
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
        
        # Session Manager (Goodbye handling)
        Node(
            package='conversational_client',
            executable='session_manager_node',
            name='session_manager_node',
            output='screen',
        ),

        # Voice Command Intent (recognizes one of the 5 commands -> /recognized_commands)
        Node(
            package='conversational_client',
            executable='voice_command_node',
            name='voice_command_node',
            output='screen',
            parameters=[{
                'command_topic': 'recognized_commands',
                'min_transcription_confidence': 0.45,
                'default_steps': 1,
                'max_steps': 20,
                'enable_tts_ack': False,
            }]
        ),

        # Robot Command Gate (safety: confidence + "stop" cancel + risky confirmation;
        # forwards approved commands to the motion team on /robot_commands)
        Node(
            package='conversational_client',
            executable='robot_command_gate_node',
            name='robot_command_gate_node',
            output='screen',
            parameters=[{
                'input_topic': 'recognized_commands',
                'command_topic': 'robot_commands',
                'execution_enabled': True,
                'enable_voice_cancel': True,
                'enable_risky_confirmation': True,
                'confirmation_timeout_s': 12.0,
                'risky_steps_threshold': 5,
                'risky_turn_angle_deg': 150.0,
            }]
        ),
        ]),  # end GroupAction(namespace)
    ])
