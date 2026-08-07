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
    infer_addressing_intent,
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
        self.declare_parameter('semantic_addressing_enabled', True)
        self.declare_parameter('loud_environment_mode', True)
        self.declare_parameter('multi_speaker_window_s', 8.0)
        self.declare_parameter('multi_speaker_switch_threshold', 2)
        self.declare_parameter('indirect_address_score_threshold', 0.62)
        self.declare_parameter('loud_indirect_address_score_threshold', 0.74)

        self.focus_timeout_s = float(self.get_parameter('focus_timeout_s').value)
        self.focus_recognition_window_s = float(
            self.get_parameter('focus_recognition_window_s').value
        )
        self.unknown_speaker_grace_s = float(self.get_parameter('unknown_speaker_grace_s').value)
        self.allow_known_speaker_switch_without_address = bool(
            self.get_parameter('allow_known_speaker_switch_without_address').value
        )
        self.semantic_addressing_enabled = bool(
            self.get_parameter('semantic_addressing_enabled').value
        )
        self.loud_environment_mode = bool(self.get_parameter('loud_environment_mode').value)
        self.multi_speaker_window_s = float(self.get_parameter('multi_speaker_window_s').value)
        self.multi_speaker_switch_threshold = int(
            self.get_parameter('multi_speaker_switch_threshold').value
        )
        self.indirect_address_score_threshold = float(
            self.get_parameter('indirect_address_score_threshold').value
        )
        self.loud_indirect_address_score_threshold = float(
            self.get_parameter('loud_indirect_address_score_threshold').value
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
        self.recent_speaker_events: list[tuple[float, str]] = []
        self.last_addressing_intent = None
        self.last_multi_speaker_context = False

        self.session_sub = self.create_subscription(Bool, 'session_active', self._session_callback, 10)
        self.pause_sub = self.create_subscription(Bool, 'conversation_pause', self._pause_callback, 10)
        self.speaker_sub = self.create_subscription(String, 'speaker_id', self._speaker_callback, 10)
        self.transcription_sub = self.create_subscription(
            Transcription,
            'transcription',
            self._transcription_callback,
            10,
        )

        self.attended_pub = self.create_publisher(Transcription, 'attended_transcription', 10)
        self.status_pub = self.create_publisher(String, 'attention_status', 10)

        self.get_logger().info('Attention Manager started')

    def _session_callback(self, msg: Bool):
        self.session_active = bool(msg.data)
        if not self.session_active:
            self.speaker_tracker.reset()
            self.current_speaker = 'Unknown'
            self.last_raw_speaker = 'Unknown'
            self.focused_speaker = 'Unknown'
            self.last_focus_time = 0.0
            self.pending_focus_speaker = 'Unknown'
            self.pending_focus_at = 0.0
            self.recent_speaker_events.clear()
            self._publish_status(False, False, 'session_inactive', None, False)

    def _pause_callback(self, msg: Bool):
        self.conversation_paused = bool(msg.data)

    def _speaker_callback(self, msg: String):
        raw_speaker = msg.data.strip() or 'Unknown'
        self.last_raw_speaker = raw_speaker
        self.current_speaker = self.speaker_tracker.update(raw_speaker)
        self._record_speaker_event(self.current_speaker)
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
        addressing_intent = (
            infer_addressing_intent(normalized, text)
            if self.semantic_addressing_enabled
            else None
        )
        multi_speaker_context = self._has_multi_speaker_context()
        allow, reason = self._should_allow(
            direct_address,
            reengagement,
            robot_directive,
            control_action,
            normalized,
            addressing_intent,
            multi_speaker_context,
        )
        self.last_addressing_intent = addressing_intent
        self.last_multi_speaker_context = multi_speaker_context
        self._publish_status(allow, direct_address, reason, addressing_intent, multi_speaker_context)

        if not allow:
            self.get_logger().info(
                f'Ignoring likely side conversation from speaker={self.current_speaker}: '
                f'"{text}" ({reason})'
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

    def _should_allow(
        self,
        direct_address: bool,
        reengagement: bool,
        robot_directive: bool,
        control_action: str | None,
        normalized_text: str,
        addressing_intent,
        multi_speaker_context: bool,
    ):
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
            direct_address=direct_address,
            reengagement=reengagement,
            robot_directive=robot_directive,
            control_action=control_action,
            normalized_text=normalized_text,
            semantic_addressing_score=(
                addressing_intent.score if addressing_intent is not None else 0.0
            ),
            semantic_side_score=(
                addressing_intent.side_score if addressing_intent is not None else 0.0
            ),
            multi_speaker_context=multi_speaker_context,
            loud_environment_mode=self.loud_environment_mode,
            indirect_address_score_threshold=self.indirect_address_score_threshold,
            loud_indirect_address_score_threshold=self.loud_indirect_address_score_threshold,
        )
        self.focused_speaker = effective_focus
        self.last_focus_time = effective_focus_time
        return allow, reason

    def _publish_status(
        self,
        allow: bool,
        direct_address: bool,
        reason: str,
        addressing_intent,
        multi_speaker_context: bool,
    ):
        payload = {
            'allow_response': bool(allow),
            'direct_address': bool(direct_address),
            'reason': reason,
            'focused_speaker': self.focused_speaker,
            'current_speaker': self.current_speaker,
            'session_active': self.session_active,
            'conversation_paused': self.conversation_paused,
            'loud_environment_mode': self.loud_environment_mode,
            'multi_speaker_context': bool(multi_speaker_context),
        }
        if addressing_intent is not None:
            payload['semantic_addressing'] = {
                'score': addressing_intent.score,
                'side_score': addressing_intent.side_score,
                'label': addressing_intent.label,
                'features': list(addressing_intent.features),
            }
        msg = String()
        msg.data = json.dumps(payload, separators=(',', ':'))
        self.status_pub.publish(msg)

    def _record_speaker_event(self, speaker: str):
        now = time.monotonic()
        speaker = (speaker or '').strip() or 'Unknown'
        if speaker == 'Unknown':
            return
        self.recent_speaker_events.append((now, speaker))
        cutoff = now - max(0.5, self.multi_speaker_window_s)
        self.recent_speaker_events = [
            (event_time, event_speaker)
            for event_time, event_speaker in self.recent_speaker_events
            if event_time >= cutoff
        ]

    def _has_multi_speaker_context(self) -> bool:
        now = time.monotonic()
        cutoff = now - max(0.5, self.multi_speaker_window_s)
        events = [
            (event_time, speaker)
            for event_time, speaker in self.recent_speaker_events
            if event_time >= cutoff and speaker != 'Unknown'
        ]
        self.recent_speaker_events = events
        if len({speaker for _, speaker in events}) >= 2:
            return True

        switches = 0
        previous = None
        for _, speaker in events:
            if previous is not None and speaker != previous:
                switches += 1
            previous = speaker
        return switches >= self.multi_speaker_switch_threshold

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
