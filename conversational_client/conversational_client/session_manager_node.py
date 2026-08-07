#!/usr/bin/env python3
"""
session_manager_node.py - Manages session state and closing on "Goodbye"

This node listens to the transcription and decides when to close the session.
"""
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String
from conversational_interfaces.msg import Transcription

from .session_text_utils import detect_goodbye_keyword, goodbye_tts_command

class SessionManagerNode(Node):
    def __init__(self):
        super().__init__('session_manager_node')
        self.declare_parameter('transcription_topic', 'attended_transcription')
        transcription_topic = str(self.get_parameter('transcription_topic').value)
        self.current_backend = 'legacy'

        # Subscriber to server transcription (to detect goodbye intent in the text)
        self.transcription_sub = self.create_subscription(
            Transcription,
            transcription_topic,
            self.transcription_callback,
            10
        )

        # Track active backend — a speech-to-speech backend says goodbye itself,
        # so we skip the cached TTS sound to avoid a double goodbye.
        self.backend_sub = self.create_subscription(
            String,
            'conversation_backend',
            self._backend_callback,
            10
        )

        # Publisher for session control
        self.session_pub = self.create_publisher(Bool, 'end_session_external', 10)
        self.tts_cmd_pub = self.create_publisher(String, 'tts_command', 10)
        
        self.get_logger().info('✅ Session Manager started. Listening for Goodbye...')

    def _backend_callback(self, msg: String):
        self.current_backend = (msg.data or '').strip() or 'legacy'

    def transcription_callback(self, msg: Transcription):
        """Check if text contains goodbye words."""
        text = (msg.text or '').strip()
        detected_keyword = detect_goodbye_keyword(text)
        if not detected_keyword:
            return

        self.get_logger().info(
            f'👋 Goodbye detected in text: "{detected_keyword}". Closing session.'
        )

        # Play cached goodbye sound only for legacy backend.
        # A speech-to-speech backend responds with its own farewell.
        if self.current_backend == 'legacy':
            tts_cmd = String()
            tts_cmd.data = goodbye_tts_command(msg.language)
            self.tts_cmd_pub.publish(tts_cmd)

        end_msg = Bool()
        end_msg.data = True
        self.session_pub.publish(end_msg)
        return


def main(args=None):
    rclpy.init(args=args)
    node = SessionManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass

if __name__ == '__main__':
    main()
