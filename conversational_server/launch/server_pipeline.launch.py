"""
Launch file for the server-side pipeline.
Starts ASR, LLM, and TTS nodes.
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction, SetEnvironmentVariable
from launch.conditions import IfCondition
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch.substitutions import PythonExpression
from launch_ros.actions import Node, PushRosNamespace


def generate_launch_description():
    return LaunchDescription([
        # Declare arguments
        # ROS_DOMAIN_ID isolates this system on the DDS network. Defaults to 11,
        # respects an already-exported ROS_DOMAIN_ID, overridable with ros_domain_id:=<N>.
        DeclareLaunchArgument(
            'ros_domain_id',
            default_value=EnvironmentVariable('ROS_DOMAIN_ID', default_value='11'),
            description='DDS domain id shared by all nodes (default 11)'
        ),
        SetEnvironmentVariable('ROS_DOMAIN_ID', LaunchConfiguration('ros_domain_id')),

        DeclareLaunchArgument(
            'conversation_backend',
            default_value='gemini_live',
            description='Conversation backend (legacy/gemini_live)'
        ),
        DeclareLaunchArgument(
            'asr_model_size',
            default_value='small',
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
            'capture_during_playback',
            default_value='true',
            description='Stream microphone audio to Gemini while robot playback is active'
        ),
        DeclareLaunchArgument(
            'vad_threshold',
            default_value='0.82',
            description='Sensitivity threshold for VAD (higher means less sensitive to noise)'
        ),
        DeclareLaunchArgument('namespace', default_value='voice', description='ROS namespace for all nodes (default voice)'),

        # Every node runs under `namespace` (default /voice); node topic names are relative.
        GroupAction([
            PushRosNamespace(LaunchConfiguration('namespace')),

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
                'model_size': LaunchConfiguration('asr_model_size'),
                'device': 'cpu',
                'compute_type': 'int8',
                'language': 'ro_en',  # Force EN/RO detection only
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
            }]
        ),
        
        # TTS Node
        Node(
            package='conversational_server',
            executable='tts_node',
            name='tts_node',
            output='screen',
            parameters=[{
                'voice_en': 'en-GB-RyanNeural',
                'voice_ro': 'ro-RO-EmilNeural',
            }]
        ),



        Node(
            package='conversational_server',
            executable='gemini_live_node',
            name='gemini_live_node',
            output='screen',
            condition=IfCondition(PythonExpression(["'", LaunchConfiguration('conversation_backend'), "' == 'gemini_live'"])),
            parameters=[{
                'model': 'gemini-2.5-flash-native-audio-latest',
                'voice': 'Kore',
                'capture_during_playback': LaunchConfiguration('capture_during_playback'),
                'vad_threshold': LaunchConfiguration('vad_threshold'),
            }]
        ),
        ]),  # end GroupAction(namespace)
    ])
