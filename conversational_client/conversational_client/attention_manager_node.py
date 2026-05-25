#!/usr/bin/env python3
"""
Attention Manager Node.

Filters transcription events so the robot responds primarily to the focused speaker
and ignores likely side conversations unless the robot is directly addressed.
"""
import json
import math
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


class OnlineAttentionPolicy:
    """Tiny online contextual bandit for ambiguous respond/ignore routing."""

    POLICY_VERSION = 2
    DEFAULT_WEIGHTS = {
        'bias': -0.35,
        'deterministic_allow': 0.45,
        'reason_no_focus_yet': 0.35,
        'reason_focused_speaker': 1.0,
        'reason_speaker_switch_without_address': 0.25,
        'reason_unknown_side_conversation': -1.1,
        'speaker_known': 0.45,
        'speaker_unknown': -0.45,
        'current_speaker_is_focus': 1.0,
        'speaker_switch': -0.45,
        'direct_address': 3.0,
        'robot_directive': 2.5,
        'control_action': 2.0,
        'recent_assistant_turn': 0.75,
        'recent_assistant_question_mark': 1.35,
        'current_question_mark': 1.25,
        'short_turn': 0.35,
        'long_turn': -0.65,
        'single_diarized_speaker': 0.45,
        'multi_diarized_speaker': -2.4,
        'session_fresh': 0.6,
    }

    def __init__(self, path: Path, *, learning_rate: float, threshold: float):
        self.path = path
        self.learning_rate = max(0.001, float(learning_rate))
        self.threshold = min(0.95, max(0.05, float(threshold)))
        self.weights = dict(self.DEFAULT_WEIGHTS)
        self.updates = 0
        self._load()

    def predict(self, features: dict[str, float]) -> tuple[bool, float]:
        z = 0.0
        for name, value in features.items():
            z += self.weights.get(name, 0.0) * float(value)
        probability = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
        return probability >= self.threshold, probability

    def update(self, features: dict[str, float], target_respond: bool, *, scale: float = 1.0):
        target = 1.0 if target_respond else 0.0
        _, probability = self.predict(features)
        error = target - probability
        step = self.learning_rate * max(0.05, min(1.0, float(scale)))
        for name, value in features.items():
            self.weights[name] = self.weights.get(name, 0.0) + step * error * float(value)
        self.updates += 1
        self._save()

    def top_contributions(self, features: dict[str, float], *, limit: int = 5) -> list[tuple[str, float]]:
        contributions = []
        for name, value in features.items():
            contribution = self.weights.get(name, 0.0) * float(value)
            if abs(contribution) > 0.0001:
                contributions.append((name, contribution))
        contributions.sort(key=lambda item: abs(item[1]), reverse=True)
        return contributions[:max(1, int(limit))]

    def _load(self):
        try:
            with self.path.open('r', encoding='utf-8') as handle:
                payload = json.load(handle)
        except Exception:
            return
        if int(payload.get('version', 0) or 0) != self.POLICY_VERSION:
            return
        weights = payload.get('weights', {})
        if isinstance(weights, dict):
            for name, value in weights.items():
                try:
                    self.weights[str(name)] = float(value)
                except (TypeError, ValueError):
                    continue
        try:
            self.updates = int(payload.get('updates', 0) or 0)
        except (TypeError, ValueError):
            self.updates = 0

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('w', encoding='utf-8') as handle:
                json.dump(
                    {
                        'version': self.POLICY_VERSION,
                        'updates': self.updates,
                        'weights': self.weights,
                        'updated_at': time.time(),
                    },
                    handle,
                    indent=2,
                    sort_keys=True,
                )
        except Exception:
            return


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
        self.declare_parameter('initial_session_reply_grace_s', 30.0)
        self.declare_parameter('attention_learning_enabled', True)
        self.declare_parameter('attention_learning_rate', 0.08)
        self.declare_parameter('attention_learning_threshold', 0.58)
        self.declare_parameter('attention_learning_min_confidence', 0.18)
        self.declare_parameter('attention_learning_path', 'voices/attention_policy.json')
        self.declare_parameter('attention_learning_llm_teacher_scale', 0.25)
        self.declare_parameter('attention_learning_route_enabled', False)
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
        self.initial_session_reply_grace_s = float(
            self.get_parameter('initial_session_reply_grace_s').value
        )
        self.attention_learning_enabled = bool(
            self.get_parameter('attention_learning_enabled').value
        )
        self.attention_learning_min_confidence = float(
            self.get_parameter('attention_learning_min_confidence').value
        )
        self.attention_learning_llm_teacher_scale = float(
            self.get_parameter('attention_learning_llm_teacher_scale').value
        )
        self.attention_learning_route_enabled = bool(
            self.get_parameter('attention_learning_route_enabled').value
        )
        self.attention_policy = OnlineAttentionPolicy(
            self._resolve_workspace_path(
                str(self.get_parameter('attention_learning_path').value)
            ),
            learning_rate=float(self.get_parameter('attention_learning_rate').value),
            threshold=float(self.get_parameter('attention_learning_threshold').value),
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
        self.session_started_at = 0.0
        self.last_learning_example = None

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
        self.learning_feedback_sub = self.create_subscription(
            String,
            '/attention_feedback',
            self._attention_feedback_callback,
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
            self.session_started_at = 0.0
            self.last_learning_example = None
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
        allow, reason = self._should_allow(
            direct_address,
            reengagement,
            robot_directive,
            control_action,
            normalized,
        )

        learning_features = self._build_attention_learning_features(
            text=text,
            normalized_text=normalized,
            deterministic_allow=allow,
            deterministic_reason=reason,
            direct_address=direct_address,
            robot_directive=robot_directive,
            control_action=control_action,
        )
        learned_route = self._route_with_attention_learning(learning_features, allow, reason)
        routed_by_learning = learned_route is not None
        if learned_route is not None:
            allow, reason = learned_route

        if not routed_by_learning and self._needs_llm_address_router(
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
                self._learn_attention_example(
                    learning_features,
                    allow,
                    scale=self.attention_learning_llm_teacher_scale,
                )
            else:
                fallback = self._router_failure_fallback(
                    deterministic_allow=allow,
                    deterministic_reason=reason,
                    features=learning_features,
                    text=text,
                    normalized_text=normalized,
                )
                if fallback is not None:
                    allow, reason = fallback
                elif allow and self.llm_address_router_fail_closed:
                    allow, reason = False, f'address_router_failed_closed_after_{reason}'

        self._publish_status(allow, direct_address, reason)
        self._remember_learning_example(learning_features, allow, reason, text)

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

    def _build_attention_learning_features(
        self,
        *,
        text: str,
        normalized_text: str,
        deterministic_allow: bool,
        deterministic_reason: str,
        direct_address: bool,
        robot_directive: bool,
        control_action: str | None,
    ) -> dict[str, float]:
        words = normalized_text.split()
        now = time.monotonic()
        recent_assistant_age = (
            now - self.last_assistant_at
            if self.last_assistant_at
            else None
        )
        recent_assistant_turn = (
            recent_assistant_age is not None
            and recent_assistant_age <= self.assistant_question_reply_window_s
        )
        session_age = now - self.session_started_at if self.session_started_at else None
        has_focus = self.focused_speaker not in ('', 'Unknown')
        speaker_known = self.current_speaker not in ('', 'Unknown')
        speaker_count = self._diarized_speaker_count()

        features = {
            'bias': 1.0,
            'deterministic_allow': 1.0 if deterministic_allow else 0.0,
            f'reason_{deterministic_reason}': 1.0,
            'speaker_known': 1.0 if speaker_known else 0.0,
            'speaker_unknown': 1.0 if not speaker_known else 0.0,
            'has_focus': 1.0 if has_focus else 0.0,
            'current_speaker_is_focus': (
                1.0 if speaker_known and self.current_speaker == self.focused_speaker else 0.0
            ),
            'speaker_switch': (
                1.0 if speaker_known and has_focus and self.current_speaker != self.focused_speaker
                else 0.0
            ),
            'direct_address': 1.0 if direct_address else 0.0,
            'robot_directive': 1.0 if robot_directive else 0.0,
            'control_action': 1.0 if control_action is not None else 0.0,
            'recent_assistant_turn': 1.0 if recent_assistant_turn else 0.0,
            'recent_assistant_question_mark': (
                1.0 if recent_assistant_turn and '?' in self.last_assistant_text else 0.0
            ),
            'current_question_mark': 1.0 if '?' in (text or '') else 0.0,
            'short_turn': 1.0 if len(words) <= 8 else 0.0,
            'long_turn': 1.0 if len(words) > 18 else 0.0,
            'single_diarized_speaker': 1.0 if speaker_count == 1 else 0.0,
            'multi_diarized_speaker': 1.0 if speaker_count > 1 else 0.0,
            'session_fresh': (
                1.0
                if session_age is not None and session_age <= self.initial_session_reply_grace_s
                else 0.0
            ),
        }
        return features

    def _route_with_attention_learning(
        self,
        features: dict[str, float],
        deterministic_allow: bool,
        deterministic_reason: str,
    ) -> tuple[bool, str] | None:
        if not self.attention_learning_enabled:
            return None
        if not self.attention_learning_route_enabled:
            return None
        if features.get('direct_address') or features.get('robot_directive') or features.get('control_action'):
            return None
        if deterministic_reason not in {
            'no_focus_yet',
            'focused_speaker',
            'speaker_switch_without_address',
            'unknown_side_conversation',
            'different_speaker_without_address',
        }:
            return None

        respond, probability = self.attention_policy.predict(features)
        confidence = abs(probability - 0.5)
        if confidence < self.attention_learning_min_confidence:
            return None
        if respond and deterministic_reason == 'focused_speaker':
            return None
        if respond and deterministic_reason == 'no_focus_yet' and not (
            features.get('session_fresh')
            and features.get('single_diarized_speaker')
            and (
                features.get('current_question_mark')
                or features.get('recent_assistant_question_mark')
            )
        ):
            return None
        if respond == deterministic_allow and deterministic_reason not in {
            'no_focus_yet',
            'speaker_switch_without_address',
            'unknown_side_conversation',
        }:
            return None

        reason = f'attention_learning_{"respond" if respond else "ignore"}:{probability:.2f}'
        top_features = ','.join(
            f'{name}={value:+.2f}'
            for name, value in self.attention_policy.top_contributions(features, limit=4)
        )
        self.get_logger().info(
            f'🧠 Attention learning: respond={respond}, p={probability:.2f}, '
            f'base={deterministic_reason}, top={top_features}'
        )
        return respond, reason

    def _learn_attention_example(
        self,
        features: dict[str, float] | None,
        target_respond: bool,
        *,
        scale: float = 1.0,
    ):
        if not self.attention_learning_enabled or not features:
            return
        if scale <= 0.0:
            return
        self.attention_policy.update(features, bool(target_respond), scale=scale)

    def _remember_learning_example(
        self,
        features: dict[str, float],
        allow: bool,
        reason: str,
        text: str,
    ):
        self.last_learning_example = {
            'features': dict(features),
            'allow': bool(allow),
            'reason': reason,
            'text': text[:240],
            'at': time.monotonic(),
        }

    def _attention_feedback_callback(self, msg: String):
        target = self._parse_attention_feedback(msg.data)
        if target is None:
            self.get_logger().warn(
                'Invalid /attention_feedback. Use JSON like {"should_respond": true}.'
            )
            return
        if not self.last_learning_example:
            self.get_logger().warn('No attention decision available for feedback yet.')
            return
        self._learn_attention_example(
            self.last_learning_example.get('features'),
            target,
            scale=1.0,
        )
        self.get_logger().info(
            f'🧠 Attention learning updated from feedback: should_respond={target}, '
            f'last_reason={self.last_learning_example.get("reason", "")}'
        )

    @staticmethod
    def _parse_attention_feedback(data: str) -> bool | None:
        try:
            payload = json.loads(data or '{}')
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        for key in ('should_respond', 'respond'):
            if key in payload:
                return bool(payload[key])
        if 'reward' in payload:
            try:
                return float(payload['reward']) > 0.0
            except (TypeError, ValueError):
                return None
        return None

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
            'unknown_side_conversation',
            'different_speaker_without_address',
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
        now = time.monotonic()
        focus_age_s = None
        if self.last_focus_time:
            focus_age_s = round(now - self.last_focus_time, 2)
        session_age_s = None
        if self.session_started_at:
            session_age_s = round(now - self.session_started_at, 2)
        router_input = {
            'current_text': text,
            'normalized_text': normalized_text,
            'deterministic_attention_reason': deterministic_reason,
            'session_active': self.session_active,
            'conversation_paused': self.conversation_paused,
            'session_age_s': session_age_s,
            'is_first_address_decision_since_wake': len(self.recent_address_events) == 0,
            'current_speaker': self.current_speaker,
            'focused_speaker': self.focused_speaker,
            'focus_age_s': focus_age_s,
            'last_assistant_text': self.last_assistant_text[-500:],
            'last_assistant_age_s': (
                round(now - self.last_assistant_at, 2)
                if self.last_assistant_at
                else None
            ),
            'direct_robot_address': direct_address,
            'reengagement_phrase': reengagement,
            'robot_directive': robot_directive,
            'control_action': control_action or '',
            'turn_features': {
                'word_count': len(normalized_text.split()),
                'has_question_mark': '?' in (text or ''),
                'looks_incomplete': (
                    bool(normalized_text.split())
                    and normalized_text.split()[-1] in {'si', 'and', 'or', 'sau', 'ca'}
                ),
                'has_recent_prior_ignore': any(
                    not event.get('allowed', False)
                    for event in self.recent_address_events[-2:]
                ),
            },
            'elevenlabs_diarization': {
                'dominant_speaker_id': diarization.get('dominant_speaker_id', ''),
                'speaker_count': speaker_count,
                'speaker_word_counts': speaker_counts,
                'segments': (diarization.get('segments', []) or [])[:8],
            },
            'recent_address_events': self.recent_address_events[-self.llm_address_router_recent_turns:],
        }
        instructions = (
            'You are a professional address router for a wake-word voice robot. Your only job is to decide '
            'whether the assistant should answer the current transcription. Use pragmatic conversation context, '
            'not keyword matching. In an active session, do not require the user to say the robot name every turn. '
            'A statement can be addressed to the robot even when it is not phrased as a question, especially '
            'when it is the first intelligible turn after wake word, a correction of a previous routing mistake, '
            'a continuation after an assistant answer, or a meta-comment about the robot/system behavior. '
            'Ignore only when the turn is clearly overheard human-to-human talk, a fragment/noise, an unrelated '
            'aside, or multi-speaker discussion not meant for the robot. Treat diarization as weak evidence: '
            'multiple speakers increases side-conversation risk, but it is not decisive by itself. A focused '
            'speaker is also weak evidence: it does not automatically mean respond. '
            'For first turns after wake word from a single speaker, prefer responding unless the text is clearly '
            'not for the robot. For corrections like "you ignored that" or "it did not respond", respond. '
            'Return compact JSON. Do not include hidden chain-of-thought; provide a concise professional rationale.'
        )
        body = {
            'model': self.llm_address_router_model,
            'instructions': instructions,
            'input': [{
                'role': 'user',
                'content': json.dumps(router_input, ensure_ascii=False),
            }],
            'max_output_tokens': 700,
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
                            'category': {
                                'type': 'string',
                                'enum': [
                                    'direct_request',
                                    'implicit_address',
                                    'answer_to_assistant',
                                    'router_correction',
                                    'robot_control',
                                    'side_conversation',
                                    'fragment_or_noise',
                                    'uncertain',
                                ],
                            },
                            'rationale': {'type': 'string'},
                            'evidence': {'type': 'string'},
                            'suggested_handling': {'type': 'string'},
                        },
                        'required': [
                            'respond',
                            'confidence',
                            'category',
                            'rationale',
                            'evidence',
                            'suggested_handling',
                        ],
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
            response_payload = response.json()
            raw_text = self._extract_response_text(response_payload)
            if not raw_text:
                status = str(response_payload.get('status', '') or '').strip()
                details = response_payload.get('incomplete_details') or response_payload.get('error') or ''
                raise ValueError(f'empty address router output status={status} details={details}')
            payload = self._parse_router_json(raw_text)
            respond = bool(payload.get('respond', False))
            confidence = float(payload.get('confidence', 0.0) or 0.0)
            category = str(payload.get('category', '') or 'uncertain').strip()
            rationale = str(payload.get('rationale', '') or '').strip()
            evidence = str(payload.get('evidence', '') or '').strip()
            suggested_handling = str(payload.get('suggested_handling', '') or '').strip()
            if confidence < 0.55:
                respond = False
                rationale = rationale or 'low confidence address decision'
            router_reason = f'{category}: {rationale}'.strip()
            reason = f'address_router_{"respond" if respond else "ignore"}:{router_reason}'
            self.get_logger().info(
                f'🧭 Address router: respond={respond}, confidence={confidence:.2f}, '
                f'category={category}, rationale={rationale}, evidence={evidence}, '
                f'handling={suggested_handling}'
            )
            return respond, reason
        except Exception as exc:
            self.get_logger().warn(f'Address router failed: {exc}')
            return None

    def _router_failure_fallback(
        self,
        *,
        deterministic_allow: bool,
        deterministic_reason: str,
        features: dict[str, float],
        text: str,
        normalized_text: str,
    ) -> tuple[bool, str] | None:
        if not deterministic_allow:
            return None
        if not self.session_active or self.conversation_paused:
            return None
        words = normalized_text.split()
        if not words or len(words) > 12:
            return None
        if features.get('multi_diarized_speaker') or features.get('long_turn'):
            return None

        if (
            deterministic_reason == 'no_focus_yet'
            and not self.recent_address_events
            and features.get('session_fresh')
            and features.get('single_diarized_speaker')
            and (
                features.get('current_question_mark')
                or not self._looks_like_fragment(normalized_text)
            )
        ):
            return True, 'address_router_failed_open:first_fresh_single_speaker_turn'

        if (
            deterministic_reason in {'focused_speaker', 'speaker_switch_without_address'}
            and self._looks_like_request_opening(normalized_text)
        ):
            return True, 'address_router_failed_open:request_opening_from_active_exchange'
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
    def _looks_like_fragment(normalized_text: str) -> bool:
        words = normalized_text.split()
        if not words:
            return True
        if len(words) <= 2:
            return True
        return words[-1] in {'si', 'and', 'or', 'sau', 'ca', 'to', 'for', 'with'}

    @staticmethod
    def _looks_like_request_opening(normalized_text: str) -> bool:
        words = normalized_text.split()
        if len(words) < 3 or len(words) > 12:
            return False
        modals = {
            'can', 'could', 'would', 'will', 'should', 'please',
            'poti', 'puteti', 'vrei', 'vreti',
        }
        request_verbs = {
            'tell', 'explain', 'show', 'check', 'calculate', 'find', 'search',
            'help', 'give', 'make', 'say', 'look', 'spune', 'explica',
            'verifica', 'calculeaza', 'cauta', 'ajuta',
        }
        second_person = {'you', 'tu', 'tine', 'imi', 'mi'}
        has_modal = any(word in modals for word in words)
        has_request_verb = any(word in request_verbs for word in words)
        has_second_person = any(word in second_person for word in words)
        return has_request_verb and (has_modal or has_second_person)

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

    def _diarized_speaker_count(self) -> int:
        diarization = self.latest_diarization if isinstance(self.latest_diarization, dict) else {}
        speaker_counts = diarization.get('speaker_word_counts', {}) or {}
        count = 0
        for word_count in speaker_counts.values():
            try:
                if int(word_count or 0) > 0:
                    count += 1
            except (TypeError, ValueError):
                continue
        return count

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

    @staticmethod
    def _resolve_workspace_path(path_value: str) -> Path:
        path = Path(path_value or '').expanduser()
        if path.is_absolute():
            return path
        for base in (Path(__file__).resolve(), Path.cwd().resolve()):
            for parent in [base] + list(base.parents):
                if parent.name == 'voice_ros2':
                    return parent / path
        return Path.cwd() / path

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
