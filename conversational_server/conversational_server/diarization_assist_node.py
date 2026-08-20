#!/usr/bin/env python3
"""
OpenAI diarization assist node.

This node is intentionally an assist layer, not the primary speaker-id path. It
uses known enrollment clips to ask OpenAI's diarization transcription model for
a speaker candidate when the local speaker-id pipeline reports Unknown.
"""
import base64
import io
import json
import mimetypes
import os
import threading
import time
import wave
from pathlib import Path

import numpy as np
import requests

try:
    import rclpy
    from rclpy.node import Node
    from conversational_interfaces.msg import Audio
    from std_msgs.msg import String
    RCLPY_AVAILABLE = True
except ImportError:
    rclpy = None
    Node = object
    Audio = object
    String = object
    RCLPY_AVAILABLE = False

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False


OPENAI_TRANSCRIPTIONS_URL = 'https://api.openai.com/v1/audio/transcriptions'


def _find_workspace_root() -> Path | None:
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


def pcm16_to_wav_bytes(samples, sample_rate: int, channels: int = 1) -> bytes:
    pcm = np.array(samples or [], dtype=np.int16)
    channels = max(1, int(channels or 1))
    sample_rate = max(1, int(sample_rate or 16000))

    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


def _trim_wav_file(path: Path, max_seconds: float) -> bytes:
    with wave.open(str(path), 'rb') as source:
        params = source.getparams()
        frames_to_read = source.getnframes()
        if max_seconds > 0:
            frames_to_read = min(frames_to_read, int(source.getframerate() * max_seconds))
        frames = source.readframes(max(0, frames_to_read))

    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setparams(params)
        out.writeframes(frames)
    return buffer.getvalue()


def reference_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), 'rb') as wav:
            rate = float(wav.getframerate() or 0)
            if rate <= 0:
                return 0.0
            return float(wav.getnframes()) / rate
    except Exception:
        return 0.0


def file_to_data_url(path: Path, *, max_seconds: float = 8.0) -> str:
    mime = mimetypes.guess_type(str(path))[0] or 'audio/wav'
    if path.suffix.lower() == '.wav':
        try:
            data = _trim_wav_file(path, max_seconds)
            mime = 'audio/wav'
        except Exception:
            data = path.read_bytes()
    else:
        data = path.read_bytes()
    encoded = base64.b64encode(data).decode('ascii')
    return f'data:{mime};base64,{encoded}'


def load_known_speaker_references(
    enrollment_dir: str,
    *,
    max_speakers: int = 4,
    min_seconds: float = 1.5,
    max_seconds: float = 8.0,
) -> list[tuple[str, str]]:
    root = Path(enrollment_dir).expanduser()
    if not root.is_dir():
        return []

    references: list[tuple[str, str]] = []
    for path in sorted(root.glob('*.wav')):
        if len(references) >= max(0, int(max_speakers)):
            break
        duration = reference_duration_seconds(path)
        if duration and duration < max(0.0, float(min_seconds)):
            continue
        label = path.stem.strip()
        if not label:
            continue
        references.append((label, file_to_data_url(path, max_seconds=max_seconds)))
    return references


def _segment_duration(segment: dict) -> float:
    try:
        start = float(segment.get('start', 0.0) or 0.0)
        end = float(segment.get('end', start) or start)
        return max(0.0, end - start)
    except Exception:
        return 0.0


def dominant_speaker_from_diarized_payload(payload: dict) -> tuple[str, float, list[dict]]:
    segments = payload.get('segments', []) if isinstance(payload, dict) else []
    if not isinstance(segments, list):
        return 'Unknown', 0.0, []

    totals: dict[str, float] = {}
    fallback_counts: dict[str, float] = {}
    clean_segments: list[dict] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        speaker = str(segment.get('speaker', '') or '').strip()
        if not speaker:
            continue
        duration = _segment_duration(segment)
        totals[speaker] = totals.get(speaker, 0.0) + duration
        fallback_counts[speaker] = fallback_counts.get(speaker, 0.0) + 1.0
        clean_segments.append({
            'speaker': speaker,
            'start': segment.get('start', 0.0),
            'end': segment.get('end', 0.0),
            'text': str(segment.get('text', '') or '')[:160],
        })

    if not totals:
        return 'Unknown', 0.0, clean_segments

    total_duration = sum(totals.values())
    if total_duration <= 0.0:
        speaker, count = max(fallback_counts.items(), key=lambda item: item[1])
        return speaker, count / max(1.0, sum(fallback_counts.values())), clean_segments

    speaker, duration = max(totals.items(), key=lambda item: item[1])
    return speaker, duration / total_duration, clean_segments


class DiarizationAssistNode(Node):
    def __init__(self):
        if not RCLPY_AVAILABLE:
            raise RuntimeError('rclpy is required to run DiarizationAssistNode')
        super().__init__('diarization_assist_node')

        if DOTENV_AVAILABLE:
            workspace_root = _find_workspace_root()
            env_path = workspace_root / '.env' if workspace_root else None
            if env_path and env_path.exists():
                load_dotenv(dotenv_path=env_path)

        workspace_root = _find_workspace_root()
        default_enrollment_dir = (
            workspace_root / 'voices' / 'enrollment'
            if workspace_root
            else Path.cwd() / 'voices' / 'enrollment'
        )

        self.declare_parameter('enabled', True)
        self.declare_parameter('model', 'gpt-4o-transcribe-diarize')
        self.declare_parameter('enrollment_dir', str(default_enrollment_dir))
        self.declare_parameter('min_segment_seconds', 1.0)
        self.declare_parameter('max_segment_seconds', 24.0)
        self.declare_parameter('only_when_unknown', True)
        self.declare_parameter('local_speaker_grace_s', 0.35)
        self.declare_parameter('cooldown_s', 1.5)
        self.declare_parameter('timeout_s', 12.0)
        self.declare_parameter('max_reference_speakers', 4)
        self.declare_parameter('min_reference_seconds', 1.5)
        self.declare_parameter('reference_seconds', 8.0)
        self.declare_parameter('min_dominant_share', 0.55)

        self.enabled = bool(self.get_parameter('enabled').value)
        self.model = str(self.get_parameter('model').value)
        self.enrollment_dir = str(self.get_parameter('enrollment_dir').value)
        self.min_segment_seconds = float(self.get_parameter('min_segment_seconds').value)
        self.max_segment_seconds = float(self.get_parameter('max_segment_seconds').value)
        self.only_when_unknown = bool(self.get_parameter('only_when_unknown').value)
        self.local_speaker_grace_s = max(
            0.0,
            float(self.get_parameter('local_speaker_grace_s').value),
        )
        self.cooldown_s = max(0.0, float(self.get_parameter('cooldown_s').value))
        self.timeout_s = max(1.0, float(self.get_parameter('timeout_s').value))
        self.max_reference_speakers = max(
            0,
            int(self.get_parameter('max_reference_speakers').value),
        )
        self.min_reference_seconds = max(
            0.0,
            float(self.get_parameter('min_reference_seconds').value),
        )
        self.reference_seconds = max(
            0.1,
            float(self.get_parameter('reference_seconds').value),
        )
        self.min_dominant_share = max(
            0.0,
            min(1.0, float(self.get_parameter('min_dominant_share').value)),
        )

        self.api_key = os.environ.get('OPENAI_API_KEY', '').strip()
        self.current_local_speaker = 'Unknown'
        self.last_local_speaker_at = 0.0
        self.last_request_at = 0.0
        self._busy_lock = threading.Lock()
        self._busy = False

        self.candidate_pub = self.create_publisher(String, 'speaker_id_candidate', 10)
        self.speaker_sub = self.create_subscription(
            String,
            'speaker_id',
            self._speaker_callback,
            10,
        )
        self.audio_sub = self.create_subscription(
            Audio,
            'realtime_user_audio_segment',
            self._audio_segment_callback,
            10,
        )

        if not self.enabled:
            self.get_logger().info('Diarization assist node disabled by config.')
        elif not self.api_key:
            self.get_logger().warn(
                'Diarization assist enabled but OPENAI_API_KEY is not set; node will skip requests.'
            )
        else:
            self.get_logger().info(
                f'Diarization assist ready with model={self.model}, enrollment_dir={self.enrollment_dir}'
            )

    def _speaker_callback(self, msg: String):
        speaker = msg.data.strip() or 'Unknown'
        self.current_local_speaker = speaker
        self.last_local_speaker_at = time.monotonic()

    def _audio_segment_callback(self, msg: Audio):
        if not self.enabled or not self.api_key or not msg.data:
            return

        sample_rate = int(msg.sample_rate or 16000)
        channels = max(1, int(msg.channels or 1))
        duration_s = len(msg.data) / float(sample_rate * channels)
        if duration_s < self.min_segment_seconds or duration_s > self.max_segment_seconds:
            return

        now = time.monotonic()
        if self.cooldown_s > 0.0 and (now - self.last_request_at) < self.cooldown_s:
            return

        with self._busy_lock:
            if self._busy:
                return
            self._busy = True
            self.last_request_at = now

        thread = threading.Thread(
            target=self._process_segment_after_grace,
            args=(list(msg.data), sample_rate, channels, duration_s),
            daemon=True,
            name='diarization-assist-request',
        )
        thread.start()

    def _process_segment_after_grace(
        self,
        samples: list[int],
        sample_rate: int,
        channels: int,
        duration_s: float,
    ):
        try:
            if self.local_speaker_grace_s > 0.0:
                time.sleep(self.local_speaker_grace_s)

            if self.only_when_unknown and self.current_local_speaker != 'Unknown':
                return

            references = load_known_speaker_references(
                self.enrollment_dir,
                max_speakers=self.max_reference_speakers,
                min_seconds=self.min_reference_seconds,
                max_seconds=self.reference_seconds,
            )
            if not references:
                self.get_logger().debug(
                    'Diarization assist skipped: no usable enrollment references.',
                    throttle_duration_sec=10.0,
                )
                return

            wav_bytes = pcm16_to_wav_bytes(samples, sample_rate, channels)
            payload = self._call_openai_diarization(wav_bytes, references)
            speaker, share, segments = dominant_speaker_from_diarized_payload(payload)
            known_names = {name for name, _ in references}
            if speaker not in known_names or share < self.min_dominant_share:
                self.get_logger().debug(
                    f'Diarization assist no confident known speaker: speaker={speaker}, share={share:.2f}'
                )
                return

            self._publish_candidate(
                speaker=speaker,
                confidence=share,
                duration_s=duration_s,
                segments=segments,
            )
        except Exception as exc:
            self.get_logger().warn(f'Diarization assist failed: {exc}')
        finally:
            with self._busy_lock:
                self._busy = False

    def _call_openai_diarization(
        self,
        wav_bytes: bytes,
        references: list[tuple[str, str]],
    ) -> dict:
        data = [
            ('model', self.model),
            ('response_format', 'diarized_json'),
            ('chunking_strategy', 'auto'),
        ]
        for name, data_url in references:
            data.append(('known_speaker_names[]', name))
            data.append(('known_speaker_references[]', data_url))

        response = requests.post(
            OPENAI_TRANSCRIPTIONS_URL,
            headers={'Authorization': f'Bearer {self.api_key}'},
            data=data,
            files={'file': ('utterance.wav', wav_bytes, 'audio/wav')},
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise ValueError('OpenAI diarization response was not a JSON object.')
        return parsed

    def _publish_candidate(
        self,
        *,
        speaker: str,
        confidence: float,
        duration_s: float,
        segments: list[dict],
    ):
        out = String()
        out.data = json.dumps(
            {
                'source': 'openai_diarization',
                'speaker': speaker,
                'confidence': round(float(confidence), 3),
                'segment_seconds': round(float(duration_s), 3),
                'segments': segments[:6],
                'created_at': time.time(),
            },
            separators=(',', ':'),
        )
        self.candidate_pub.publish(out)
        self.get_logger().info(
            f'Diarization assist candidate: {speaker} (confidence={confidence:.2f})'
        )


def main(args=None):
    if not RCLPY_AVAILABLE:
        raise RuntimeError('rclpy is required to run DiarizationAssistNode')
    rclpy.init(args=args)
    node = DiarizationAssistNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
