#!/usr/bin/env python3
"""
Person Memory Store Node.

Keeps a JSON-backed store of per-speaker names, preferences, and simple facts.
"""
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rclpy
import soundfile as sf
from rclpy.node import Node
from conversational_interfaces.msg import Audio, Transcription
from std_msgs.msg import String

from .conversation_utils import normalize_text
from .conversation_utils import StickySpeakerTracker
from .person_profile_utils import (
    default_preferred_name_for_voice_label,
    extract_fact,
    extract_language_preference,
    extract_preferred_name,
    migrate_legacy_auto_voice_labels,
    normalize_person_record,
    resolve_preferred_name_update,
    write_speaker_profile_sidecar,
)


def _find_workspace_root() -> Path | None:
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


class PersonMemoryStoreNode(Node):
    def __init__(self):
        super().__init__('person_memory_store_node')

        workspace_root = _find_workspace_root()
        default_memory_path = os.path.join(
            str(workspace_root) if workspace_root else os.getcwd(),
            'voices',
            'person_memory.json',
        )

        self.declare_parameter('memory_file', default_memory_path)
        self.declare_parameter('max_facts_per_person', 20)
        self.declare_parameter('sticky_speaker_timeout_s', 60.0)
        self.declare_parameter('speaker_switch_hits_required', 2)
        self.declare_parameter('auto_enroll_unknown_speakers', True)
        self.declare_parameter('max_enrollment_segment_age_s', 8.0)
        self.declare_parameter('min_enrollment_segment_seconds', 1.2)

        self.memory_file = str(self.get_parameter('memory_file').value)
        self.max_facts_per_person = int(self.get_parameter('max_facts_per_person').value)
        self.speaker_tracker = StickySpeakerTracker(
            float(self.get_parameter('sticky_speaker_timeout_s').value),
            int(self.get_parameter('speaker_switch_hits_required').value),
        )
        self.auto_enroll_unknown_speakers = bool(
            self.get_parameter('auto_enroll_unknown_speakers').value
        )
        self.max_enrollment_segment_age_s = float(
            self.get_parameter('max_enrollment_segment_age_s').value
        )
        self.min_enrollment_segment_seconds = float(
            self.get_parameter('min_enrollment_segment_seconds').value
        )
        self.pending_enrollment_dir = os.path.join(
            str(workspace_root) if workspace_root else os.getcwd(),
            'voices',
            'pending_enrollment',
        )
        self.enrollment_dir = os.path.join(
            str(workspace_root) if workspace_root else os.getcwd(),
            'voices',
            'enrollment',
        )

        self.current_speaker = 'Unknown'
        self.last_raw_speaker = 'Unknown'
        self.memory = self._load_memory()
        self.latest_segment = None
        self.pending_enrollment_paths = set()

        self.speaker_sub = self.create_subscription(String, '/speaker_id', self._speaker_callback, 10)
        self.audio_segment_sub = self.create_subscription(
            Audio,
            '/audio_segment',
            self._remember_latest_segment,
            10,
        )
        self.realtime_audio_segment_sub = self.create_subscription(
            Audio,
            '/realtime_user_audio_segment',
            self._remember_latest_segment,
            10,
        )
        self.transcription_sub = self.create_subscription(
            Transcription,
            '/attended_transcription',
            self._transcription_callback,
            10,
        )
        self.enrollment_status_sub = self.create_subscription(
            String,
            '/speaker_enrollment_status',
            self._enrollment_status_callback,
            10,
        )
        self.context_pub = self.create_publisher(String, '/person_context', 10)
        self.enrollment_request_pub = self.create_publisher(String, '/speaker_enrollment_request', 10)

        self.get_logger().info(f'Person Memory Store started: {self.memory_file}')

    def _speaker_callback(self, msg: String):
        self.last_raw_speaker = msg.data.strip() or 'Unknown'
        speaker = self.speaker_tracker.update(self.last_raw_speaker)
        self.current_speaker = speaker
        if speaker == 'Unknown':
            self._publish_context()
            return
        self._touch_person(speaker)
        self._publish_context()

    def _remember_latest_segment(self, msg: Audio):
        if not msg.data:
            return
        self.latest_segment = {
            'data': list(msg.data),
            'sample_rate': int(msg.sample_rate),
            'channels': int(msg.channels or 1),
            'captured_at': time.monotonic(),
        }

    def _transcription_callback(self, msg: Transcription):
        normalized = normalize_text(msg.text)
        if not normalized:
            return

        preferred_name = self._extract_preferred_name(normalized)
        preferred_language = self._extract_language_preference(normalized)
        fact = self._extract_fact(normalized)

        should_attempt_enrollment = bool(preferred_name) and self.last_raw_speaker == 'Unknown'

        if should_attempt_enrollment:
            if preferred_name and self.auto_enroll_unknown_speakers:
                self._request_speaker_enrollment(preferred_name, preferred_language, fact)
            return

        if self.current_speaker == 'Unknown':
            return

        record = self._touch_person(self.current_speaker)
        updated = False

        if preferred_name:
            action, existing_name, introduced_name = resolve_preferred_name_update(
                str(record.get('preferred_name', '') or ''),
                preferred_name,
            )
            if action == 'set':
                record['preferred_name'] = introduced_name
                updated = True
            elif action == 'conflict':
                self.get_logger().warning(
                    f'Ignoring introduced name "{introduced_name}" for speaker={self.current_speaker}; '
                    f'profile already stores "{existing_name}"'
                )

        if preferred_language and record.get('preferred_language') != preferred_language:
            record['preferred_language'] = preferred_language
            updated = True

        if fact:
            facts = list(record.get('facts', []))
            if fact not in facts:
                facts.append(fact)
                record['facts'] = facts[-self.max_facts_per_person:]
                updated = True

        if updated:
            self._save_memory()
            self.get_logger().info(f'Updated memory for speaker={self.current_speaker}')
            self._publish_context()

    def _request_speaker_enrollment(self, preferred_name: str, preferred_language: str, fact: str):
        if self.latest_segment is None:
            self.get_logger().warning(
                f'Cannot enroll "{preferred_name}" yet: no recent audio segment available'
            )
            return

        segment_age_s = time.monotonic() - float(self.latest_segment.get('captured_at', 0.0))
        if segment_age_s > self.max_enrollment_segment_age_s:
            self.get_logger().warning(
                f'Cannot enroll "{preferred_name}" yet: latest segment is too old ({segment_age_s:.1f}s)'
            )
            return

        duration_s = len(self.latest_segment['data']) / max(1, int(self.latest_segment['sample_rate']))
        if duration_s < self.min_enrollment_segment_seconds:
            self.get_logger().warning(
                f'Cannot enroll "{preferred_name}" yet: latest segment is too short ({duration_s:.2f}s)'
            )
            return

        os.makedirs(self.pending_enrollment_dir, exist_ok=True)
        request_id = str(uuid.uuid4())
        source_wav = os.path.join(self.pending_enrollment_dir, f'{request_id}.wav')

        audio = np.array(self.latest_segment['data'], dtype=np.int16)
        sample_rate = int(self.latest_segment['sample_rate'])
        sf.write(source_wav, audio, sample_rate, subtype='PCM_16')
        self.pending_enrollment_paths.add(source_wav)

        payload = {
            'request_id': request_id,
            'preferred_name': preferred_name,
            'preferred_language': preferred_language,
            'facts': [fact] if fact else [],
            'source_wav': source_wav,
        }
        msg = String()
        msg.data = json.dumps(payload, separators=(',', ':'))
        self.enrollment_request_pub.publish(msg)
        self.get_logger().info(
            f'Requested automatic speaker enrollment for "{preferred_name}" from recent audio'
        )

    def _enrollment_status_callback(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except Exception:
            return

        source_wav = str(payload.get('source_wav', '') or '')
        if source_wav:
            self.pending_enrollment_paths.discard(source_wav)

        if not bool(payload.get('success', False)):
            reason = str(payload.get('reason', 'unknown_error') or 'unknown_error')
            preferred_name = str(payload.get('preferred_name', '') or '')
            self.get_logger().warning(
                f'Automatic enrollment failed for "{preferred_name or "unknown"}": {reason}'
            )
            return

        voice_label = str(payload.get('voice_label', '') or '').strip()
        if not voice_label:
            return

        record = self._touch_person(voice_label)
        preferred_name = str(payload.get('preferred_name', '') or '').strip()
        preferred_language = str(payload.get('preferred_language', '') or '').strip()
        facts = list(payload.get('facts', []) or [])

        if preferred_name:
            record['preferred_name'] = preferred_name
        if preferred_language:
            record['preferred_language'] = preferred_language
        if facts:
            known_facts = list(record.get('facts', []))
            for fact in facts:
                if fact and fact not in known_facts:
                    known_facts.append(fact)
            record['facts'] = known_facts[-self.max_facts_per_person:]

        self._save_memory()
        self.current_speaker = voice_label
        self.get_logger().info(
            f'Automatic enrollment completed for "{preferred_name or voice_label}" as {voice_label}'
        )
        self._publish_context()

    def _touch_person(self, speaker: str):
        people = self.memory.setdefault('people', {})
        record = people.get(speaker)
        if record is None:
            record = normalize_person_record(
                speaker,
                {
                    'preferred_name': default_preferred_name_for_voice_label(speaker),
                    'preferred_language': '',
                    'facts': [],
                    'last_seen': '',
                },
            )
            people[speaker] = record
        else:
            normalized = normalize_person_record(speaker, record)
            if normalized != record:
                record = normalized
                people[speaker] = record
        record['last_seen'] = datetime.now(timezone.utc).isoformat()
        self._save_memory()
        return record

    def _publish_context(self):
        if self.current_speaker == 'Unknown':
            payload = {
                'speaker': 'Unknown',
                'preferred_name': '',
                'preferred_language': '',
                'facts': [],
            }
        else:
            record = self._touch_person(self.current_speaker)
            payload = {
                'speaker': self.current_speaker,
                'preferred_name': record.get('preferred_name', ''),
                'preferred_language': record.get('preferred_language', ''),
                'facts': record.get('facts', []),
            }
        msg = String()
        msg.data = json.dumps(payload, separators=(',', ':'))
        self.context_pub.publish(msg)

    def _load_memory(self):
        try:
            with open(self.memory_file, 'r', encoding='utf-8') as handle:
                memory = json.load(handle)
        except FileNotFoundError:
            memory = {'people': {}}
        except Exception as exc:
            self.get_logger().warning(f'Failed to load person memory: {exc}')
            memory = {'people': {}}

        normalized_memory, changed, mapping = migrate_legacy_auto_voice_labels(
            memory,
            self.enrollment_dir,
        )
        if mapping:
            self.get_logger().info(
                f'Migrated {len(mapping)} legacy auto-enrolled speaker labels to neutral IDs'
            )
        if changed:
            memory_dir = os.path.dirname(self.memory_file)
            if memory_dir:
                os.makedirs(memory_dir, exist_ok=True)
            with open(self.memory_file, 'w', encoding='utf-8') as handle:
                json.dump(normalized_memory, handle, indent=2, ensure_ascii=False)
        self._sync_profile_sidecars(normalized_memory)
        return normalized_memory

    def _save_memory(self):
        memory_dir = os.path.dirname(self.memory_file)
        if memory_dir:
            os.makedirs(memory_dir, exist_ok=True)
        with open(self.memory_file, 'w', encoding='utf-8') as handle:
            json.dump(self.memory, handle, indent=2, ensure_ascii=False)
        self._sync_profile_sidecars(self.memory)

    def _sync_profile_sidecars(self, memory: dict):
        for voice_label, record in (memory.get('people', {}) or {}).items():
            write_speaker_profile_sidecar(self.enrollment_dir, voice_label, record)

    @staticmethod
    def _extract_preferred_name(normalized: str) -> str:
        return extract_preferred_name(normalized)

    @staticmethod
    def _extract_language_preference(normalized: str) -> str:
        return extract_language_preference(normalized)

    @staticmethod
    def _extract_fact(normalized: str) -> str:
        return extract_fact(normalized)


def main(args=None):
    rclpy.init(args=args)
    node = PersonMemoryStoreNode()
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
