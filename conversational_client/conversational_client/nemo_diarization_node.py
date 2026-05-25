#!/usr/bin/env python3
"""
NVIDIA NeMo / Sortformer diarization node.

This node is intentionally optional: the normal SpeechBrain speaker_id_node stays
the default backend, while this node can be enabled with speaker_backend:=nemo.
It publishes the same /speaker_id topic consumed by the attention and OpenAI
Realtime nodes, plus a JSON /diarization_result topic for debugging.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rclpy
from conversational_interfaces.msg import Audio
from rclpy.node import Node
from std_msgs.msg import Bool, String

try:
    import soundfile as sf
    SOUNDFILE_AVAILABLE = True
except Exception:
    sf = None
    SOUNDFILE_AVAILABLE = False

try:
    from scipy.signal import resample_poly
    SCIPY_AVAILABLE = True
except Exception:
    resample_poly = None
    SCIPY_AVAILABLE = False

try:
    from .speaker_manager import SpeakerManager
    SPEAKER_MANAGER_AVAILABLE = True
    _SPEAKER_MANAGER_IMPORT_ERROR = ''
except Exception as exc:
    SpeakerManager = None
    SPEAKER_MANAGER_AVAILABLE = False
    _SPEAKER_MANAGER_IMPORT_ERROR = str(exc)


TARGET_SAMPLE_RATE = 16000

# Numba can try to integrate with the system coverage module while importing
# NeMo/librosa. On Ubuntu/Python 3.12 this can fail before NeMo loads.
os.environ.setdefault('NUMBA_JIT_COVERAGE', '0')
os.environ.setdefault('MPLCONFIGDIR', '/tmp/matplotlib')


def _patch_numba_coverage_compat():
    try:
        import coverage

        coverage_types = getattr(coverage, 'types', None)
        if (
            coverage_types is not None
            and not hasattr(coverage_types, 'Tracer')
            and hasattr(coverage_types, 'TracerCore')
        ):
            coverage_types.Tracer = coverage_types.TracerCore
        if coverage_types is not None and not hasattr(coverage_types, 'TShouldTraceFn'):
            coverage_types.TShouldTraceFn = getattr(
                coverage_types,
                'TTraceFn',
                Callable,
            )
        if coverage_types is not None and not hasattr(
            coverage_types,
            'TShouldStartContextFn',
        ):
            coverage_types.TShouldStartContextFn = Callable
    except Exception:
        pass


def _find_workspace_root() -> Path | None:
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


def _bool_param(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


class NemoDiarizationNode(Node):
    """Runs NVIDIA NeMo Sortformer diarization and publishes /speaker_id."""

    def __init__(self):
        super().__init__('nemo_diarization_node')

        workspace_root = _find_workspace_root()
        default_enrollment_dir = os.path.join(
            str(workspace_root) if workspace_root else os.getcwd(),
            'voices',
            'enrollment',
        )

        self.declare_parameter('mode', 'streaming_sortformer')
        self.declare_parameter('model_name', 'nvidia/diar_streaming_sortformer_4spk-v2.1')
        self.declare_parameter('offline_model_name', 'nvidia/diar_sortformer_4spk-v1')
        self.declare_parameter('device', 'auto')
        self.declare_parameter('batch_size', 1)
        self.declare_parameter('speaker_prefix', 'nvidia_spk_')
        self.declare_parameter('publish_unknown_on_error', True)
        self.declare_parameter('selection_window_seconds', 2.0)
        self.declare_parameter('min_audio_seconds', 1.0)
        self.declare_parameter('use_vad_segments', True)
        self.declare_parameter('use_realtime_segments', True)
        self.declare_parameter('streaming_audio_enabled', False)
        self.declare_parameter('streaming_window_seconds', 12.0)
        self.declare_parameter('streaming_hop_seconds', 1.0)
        self.declare_parameter('streaming_min_buffer_seconds', 2.0)
        self.declare_parameter('streaming_ignore_playback', True)
        self.declare_parameter('streaming_post_playback_ms', 900)
        self.declare_parameter('streaming_chunk_len', 6)
        self.declare_parameter('streaming_right_context', 7)
        self.declare_parameter('streaming_fifo_len', 188)
        self.declare_parameter('streaming_spkcache_update_period', 144)
        self.declare_parameter('streaming_spkcache_len', 188)
        self.declare_parameter('identity_matching_enabled', True)
        self.declare_parameter('enrollment_dir', default_enrollment_dir)
        self.declare_parameter('similarity_threshold', 0.45)
        self.declare_parameter('similarity_margin', 0.12)
        self.declare_parameter('identity_min_audio_seconds', 0.8)

        self.mode = str(self.get_parameter('mode').value or 'streaming_sortformer')
        self.model_name = str(self.get_parameter('model_name').value or '')
        self.offline_model_name = str(self.get_parameter('offline_model_name').value or '')
        self.device_name = str(self.get_parameter('device').value or 'auto')
        self.batch_size = max(1, int(self.get_parameter('batch_size').value))
        self.speaker_prefix = str(self.get_parameter('speaker_prefix').value or '')
        self.publish_unknown_on_error = _bool_param(
            self.get_parameter('publish_unknown_on_error').value
        )
        self.selection_window_seconds = max(
            0.1,
            float(self.get_parameter('selection_window_seconds').value),
        )
        self.min_audio_seconds = max(0.1, float(self.get_parameter('min_audio_seconds').value))
        self.use_vad_segments = _bool_param(self.get_parameter('use_vad_segments').value)
        self.use_realtime_segments = _bool_param(
            self.get_parameter('use_realtime_segments').value
        )
        self.streaming_audio_enabled = _bool_param(
            self.get_parameter('streaming_audio_enabled').value
        )
        self.streaming_window_seconds = max(
            2.0,
            float(self.get_parameter('streaming_window_seconds').value),
        )
        self.streaming_hop_seconds = max(
            0.2,
            float(self.get_parameter('streaming_hop_seconds').value),
        )
        self.streaming_min_buffer_seconds = max(
            0.5,
            float(self.get_parameter('streaming_min_buffer_seconds').value),
        )
        self.streaming_ignore_playback = _bool_param(
            self.get_parameter('streaming_ignore_playback').value
        )
        self.streaming_post_playback_ms = max(
            0,
            int(self.get_parameter('streaming_post_playback_ms').value),
        )
        self.identity_matching_enabled = _bool_param(
            self.get_parameter('identity_matching_enabled').value
        )
        self.enrollment_dir = str(self.get_parameter('enrollment_dir').value)
        self.similarity_threshold = float(self.get_parameter('similarity_threshold').value)
        self.similarity_margin = float(self.get_parameter('similarity_margin').value)
        self.identity_min_audio_seconds = max(
            0.1,
            float(self.get_parameter('identity_min_audio_seconds').value),
        )

        self.model = None
        self.torch = None
        self.speaker_manager = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._busy_lock = threading.Lock()
        self._busy = False
        self._last_streaming_submit = 0.0
        self._streaming_buffer = np.array([], dtype=np.float32)
        self._last_published_speaker = ''
        self.robot_speaking = False
        self._last_robot_speaking_end_ms = 0

        self.speaker_pub = self.create_publisher(String, '/speaker_id', 10)
        self.diarization_pub = self.create_publisher(String, '/diarization_result', 10)

        self._init_identity_matching()
        self._init_model()
        self._init_subscriptions()

        self.get_logger().info(
            f'NVIDIA NeMo diarization node started mode={self.mode}, '
            f'model={self._selected_model_name()}, device={self.device_name}'
        )

    def destroy_node(self):
        self._executor.shutdown(wait=False, cancel_futures=True)
        return super().destroy_node()

    def _init_identity_matching(self):
        if not self.identity_matching_enabled:
            return
        if not SPEAKER_MANAGER_AVAILABLE:
            self.get_logger().warn(
                'Known-speaker matching disabled: SpeakerManager import failed: '
                f'{_SPEAKER_MANAGER_IMPORT_ERROR}'
            )
            return
        if not os.path.isdir(self.enrollment_dir):
            self.get_logger().warn(
                f'Known-speaker matching disabled: enrollment dir missing: {self.enrollment_dir}'
            )
            return
        if not any(name.endswith('.wav') for name in os.listdir(self.enrollment_dir)):
            self.get_logger().warn(
                f'Known-speaker matching disabled: no .wav files in {self.enrollment_dir}'
            )
            return
        try:
            self.speaker_manager = SpeakerManager(
                self.enrollment_dir,
                threshold=self.similarity_threshold,
                min_margin=self.similarity_margin,
            )
            self.get_logger().info(
                'Known-speaker matching enabled for NeMo diarization: '
                f'{", ".join(self.speaker_manager.get_speakers())}'
            )
        except Exception as exc:
            self.get_logger().warn(f'Known-speaker matching disabled: {exc}')
            self.speaker_manager = None

    def _init_model(self):
        _patch_numba_coverage_compat()
        try:
            import torch
            from nemo.collections.asr.models import SortformerEncLabelModel
        except Exception as exc:
            self.get_logger().error(
                'NVIDIA NeMo diarization is not installed. Install NeMo ASR support '
                'and relaunch with speaker_backend:=nemo. Import error: '
                f'{exc}'
            )
            return

        self.torch = torch
        if self.device_name == 'auto':
            self.device_name = 'cuda' if torch.cuda.is_available() else 'cpu'

        model_ref = self._selected_model_name()
        try:
            if model_ref.endswith('.nemo') or os.path.exists(model_ref):
                self.model = SortformerEncLabelModel.restore_from(
                    restore_path=model_ref,
                    map_location=self.device_name,
                )
            else:
                self.model = SortformerEncLabelModel.from_pretrained(model_ref)

            if hasattr(self.model, 'to'):
                self.model = self.model.to(self.device_name)
            if hasattr(self.model, 'eval'):
                self.model.eval()
            self._maybe_set_streaming_config()
        except Exception as exc:
            self.get_logger().error(
                f'Failed to load NeMo Sortformer model "{model_ref}": {exc}'
            )
            self.model = None

    def _selected_model_name(self) -> str:
        if self.mode == 'sortformer':
            return self.offline_model_name
        return self.model_name

    def _maybe_set_streaming_config(self):
        if self.model is None or 'streaming' not in self.mode:
            return
        chunk_len = int(self.get_parameter('streaming_chunk_len').value)
        right_context = int(self.get_parameter('streaming_right_context').value)
        fifo_len = int(self.get_parameter('streaming_fifo_len').value)
        spkcache_update_period = int(
            self.get_parameter('streaming_spkcache_update_period').value
        )
        spkcache_len = int(self.get_parameter('streaming_spkcache_len').value)

        if hasattr(self.model, 'set_streaming_config'):
            try:
                self.model.set_streaming_config(
                    chunk_len=chunk_len,
                    shift_len=chunk_len,
                    right_context=right_context,
                    fifo_len=fifo_len,
                    spkcache_update_period=spkcache_update_period,
                    spkcache_len=spkcache_len,
                )
                return
            except Exception as exc:
                self.get_logger().warn(f'Could not apply set_streaming_config: {exc}')

        modules = getattr(self.model, 'sortformer_modules', None)
        if modules is None:
            self.get_logger().warn(
                'Selected model does not expose Sortformer streaming configuration hooks.'
            )
            return
        try:
            modules.chunk_len = chunk_len
            modules.chunk_right_context = right_context
            modules.fifo_len = fifo_len
            modules.spkcache_update_period = spkcache_update_period
            modules.spkcache_len = spkcache_len
            if hasattr(modules, '_check_streaming_parameters'):
                modules._check_streaming_parameters()
        except Exception as exc:
            self.get_logger().warn(f'Could not apply streaming Sortformer config: {exc}')

    def _init_subscriptions(self):
        if self.use_vad_segments:
            self.create_subscription(Audio, '/audio_segment', self.segment_callback, 10)
        if self.use_realtime_segments:
            self.create_subscription(
                Audio,
                '/realtime_user_audio_segment',
                self.segment_callback,
                10,
            )
        if self.streaming_audio_enabled:
            self.create_subscription(Audio, '/audio_raw', self.raw_audio_callback, 10)
            self.create_subscription(Bool, '/is_speaking', self.speaking_callback, 10)

    def speaking_callback(self, msg: Bool):
        was_speaking = self.robot_speaking
        self.robot_speaking = bool(msg.data)
        if was_speaking and not self.robot_speaking:
            self._last_robot_speaking_end_ms = int(time.time() * 1000)

    def segment_callback(self, msg: Audio):
        audio = self._audio_msg_to_float32_mono(msg)
        sample_rate = int(msg.sample_rate or TARGET_SAMPLE_RATE)
        audio = self._ensure_16khz(audio, sample_rate)
        duration = len(audio) / TARGET_SAMPLE_RATE
        if duration < self.min_audio_seconds:
            return
        self._submit_diarization(audio, source='segment')

    def raw_audio_callback(self, msg: Audio):
        if self._should_ignore_streaming_audio():
            return

        audio = self._audio_msg_to_float32_mono(msg)
        sample_rate = int(msg.sample_rate or TARGET_SAMPLE_RATE)
        audio = self._ensure_16khz(audio, sample_rate)
        if audio.size == 0:
            return

        self._streaming_buffer = np.concatenate([self._streaming_buffer, audio])
        max_samples = int(self.streaming_window_seconds * TARGET_SAMPLE_RATE)
        if len(self._streaming_buffer) > max_samples:
            self._streaming_buffer = self._streaming_buffer[-max_samples:]

        now = time.monotonic()
        duration = len(self._streaming_buffer) / TARGET_SAMPLE_RATE
        if duration < self.streaming_min_buffer_seconds:
            return
        if now - self._last_streaming_submit < self.streaming_hop_seconds:
            return
        self._last_streaming_submit = now
        self._submit_diarization(self._streaming_buffer.copy(), source='stream')

    def _should_ignore_streaming_audio(self) -> bool:
        if not self.streaming_ignore_playback:
            return False
        if self.robot_speaking:
            self._streaming_buffer = np.array([], dtype=np.float32)
            return True
        if self._last_robot_speaking_end_ms <= 0:
            return False
        now_ms = int(time.time() * 1000)
        if (now_ms - self._last_robot_speaking_end_ms) <= self.streaming_post_playback_ms:
            self._streaming_buffer = np.array([], dtype=np.float32)
            return True
        return False

    def _submit_diarization(self, audio: np.ndarray, *, source: str):
        if self.model is None:
            if self.publish_unknown_on_error:
                self._publish_speaker('Unknown')
            return
        with self._busy_lock:
            if self._busy:
                self.get_logger().debug(
                    'Skipping diarization request because previous inference is still running'
                )
                return
            self._busy = True
        future = self._executor.submit(self._process_audio, audio, source)
        future.add_done_callback(self._process_done)

    def _process_done(self, future):
        with self._busy_lock:
            self._busy = False
        try:
            future.result()
        except Exception as exc:
            self.get_logger().error(f'NeMo diarization failed: {exc}')
            if self.publish_unknown_on_error:
                self._publish_speaker('Unknown')

    def _process_audio(self, audio: np.ndarray, source: str):
        diarization = self._run_diarization(audio)
        segments = self._parse_diarization(diarization)
        selected_raw = self._select_current_speaker(segments, len(audio) / TARGET_SAMPLE_RATE)
        if not selected_raw:
            self._publish_diarization_json(source, 'Unknown', '', segments, audio)
            if self.publish_unknown_on_error:
                self._publish_speaker('Unknown')
            return

        fallback_label = self._normalize_speaker_label(selected_raw)
        speaker = self._match_known_speaker(audio, segments, selected_raw) or fallback_label
        self._publish_diarization_json(source, speaker, fallback_label, segments, audio)
        self._publish_speaker(speaker)

    def _run_diarization(self, audio: np.ndarray):
        if self.model is None:
            return []

        audio = np.asarray(audio, dtype=np.float32)
        if self.mode == 'sortformer':
            return self._diarize_file(audio)

        try:
            with self._torch_inference_context():
                return self.model.diarize(
                    audio=[audio],
                    batch_size=self.batch_size,
                    sample_rate=TARGET_SAMPLE_RATE,
                )
        except TypeError:
            return self._diarize_file(audio)

    def _diarize_file(self, audio: np.ndarray):
        if not SOUNDFILE_AVAILABLE:
            raise RuntimeError('soundfile is required for file-based NeMo diarization')

        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp_path = tmp.name
        try:
            sf.write(tmp_path, audio, TARGET_SAMPLE_RATE)
            with self._torch_inference_context():
                try:
                    return self.model.diarize(
                        audio=[tmp_path],
                        batch_size=self.batch_size,
                    )
                except TypeError:
                    return self.model.diarize(paths2audio_files=[tmp_path])
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _torch_inference_context(self):
        if self.torch is None:
            return _NullContext()
        return self.torch.inference_mode()

    def _parse_diarization(self, result) -> list[dict]:
        if result is None:
            return []
        entries = result
        if isinstance(entries, tuple):
            entries = entries[0]
        if isinstance(entries, dict):
            for key in ('segments', 'predicted_segments', 'diarization'):
                if key in entries:
                    entries = entries[key]
                    break
        if isinstance(entries, list) and len(entries) == 1 and isinstance(entries[0], list):
            entries = entries[0]

        parsed = []
        for item in entries or []:
            segment = self._parse_segment(item)
            if segment is not None:
                parsed.append(segment)
        parsed.sort(key=lambda item: (item['start'], item['end']))
        return parsed

    @staticmethod
    def _parse_segment(item) -> dict | None:
        if isinstance(item, str):
            parts = item.strip().split()
            if len(parts) < 3:
                return None
            try:
                return {
                    'start': float(parts[0]),
                    'end': float(parts[1]),
                    'speaker': str(parts[2]),
                }
            except ValueError:
                return None
        if isinstance(item, dict):
            try:
                return {
                    'start': float(item.get('start', item.get('begin'))),
                    'end': float(item.get('end', item.get('stop'))),
                    'speaker': str(item.get('speaker', item.get('label'))),
                }
            except (TypeError, ValueError):
                return None
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            try:
                return {'start': float(item[0]), 'end': float(item[1]), 'speaker': str(item[2])}
            except (TypeError, ValueError):
                return None
        return None

    def _select_current_speaker(self, segments: list[dict], duration: float) -> str:
        if not segments:
            return ''
        window_start = max(0.0, duration - self.selection_window_seconds)
        scores = {}
        for segment in segments:
            overlap = max(0.0, min(duration, segment['end']) - max(window_start, segment['start']))
            if overlap > 0.0:
                scores[segment['speaker']] = scores.get(segment['speaker'], 0.0) + overlap
        if not scores:
            for segment in segments:
                scores[segment['speaker']] = scores.get(segment['speaker'], 0.0) + max(
                    0.0,
                    segment['end'] - segment['start'],
                )
        if not scores:
            return ''
        return max(scores.items(), key=lambda item: item[1])[0]

    def _match_known_speaker(self, audio: np.ndarray, segments: list[dict], raw_speaker: str) -> str:
        if self.speaker_manager is None:
            return ''
        speaker_audio = self._extract_speaker_audio(audio, segments, raw_speaker)
        duration = len(speaker_audio) / TARGET_SAMPLE_RATE
        if duration < self.identity_min_audio_seconds:
            return ''
        try:
            match = self.speaker_manager.identify_with_details(speaker_audio)
        except Exception as exc:
            self.get_logger().warn(f'Known-speaker matching failed: {exc}')
            return ''
        if match.speaker_name != 'Unknown':
            self.get_logger().info(
                f'NeMo speaker {self._normalize_speaker_label(raw_speaker)} matched '
                f'{match.speaker_name} (score={match.best_score:.3f}, reason={match.reason})'
            )
            return match.speaker_name
        return ''

    def _extract_speaker_audio(
        self,
        audio: np.ndarray,
        segments: list[dict],
        raw_speaker: str,
    ) -> np.ndarray:
        chunks = []
        for segment in segments:
            if segment['speaker'] != raw_speaker:
                continue
            start = max(0, int(segment['start'] * TARGET_SAMPLE_RATE))
            end = min(len(audio), int(segment['end'] * TARGET_SAMPLE_RATE))
            if end > start:
                chunks.append(audio[start:end])
        if not chunks:
            return np.array([], dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False)

    def _normalize_speaker_label(self, raw_speaker: str) -> str:
        raw = (raw_speaker or '').strip()
        match = re.search(r'(\d+)$', raw)
        if match:
            return f'{self.speaker_prefix}{match.group(1)}'
        normalized = re.sub(r'[^A-Za-z0-9_]+', '_', raw).strip('_')
        if not normalized:
            normalized = 'unknown'
        return f'{self.speaker_prefix}{normalized}'

    def _publish_speaker(self, speaker: str):
        speaker = speaker or 'Unknown'
        if speaker == self._last_published_speaker:
            return
        self._last_published_speaker = speaker
        msg = String()
        msg.data = speaker
        self.speaker_pub.publish(msg)
        self.get_logger().info(f'Published /speaker_id from NeMo diarization: {speaker}')

    def _publish_diarization_json(
        self,
        source: str,
        speaker: str,
        raw_speaker: str,
        segments: list[dict],
        audio: np.ndarray,
    ):
        payload = {
            'backend': 'nvidia_nemo_sortformer',
            'mode': self.mode,
            'source': source,
            'speaker': speaker,
            'raw_speaker': raw_speaker,
            'duration': len(audio) / TARGET_SAMPLE_RATE,
            'segments': segments,
        }
        msg = String()
        msg.data = json.dumps(payload)
        self.diarization_pub.publish(msg)

    @staticmethod
    def _audio_msg_to_float32_mono(msg: Audio) -> np.ndarray:
        if not msg.data:
            return np.array([], dtype=np.float32)
        audio = np.asarray(msg.data, dtype=np.int16)
        channels = int(msg.channels or 1)
        if channels > 1:
            usable = (len(audio) // channels) * channels
            audio = audio[:usable].reshape(-1, channels).mean(axis=1).astype(np.int16)
        return audio.astype(np.float32) / 32768.0

    @staticmethod
    def _ensure_16khz(audio: np.ndarray, sample_rate: int) -> np.ndarray:
        if audio.size == 0 or sample_rate == TARGET_SAMPLE_RATE:
            return audio.astype(np.float32, copy=False)
        if SCIPY_AVAILABLE:
            from math import gcd

            factor = gcd(sample_rate, TARGET_SAMPLE_RATE)
            up = TARGET_SAMPLE_RATE // factor
            down = sample_rate // factor
            return resample_poly(audio, up, down).astype(np.float32)

        duration = len(audio) / float(sample_rate)
        target_len = max(1, int(round(duration * TARGET_SAMPLE_RATE)))
        source_x = np.linspace(0.0, duration, num=len(audio), endpoint=False)
        target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
        return np.interp(target_x, source_x, audio).astype(np.float32)


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def main(args=None):
    rclpy.init(args=args)
    node = NemoDiarizationNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
