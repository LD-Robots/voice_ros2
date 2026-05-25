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
    advance_attention_focus,
    decide_attention,
    StickySpeakerTracker,
    has_direct_robot_address,
    is_reengagement_phrase,
    normalize_text,
    detect_control_action,
)
from .robot_command_utils import looks_like_robot_command


class AttentionManagerNode(Node):
    def __init__(self):
        super().__init__('attention_manager_node')

        self.declare_parameter('focus_timeout_s', 45.0)
        self.declare_parameter('focus_recognition_window_s', 3.0)
        self.declare_parameter('unknown_speaker_grace_s', 20.0)
        self.declare_parameter('sticky_speaker_timeout_s', 60.0)
        self.declare_parameter('speaker_switch_hits_required', 2)
        self.declare_parameter('allow_known_speaker_switch_without_address', True)
        self.declare_parameter('require_direct_address_for_new_focus', False)
        self.declare_parameter('initial_turn_after_wake_grace_s', 8.0)

        self.focus_timeout_s = float(self.get_parameter('focus_timeout_s').value)
        self.focus_recognition_window_s = float(
            self.get_parameter('focus_recognition_window_s').value
        )
        self.unknown_speaker_grace_s = float(self.get_parameter('unknown_speaker_grace_s').value)
        self.allow_known_speaker_switch_without_address = bool(
            self.get_parameter('allow_known_speaker_switch_without_address').value
        )
        self.require_direct_address_for_new_focus = bool(
            self.get_parameter('require_direct_address_for_new_focus').value
        )
        self.initial_turn_after_wake_grace_s = max(
            0.0,
            float(self.get_parameter('initial_turn_after_wake_grace_s').value),
        )
        self.speaker_tracker = StickySpeakerTracker(
            float(self.get_parameter('sticky_speaker_timeout_s').value),
            int(self.get_parameter('speaker_switch_hits_required').value),
        )

        self.session_active = False
        self.conversation_paused = False
        self.current_speaker = 'Unknown'
        self.last_raw_speaker = 'Unknown'
        self.focused_speaker = 'Unknown'
        self.last_focus_time = 0.0
        self.pending_focus_speaker = 'Unknown'
        self.pending_focus_at = 0.0
        self.session_started_at = 0.0
        self.accepted_turns_since_session = 0

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
        was_active = self.session_active
        self.session_active = bool(msg.data)
        if self.session_active and not was_active:
            self.session_started_at = time.monotonic()
            self.accepted_turns_since_session = 0
        if not self.session_active:
            self.speaker_tracker.reset()
            self.current_speaker = 'Unknown'
            self.last_raw_speaker = 'Unknown'
            self.focused_speaker = 'Unknown'
            self.last_focus_time = 0.0
            self.pending_focus_speaker = 'Unknown'
            self.pending_focus_at = 0.0
            self.session_started_at = 0.0
            self.accepted_turns_since_session = 0
            self._publish_status(False, False, 'session_inactive')

    def _pause_callback(self, msg: Bool):
        self.conversation_paused = bool(msg.data)

    def _speaker_callback(self, msg: String):
        raw_speaker = msg.data.strip() or 'Unknown'
        self.last_raw_speaker = raw_speaker
        self.current_speaker = self.speaker_tracker.update(raw_speaker)
        if raw_speaker != 'Unknown' and self.current_speaker == raw_speaker:
            self.pending_focus_speaker = raw_speaker
            self.pending_focus_at = time.monotonic()

    def _transcription_callback(self, msg: Transcription):
        text = (msg.text or '').strip()
        if not text:
            return

        focus_candidate = self._consume_focus_candidate()
        normalized = normalize_text(text)
        if not normalized:
            return

        direct_address = has_direct_robot_address(normalized)
        reengagement = is_reengagement_phrase(normalized)
        robot_directive = looks_like_robot_command(text, require_direct_robot_address=True)
        control_action = detect_control_action(normalized)
        allow, reason = self._should_allow(
            direct_address,
            reengagement,
            robot_directive,
            control_action,
            normalized,
        )
        self._publish_status(allow, direct_address, reason)

        if not allow:
            self.get_logger().info(
                f'Ignoring likely side conversation from speaker={self.current_speaker}: "{text}"'
            )
            return

        if focus_candidate != 'Unknown' and self.focused_speaker != focus_candidate:
            self.get_logger().info(
                f'Attention focus switched: {self.focused_speaker} -> {focus_candidate}'
            )
        self.focused_speaker, self.last_focus_time = advance_attention_focus(
            current_speaker=self.current_speaker,
            focused_speaker=self.focused_speaker,
            last_focus_time=self.last_focus_time,
            allow=allow,
            recognized_speaker=focus_candidate,
        )

        self.attended_pub.publish(msg)
        self.accepted_turns_since_session += 1

    def _should_allow(
        self,
        direct_address: bool,
        reengagement: bool,
        robot_directive: bool,
        control_action: str | None,
        normalized_text: str,
    ):
        initial_turn_grace = (
            self.accepted_turns_since_session == 0
            and self.session_started_at > 0.0
            and (time.monotonic() - self.session_started_at) <= self.initial_turn_after_wake_grace_s
        )
        allow, reason, effective_focus, effective_focus_time = decide_attention(
            session_active=self.session_active,
            conversation_paused=self.conversation_paused,
            current_speaker=self.current_speaker,
            focused_speaker=self.focused_speaker,
            last_focus_time=self.last_focus_time,
            focus_timeout_s=self.focus_timeout_s,
            allow_known_speaker_switch_without_address=(
                self.allow_known_speaker_switch_without_address
            ),
            require_direct_address_for_new_focus=self.require_direct_address_for_new_focus,
            initial_turn_grace=initial_turn_grace,
            direct_address=direct_address,
            reengagement=reengagement,
            robot_directive=robot_directive,
            control_action=control_action,
            normalized_text=normalized_text,
        )
        self.focused_speaker = effective_focus
        self.last_focus_time = effective_focus_time
        return allow, reason

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

    def _consume_focus_candidate(self) -> str:
        now = time.monotonic()
        candidate = self.pending_focus_speaker
        candidate_at = self.pending_focus_at
        self.pending_focus_speaker = 'Unknown'
        self.pending_focus_at = 0.0
        if candidate == 'Unknown':
            return 'Unknown'
        if (now - candidate_at) > self.focus_recognition_window_s:
            return 'Unknown'
        return candidate


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
