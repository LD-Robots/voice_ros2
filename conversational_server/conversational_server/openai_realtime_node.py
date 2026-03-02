#!/usr/bin/env python3
"""
OpenAI Realtime Node.

Streams microphone audio to OpenAI Realtime over WebSocket and publishes
assistant audio back into the existing ROS2 audio playback pipeline.
"""
import base64
import json
import os
import re
import threading
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio, TextChunk, Transcription, RobotCommand
from std_msgs.msg import Bool, String

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
        self.declare_parameter('short_transcript_dedupe_window_s', 4.0)
        self.declare_parameter('reconnect_delay_s', 3.0)
        self.declare_parameter(
            'instructions',
            (
                "You are Robot, a friendly bilingual Romanian/English humanoid robot buddy. "
                "Always answer in the same language as the latest user utterance. "
                "Keep answers short, natural, spoken, with no markdown and no emojis. "
                "If the user gives a robot movement or behavior command, stay silent because "
                "the robot control stack handles that request locally."
            ),
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
        self.short_transcript_dedupe_window_s = float(
            self.get_parameter('short_transcript_dedupe_window_s').value
        )
        self.reconnect_delay_s = float(self.get_parameter('reconnect_delay_s').value)
        self.base_instructions = str(self.get_parameter('instructions').value)

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
        self.current_backend = 'openai_realtime'
        self.conversation_language = ''
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
        self._paused_transcript_pending = ''
        self._paused_transcript_at = 0.0
        self._last_accepted_user_transcript_norm = ''
        self._last_accepted_user_transcript_at = 0.0

        self.audio_pub = self.create_publisher(Audio, '/audio_out', 10)
        self.transcription_pub = self.create_publisher(Transcription, '/transcription', 10)
        self.stream_pub = self.create_publisher(TextChunk, '/llm_stream', 10)
        self.response_pub = self.create_publisher(Transcription, '/llm_response', 10)
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
        self.attended_transcription_sub = self.create_subscription(
            Transcription,
            '/attended_transcription',
            self.attended_transcription_callback,
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
            self._cancel_and_clear()

    def speaking_callback(self, msg: Bool):
        self.robot_speaking = bool(msg.data)

    def pause_callback(self, msg: Bool):
        paused = bool(msg.data)
        if paused == self.conversation_paused:
            return
        self.conversation_paused = paused
        if paused:
            self.get_logger().info('OpenAI conversation paused: listening without responding')
            self._truncate_current_audio('conversation_paused')
        else:
            self.get_logger().info('OpenAI conversation resumed')
        self._refresh_session()
        if not paused and self._paused_transcript_pending:
            self._send_event({'type': 'response.create'})
            self._paused_transcript_pending = ''
            self._paused_transcript_at = 0.0

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
        speaker = msg.data.strip() or 'Unknown'
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
        self._refresh_session()

    def attended_transcription_callback(self, msg: Transcription):
        text = (msg.text or '').strip()
        if not text:
            return
        language = self._infer_language_from_text(text)
        if language and language != self.conversation_language:
            self.conversation_language = language
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
            stop_msg = Bool()
            stop_msg.data = True
            self.tts_stop_pub.publish(stop_msg)
            self._truncate_current_audio('speech_started')
            return

        if event_type == 'input_audio_buffer.speech_stopped':
            self.get_logger().info('OpenAI Realtime detected user speech stop')
            return

        if event_type == 'response.created':
            response = event.get('response', {}) or {}
            response_id = str(response.get('id', '') or '')
            self._mark_response_active(response_id)
            if response_id:
                self.get_logger().info(f'OpenAI Realtime started response {response_id}')
            return

        if event_type == 'conversation.item.input_audio_transcription.completed':
            transcript = (event.get('transcript') or '').strip()
            if transcript:
                ignore_reason = self._ignored_transcript_reason(transcript)
                if ignore_reason:
                    self.get_logger().info(
                        f'Ignoring short/accidental transcript ({ignore_reason}): {transcript}'
                    )
                    self._cancel_current_pending_response(ignore_reason)
                    return
                self.get_logger().info(f'OpenAI transcript: {transcript}')
                normalized = self._normalize_text(transcript)
                if normalized:
                    self._last_accepted_user_transcript_norm = normalized
                    self._last_accepted_user_transcript_at = time.monotonic()
                if self.conversation_paused:
                    self._paused_transcript_pending = transcript
                    self._paused_transcript_at = time.monotonic()
                if self._pending_resume_text:
                    self._resume_requested = self._is_resume_request(transcript)
                    if self._resume_requested:
                        self.get_logger().info('Resume request detected for interrupted reply')
                out = Transcription()
                out.text = transcript
                out.language = ''
                out.confidence = 1.0
                self.transcription_pub.publish(out)
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
            'create_response': not self.conversation_paused,
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

    def _build_instructions(self) -> str:
        extras = []
        if self.current_speaker != 'Unknown':
            extras.append(f'Current identified speaker: {self.current_speaker}.')
        if self.conversation_language:
            extras.append(
                f'Current conversation language is {self.conversation_language}. '
                f'Keep responding in {self.conversation_language} until a new robot-directed user utterance clearly switches languages. '
                'Ignore side conversations heard while paused when choosing response language.'
            )
        preferred_name = self.person_context.get('preferred_name', '')
        if preferred_name:
            extras.append(f'Preferred name for this speaker: {preferred_name}.')
        preferred_language = self.person_context.get('preferred_language', '')
        if preferred_language and not self.conversation_language:
            extras.append(f'Preferred language for this speaker: {preferred_language}.')
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
        if self._response_active:
            self._send_event({'type': 'response.cancel'})
            self._mark_response_inactive()
        self._send_event({'type': 'input_audio_buffer.clear'})
        self.get_logger().debug(f'Canceled pending realtime response ({reason})')

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
        normalized = self._normalize_text(text)
        resume_patterns = (
            'continue',
            'resume',
            'continue please',
            'continua',
            'continua te rog',
            'reia',
            'reia raspunsul',
            'reia de unde ai ramas',
            'continua raspunsul',
            'continue the answer',
            'continue your answer',
            'pick up where you left off',
        )
        return any(pattern in normalized for pattern in resume_patterns)

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
        normalized = unicodedata.normalize('NFKD', text.lower())
        normalized = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = re.sub(r'[^a-z0-9\\s]+', ' ', normalized)
        return ' '.join(normalized.split())

    @classmethod
    def _infer_language_from_text(cls, text: str) -> str:
        normalized = cls._normalize_text(text)
        if not normalized:
            return ''

        romanian_markers = {
            'si', 'sunt', 'asta', 'aceasta', 'vreau', 'vorbesc', 'cineva', 'robotul',
            'salut', 'poate', 'poti', 'te', 'rog', 'despre', 'cum', 'mai', 'bine',
            'acum', 'da', 'nu', 'ceva', 'proiectul', 'meu', 'mea', 'romanian',
        }
        english_markers = {
            'the', 'and', 'with', 'about', 'please', 'hello', 'wait', 'project',
            'just', 'back', 'know', 'something', 'tell', 'name', 'working', 'robot',
            'how', 'what', 'why', 'now', 'english',
        }

        words = normalized.split()
        if not words:
            return ''

        ro_score = sum(1 for word in words if word in romanian_markers)
        en_score = sum(1 for word in words if word in english_markers)

        if ro_score > en_score:
            return 'Romanian'
        if en_score > ro_score:
            return 'English'
        return ''


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
