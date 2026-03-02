#!/usr/bin/env python3
"""
Conversation Control Node.

Detects short conversational control phrases such as stop / continue / repeat / hold on,
publishes local playback stop when needed, and emits structured control events.
"""
import json
import time

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription
from std_msgs.msg import Bool, String

from .conversation_utils import (
    detect_control_action,
    has_direct_robot_address,
    is_reengagement_phrase,
    normalize_text,
)


class ConversationControlNode(Node):
    def __init__(self):
        super().__init__('conversation_control_node')

        self.current_speaker = 'Unknown'
        self.conversation_paused = False

        self.transcription_sub = self.create_subscription(
            Transcription,
            '/attended_transcription',
            self._transcription_callback,
            10,
        )
        self.speaker_sub = self.create_subscription(String, '/speaker_id', self._speaker_callback, 10)
        self.pause_sub = self.create_subscription(Bool, '/conversation_pause', self._pause_callback, 10)

        self.stop_pub = self.create_publisher(Bool, '/tts_stop', 10)
        self.control_pub = self.create_publisher(String, '/conversation_control', 10)
        self.pause_pub = self.create_publisher(Bool, '/conversation_pause', 10)

        self.get_logger().info('Conversation Control Node started')

    def _speaker_callback(self, msg: String):
        self.current_speaker = msg.data.strip() or 'Unknown'

    def _pause_callback(self, msg: Bool):
        self.conversation_paused = bool(msg.data)

    def _transcription_callback(self, msg: Transcription):
        text = (msg.text or '').strip()
        normalized = normalize_text(text)
        action = detect_control_action(normalized)
        direct_address = has_direct_robot_address(normalized)
        reengagement = is_reengagement_phrase(normalized)
        if not action:
            if self.conversation_paused and (direct_address or reengagement):
                pause_msg = Bool()
                pause_msg.data = False
                self.pause_pub.publish(pause_msg)
                self.get_logger().info(
                    f'Conversation resumed from paused state by speaker={self.current_speaker}'
                )
            return

        if action in ('stop', 'hold_on'):
            stop_msg = Bool()
            stop_msg.data = True
            self.stop_pub.publish(stop_msg)

        if action == 'hold_on':
            pause_msg = Bool()
            pause_msg.data = True
            self.pause_pub.publish(pause_msg)
        elif action in ('continue', 'repeat') and self.conversation_paused:
            pause_msg = Bool()
            pause_msg.data = False
            self.pause_pub.publish(pause_msg)

        payload = {
            'action': action,
            'speaker': self.current_speaker,
            'text': text,
            'language': msg.language or '',
            'timestamp': time.time(),
        }
        out = String()
        out.data = json.dumps(payload, separators=(',', ':'))
        self.control_pub.publish(out)
        self.get_logger().info(f'Conversation control action={action} speaker={self.current_speaker}')


def main(args=None):
    rclpy.init(args=args)
    node = ConversationControlNode()
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
