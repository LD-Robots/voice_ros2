#!/usr/bin/env python3
"""
Attention Manager Node.

Filters transcription events so the robot responds primarily to the focused speaker
and ignores likely side conversations unless the robot is directly addressed.
"""
import json
import os
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription
from std_msgs.msg import Bool, String
import requests

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

        self._load_env()

        self.declare_parameter('focus_timeout_s', 45.0)
        self.declare_parameter('focus_recognition_window_s', 3.0)
        self.declare_parameter('unknown_speaker_grace_s', 20.0)
        self.declare_parameter('sticky_speaker_timeout_s', 60.0)
        self.declare_parameter('speaker_switch_hits_required', 2)
        self.declare_parameter('allow_known_speaker_switch_without_address', True)
        self.declare_parameter('llm_address_router_enabled', True)
        self.declare_parameter('llm_address_router_model', 'gpt-5-mini')
        self.declare_parameter('llm_address_router_reasoning_effort', 'minimal')
        self.declare_parameter('llm_address_router_timeout_s', 4.0)
        self.declare_parameter('llm_address_router_fail_closed', True)
        self.declare_parameter('llm_address_router_recent_turns', 8)
        self.declare_parameter('assistant_question_reply_window_s', 25.0)
        self.declare_parameter('openai_api_key_env', 'OPENAI_API_KEY')

        self.focus_timeout_s = float(self.get_parameter('focus_timeout_s').value)
        self.focus_recognition_window_s = float(
            self.get_parameter('focus_recognition_window_s').value
        )
        self.unknown_speaker_grace_s = float(self.get_parameter('unknown_speaker_grace_s').value)
        self.allow_known_speaker_switch_without_address = bool(
            self.get_parameter('allow_known_speaker_switch_without_address').value
        )
        self.llm_address_router_enabled = bool(
            self.get_parameter('llm_address_router_enabled').value
        )
        self.llm_address_router_model = str(
            self.get_parameter('llm_address_router_model').value
        )
        self.llm_address_router_reasoning_effort = str(
            self.get_parameter('llm_address_router_reasoning_effort').value or ''
        ).strip()
        self.llm_address_router_timeout_s = float(
            self.get_parameter('llm_address_router_timeout_s').value
        )
        self.llm_address_router_fail_closed = bool(
            self.get_parameter('llm_address_router_fail_closed').value
        )
        self.llm_address_router_recent_turns = int(
            self.get_parameter('llm_address_router_recent_turns').value
        )
        self.assistant_question_reply_window_s = float(
            self.get_parameter('assistant_question_reply_window_s').value
        )
        self.openai_api_key_env = str(self.get_parameter('openai_api_key_env').value)
        self.openai_api_key = os.environ.get(self.openai_api_key_env, '')
        if self.llm_address_router_enabled and not self.openai_api_key:
            self.get_logger().warn(
                f'{self.openai_api_key_env} not set; address router will fall back to local rules.'
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
        self.latest_diarization = {}
        self.recent_address_events = []
        self.last_assistant_text = ''
        self.last_assistant_at = 0.0

        self.session_sub = self.create_subscription(Bool, '/session_active', self._session_callback, 10)
        self.pause_sub = self.create_subscription(Bool, '/conversation_pause', self._pause_callback, 10)
        self.speaker_sub = self.create_subscription(String, '/speaker_id', self._speaker_callback, 10)
        self.diarization_sub = self.create_subscription(
            String,
            '/elevenlabs_diarization',
            self._diarization_callback,
            10,
        )
        self.transcription_sub = self.create_subscription(
            Transcription,
            '/transcription',
            self._transcription_callback,
            10,
        )
        self.assistant_response_sub = self.create_subscription(
            Transcription,
            '/llm_response',
            self._assistant_response_callback,
            10,
        )

        self.attended_pub = self.create_publisher(Transcription, '/attended_transcription', 10)
        self.status_pub = self.create_publisher(String, '/attention_status', 10)

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
            self.last_assistant_text = ''
            self.last_assistant_at = 0.0
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

    def _diarization_callback(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except Exception:
            return
        self.latest_diarization = payload if isinstance(payload, dict) else {}

    def _assistant_response_callback(self, msg: Transcription):
        text = (msg.text or '').strip()
        if not text:
            return
        self.last_assistant_text = text
        self.last_assistant_at = time.monotonic()

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
        explicit_reengagement = self._is_explicit_robot_reengagement(normalized)
        answers_recent_robot_question = self._answers_recent_robot_question(normalized)
        allow, reason = self._should_allow(
            direct_address,
            reengagement or explicit_reengagement or answers_recent_robot_question,
            robot_directive,
            control_action,
            normalized,
        )

        if not allow and explicit_reengagement and self.session_active:
            allow, reason = True, 'explicit_robot_reengagement'
        if answers_recent_robot_question and self.session_active:
            allow, reason = True, 'answer_to_recent_robot_question'
            self.get_logger().info(
                '🧭 Address local: respond=True, reason=answer_to_recent_robot_question'
            )

        if allow and not answers_recent_robot_question and self._needs_llm_address_router(
            reason=reason,
            direct_address=direct_address,
            robot_directive=robot_directive,
            control_action=control_action,
        ):
            routed = self._route_addressed_to_robot(
                text=text,
                normalized_text=normalized,
                deterministic_reason=reason,
                direct_address=direct_address,
                reengagement=reengagement,
                robot_directive=robot_directive,
                control_action=control_action,
            )
            if routed is not None:
                allow, reason = routed
            elif self.llm_address_router_fail_closed:
                allow, reason = False, f'address_router_failed_closed_after_{reason}'
        elif allow and explicit_reengagement:
            reason = f'explicit_robot_reengagement:{reason}'

        self._publish_status(allow, direct_address, reason)

        if not allow:
            self.get_logger().info(
                f'Ignoring likely side conversation from speaker={self.current_speaker}: "{text}"'
            )
            self._remember_address_event(False, reason, text)
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
        self._remember_address_event(True, reason, text)

    def _should_allow(
        self,
        direct_address: bool,
        reengagement: bool,
        robot_directive: bool,
        control_action: str | None,
        normalized_text: str,
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

    def _needs_llm_address_router(
        self,
        *,
        reason: str,
        direct_address: bool,
        robot_directive: bool,
        control_action: str | None,
    ) -> bool:
        if not self.llm_address_router_enabled or not self.openai_api_key:
            return False
        if direct_address or robot_directive or control_action is not None:
            return False
        return reason in {
            'no_focus_yet',
            'focused_speaker',
            'speaker_switch_without_address',
            'explicit_robot_reengagement',
        }

    def _route_addressed_to_robot(
        self,
        *,
        text: str,
        normalized_text: str,
        deterministic_reason: str,
        direct_address: bool,
        reengagement: bool,
        robot_directive: bool,
        control_action: str | None,
    ) -> tuple[bool, str] | None:
        diarization = self.latest_diarization if isinstance(self.latest_diarization, dict) else {}
        speaker_counts = diarization.get('speaker_word_counts', {}) or {}
        speaker_count = len(speaker_counts)
        focus_age_s = None
        if self.last_focus_time:
            focus_age_s = round(time.monotonic() - self.last_focus_time, 2)
        router_input = {
            'current_text': text,
            'normalized_text': normalized_text,
            'deterministic_attention_reason': deterministic_reason,
            'session_active': self.session_active,
            'conversation_paused': self.conversation_paused,
            'current_speaker': self.current_speaker,
            'focused_speaker': self.focused_speaker,
            'focus_age_s': focus_age_s,
            'last_assistant_text': self.last_assistant_text[-500:],
            'last_assistant_age_s': (
                round(time.monotonic() - self.last_assistant_at, 2)
                if self.last_assistant_at
                else None
            ),
            'direct_robot_address': direct_address,
            'reengagement_phrase': reengagement,
            'robot_directive': robot_directive,
            'control_action': control_action or '',
            'elevenlabs_diarization': {
                'dominant_speaker_id': diarization.get('dominant_speaker_id', ''),
                'speaker_count': speaker_count,
                'speaker_word_counts': speaker_counts,
                'segments': (diarization.get('segments', []) or [])[:8],
            },
            'recent_address_events': self.recent_address_events[-self.llm_address_router_recent_turns:],
        }
        instructions = (
            'You are an address router for a voice robot in a room with multiple people. '
            'Decide whether the current transcription is addressed to the robot or is side conversation '
            'between humans. Default to ignore when uncertain. Respond only when the user clearly asks '
            'the robot/assistant for something, answers the robot’s immediately previous question, gives '
            'a robot control command, or continues an active exchange with the robot. Ignore overheard '
            'human-to-human discussion, comments not requiring the robot, fragments, and unrelated group talk. '
            'If the user explicitly corrects the routing decision, for example "this is not side conversation", '
            '"I am talking to you", or "can we talk about this", treat that as likely addressed to the robot. '
            'If the last assistant message asked a question, treat short or first-person answers as addressed '
            'to the robot unless they are clearly human-to-human. '
            'Be stricter when diarization shows multiple speakers or the speaker is Unknown. '
            'Return only compact JSON with keys: respond (boolean), confidence (number 0..1), reason (string).'
        )
        body = {
            'model': self.llm_address_router_model,
            'instructions': instructions,
            'input': [{
                'role': 'user',
                'content': json.dumps(router_input, ensure_ascii=False),
            }],
            'max_output_tokens': 120,
            'store': False,
            'text': {
                'format': {
                    'type': 'json_schema',
                    'name': 'address_router_decision',
                    'strict': True,
                    'schema': {
                        'type': 'object',
                        'properties': {
                            'respond': {'type': 'boolean'},
                            'confidence': {'type': 'number'},
                            'reason': {'type': 'string'},
                        },
                        'required': ['respond', 'confidence', 'reason'],
                        'additionalProperties': False,
                    },
                },
            },
        }
        if self.llm_address_router_reasoning_effort:
            body['reasoning'] = {'effort': self.llm_address_router_reasoning_effort}

        try:
            response = requests.post(
                'https://api.openai.com/v1/responses',
                headers={
                    'Authorization': f'Bearer {self.openai_api_key}',
                    'Content-Type': 'application/json',
                },
                json=body,
                timeout=max(1.0, self.llm_address_router_timeout_s),
            )
            if not response.ok:
                raise RuntimeError(f'HTTP {response.status_code}: {response.text[:200]}')
            raw_text = self._extract_response_text(response.json())
            payload = self._parse_router_json(raw_text)
            respond = bool(payload.get('respond', False))
            confidence = float(payload.get('confidence', 0.0) or 0.0)
            router_reason = str(payload.get('reason', '') or '').strip()
            if confidence < 0.55:
                respond = False
                router_reason = router_reason or 'low confidence address decision'
            reason = f'address_router_{"respond" if respond else "ignore"}:{router_reason}'
            self.get_logger().info(
                f'🧭 Address router: respond={respond}, confidence={confidence:.2f}, '
                f'reason={router_reason}'
            )
            return respond, reason
        except Exception as exc:
            self.get_logger().warn(f'Address router failed: {exc}')
            return None

    @staticmethod
    def _extract_response_text(payload: dict) -> str:
        output_text = str(payload.get('output_text', '') or '').strip()
        if output_text:
            return output_text
        parts = []
        for item in payload.get('output', []) or []:
            for content in item.get('content', []) or []:
                text = str(content.get('text', '') or '').strip()
                if text:
                    parts.append(text)
        return '\n'.join(parts).strip()

    @staticmethod
    def _parse_router_json(raw_text: str) -> dict:
        text = (raw_text or '').strip()
        if text.startswith('```'):
            import re
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
        return json.loads(text)

    def _remember_address_event(self, allowed: bool, reason: str, text: str):
        self.recent_address_events.append({
            'allowed': bool(allowed),
            'reason': reason,
            'speaker': self.current_speaker,
            'text': text[:240],
            'at': round(time.time(), 3),
        })
        keep = max(4, self.llm_address_router_recent_turns * 2)
        if len(self.recent_address_events) > keep:
            self.recent_address_events = self.recent_address_events[-keep:]

    @staticmethod
    def _is_explicit_robot_reengagement(normalized_text: str) -> bool:
        if not normalized_text:
            return False
        phrases = (
            'not a side conversation',
            'not side conversation',
            'not side convo',
            'this is not side conversation',
            'it is not side conversation',
            'i am talking to you',
            'im talking to you',
            'i m talking to you',
            'i speak with you',
            'i am speaking to you',
            'i m speaking to you',
            'can we talk about this',
            'can i talk with you',
            'can i speak with you',
            'lets talk about this',
            'let s talk about this',
            'hai sa vorbim',
            'hai sa vorbim despre',
            'vorbesc cu tine',
            'iti vorbesc tie',
            'nu e conversatie laterala',
            'nu este conversatie laterala',
            'nu e side conversation',
        )
        return any(phrase in normalized_text for phrase in phrases)

    def _answers_recent_robot_question(self, normalized_text: str) -> bool:
        if not normalized_text or not self.last_assistant_text or not self.last_assistant_at:
            return False
        age_s = time.monotonic() - self.last_assistant_at
        if age_s > self.assistant_question_reply_window_s:
            return False
        assistant_text = self.last_assistant_text.strip()
        assistant_norm = normalize_text(assistant_text)
        asked_question = '?' in assistant_text or any(
            phrase in assistant_norm
            for phrase in (
                'how are you',
                'do you want',
                'would you like',
                'can you',
                'should i',
                'what do you',
                'cum esti',
                'vrei sa',
                'pot sa',
                'ce vrei',
            )
        )
        if not asked_question:
            return False
        words = normalized_text.split()
        if not words:
            return False
        if len(words) <= 4 and words[0] in {
            'yes', 'yeah', 'yep', 'no', 'nope', 'ok', 'okay', 'sure',
            'da', 'nu', 'bine', 'sigur',
        }:
            return True
        if normalized_text.startswith((
            'i ', 'im ', 'i m ', 'i am ', 'i want ', 'i would ', 'my ',
            'me ', 'fine ', 'good ', 'not really ', 'sunt ', 'ma ', 'imi ',
            'vreau ', 'nu vreau ', 'as vrea ',
        )):
            return True
        if any(phrase in normalized_text for phrase in (
            'doing fine',
            'doing good',
            'i am fine',
            'im fine',
            'i m fine',
            'sunt bine',
            'ma simt bine',
        )):
            return True
        return False

    @staticmethod
    def _load_env():
        for base in (Path(__file__).resolve(), Path.cwd().resolve()):
            for parent in [base] + list(base.parents):
                if parent.name == 'voice_ros2':
                    env_path = parent / '.env'
                    if env_path.exists():
                        try:
                            from dotenv import load_dotenv
                            load_dotenv(dotenv_path=env_path)
                        except Exception:
                            pass
                    return

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
