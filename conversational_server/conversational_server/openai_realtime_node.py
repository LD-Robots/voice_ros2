#!/usr/bin/env python3
"""
OpenAI Realtime Node.

Streams microphone audio to OpenAI Realtime over WebSocket and publishes
assistant audio back into the existing ROS2 audio playback pipeline.
"""
import base64
import json
import os
import threading
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio, TextChunk, Transcription, RobotCommand
from conversational_client.conversation_utils import (
    advance_attention_focus,
    can_accept_control_action,
    can_accept_reengagement,
    decide_attention,
    detect_control_action,
    has_direct_robot_address,
    is_reengagement_phrase,
)
from std_msgs.msg import Bool, String
from .language_utils import ConversationLanguageTracker
from .prompt_config import load_prompt_defaults
from .realtime_text_utils import (
    StickySpeakerTracker,
    is_resume_request,
    normalize_realtime_text,
    should_preserve_paused_transcript,
)

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print("⚠️ websocket-client not installed. Run: pip install websocket-client")

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False


def _find_workspace_root() -> Path | None:
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


class OpenAIRealtimeNode(Node):
    def __init__(self):
        super().__init__('openai_realtime_node')

        if DOTENV_AVAILABLE:
            workspace_root = _find_workspace_root()
            env_path = workspace_root / '.env' if workspace_root else None
            if env_path and env_path.exists():
                load_dotenv(dotenv_path=env_path)

        self.declare_parameter('model', 'gpt-realtime-mini')
        self.declare_parameter('voice', 'cedar')
        self.declare_parameter('api_base_url', 'wss://api.openai.com/v1/realtime')
        self.declare_parameter('input_sample_rate', 16000)
        self.declare_parameter('api_sample_rate', 24000)
        self.declare_parameter('capture_during_playback', False)
        self.declare_parameter('input_transcription_enabled', True)
        self.declare_parameter('input_transcription_model', 'gpt-4o-mini-transcribe')
        self.declare_parameter('vad_threshold', 0.65)
        self.declare_parameter('vad_prefix_padding_ms', 400)
        self.declare_parameter('vad_silence_duration_ms', 800)
        self.declare_parameter('response_create_delay_ms', 250)
        self.declare_parameter('local_response_gating', True)
        self.declare_parameter('short_transcript_dedupe_window_s', 4.0)
        self.declare_parameter('reconnect_delay_s', 3.0)
        self.declare_parameter('sticky_speaker_timeout_s', 60.0)
        self.declare_parameter('speaker_switch_hits_required', 2)
        self.declare_parameter('language_switch_hits_required', 2)
        self.declare_parameter('focus_timeout_s', 45.0)
        self.declare_parameter('focus_recognition_window_s', 3.0)
        self.declare_parameter('utterance_capture_prefix_ms', 400)
        self.declare_parameter('utterance_capture_min_ms', 800)
        self.declare_parameter(
            'instructions',
            str(load_prompt_defaults().get('realtime_instructions', '')),
        )

        self.model = str(self.get_parameter('model').value)
        self.voice = str(self.get_parameter('voice').value)
        self.api_base_url = str(self.get_parameter('api_base_url').value).rstrip('/')
        self.input_sample_rate = int(self.get_parameter('input_sample_rate').value)
        self.api_sample_rate = int(self.get_parameter('api_sample_rate').value)
        self.capture_during_playback = bool(self.get_parameter('capture_during_playback').value)
        self.input_transcription_enabled = bool(
            self.get_parameter('input_transcription_enabled').value
        )
        self.input_transcription_model = str(
            self.get_parameter('input_transcription_model').value
        )
        self.vad_threshold = float(self.get_parameter('vad_threshold').value)
        self.vad_prefix_padding_ms = int(self.get_parameter('vad_prefix_padding_ms').value)
        self.vad_silence_duration_ms = int(
            self.get_parameter('vad_silence_duration_ms').value
        )
        self.response_create_delay_ms = max(
            0,
            int(self.get_parameter('response_create_delay_ms').value),
        )
        self.local_response_gating = bool(self.get_parameter('local_response_gating').value)
        self.short_transcript_dedupe_window_s = float(
            self.get_parameter('short_transcript_dedupe_window_s').value
        )
        self.reconnect_delay_s = float(self.get_parameter('reconnect_delay_s').value)
        self.utterance_capture_prefix_ms = int(
            self.get_parameter('utterance_capture_prefix_ms').value
        )
        self.utterance_capture_min_ms = int(
            self.get_parameter('utterance_capture_min_ms').value
        )
        self.base_instructions = str(self.get_parameter('instructions').value)
        self.speaker_tracker = StickySpeakerTracker(
            float(self.get_parameter('sticky_speaker_timeout_s').value),
            int(self.get_parameter('speaker_switch_hits_required').value),
        )
        self.focus_timeout_s = float(self.get_parameter('focus_timeout_s').value)
        self.focus_recognition_window_s = float(
            self.get_parameter('focus_recognition_window_s').value
        )
        self.language_tracker = ConversationLanguageTracker(
            int(self.get_parameter('language_switch_hits_required').value)
        )

        self.api_key = os.environ.get('OPENAI_API_KEY')
        if not self.api_key:
            self.get_logger().error('OPENAI_API_KEY environment variable not set!')
            raise RuntimeError('OPENAI_API_KEY not set')
        if not WEBSOCKET_AVAILABLE:
            self.get_logger().error('websocket-client package not installed!')
            raise RuntimeError('websocket-client not available')

        self.session_active = False
        self.conversation_paused = False
        self.robot_speaking = False
        self.waiting_for_robot_confirmation = False
        self.current_speaker = 'Unknown'
        self.last_raw_speaker = 'Unknown'
        self.current_backend = 'openai_realtime'
        self.focused_speaker = 'Unknown'
        self.last_focus_time = 0.0
        self.pending_focus_speaker = 'Unknown'
        self.pending_focus_at = 0.0
        self.person_context = {
            'speaker': 'Unknown',
            'preferred_name': '',
            'preferred_language': '',
            'facts': [],
        }

        self._ws_app = None
        self._ws_thread = threading.Thread(
            target=self._websocket_loop,
            daemon=True,
            name='openai-realtime-ws',
        )
        self._running = True
        self._connected = threading.Event()
        self._send_lock = threading.Lock()

        self._assistant_text = defaultdict(str)
        self._published_final_items = set()
        self._active_response_id = ''
        self._response_active = False
        self._audio_chunks_sent = 0
        self._seen_output_audio_for_response = set()
        self._last_playback_progress = {
            'stream_id': '',
            'item_id': '',
            'played_ms': 0,
            'stopped': False,
        }
        self._last_truncate_signature = ('', -1)
        self._last_truncate_time = 0.0
        self._pending_resume_text = ''
        self._pending_resume_remaining = ''
        self._pending_resume_played_ms = 0
        self._resume_requested = False
        self._last_response_request_item_id = ''
        self._pending_response_timer = None
        self._pending_response_item_id = ''
        self._pending_response_reason = ''
        self._paused_transcript_pending = ''
        self._paused_transcript_at = 0.0
        self._last_accepted_user_transcript_norm = ''
        self._last_accepted_user_transcript_at = 0.0
        self._recent_input_audio = []
        self._current_user_audio = []
        self._capture_user_audio = False
        self._current_input_sample_rate = self.input_sample_rate
        self._current_input_channels = 1

        self.audio_pub = self.create_publisher(Audio, '/audio_out', 10)
        self.user_audio_segment_pub = self.create_publisher(Audio, '/realtime_user_audio_segment', 10)
        self.transcription_pub = self.create_publisher(Transcription, '/transcription', 10)
        self.stream_pub = self.create_publisher(TextChunk, '/llm_stream', 10)
        self.response_pub = self.create_publisher(Transcription, '/llm_response', 10)
        self.pause_state_pub = self.create_publisher(Bool, '/conversation_pause', 10)
        self.tts_stop_pub = self.create_publisher(Bool, '/tts_stop', 10)
        self.status_pub = self.create_publisher(String, '/openai_realtime_status', 10)

        self.audio_sub = self.create_subscription(Audio, '/audio_raw', self.audio_callback, 10)
        self.session_sub = self.create_subscription(
            Bool,
            '/session_active',
            self.session_callback,
            10,
        )
        self.speaking_sub = self.create_subscription(
            Bool,
            '/is_speaking',
            self.speaking_callback,
            10,
        )
        self.stop_sub = self.create_subscription(Bool, '/tts_stop', self.stop_callback, 10)
        self.progress_sub = self.create_subscription(
            String,
            '/audio_playback_progress',
            self.playback_progress_callback,
            10,
        )
        self.speaker_sub = self.create_subscription(
            String,
            '/speaker_id',
            self.speaker_callback,
            10,
        )
        self.robot_command_sub = self.create_subscription(
            RobotCommand,
            '/robot_command',
            self.robot_command_callback,
            10,
        )
        self.robot_status_sub = self.create_subscription(
            String,
            '/robot_command_status',
            self.robot_status_callback,
            10,
        )
        self.backend_sub = self.create_subscription(
            String,
            '/conversation_backend',
            self.backend_callback,
            10,
        )
        self.person_context_sub = self.create_subscription(
            String,
            '/person_context',
            self.person_context_callback,
            10,
        )
        self.pause_sub = self.create_subscription(
            Bool,
            '/conversation_pause',
            self.pause_callback,
            10,
        )

        self._ws_thread.start()
        self._publish_status('connecting')
        self.get_logger().info(
            f'OpenAI Realtime Node started with model={self.model}, voice={self.voice}'
        )

    def destroy_node(self):
        self._running = False
        self._connected.clear()
        self._cancel_pending_response_create()
        try:
            if self._ws_app is not None:
                self._ws_app.close()
        except Exception:
            pass
        return super().destroy_node()

    def session_callback(self, msg: Bool):
        self.session_active = bool(msg.data)
        if self.session_active:
            self.get_logger().info('OpenAI conversation session is ACTIVE')
            self._refresh_session()
        else:
            self.get_logger().info('OpenAI conversation session is INACTIVE')
            self.speaker_tracker.reset()
            self.current_speaker = 'Unknown'
            self.last_raw_speaker = 'Unknown'
            self.language_tracker.reset()
            self.focused_speaker = 'Unknown'
            self.last_focus_time = 0.0
            self.pending_focus_speaker = 'Unknown'
            self.pending_focus_at = 0.0
            self._cancel_and_clear()

    def speaking_callback(self, msg: Bool):
        self.robot_speaking = bool(msg.data)

    def pause_callback(self, msg: Bool):
        self._apply_pause_state(bool(msg.data), publish=False)

    def _apply_pause_state(self, paused: bool, *, publish: bool):
        if paused == self.conversation_paused:
            return
        self.conversation_paused = paused
        if paused:
            self.get_logger().info('OpenAI conversation paused: listening without responding')
            self._cancel_pending_response_create()
            self._truncate_current_audio('conversation_paused')
        else:
            self.get_logger().info('OpenAI conversation resumed')
        self._refresh_session()
        if not paused and self._paused_transcript_pending:
            self._schedule_response_create('', reason='resume_from_pause')
            self._paused_transcript_pending = ''
            self._paused_transcript_at = 0.0
        if publish:
            msg = Bool()
            msg.data = paused
            self.pause_state_pub.publish(msg)

    def backend_callback(self, msg: String):
        backend = msg.data.strip() or 'legacy'
        if backend == self.current_backend:
            return
        self.current_backend = backend
        if backend != 'openai_realtime':
            self._cancel_and_clear()
        self._refresh_session()

    def stop_callback(self, msg: Bool):
        if msg.data:
            self._truncate_current_audio('tts_stop')
            self._clear_playback_progress()

    def playback_progress_callback(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except Exception:
            return
        if bool(payload.get('stopped', False)):
            self._clear_playback_progress()
            self._mark_response_inactive()
            return
        self._last_playback_progress = {
            'stream_id': str(payload.get('stream_id', '') or ''),
            'item_id': str(payload.get('item_id', '') or ''),
            'played_ms': int(payload.get('played_ms', 0) or 0),
            'stopped': bool(payload.get('stopped', False)),
        }

    def speaker_callback(self, msg: String):
        raw_speaker = msg.data.strip() or 'Unknown'
        self.last_raw_speaker = raw_speaker
        speaker = self.speaker_tracker.update(raw_speaker)
        if raw_speaker != 'Unknown' and speaker == raw_speaker:
            self.pending_focus_speaker = raw_speaker
            self.pending_focus_at = time.monotonic()
        if speaker != self.current_speaker:
            self.current_speaker = speaker
            self._refresh_session()

    def person_context_callback(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except Exception:
            return
        self.person_context = {
            'speaker': str(payload.get('speaker', 'Unknown') or 'Unknown'),
            'preferred_name': str(payload.get('preferred_name', '') or ''),
            'preferred_language': str(payload.get('preferred_language', '') or ''),
            'facts': list(payload.get('facts', []) or []),
        }
        self.language_tracker.seed(self.person_context.get('preferred_language', ''))
        self._refresh_session()

    def robot_command_callback(self, msg: RobotCommand):
        # Keep robot motion logic local; suppress assistant chatter for commands.
        self._truncate_current_audio('robot_command')

    def robot_status_callback(self, msg: String):
        status = msg.data.strip()
        mute_statuses = {'confirmation_required'}
        resume_statuses = {
            'confirmation_accepted',
            'confirmation_rejected',
            'confirmation_timeout',
            'canceled',
            'completed',
            'executing',
            'stopped',
        }

        if status in mute_statuses and not self.waiting_for_robot_confirmation:
            self.waiting_for_robot_confirmation = True
            self._refresh_session()
        elif status in resume_statuses and self.waiting_for_robot_confirmation:
            self.waiting_for_robot_confirmation = False
            self._refresh_session()

    def audio_callback(self, msg: Audio):
        if not self.session_active or not self._connected.is_set():
            return
        if self.current_backend != 'openai_realtime':
            return
        if not self.capture_during_playback and self.robot_speaking:
            return
        if not msg.data:
            return

        self._current_input_sample_rate = int(msg.sample_rate or self.input_sample_rate)
        self._current_input_channels = int(msg.channels or 1)
        self._remember_input_audio(msg.data)
        if self._capture_user_audio:
            self._current_user_audio.extend(msg.data)

        pcm = np.array(msg.data, dtype=np.int16)
        pcm = self._resample_pcm16(pcm, int(msg.sample_rate), self.api_sample_rate)
        encoded = base64.b64encode(pcm.tobytes()).decode('ascii')
        self._send_event({
            'type': 'input_audio_buffer.append',
            'audio': encoded,
        })
        self._audio_chunks_sent += 1
        if self._audio_chunks_sent % 50 == 0:
            self.get_logger().info(
                f'Streaming audio to OpenAI Realtime ({self._audio_chunks_sent} chunks sent)'
            )

    def _websocket_loop(self):
        while self._running:
            url = f'{self.api_base_url}?model={self.model}'
            headers = [
                f'Authorization: Bearer {self.api_key}',
                'OpenAI-Beta: realtime=v1',
            ]
            self._ws_app = websocket.WebSocketApp(
                url,
                header=headers,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            try:
                self._ws_app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                self.get_logger().error(f'OpenAI Realtime WebSocket failed: {exc}')

            self._connected.clear()
            if self._running:
                time.sleep(max(1.0, self.reconnect_delay_s))

    def _on_open(self, ws):
        self.get_logger().info('Connected to OpenAI Realtime API')
        self._connected.set()
        self._publish_status('online')
        self._refresh_session()

    def _on_close(self, ws, status_code, msg):
        self._connected.clear()
        self._publish_status('offline')
        self.get_logger().warn(f'OpenAI Realtime disconnected: code={status_code}, msg={msg}')

    def _on_error(self, ws, error):
        self._publish_status('error')
        self.get_logger().error(f'OpenAI Realtime error: {error}')

    def _on_message(self, ws, message):
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            self.get_logger().warn('Received non-JSON event from OpenAI Realtime')
            return

        event_type = event.get('type', '')

        if event_type == 'input_audio_buffer.speech_started':
            self.get_logger().info('OpenAI Realtime detected user speech start')
            self._cancel_pending_response_create()
            stop_msg = Bool()
            stop_msg.data = True
            self.tts_stop_pub.publish(stop_msg)
            self._truncate_current_audio('speech_started')
            self._start_user_audio_capture()
            return

        if event_type == 'input_audio_buffer.speech_stopped':
            self.get_logger().info('OpenAI Realtime detected user speech stop')
            self._publish_captured_user_audio_segment()
            return

        if event_type == 'response.created':
            response = event.get('response', {}) or {}
            response_id = str(response.get('id', '') or '')
            self._mark_response_active(response_id)
            if response_id:
                self.get_logger().info(f'OpenAI Realtime started response {response_id}')
            return

        if event_type == 'conversation.item.input_audio_transcription.completed':
            item_id = str(event.get('item_id', '') or '')
            transcript = (event.get('transcript') or '').strip()
            if transcript:
                ignore_reason = self._ignored_transcript_reason(transcript)
                if ignore_reason:
                    self.get_logger().info(
                        f'Ignoring short/accidental transcript ({ignore_reason}): {transcript}'
                    )
                    self._cancel_current_pending_response(ignore_reason)
                    self._delete_conversation_item(item_id, ignore_reason)
                    return
                self.get_logger().info(f'OpenAI transcript: {transcript}')
                focus_candidate = self._consume_focus_candidate()
                normalized = self._normalize_text(transcript)
                direct_address = has_direct_robot_address(normalized)
                control_action = detect_control_action(normalized)
                reengagement = is_reengagement_phrase(normalized)
                if normalized:
                    self._last_accepted_user_transcript_norm = normalized
                    self._last_accepted_user_transcript_at = time.monotonic()
                if control_action == 'hold_on' and can_accept_control_action(
                    control_action,
                    current_speaker=self.current_speaker,
                    focused_speaker=self.focused_speaker,
                    session_active=self.session_active,
                    conversation_paused=self.conversation_paused,
                    direct_address=direct_address,
                    normalized_text=normalized,
                ):
                    self._delete_conversation_item(item_id, 'pause_command')
                    self._apply_pause_state(True, publish=True)
                    return
                allow, reason, effective_focus, effective_focus_time = decide_attention(
                    session_active=self.session_active,
                    conversation_paused=self.conversation_paused,
                    current_speaker=self.current_speaker,
                    focused_speaker=self.focused_speaker,
                    last_focus_time=self.last_focus_time,
                    focus_timeout_s=self.focus_timeout_s,
                    direct_address=direct_address,
                    reengagement=reengagement,
                    robot_directive=False,
                    control_action=control_action,
                    normalized_text=normalized,
                )
                self.focused_speaker = effective_focus
                self.last_focus_time = effective_focus_time
                if not allow:
                    self._delete_conversation_item(item_id, reason)
                    self.get_logger().info(
                        f'Ignored realtime side conversation from speaker={self.current_speaker}: "{transcript}" ({reason})'
                    )
                    return
                if self.conversation_paused:
                    if control_action in ('continue', 'repeat') or reengagement:
                        self._paused_transcript_pending = transcript
                        self._paused_transcript_at = time.monotonic()
                        self._apply_pause_state(False, publish=True)
                        return
                    self._delete_conversation_item(item_id, reason)
                    return
                self.focused_speaker, self.last_focus_time = advance_attention_focus(
                    current_speaker=self.current_speaker,
                    focused_speaker=self.focused_speaker,
                    last_focus_time=self.last_focus_time,
                    allow=True,
                    recognized_speaker=focus_candidate,
                )
                if self._pending_resume_text:
                    self._resume_requested = self._is_resume_request(transcript)
                    if self._resume_requested:
                        self.get_logger().info('Resume request detected for interrupted reply')
                active_language = self.language_tracker.observe(
                    transcript,
                    preferred_language=self.person_context.get('preferred_language', ''),
                )
                self._refresh_session()
                out = Transcription()
                out.text = transcript
                out.language = active_language
                out.confidence = 1.0
                self.transcription_pub.publish(out)
                self._schedule_response_create(item_id, reason='accepted_transcript')
            return

        if event_type in ('response.audio.delta', 'response.output_audio.delta'):
            self._handle_output_audio_delta(event)
            return

        if event_type in (
            'response.audio_transcript.delta',
            'response.output_audio_transcript.delta',
            'response.text.delta',
        ):
            self._handle_text_delta(event)
            return

        if event_type in (
            'response.audio_transcript.done',
            'response.output_audio_transcript.done',
            'response.text.done',
        ):
            self._handle_text_done(event)
            return

        if event_type == 'response.done':
            self._handle_response_done(event)
            return

        if event_type == 'error':
            error = event.get('error', {})
            message = error.get('message') or str(error)
            if self._is_benign_realtime_error(message):
                self.get_logger().warn(f'OpenAI Realtime benign API warning: {message}')
            else:
                self._publish_status('error')
                self.get_logger().error(f'OpenAI Realtime API error: {message}')

    def _handle_output_audio_delta(self, event: dict):
        delta = event.get('delta')
        if not delta:
            return

        self._mark_response_active(str(event.get('response_id', '') or ''))
        response_id = str(event.get('response_id', '') or '')
        if response_id and response_id not in self._seen_output_audio_for_response:
            self._seen_output_audio_for_response.add(response_id)
            self.get_logger().info(f'OpenAI Realtime started audio output for response {response_id}')

        try:
            pcm_bytes = base64.b64decode(delta)
        except Exception as exc:
            self.get_logger().error(f'Failed to decode OpenAI audio delta: {exc}')
            return

        pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
        if pcm.size == 0:
            return

        out = Audio()
        out.sample_rate = self.api_sample_rate
        out.channels = 1
        out.data = pcm.tolist()
        out.stream_id = str(event.get('response_id', '') or '')
        out.item_id = str(event.get('item_id', '') or '')
        self.audio_pub.publish(out)

    def _handle_text_delta(self, event: dict):
        item_id = str(event.get('item_id', '') or '')
        response_id = str(event.get('response_id', '') or '')
        delta = str(event.get('delta', '') or '')
        if not delta:
            return

        self._mark_response_active(response_id)

        self._assistant_text[item_id] += delta

        chunk = TextChunk()
        chunk.text = delta
        chunk.language = ''
        chunk.is_final = False
        chunk.session_id = response_id or item_id
        self.stream_pub.publish(chunk)

    def _handle_text_done(self, event: dict):
        item_id = str(event.get('item_id', '') or '')
        response_id = str(event.get('response_id', '') or '')
        transcript = str(event.get('transcript') or event.get('text') or '').strip()
        if transcript:
            self._assistant_text[item_id] = transcript

        self._publish_assistant_final(item_id, response_id)

    def _handle_response_done(self, event: dict):
        response = event.get('response', {}) or {}
        response_id = str(response.get('id', '') or '')
        output_items = response.get('output', []) or []

        for item in output_items:
            item_id = str(item.get('id', '') or '')
            content = item.get('content', []) or []
            if item_id and item_id not in self._assistant_text:
                text_parts = []
                for part in content:
                    transcript = part.get('transcript') or part.get('text') or ''
                    if transcript:
                        text_parts.append(str(transcript))
                if text_parts:
                    self._assistant_text[item_id] = ''.join(text_parts)

            text = self._assistant_text.get(item_id, '').strip()
            if not text:
                continue

            self._publish_assistant_final(item_id, response_id)

        if response_id:
            self.get_logger().info(f'OpenAI Realtime finished response {response_id}')
        if response_id:
            self._mark_response_inactive(response_id)
        else:
            self._mark_response_inactive()
        self._last_response_request_item_id = ''
        if self._pending_resume_text:
            self._clear_pending_resume()
            self._refresh_session()

    def _refresh_session(self):
        if not self._connected.is_set() or self.current_backend != 'openai_realtime':
            return

        instructions = self._build_instructions()
        turn_detection = {
            'type': 'server_vad',
            'threshold': self.vad_threshold,
            'prefix_padding_ms': self.vad_prefix_padding_ms,
            'silence_duration_ms': self.vad_silence_duration_ms,
            'create_response': (not self.local_response_gating) and (not self.conversation_paused),
            'interrupt_response': not self.conversation_paused,
        }

        session = {
            'modalities': ['text', 'audio'],
            'instructions': instructions,
            'voice': self.voice,
            'input_audio_format': 'pcm16',
            'output_audio_format': 'pcm16',
            'turn_detection': turn_detection,
        }
        if self.input_transcription_enabled:
            session['input_audio_transcription'] = {
                'model': self.input_transcription_model,
            }

        self._send_event({
            'type': 'session.update',
            'session': session,
        })

    def _remember_input_audio(self, samples):
        if not samples:
            return
        max_samples = max(
            1,
            int(self._current_input_sample_rate * max(0, self.utterance_capture_prefix_ms) / 1000.0),
        )
        self._recent_input_audio.extend(samples)
        if len(self._recent_input_audio) > max_samples:
            self._recent_input_audio = self._recent_input_audio[-max_samples:]

    def _start_user_audio_capture(self):
        self._capture_user_audio = True
        self._current_user_audio = list(self._recent_input_audio)

    def _publish_captured_user_audio_segment(self):
        if not self._capture_user_audio:
            return
        self._capture_user_audio = False

        min_samples = max(
            1,
            int(self._current_input_sample_rate * max(0, self.utterance_capture_min_ms) / 1000.0),
        )
        if len(self._current_user_audio) < min_samples:
            self._current_user_audio = []
            return

        out = Audio()
        out.sample_rate = self._current_input_sample_rate
        out.channels = self._current_input_channels
        out.data = list(self._current_user_audio)
        self.user_audio_segment_pub.publish(out)
        self._current_user_audio = []

    def _build_instructions(self) -> str:
        extras = []
        if self.current_speaker != 'Unknown':
            extras.append(f'Current identified speaker: {self.current_speaker}.')
        preferred_name = self.person_context.get('preferred_name', '')
        if preferred_name:
            extras.append(f'Preferred name for this speaker: {preferred_name}.')
        preferred_language = self.person_context.get('preferred_language', '')
        if preferred_language:
            extras.append(f'Preferred language for this speaker: {preferred_language}.')
        conversation_language = self.language_tracker.current_language
        if conversation_language == 'ro':
            extras.append(
                'Current conversation language is Romanian. Keep speaking Romanian unless the user clearly asks to switch language.'
            )
        elif conversation_language == 'en':
            extras.append(
                'Current conversation language is English. Keep speaking English unless the user clearly asks to switch language.'
            )
        facts = self.person_context.get('facts', []) or []
        if facts:
            extras.append('Known personal facts: ' + '; '.join(str(fact) for fact in facts[:8]) + '.')
        if self.waiting_for_robot_confirmation:
            extras.append(
                'A risky robot command is awaiting local confirmation. '
                'Do not speak. Let the user answer yes/no without assistant chatter.'
            )
        if self.conversation_paused:
            extras.append(
                'The user told you to wait because they are talking with someone else. '
                'Stay silent until the local controller resumes the conversation.'
            )
        if self._pending_resume_text:
            extras.append(
                'There is an interrupted assistant reply pending. '
                'If the user asks to continue, resume, reia raspunsul, continua, or continue please, '
                'continue that interrupted reply from where it was cut, without restarting from the beginning.'
            )
            if self._pending_resume_remaining:
                extras.append(
                    f'Approximate remaining part of the interrupted reply: "{self._pending_resume_remaining}"'
                )
            else:
                extras.append(
                    f'Interrupted reply to continue from naturally: "{self._pending_resume_text}"'
                )
            extras.append(
                'If the user asks a different question or changes topic, answer the new request normally and '
                'ignore the interrupted reply.'
            )
        return ' '.join([self.base_instructions, *extras]).strip()

    def _cancel_and_clear(self):
        self._cancel_pending_response_create()
        if not self._connected.is_set():
            return
        if self._response_active:
            self._send_event({'type': 'response.cancel'})
        self._send_event({'type': 'input_audio_buffer.clear'})
        self._assistant_text.clear()
        self._published_final_items.clear()
        self._seen_output_audio_for_response.clear()
        self._clear_playback_progress()
        self._clear_pending_resume()
        self._last_response_request_item_id = ''
        self._capture_user_audio = False
        self._current_user_audio = []
        self._recent_input_audio = []
        self._mark_response_inactive()

    def _truncate_current_audio(self, reason: str):
        if not self._connected.is_set():
            return

        item_id = self._last_playback_progress.get('item_id', '')
        played_ms = int(self._last_playback_progress.get('played_ms', 0) or 0)
        signature = (item_id, played_ms)
        now = time.monotonic()

        if (
            signature == self._last_truncate_signature
            and (now - self._last_truncate_time) < 0.5
        ):
            return

        self._last_truncate_signature = signature
        self._last_truncate_time = now

        should_truncate = self._response_active or bool(item_id)
        if not should_truncate:
            return

        self._capture_pending_resume(item_id, played_ms)
        if self._response_active:
            self._send_event({'type': 'response.cancel'})
        if item_id:
            self._send_event({
                'type': 'conversation.item.truncate',
                'item_id': item_id,
                'content_index': 0,
                'audio_end_ms': max(0, played_ms),
            })
        self._mark_response_inactive()
        self.get_logger().debug(
            f'Truncated current assistant audio ({reason}): item={item_id}, played_ms={played_ms}'
        )
        self._refresh_session()

    def _cancel_pending_response_create(self):
        timer = self._pending_response_timer
        self._pending_response_timer = None
        self._pending_response_item_id = ''
        self._pending_response_reason = ''
        if timer is None:
            return
        try:
            timer.cancel()
        except Exception:
            pass

    def _schedule_response_create(self, item_id: str, *, reason: str) -> bool:
        if self.response_create_delay_ms <= 0:
            return self._request_response_create(item_id, reason=reason)
        if self.conversation_paused or self.waiting_for_robot_confirmation:
            return False
        if self._response_active:
            return False
        if item_id and item_id == self._last_response_request_item_id:
            return False

        self._cancel_pending_response_create()
        self._pending_response_item_id = item_id
        self._pending_response_reason = reason
        delay_s = self.response_create_delay_ms / 1000.0
        self._pending_response_timer = self.create_timer(
            delay_s,
            self._fire_pending_response_create,
        )
        self.get_logger().debug(
            f'Scheduled realtime response in {delay_s:.3f}s ({reason}) for item={item_id or "n/a"}'
        )
        return True

    def _fire_pending_response_create(self):
        item_id = self._pending_response_item_id
        reason = self._pending_response_reason or 'delayed_response'
        self._cancel_pending_response_create()
        self._request_response_create(item_id, reason=reason)

    def _send_event(self, event: dict) -> bool:
        if not self._connected.is_set() or self._ws_app is None:
            return False

        payload = json.dumps(event, separators=(',', ':'))
        try:
            with self._send_lock:
                self._ws_app.send(payload)
            return True
        except Exception as exc:
            self.get_logger().error(f'Failed to send OpenAI Realtime event: {exc}')
            self._connected.clear()
            return False

    @staticmethod
    def _resample_pcm16(audio: np.ndarray, original_rate: int, target_rate: int) -> np.ndarray:
        if original_rate == target_rate or audio.size == 0:
            return audio.astype(np.int16, copy=False)

        duration = audio.size / float(original_rate)
        target_samples = max(1, int(round(duration * target_rate)))

        source_positions = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
        target_positions = np.linspace(0.0, 1.0, num=target_samples, endpoint=False)
        resampled = np.interp(target_positions, source_positions, audio.astype(np.float32))
        return np.clip(np.round(resampled), -32768, 32767).astype(np.int16)

    def _publish_assistant_final(self, item_id: str, response_id: str):
        if not item_id or item_id in self._published_final_items:
            return

        text = self._assistant_text.get(item_id, '').strip()
        if not text:
            return

        out = Transcription()
        out.text = text
        out.language = ''
        out.confidence = 1.0
        self.response_pub.publish(out)

        final_chunk = TextChunk()
        final_chunk.text = ''
        final_chunk.language = ''
        final_chunk.is_final = True
        final_chunk.session_id = response_id or item_id
        self.stream_pub.publish(final_chunk)

        self._published_final_items.add(item_id)

    def _mark_response_active(self, response_id: str = ''):
        self._response_active = True
        if response_id:
            self._active_response_id = response_id

    def _mark_response_inactive(self, response_id: str = ''):
        if not response_id or response_id == self._active_response_id:
            self._response_active = False
            self._active_response_id = ''

    def _clear_playback_progress(self):
        self._last_playback_progress = {
            'stream_id': '',
            'item_id': '',
            'played_ms': 0,
            'stopped': False,
        }
        self._last_truncate_signature = ('', -1)

    def _publish_status(self, status: str):
        msg = String()
        msg.data = status
        self.status_pub.publish(msg)

    def _cancel_current_pending_response(self, reason: str):
        self._cancel_pending_response_create()
        if self._response_active:
            self._send_event({'type': 'response.cancel'})
            self._mark_response_inactive()
        self._send_event({'type': 'input_audio_buffer.clear'})
        self.get_logger().debug(f'Canceled pending realtime response ({reason})')

    def _delete_conversation_item(self, item_id: str, reason: str):
        if not item_id:
            return
        self._send_event({
            'type': 'conversation.item.delete',
            'item_id': item_id,
        })
        self.get_logger().debug(f'Deleted realtime conversation item {item_id} ({reason})')

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

    @staticmethod
    def _is_benign_realtime_error(message: str) -> bool:
        normalized = (message or '').strip().lower()
        return (
            'cancellation failed: no active response found' in normalized
            or ('audio content of' in normalized and 'already shorter than' in normalized)
        )

    def _capture_pending_resume(self, item_id: str, played_ms: int):
        text = self._assistant_text.get(item_id, '').strip() if item_id else ''
        if not text:
            return

        self._pending_resume_text = text
        self._pending_resume_played_ms = max(0, int(played_ms))
        self._pending_resume_remaining = self._estimate_remaining_text(text, played_ms)
        self._resume_requested = False

    def _clear_pending_resume(self):
        self._pending_resume_text = ''
        self._pending_resume_remaining = ''
        self._pending_resume_played_ms = 0
        self._resume_requested = False

    def _request_response_create(self, item_id: str, *, reason: str) -> bool:
        self._cancel_pending_response_create()
        if self.conversation_paused or self.waiting_for_robot_confirmation:
            return False
        if self._response_active:
            return False
        if item_id and item_id == self._last_response_request_item_id:
            return False
        if not self._send_event({'type': 'response.create'}):
            return False
        self._last_response_request_item_id = item_id
        self.get_logger().debug(
            f'Requested realtime response ({reason}) for item={item_id or "n/a"}'
        )
        return True

    def _estimate_remaining_text(self, text: str, played_ms: int) -> str:
        clean_text = text.strip()
        if not clean_text:
            return ''
        if played_ms <= 0:
            return clean_text

        chars_per_second = 18.0
        approx_chars_spoken = int((played_ms / 1000.0) * chars_per_second)
        if approx_chars_spoken <= 0:
            return clean_text
        if approx_chars_spoken >= len(clean_text):
            return ''

        cut_idx = approx_chars_spoken
        while cut_idx < len(clean_text) and not clean_text[cut_idx].isspace():
            cut_idx += 1
        if cut_idx >= len(clean_text):
            return ''

        return clean_text[cut_idx:].lstrip(' ,.;:!?-')

    def _is_resume_request(self, text: str) -> bool:
        return is_resume_request(text)

    def _should_preserve_paused_transcript(self, text: str) -> bool:
        return should_preserve_paused_transcript(text)

    def _ignored_transcript_reason(self, text: str) -> str:
        normalized = self._normalize_text(text)
        if not normalized:
            return 'empty'

        control_patterns = (
            'stop',
            'stop talking',
            'be quiet',
            'quiet',
            'hold on',
            'wait',
            'wait a second',
            'wait a little',
            'wait a bit',
            'continue',
            'resume',
            'repeat',
            'say that again',
            'continua',
            'reia',
            'repeta',
            'stai putin',
            'asteapta putin',
            'opreste',
            'taci',
        )
        if any(pattern == normalized or f' {pattern} ' in f' {normalized} ' for pattern in control_patterns):
            return ''

        words = normalized.split()
        yes_no_words = {'yes', 'no', 'da', 'nu'}
        if len(words) == 1 and words[0] in yes_no_words:
            if self.waiting_for_robot_confirmation:
                return ''
            return 'stray_yes_no'

        now = time.monotonic()
        if (
            normalized
            and normalized == self._last_accepted_user_transcript_norm
            and (now - self._last_accepted_user_transcript_at) <= self.short_transcript_dedupe_window_s
            and len(words) <= 4
        ):
            return 'duplicate_short_turn'

        if len(words) == 1 and len(words[0]) <= 4:
            return 'single_short_word'

        if len(words) <= 2 and len(normalized) <= 6:
            return 'very_short_turn'

        return ''

    @staticmethod
    def _normalize_text(text: str) -> str:
        return normalize_realtime_text(text)


def main(args=None):
    rclpy.init(args=args)
    node = None

    try:
        node = OpenAIRealtimeNode()
        rclpy.spin(node)
    except RuntimeError as exc:
        print(f'Failed to start OpenAI Realtime node: {exc}')
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()
