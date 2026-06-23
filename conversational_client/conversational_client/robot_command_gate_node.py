#!/usr/bin/env python3
"""
Robot Command Gate Node.

Variant A: this node does NOT drive any hardware. The motion team subscribes to
/robot_commands and runs one script per command. This node is the safety gate
that sits between voice recognition and the motion team:

  * drops commands below a confidence threshold
  * honors a spoken "stop" (cancel) at any time -> tells the motion team to halt
    via /robot_command_status = "canceled"
  * holds risky commands (long move_forward, full turn_arround) until the same
    speaker confirms with "yes"

Only the 5 supported commands are ever forwarded:
    move_forward(1), raise_hand(2), turn_arround(3), clap(4), say_hi(5)

Subscribes to:
  - /recognized_commands     (RobotCommand, raw recognizer output)
  - /attended_transcription  (Transcription, for "stop" cancel and yes/no confirmation)
  - /speaker_id              (String)

Publishes to:
  - /robot_commands          (RobotCommand, approved commands -> motion team)
  - /robot_commands/<name>   (RobotCommand, per-command convenience topics)
  - /robot_command_status    (String, lifecycle: dispatched/canceled/confirmation_*)
  - /tts_command             (String, spoken confirmation prompt)
"""
import json
import re
import threading
import time
import unicodedata

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import RobotCommand, Transcription
from std_msgs.msg import String

from .robot_command_utils import COMMAND_IDS


class RobotCommandGateNode(Node):
    def __init__(self):
        super().__init__('robot_command_gate_node')

        self.declare_parameter('execution_enabled', True)
        self.declare_parameter('min_command_confidence', 0.60)
        self.declare_parameter('input_topic', '/recognized_commands')
        self.declare_parameter('command_topic', '/robot_commands')
        self.declare_parameter('per_command_topic_prefix', '/robot_commands')
        self.declare_parameter('publish_per_command_topics', True)
        self.declare_parameter('status_topic', '/robot_command_status')
        self.declare_parameter('transcription_topic', '/attended_transcription')

        # Safety: spoken cancel ("stop")
        self.declare_parameter('enable_voice_cancel', True)
        self.declare_parameter('cancel_words', 'stop,cancel,halt,opreste,anuleaza,stai')

        # Safety: confirmation for risky commands
        self.declare_parameter('enable_risky_confirmation', True)
        self.declare_parameter('confirmation_timeout_s', 12.0)
        self.declare_parameter('confirmation_accept_words', 'yes,confirm,ok,da,confirma')
        self.declare_parameter('confirmation_reject_words', 'no,reject,nu,anuleaza')
        self.declare_parameter('require_same_speaker_for_confirmation', True)
        self.declare_parameter('risky_steps_threshold', 5)
        self.declare_parameter('risky_turn_angle_deg', 150.0)
        self.declare_parameter('require_confirmation_for_raise_hand', False)

        self.execution_enabled = bool(self.get_parameter('execution_enabled').value)
        self.min_command_confidence = float(self.get_parameter('min_command_confidence').value)
        input_topic = str(self.get_parameter('input_topic').value)
        self.command_topic = str(self.get_parameter('command_topic').value)
        self.per_command_topic_prefix = str(self.get_parameter('per_command_topic_prefix').value).rstrip('/')
        self.publish_per_command_topics = bool(self.get_parameter('publish_per_command_topics').value)
        status_topic = str(self.get_parameter('status_topic').value)
        transcription_topic = str(self.get_parameter('transcription_topic').value)

        self.enable_voice_cancel = bool(self.get_parameter('enable_voice_cancel').value)
        self.cancel_words = self._parse_words(self.get_parameter('cancel_words').value)

        self.enable_risky_confirmation = bool(self.get_parameter('enable_risky_confirmation').value)
        self.confirmation_timeout_s = float(self.get_parameter('confirmation_timeout_s').value)
        self.confirm_accept_words = self._parse_words(self.get_parameter('confirmation_accept_words').value)
        self.confirm_reject_words = self._parse_words(self.get_parameter('confirmation_reject_words').value)
        self.require_same_speaker_for_confirmation = bool(
            self.get_parameter('require_same_speaker_for_confirmation').value
        )
        self.risky_steps_threshold = int(self.get_parameter('risky_steps_threshold').value)
        self.risky_turn_angle_deg = float(self.get_parameter('risky_turn_angle_deg').value)
        self.require_confirmation_for_raise_hand = bool(
            self.get_parameter('require_confirmation_for_raise_hand').value
        )

        self.current_speaker = 'Unknown'

        self._pending_lock = threading.Lock()
        self._pending_confirmation = None
        self._pending_confirmation_speaker = 'Unknown'
        self._pending_confirmation_deadline = 0.0

        self.command_sub = self.create_subscription(
            RobotCommand, input_topic, self._command_callback, 10
        )
        self.transcription_sub = self.create_subscription(
            Transcription, transcription_topic, self._transcription_callback, 10
        )
        self.speaker_sub = self.create_subscription(
            String, '/speaker_id', self._speaker_callback, 10
        )

        self.command_pub = self.create_publisher(RobotCommand, self.command_topic, 10)
        self.status_pub = self.create_publisher(String, status_topic, 10)
        self.tts_cmd_pub = self.create_publisher(String, '/tts_command', 10)

        self.per_command_pubs = {}
        if self.publish_per_command_topics:
            for command_name in COMMAND_IDS:
                topic = f'{self.per_command_topic_prefix}/{command_name}'
                self.per_command_pubs[command_name] = self.create_publisher(RobotCommand, topic, 10)

        self._confirm_timer = self.create_timer(0.25, self._confirmation_timer_callback)

        self.get_logger().info(
            'Robot Command Gate started: '
            f'input_topic={input_topic}, command_topic={self.command_topic}, '
            f'execution_enabled={self.execution_enabled}, '
            f'voice_cancel={self.enable_voice_cancel}, risky_confirmation={self.enable_risky_confirmation}'
        )

    # ------------------------------------------------------------------ inputs

    def _speaker_callback(self, msg: String):
        speaker = msg.data.strip()
        self.current_speaker = speaker if speaker else 'Unknown'

    def _command_callback(self, msg: RobotCommand):
        if not self.execution_enabled:
            return

        name = self._command_name(msg)
        if name not in COMMAND_IDS:
            self.get_logger().warn(f'Ignoring unsupported command: "{name}"')
            return

        if msg.confidence < self.min_command_confidence:
            self.get_logger().debug(
                f'Ignoring command below threshold ({msg.confidence:.2f} < {self.min_command_confidence:.2f})'
            )
            return

        self.get_logger().info(
            f'Received command: id={msg.command_id}, name={name}, steps={msg.steps}, '
            f'speaker={msg.speaker or "Unknown"}, source="{msg.source_text}"'
        )

        if self._requires_confirmation(msg):
            self._set_pending_confirmation(msg)
            return

        self._clear_pending_confirmation('replaced by a new command')
        self._dispatch(msg)

    def _transcription_callback(self, msg: Transcription):
        text = self._normalize_text(msg.text)
        if not text:
            return

        # "stop" cancels at any time and tells the motion team to halt.
        if self.enable_voice_cancel and self._contains_any(text, self.cancel_words):
            self._clear_pending_confirmation('canceled by voice')
            self._publish_status('canceled')
            self.get_logger().info('Voice cancel ("stop") -> published status=canceled')
            return

        pending = self._get_pending_confirmation()
        if pending is None:
            return

        pending_msg, pending_speaker, deadline = pending
        if time.monotonic() > deadline:
            self._clear_pending_confirmation('confirmation timeout')
            self._publish_status('confirmation_timeout')
            return

        if self.require_same_speaker_for_confirmation:
            current_speaker = self.current_speaker if self.current_speaker else 'Unknown'
            if pending_speaker != 'Unknown' and current_speaker != pending_speaker:
                self.get_logger().warn(
                    'Ignoring confirmation from a different speaker: '
                    f'expected={pending_speaker}, got={current_speaker}'
                )
                return

        if self._contains_any(text, self.confirm_accept_words):
            self._clear_pending_confirmation('confirmed')
            self._publish_status('confirmation_accepted')
            self._dispatch(pending_msg)
            return

        if self._contains_any(text, self.confirm_reject_words):
            self._clear_pending_confirmation('rejected')
            self._publish_status('confirmation_rejected')
            return

    # ------------------------------------------------------------------ dispatch

    def _dispatch(self, msg: RobotCommand):
        name = self._command_name(msg)
        self.command_pub.publish(msg)
        per_command_pub = self.per_command_pubs.get(name)
        if per_command_pub is not None:
            per_command_pub.publish(msg)
        self._publish_status(f'dispatched:{name}')
        self.get_logger().info(
            f'Dispatched to motion team: id={msg.command_id}, name={name}, steps={msg.steps}, '
            f'speaker={msg.speaker or "Unknown"}, topic={self.command_topic}, '
            f'per_command_topic={self.per_command_topic_prefix}/{name}'
        )

    # ------------------------------------------------------------------ safety

    def _requires_confirmation(self, msg: RobotCommand) -> bool:
        if not self.enable_risky_confirmation:
            return False

        name = self._command_name(msg)
        if name == 'raise_hand':
            return self.require_confirmation_for_raise_hand
        if name == 'turn_arround':
            angle_deg = self._extract_angle_deg(msg.parameters_json)
            return angle_deg >= max(5.0, self.risky_turn_angle_deg)
        if name == 'move_forward':
            steps = int(msg.steps) if msg.steps > 0 else 1
            return steps >= max(1, self.risky_steps_threshold)
        return False

    def _set_pending_confirmation(self, msg: RobotCommand):
        deadline = time.monotonic() + max(1.0, self.confirmation_timeout_s)
        speaker = msg.speaker if msg.speaker else 'Unknown'
        with self._pending_lock:
            self._pending_confirmation = msg
            self._pending_confirmation_speaker = speaker
            self._pending_confirmation_deadline = deadline
        self.get_logger().info(
            f'Confirmation required for risky command: name={self._command_name(msg)}, '
            f'steps={msg.steps}, speaker={speaker}'
        )
        self._publish_status('confirmation_required')
        prompt = String()
        prompt.data = 'confirm_ro' if (msg.language or '').lower().startswith('ro') else 'confirm_en'
        self.tts_cmd_pub.publish(prompt)

    def _get_pending_confirmation(self):
        with self._pending_lock:
            if self._pending_confirmation is None:
                return None
            return (
                self._pending_confirmation,
                self._pending_confirmation_speaker,
                self._pending_confirmation_deadline,
            )

    def _clear_pending_confirmation(self, reason: str):
        with self._pending_lock:
            had_pending = self._pending_confirmation is not None
            self._pending_confirmation = None
            self._pending_confirmation_speaker = 'Unknown'
            self._pending_confirmation_deadline = 0.0
        if had_pending:
            self.get_logger().info(f'Pending confirmation cleared: {reason}')

    def _confirmation_timer_callback(self):
        pending = self._get_pending_confirmation()
        if pending is None:
            return
        _, _, deadline = pending
        if time.monotonic() > deadline:
            self._clear_pending_confirmation('timeout')
            self._publish_status('confirmation_timeout')

    # ------------------------------------------------------------------ helpers

    def _publish_status(self, status: str):
        msg = String()
        msg.data = status
        self.status_pub.publish(msg)

    @staticmethod
    def _extract_angle_deg(parameters_json: str) -> float:
        if not parameters_json:
            return 90.0
        try:
            payload = json.loads(parameters_json)
            value = float(payload.get('angle_deg', 90.0))
            return max(5.0, min(360.0, value))
        except Exception:
            return 90.0

    @staticmethod
    def _parse_words(text: str):
        if not text:
            return set()
        return {item.strip().lower() for item in str(text).split(',') if item.strip()}

    @staticmethod
    def _contains_any(text: str, words):
        tokens = set(text.split())
        for word in words:
            if ' ' in word:
                if word in text:
                    return True
            elif word in tokens:
                return True
        return False

    @staticmethod
    def _normalize_text(text: str):
        if not text:
            return ''
        text = str(text).strip().lower()
        text = unicodedata.normalize('NFD', text)
        text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
        text = re.sub(r'[^a-z0-9\s]', ' ', text)
        return ' '.join(text.split())

    @staticmethod
    def _command_name(msg: RobotCommand) -> str:
        return (getattr(msg, 'command_name', '') or '').strip().lower()


def main(args=None):
    rclpy.init(args=args)
    node = RobotCommandGateNode()
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
