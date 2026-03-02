#!/usr/bin/env python3
"""
Attention Manager Node.

Filters transcription events so the robot responds primarily to the focused speaker
and ignores likely side conversations unless the robot is directly addressed.
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
    is_robot_directive,
    normalize_text,
)


class AttentionManagerNode(Node):
    def __init__(self):
        super().__init__('attention_manager_node')

        self.declare_parameter('focus_timeout_s', 45.0)
        self.declare_parameter('unknown_speaker_grace_s', 20.0)

        self.focus_timeout_s = float(self.get_parameter('focus_timeout_s').value)
        self.unknown_speaker_grace_s = float(self.get_parameter('unknown_speaker_grace_s').value)

        self.session_active = False
        self.conversation_paused = False
        self.current_speaker = 'Unknown'
        self.focused_speaker = 'Unknown'
        self.last_focus_time = 0.0

        self.session_sub = self.create_subscription(Bool, '/session_active', self._session_callback, 10)
        self.pause_sub = self.create_subscription(Bool, '/conversation_pause', self._pause_callback, 10)
        self.speaker_sub = self.create_subscription(String, '/speaker_id', self._speaker_callback, 10)
        self.transcription_sub = self.create_subscription(
            Transcription,
            '/transcription',
            self._transcription_callback,
            10,
        )

        self.attended_pub = self.create_publisher(Transcription, '/attended_transcription', 10)
        self.status_pub = self.create_publisher(String, '/attention_status', 10)

        self.get_logger().info('Attention Manager started')

    def _session_callback(self, msg: Bool):
        self.session_active = bool(msg.data)
        if not self.session_active:
            self.focused_speaker = 'Unknown'
            self.last_focus_time = 0.0
            self._publish_status(False, False, 'session_inactive')

    def _pause_callback(self, msg: Bool):
        self.conversation_paused = bool(msg.data)

    def _speaker_callback(self, msg: String):
        speaker = msg.data.strip() or 'Unknown'
        self.current_speaker = speaker

    def _transcription_callback(self, msg: Transcription):
        text = (msg.text or '').strip()
        if not text:
            return

        normalized = normalize_text(text)
        if not normalized:
            return

        direct_address = has_direct_robot_address(normalized)
        reengagement = is_reengagement_phrase(normalized)
        robot_directive = is_robot_directive(normalized)
        control_action = detect_control_action(normalized)
        allow, reason = self._should_allow(
            direct_address,
            reengagement,
            robot_directive,
            control_action,
        )
        self._publish_status(allow, direct_address, reason)

        if not allow:
            self.get_logger().info(
                f'Ignoring likely side conversation from speaker={self.current_speaker}: "{text}"'
            )
            return

        if self.current_speaker != 'Unknown':
            if self.focused_speaker != self.current_speaker:
                self.get_logger().info(
                    f'Attention focus switched: {self.focused_speaker} -> {self.current_speaker}'
                )
            self.focused_speaker = self.current_speaker
            self.last_focus_time = time.monotonic()
        elif self.focused_speaker == 'Unknown':
            self.last_focus_time = time.monotonic()

        self.attended_pub.publish(msg)

    def _should_allow(
        self,
        direct_address: bool,
        reengagement: bool,
        robot_directive: bool,
        control_action: str | None,
    ):
        if not self.session_active:
            return False, 'session_inactive'

        if self.conversation_paused:
            if control_action in ('continue', 'repeat', 'hold_on', 'stop'):
                return True, f'paused_control_{control_action}'
            if reengagement:
                return True, 'paused_reengagement'
            if direct_address:
                return True, 'paused_direct_address'
            return False, 'paused_side_conversation'

        now = time.monotonic()
        if self.focused_speaker != 'Unknown' and (now - self.last_focus_time) > self.focus_timeout_s:
            self.focused_speaker = 'Unknown'

        if self.focused_speaker == 'Unknown':
            return True, 'no_focus_yet'

        if self.current_speaker == 'Unknown':
            if direct_address or robot_directive:
                return True, 'unknown_but_directed'
            if (now - self.last_focus_time) <= self.unknown_speaker_grace_s:
                return True, 'sticky_focus_unknown_segment'
            return False, 'unknown_side_conversation'

        if self.current_speaker == self.focused_speaker:
            return True, 'focused_speaker'

        if direct_address or robot_directive:
            return True, 'speaker_switch_with_direct_address'

        return False, 'different_speaker_without_address'

    def _publish_status(self, allow: bool, direct_address: bool, reason: str):
        payload = {
            'allow_response': bool(allow),
            'direct_address': bool(direct_address),
            'reason': reason,
            'focused_speaker': self.focused_speaker,
            'current_speaker': self.current_speaker,
            'session_active': self.session_active,
            'conversation_paused': self.conversation_paused,
        }
        msg = String()
        msg.data = json.dumps(payload, separators=(',', ':'))
        self.status_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = AttentionManagerNode()
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
