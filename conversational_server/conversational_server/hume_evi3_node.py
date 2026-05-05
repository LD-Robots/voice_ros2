#!/usr/bin/env python3
"""
Hume EVI 3 Node.

Streams microphone audio to Hume EVI over WebSocket and publishes
assistant audio/text back into the existing ROS2 pipeline.
"""
import base64
import io
import json
import os
import threading
import time
import wave
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import rclpy
from rclpy.node import Node

from conversational_interfaces.msg import Audio, RobotCommand, TextChunk, Transcription
from std_msgs.msg import Bool, String

from .prompt_config import load_prompt_defaults

try:
    import websocket

    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print('⚠️ websocket-client not installed. Run: pip install websocket-client')

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


class HumeEvi3Node(Node):
    def __init__(self):
        super().__init__('hume_evi3_node')

        if DOTENV_AVAILABLE:
            workspace_root = _find_workspace_root()
            env_path = workspace_root / '.env' if workspace_root else None
            if env_path and env_path.exists():
                load_dotenv(dotenv_path=env_path)

        self.declare_parameter('api_base_url', 'wss://api.hume.ai/v0/evi/chat')
        self.declare_parameter('config_id', '')
        self.declare_parameter('config_version', -1)
        self.declare_parameter('verbose_transcription', True)
        self.declare_parameter('send_session_settings_on_connect', True)
        self.declare_parameter('input_sample_rate', 16000)
        self.declare_parameter('input_channels', 1)
        self.declare_parameter('capture_during_playback', True)
        self.declare_parameter('reconnect_delay_s', 3.0)
        self.declare_parameter('pause_on_robot_confirmation', True)
        self.declare_parameter('stop_playback_on_user_interruption', True)
        self.declare_parameter('send_system_prompt_in_session_settings', False)
        self.declare_parameter(
            'system_prompt',
            str(load_prompt_defaults().get('realtime_instructions', '')),
        )
        self.declare_parameter('context_text', '')
        self.declare_parameter('context_type', 'persistent')

        self.api_base_url = str(self.get_parameter('api_base_url').value).rstrip('/')
        self.config_id = str(self.get_parameter('config_id').value).strip()
        self.config_version = int(self.get_parameter('config_version').value)
        self.verbose_transcription = bool(
            self.get_parameter('verbose_transcription').value
        )
        self.send_session_settings_on_connect = bool(
            self.get_parameter('send_session_settings_on_connect').value
        )
        self.input_sample_rate = int(self.get_parameter('input_sample_rate').value)
        self.input_channels = max(1, int(self.get_parameter('input_channels').value))
        self.capture_during_playback = bool(
            self.get_parameter('capture_during_playback').value
        )
        self.reconnect_delay_s = float(self.get_parameter('reconnect_delay_s').value)
        self.pause_on_robot_confirmation = bool(
            self.get_parameter('pause_on_robot_confirmation').value
        )
        self.stop_playback_on_user_interruption = bool(
            self.get_parameter('stop_playback_on_user_interruption').value
        )
        self.send_system_prompt_in_session_settings = bool(
            self.get_parameter('send_system_prompt_in_session_settings').value
        )
        self.system_prompt = str(self.get_parameter('system_prompt').value).strip()
        self.context_text = str(self.get_parameter('context_text').value).strip()
        self.context_type = str(self.get_parameter('context_type').value).strip() or 'persistent'

        if self.context_type not in ('temporary', 'persistent'):
            self.get_logger().warn(
                f'Invalid context_type="{self.context_type}"; using "persistent".'
            )
            self.context_type = 'persistent'

        self.api_key = os.environ.get('HUME_EVI')
        if not self.api_key:
            self.get_logger().error('HUME_EVI environment variable not set!')
            raise RuntimeError('HUME_EVI not set')
        if not WEBSOCKET_AVAILABLE:
            self.get_logger().error('websocket-client package not installed!')
            raise RuntimeError('websocket-client not available')

        self.session_active = False
        self.conversation_paused = False
        self.robot_speaking = False
        self.waiting_for_robot_confirmation = False
        self.current_backend = 'legacy'

        self._running = True
        self._connected = threading.Event()
        self._send_lock = threading.Lock()
        self._ws_app = None
        self._ws_thread = threading.Thread(
            target=self._websocket_loop,
            daemon=True,
            name='hume-evi3-ws',
        )

        self._current_audio_sample_rate = self.input_sample_rate
        self._current_audio_channels = self.input_channels
        self._last_sent_audio_settings = (-1, -1)
        self._assistant_turn_text = []
        self._assistant_turn_id = ''
        self._chat_id = ''
        self._chat_group_id = ''
        self._pause_sent_to_hume = False
        self._resume_sent_to_hume = False

        self.audio_pub = self.create_publisher(Audio, '/audio_out', 10)
        self.transcription_pub = self.create_publisher(Transcription, '/transcription', 10)
        self.stream_pub = self.create_publisher(TextChunk, '/llm_stream', 10)
        self.response_pub = self.create_publisher(Transcription, '/llm_response', 10)
        self.pause_state_pub = self.create_publisher(Bool, '/conversation_pause', 10)
        self.tts_stop_pub = self.create_publisher(Bool, '/tts_stop', 10)
        self.status_pub = self.create_publisher(String, '/hume_evi_status', 10)

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
        self.pause_sub = self.create_subscription(
            Bool,
            '/conversation_pause',
            self.pause_callback,
            10,
        )

        self._ws_thread.start()
        self._publish_status('connecting')
        self.get_logger().info('Hume EVI 3 Node started')

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
            self.get_logger().info('Hume conversation session is ACTIVE')
            self._refresh_session_settings(force=True)
        else:
            self.get_logger().info('Hume conversation session is INACTIVE')
            self._assistant_turn_text = []
            self._assistant_turn_id = ''

    def speaking_callback(self, msg: Bool):
        self.robot_speaking = bool(msg.data)

    def stop_callback(self, msg: Bool):
        if msg.data:
            # EVI handles interruption server-side; local playback stop is
            # already managed by audio_playback_node on /tts_stop.
            return

    def robot_command_callback(self, msg: RobotCommand):
        del msg
        self._pause_assistant(reason='robot_command')

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
            if self.pause_on_robot_confirmation:
                self._pause_assistant(reason='robot_confirmation_required')
        elif status in resume_statuses and self.waiting_for_robot_confirmation:
            self.waiting_for_robot_confirmation = False
            if self.pause_on_robot_confirmation and not self.conversation_paused:
                self._resume_assistant(reason='robot_confirmation_finished')

    def backend_callback(self, msg: String):
        backend = msg.data.strip() or 'legacy'
        if backend == self.current_backend:
            return
        self.current_backend = backend
        if backend != 'hume_evi3':
            self._assistant_turn_text = []
            self._assistant_turn_id = ''
        self._refresh_session_settings(force=True)

    def pause_callback(self, msg: Bool):
        self._apply_pause_state(bool(msg.data), publish=False)

    def _apply_pause_state(self, paused: bool, *, publish: bool):
        if paused == self.conversation_paused:
            return
        self.conversation_paused = paused
        if paused:
            self.get_logger().info('Hume conversation paused: listening without responding')
            self._pause_assistant(reason='conversation_paused')
        else:
            self.get_logger().info('Hume conversation resumed')
            if not self.waiting_for_robot_confirmation:
                self._resume_assistant(reason='conversation_resumed')
        if publish:
            out = Bool()
            out.data = paused
            self.pause_state_pub.publish(out)

    def audio_callback(self, msg: Audio):
        if not self.session_active or not self._connected.is_set():
            return
        if self.current_backend != 'hume_evi3':
            return
        if self.conversation_paused or self.waiting_for_robot_confirmation:
            return
        if not self.capture_during_playback and self.robot_speaking:
            return
        if not msg.data:
            return

        sample_rate = int(msg.sample_rate or self.input_sample_rate)
        channels = max(1, int(msg.channels or self.input_channels))
        pcm = np.array(msg.data, dtype=np.int16)
        if pcm.size == 0:
            return

        if sample_rate != self.input_sample_rate:
            pcm = self._resample_pcm16(pcm, sample_rate, self.input_sample_rate)
            sample_rate = self.input_sample_rate

        self._current_audio_sample_rate = sample_rate
        self._current_audio_channels = channels
        self._refresh_session_settings(force=False)

        encoded = base64.b64encode(pcm.tobytes()).decode('ascii')
        self._send_event({'type': 'audio_input', 'data': encoded})

    def _websocket_loop(self):
        while self._running:
            query_params = {'api_key': self.api_key}
            if self.config_id:
                query_params['config_id'] = self.config_id
            if self.config_version >= 0:
                query_params['config_version'] = str(self.config_version)
            if self.verbose_transcription:
                query_params['verbose_transcription'] = 'true'

            url = f'{self.api_base_url}?{urlencode(query_params)}'
            self._ws_app = websocket.WebSocketApp(
                url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            try:
                self._ws_app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                self.get_logger().error(f'Hume EVI WebSocket failed: {exc}')

            self._connected.clear()
            if self._running:
                time.sleep(max(1.0, self.reconnect_delay_s))

    def _on_open(self, ws):
        del ws
        self.get_logger().info('Connected to Hume EVI')
        self._connected.set()
        self._publish_status('online')
        self._pause_sent_to_hume = False
        self._resume_sent_to_hume = False
        self._refresh_session_settings(force=True)

        if self.conversation_paused or self.waiting_for_robot_confirmation:
            self._pause_assistant(reason='on_open_paused')

    def _on_close(self, ws, status_code, msg):
        del ws
        self._connected.clear()
        self._publish_status('offline')
        self.get_logger().warn(f'Hume EVI disconnected: code={status_code}, msg={msg}')

    def _on_error(self, ws, error):
        del ws
        message = str(error)
        if '401' in message or '403' in message:
            self._publish_status('auth_error')
        else:
            self._publish_status('error')
        self.get_logger().error(f'Hume EVI error: {error}')

    def _on_message(self, ws, message):
        del ws
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            self.get_logger().warn('Received non-JSON event from Hume EVI')
            return

        event_type = str(event.get('type', '') or '')

        if event_type == 'chat_metadata':
            self._chat_id = str(event.get('chat_id', '') or '')
            self._chat_group_id = str(event.get('chat_group_id', '') or '')
            self.get_logger().info(
                'Hume chat ready '
                f'(chat_id={self._chat_id or "n/a"}, chat_group_id={self._chat_group_id or "n/a"})'
            )
            return

        if event_type == 'user_interruption':
            if self.stop_playback_on_user_interruption:
                stop_msg = Bool()
                stop_msg.data = True
                self.tts_stop_pub.publish(stop_msg)
            return

        if event_type == 'audio_output':
            self._handle_audio_output(event)
            return

        if event_type == 'user_message':
            self._handle_user_message(event)
            return

        if event_type == 'assistant_message':
            self._handle_assistant_message(event)
            return

        if event_type == 'assistant_end':
            self._handle_assistant_end()
            return

        if event_type == 'error':
            code, message = self._extract_error_details(event)
            detail = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
            summary = message or 'Unknown Hume EVI error.'
            if code:
                summary = f'[{code}] {summary}'

            # Hume warnings may be surfaced with W* codes and should not force fallback.
            if code.upper().startswith('W'):
                self.get_logger().warn(f'Hume EVI API warning: {summary} | event={detail}')
                return

            self._publish_status('error')
            self.get_logger().error(f'Hume EVI API error: {summary} | event={detail}')

    def _handle_audio_output(self, event: dict):
        encoded = str(event.get('data', '') or '')
        if not encoded:
            return

        try:
            wav_bytes = base64.b64decode(encoded)
            pcm, sample_rate = self._decode_wav_to_mono_pcm16(wav_bytes)
        except Exception as exc:
            self.get_logger().error(f'Failed to decode Hume audio_output: {exc}')
            return

        if pcm.size == 0:
            return

        out = Audio()
        out.sample_rate = int(sample_rate)
        out.channels = 1
        out.data = pcm.tolist()
        out.stream_id = str(event.get('id', '') or self._assistant_turn_id or '')
        out.item_id = str(event.get('id', '') or self._assistant_turn_id or '')
        self.audio_pub.publish(out)

    def _handle_user_message(self, event: dict):
        if self.stop_playback_on_user_interruption:
            stop_msg = Bool()
            stop_msg.data = True
            self.tts_stop_pub.publish(stop_msg)

        if bool(event.get('interim', False)):
            return

        text = self._extract_message_text(event)
        if not text:
            return

        out = Transcription()
        out.text = text
        out.language = 'en'
        out.confidence = 1.0
        self.transcription_pub.publish(out)

    def _handle_assistant_message(self, event: dict):
        text = self._extract_message_text(event)
        if not text:
            return

        message_id = str(event.get('id', '') or '')
        if message_id:
            self._assistant_turn_id = message_id

        self._assistant_turn_text.append(text)

        chunk = TextChunk()
        chunk.text = text
        chunk.language = 'en'
        chunk.is_final = False
        chunk.session_id = self._assistant_turn_id or message_id or ''
        self.stream_pub.publish(chunk)

    def _handle_assistant_end(self):
        final_text = ' '.join(part.strip() for part in self._assistant_turn_text if part.strip()).strip()
        if final_text:
            out = Transcription()
            out.text = final_text
            out.language = 'en'
            out.confidence = 1.0
            self.response_pub.publish(out)

        final_chunk = TextChunk()
        final_chunk.text = ''
        final_chunk.language = 'en'
        final_chunk.is_final = True
        final_chunk.session_id = self._assistant_turn_id or self._chat_id or ''
        self.stream_pub.publish(final_chunk)

        self._assistant_turn_text = []
        self._assistant_turn_id = ''

    def _refresh_session_settings(self, *, force: bool):
        if not self._connected.is_set() or self.current_backend != 'hume_evi3':
            return
        if not self.send_session_settings_on_connect:
            return

        sample_rate = int(self._current_audio_sample_rate or self.input_sample_rate)
        channels = int(self._current_audio_channels or self.input_channels)
        signature = (sample_rate, channels)
        if not force and signature == self._last_sent_audio_settings:
            return

        payload = {
            'type': 'session_settings',
            'audio': {
                'format': 'linear16',
                'sample_rate': sample_rate,
                'channels': channels,
            },
        }
        if self.send_system_prompt_in_session_settings and self.system_prompt:
            payload['system_prompt'] = self.system_prompt
        if self.context_text:
            payload['context'] = {
                'text': self.context_text,
                'type': self.context_type,
            }

        if self._send_event(payload):
            self._last_sent_audio_settings = signature

    def _pause_assistant(self, *, reason: str):
        if not self._connected.is_set() or self.current_backend != 'hume_evi3':
            return
        if self._pause_sent_to_hume:
            return
        if self._send_event({'type': 'pause_assistant_message'}):
            self._pause_sent_to_hume = True
            self._resume_sent_to_hume = False
            self.get_logger().debug(f'Sent pause_assistant_message ({reason})')

    def _resume_assistant(self, *, reason: str):
        if not self._connected.is_set() or self.current_backend != 'hume_evi3':
            return
        if self._resume_sent_to_hume and not self._pause_sent_to_hume:
            return
        if self._send_event({'type': 'resume_assistant_message'}):
            self._pause_sent_to_hume = False
            self._resume_sent_to_hume = True
            self.get_logger().debug(f'Sent resume_assistant_message ({reason})')

    def _send_event(self, event: dict) -> bool:
        if not self._connected.is_set() or self._ws_app is None:
            return False

        payload = json.dumps(event, separators=(',', ':'))
        try:
            with self._send_lock:
                self._ws_app.send(payload)
            return True
        except Exception as exc:
            self.get_logger().error(f'Failed to send Hume event: {exc}')
            self._connected.clear()
            return False

    @staticmethod
    def _extract_message_text(event: dict) -> str:
        message = event.get('message', {}) or {}
        if isinstance(message, dict):
            text = message.get('content') or message.get('text') or ''
            if isinstance(text, list):
                parts = [str(item) for item in text if item]
                return ' '.join(parts).strip()
            return str(text).strip()
        if isinstance(message, str):
            return message.strip()

        text = event.get('text') or event.get('content') or ''
        if isinstance(text, list):
            parts = [str(item) for item in text if item]
            return ' '.join(parts).strip()
        return str(text).strip()

    @staticmethod
    def _extract_error_details(event: dict) -> tuple[str, str]:
        error = event.get('error')
        code = str(event.get('code') or '').strip()
        message = str(event.get('message') or '').strip()

        if isinstance(error, dict):
            code = str(error.get('code') or code or '').strip()
            message = str(error.get('message') or message or '').strip()
            if not message:
                detail = error.get('detail')
                if detail is not None:
                    message = str(detail).strip()
        elif isinstance(error, str):
            if not message:
                message = error.strip()
        elif error is not None and not message:
            message = str(error).strip()

        return code, message

    @staticmethod
    def _decode_wav_to_mono_pcm16(wav_bytes: bytes):
        with wave.open(io.BytesIO(wav_bytes), 'rb') as wav_file:
            channels = max(1, int(wav_file.getnchannels()))
            sample_rate = int(wav_file.getframerate())
            sample_width = int(wav_file.getsampwidth())
            frames = wav_file.readframes(wav_file.getnframes())

        if sample_width == 1:
            pcm = (np.frombuffer(frames, dtype=np.uint8).astype(np.int16) - 128) << 8
        elif sample_width == 2:
            pcm = np.frombuffer(frames, dtype='<i2').astype(np.int16)
        else:
            raise ValueError(f'Unsupported WAV sample width: {sample_width}')

        if channels > 1:
            pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)

        return pcm, sample_rate

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

    def _publish_status(self, status: str):
        msg = String()
        msg.data = status
        self.status_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = None

    try:
        node = HumeEvi3Node()
        rclpy.spin(node)
    except RuntimeError as exc:
        print(f'Failed to start Hume EVI 3 node: {exc}')
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
