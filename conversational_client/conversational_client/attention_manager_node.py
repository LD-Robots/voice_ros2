#!/usr/bin/env python3
"""
Attention Manager Node.

Filters transcription events so the robot responds primarily to the focused speaker
and ignores likely side conversations unless the robot is directly addressed.
"""
import json
import time
import math

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription
from std_msgs.msg import Bool, String, Int32

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


def get_circular_average(angles):
    if not angles:
        return -1
    x_sum = sum(math.cos(math.radians(a)) for a in angles)
    y_sum = sum(math.sin(math.radians(a)) for a in angles)
    avg_rad = math.atan2(y_sum, x_sum)
    avg_deg = math.degrees(avg_rad)
    return int(round(avg_deg)) % 360


def angular_distance(a, b):
    diff = (a - b + 180) % 360 - 180
    return abs(diff)


class AttentionManagerNode(Node):
    def __init__(self):
        super().__init__('attention_manager_node')

        self.declare_parameter('focus_timeout_s', 45.0)
        self.declare_parameter('focus_recognition_window_s', 3.0)
        self.declare_parameter('unknown_speaker_grace_s', 20.0)
        self.declare_parameter('sticky_speaker_timeout_s', 60.0)
        self.declare_parameter('speaker_switch_hits_required', 2)
        self.declare_parameter('allow_known_speaker_switch_without_address', True)
        self.declare_parameter('doa_enabled', True)
        self.declare_parameter('doa_focus_margin', 45.0)

        self.focus_timeout_s = float(self.get_parameter('focus_timeout_s').value)
        self.focus_recognition_window_s = float(
            self.get_parameter('focus_recognition_window_s').value
        )
        self.unknown_speaker_grace_s = float(self.get_parameter('unknown_speaker_grace_s').value)
        self.allow_known_speaker_switch_without_address = bool(
            self.get_parameter('allow_known_speaker_switch_without_address').value
        )
        self.doa_enabled = bool(self.get_parameter('doa_enabled').value)
        self.doa_focus_margin = float(self.get_parameter('doa_focus_margin').value)

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

        self.latest_doa_angle = -1
        self.last_segment_doa_angle = -1
        self.focused_doa_angle = -1
        self.is_speaking = False
        self.was_speaking = False
        self.current_segment_doa_angles = []

        self.session_sub = self.create_subscription(Bool, '/session_active', self._session_callback, 10)
        self.pause_sub = self.create_subscription(Bool, '/conversation_pause', self._pause_callback, 10)
        self.speaker_sub = self.create_subscription(String, '/speaker_id', self._speaker_callback, 10)
        self.transcription_sub = self.create_subscription(
            Transcription,
            '/transcription',
            self._transcription_callback,
            10,
        )
        self.doa_sub = self.create_subscription(
            Int32,
            '/doa_angle',
            self._doa_callback,
            10
        )
        self.vad_state_sub = self.create_subscription(
            Bool,
            '/voice_activity',
            self._vad_callback,
            10
        )

        self.attended_pub = self.create_publisher(Transcription, '/attended_transcription', 10)
        self.status_pub = self.create_publisher(String, '/attention_status', 10)

        self.get_logger().info('Attention Manager started')

    def _session_callback(self, msg: Bool):
        was_active = self.session_active
        self.session_active = bool(msg.data)
        if not self.session_active:
            self.speaker_tracker.reset()
            self.current_speaker = 'Unknown'
            self.last_raw_speaker = 'Unknown'
            self.focused_speaker = 'Unknown'
            self.last_focus_time = 0.0
            self.pending_focus_speaker = 'Unknown'
            self.pending_focus_at = 0.0
            self.focused_doa_angle = -1
            self._publish_status(False, False, 'session_inactive')
        elif self.session_active and not was_active:
            best_angle = -1
            if self.current_segment_doa_angles:
                best_angle = get_circular_average(self.current_segment_doa_angles)
            elif self.last_segment_doa_angle != -1:
                best_angle = self.last_segment_doa_angle
            elif self.latest_doa_angle != -1:
                best_angle = self.latest_doa_angle
            
            if best_angle != -1:
                self.focused_doa_angle = best_angle
                self.get_logger().info(f'Locking initial focus angle to {self.focused_doa_angle}°')

    def _pause_callback(self, msg: Bool):
        self.conversation_paused = bool(msg.data)

    def _speaker_callback(self, msg: String):
        raw_speaker = msg.data.strip() or 'Unknown'
        self.last_raw_speaker = raw_speaker
        self.current_speaker = self.speaker_tracker.update(raw_speaker)
        if raw_speaker != 'Unknown' and self.current_speaker == raw_speaker:
            self.pending_focus_speaker = raw_speaker
            self.pending_focus_at = time.monotonic()

    def _doa_callback(self, msg: Int32):
        self.latest_doa_angle = msg.data
        if self.is_speaking:
            self.current_segment_doa_angles.append(msg.data)

    def _vad_callback(self, msg: Bool):
        was_speaking = self.is_speaking
        self.is_speaking = bool(msg.data)
        if self.is_speaking and not was_speaking:
            self.current_segment_doa_angles = []
        elif not self.is_speaking and was_speaking:
            if self.current_segment_doa_angles:
                self.last_segment_doa_angle = get_circular_average(self.current_segment_doa_angles)
                self.get_logger().debug(f'Computed segment DOA average: {self.last_segment_doa_angle}° (from {len(self.current_segment_doa_angles)} samples)')
            else:
                self.last_segment_doa_angle = self.latest_doa_angle

    def _transcription_callback(self, msg: Transcription):
        text = (msg.text or '').strip()
        if not text:
            return

        focus_candidate = self._consume_focus_candidate()
        normalized = normalize_text(text)
        if not normalized:
            return

        # Dynamically compute current segment's average DOA to avoid race condition with slow VAD-off.
        segment_doa = -1
        if self.current_segment_doa_angles:
            segment_doa = get_circular_average(self.current_segment_doa_angles)
            self.get_logger().info(
                f"🎤 DEBUG TRANSCRIPTION DOA: Calculated segment_doa={segment_doa}° "
                f"from {len(self.current_segment_doa_angles)} samples: {self.current_segment_doa_angles[:20]}"
            )
            # Synchronize so self.last_segment_doa_angle is updated and published correctly
            self.last_segment_doa_angle = segment_doa
        elif self.last_segment_doa_angle != -1:
            segment_doa = self.last_segment_doa_angle
            self.get_logger().info(f"🎤 DEBUG TRANSCRIPTION DOA: Fallback to last_segment_doa_angle={segment_doa}°")
        else:
            segment_doa = self.latest_doa_angle
            self.last_segment_doa_angle = segment_doa
            self.get_logger().info(f"🎤 DEBUG TRANSCRIPTION DOA: Fallback to latest_doa_angle={segment_doa}°")

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
            segment_doa,
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

    def _should_allow(
        self,
        direct_address: bool,
        reengagement: bool,
        robot_directive: bool,
        control_action: str | None,
        normalized_text: str,
        segment_doa: int,
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
        )
        self.focused_speaker = effective_focus
        self.last_focus_time = effective_focus_time

        # Spatial DOA filtering: Check if the voice came from a different direction than focused speaker
        if allow and self.doa_enabled and self.focused_doa_angle != -1 and segment_doa != -1:
            dist = angular_distance(segment_doa, self.focused_doa_angle)
            if dist > self.doa_focus_margin:
                # Bypassed if directly addressed or re-engaged
                if not (direct_address or reengagement or robot_directive):
                    self.get_logger().info(
                        f'🚫 Blocking transcription: segment DOA = {segment_doa}°, '
                        f'focused DOA = {self.focused_doa_angle}° (diff = {dist:.1f}° > margin = {self.doa_focus_margin}°)'
                    )
                    return False, 'doa_side_conversation'
                else:
                    self.get_logger().info(
                        f'🗣️ DOA switch via direct address: focus angle moving from '
                        f'{self.focused_doa_angle}° to {segment_doa}°'
                    )

        if allow:
            # Update focused angle to keep tracking the current speaker
            if segment_doa != -1:
                self.focused_doa_angle = segment_doa

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
            'focused_doa_angle': int(self.focused_doa_angle),
            'last_segment_doa_angle': int(self.last_segment_doa_angle),
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
