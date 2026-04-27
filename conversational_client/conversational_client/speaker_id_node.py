#!/usr/bin/env python3
"""
speaker_id_node.py
Identifies the speaker (who is talking) based on VAD audio segments.

EXPLANATION:
- Listens to /audio_segment from audio_segment_node (audio already segmented by VAD)
- Uses SpeakerManager (from Developer A) to compare the voice against the database
- Publishes the speaker name on /speaker_id ("Vale", "Delia", "Unknown")

If the enrollment database is empty, it always publishes "Unknown".
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════════════════════

import json
import os
from pathlib import Path
import shutil
import numpy as np
import rclpy
from conversational_interfaces.msg import Audio
from rclpy.node import Node
from std_msgs.msg import String

from .person_profile_utils import (
    build_unique_speaker_label,
    default_preferred_name_for_voice_label,
    migrate_legacy_auto_voice_labels,
)

try:
    from .speaker_manager import SpeakerManager
    SPEAKER_MANAGER_AVAILABLE = True
    _SPEAKER_MANAGER_IMPORT_ERROR = ''
except Exception as exc:
    SPEAKER_MANAGER_AVAILABLE = False
    _SPEAKER_MANAGER_IMPORT_ERROR = str(exc)


def _find_workspace_root() -> Path | None:
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


# ═══════════════════════════════════════════════════════════════════
# NODE CLASS
# ═══════════════════════════════════════════════════════════════════

class SpeakerIdNode(Node):
    """
    ROS2 node that identifies the speaker based on audio segments.

    Operation:
    1. Receives audio segments on /audio_segment (from audio_segment_node)
    2. Converts audio into a SpeechBrain-compatible format
    3. Calls speaker_manager.identify() for identification
    4. Publishes the result on /speaker_id
    """

    def __init__(self):
        super().__init__('speaker_id_node')

        # ─────────────────────────────────────────────────────────
        # PARAMETERS
        # ─────────────────────────────────────────────────────────
        # Canonical enrollment path: <workspace>/voices/enrollment
        workspace_root = _find_workspace_root()
        default_enrollment_dir = os.path.join(
            str(workspace_root) if workspace_root else os.getcwd(),
            'voices',
            'enrollment'
        )
        
        self.declare_parameter('enrollment_dir', default_enrollment_dir)
        self.declare_parameter('similarity_threshold', 0.45)
        self.declare_parameter('similarity_margin', 0.12)
        self.declare_parameter('enrollment_reuse_threshold', 0.58)
        self.declare_parameter('enrollment_reuse_margin', 0.16)
        self.declare_parameter('sample_rate', 16000)

        self.enrollment_dir = self.get_parameter('enrollment_dir').value
        self.similarity_threshold = self.get_parameter('similarity_threshold').value
        self.similarity_margin = self.get_parameter('similarity_margin').value
        self.enrollment_reuse_threshold = self.get_parameter('enrollment_reuse_threshold').value
        self.enrollment_reuse_margin = self.get_parameter('enrollment_reuse_margin').value
        self.sample_rate = self.get_parameter('sample_rate').value
        self.memory_file = os.path.join(
            str(workspace_root) if workspace_root else os.getcwd(),
            'voices',
            'person_memory.json',
        )

        # ─────────────────────────────────────────────────────────
        # SPEAKER MANAGER (from Developer A)
        # ─────────────────────────────────────────────────────────
        self.speaker_manager = None
        self.db_loaded = False

        self._migrate_legacy_auto_enrollment()
        self._init_speaker_manager()

        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER — receives audio segments from VAD
        # ─────────────────────────────────────────────────────────
        self.segment_sub = self.create_subscription(
            Audio,
            '/audio_segment',
            self.segment_callback,
            10
        )
        self.realtime_segment_sub = self.create_subscription(
            Audio,
            '/realtime_user_audio_segment',
            self.segment_callback,
            10,
        )
        self.enrollment_request_sub = self.create_subscription(
            String,
            '/speaker_enrollment_request',
            self._enrollment_request_callback,
            10,
        )

        # ─────────────────────────────────────────────────────────
        # PUBLISHER — publishes speaker name
        # ─────────────────────────────────────────────────────────
        self.speaker_pub = self.create_publisher(String, '/speaker_id', 10)
        self.enrollment_status_pub = self.create_publisher(String, '/speaker_enrollment_status', 10)

        if self.db_loaded:
            self.get_logger().info('🎤 Speaker ID Node started (database loaded)')
        else:
            self.get_logger().warn(
                '🎤 Speaker ID Node started — ⚠️ No enrollment database. '
                'Will publish "Unknown" for all segments.'
            )

    # ═══════════════════════════════════════════════════════════════════
    # SPEAKER MANAGER INITIALIZATION
    # ═══════════════════════════════════════════════════════════════════

    def _init_speaker_manager(self):
        """Try to initialize SpeakerManager from Developer A."""

        # Check if module is available
        if not SPEAKER_MANAGER_AVAILABLE:
            self.get_logger().warn(
                '⚠️ speaker_manager could not be imported. '
                f'Node running in "Unknown" mode. Cause: {_SPEAKER_MANAGER_IMPORT_ERROR}'
            )
            return

        # Check if enrollment folder exists and has files
        if not os.path.isdir(self.enrollment_dir):
            self.get_logger().warn(
                f'⚠️ Enrollment folder does not exist: {self.enrollment_dir}'
            )
            return

        wav_files = [f for f in os.listdir(self.enrollment_dir) if f.endswith('.wav')]
        if not wav_files:
            self.get_logger().warn(
                f'⚠️ Enrollment folder is empty: {self.enrollment_dir}'
            )
            return

        # Initialize SpeakerManager
        try:
            self.speaker_manager = SpeakerManager(
                self.enrollment_dir,
                threshold=self.similarity_threshold,
                min_margin=self.similarity_margin,
            )
            loaded_speakers = self.speaker_manager.get_speakers()
            self.db_loaded = bool(loaded_speakers)
            if self.db_loaded:
                self.get_logger().info(
                    f'✅ Speaker database loaded: {len(loaded_speakers)} voices '
                    f'({", ".join(loaded_speakers)})'
                )
            else:
                self.get_logger().warn(
                    '⚠️ Enrollment files were found, but no speaker embeddings loaded successfully. '
                    'Check the enrollment audio files and dependencies.'
                )
        except Exception as e:
            self.get_logger().error(f'❌ Error initializing SpeakerManager: {e}')

    # ═══════════════════════════════════════════════════════════════════
    # CALLBACK — AUDIO SEGMENT PROCESSING
    # ═══════════════════════════════════════════════════════════════════

    def segment_callback(self, msg: Audio):
        """
        Receives a full audio segment (from audio_segment_node)
        and identifies the speaker.
        """
        if not msg.data:
            return

        # ─────────────────────────────────────────────────────────
        # Convert int16[] → float32 numpy (normalized [-1, 1])
        # ─────────────────────────────────────────────────────────
        audio_int16 = np.array(msg.data, dtype=np.int16)
        audio_float = audio_int16.astype(np.float32) / 32768.0
        
        duration = len(audio_float) / msg.sample_rate
        
        # Ignore very short segments (noise, clicks) for identification
        if duration < 0.8:
            self.get_logger().debug(f'🔇 Segment too short for ID ({duration:.2f}s) - skipped')
            return

        self.get_logger().debug(
            f'🔊 Segment received: {duration:.2f}s ({len(audio_float)} samples)'
        )

        # ─────────────────────────────────────────────────────────
        # Speaker identification
        # ─────────────────────────────────────────────────────────
        speaker_name = "Unknown"

        if self.db_loaded and self.speaker_manager is not None:
            try:
                # Identify the speaker
                match = self.speaker_manager.identify_with_details(audio_float)
                speaker_name = match.speaker_name
                
                # SMART LOGGING:
                # - Show INFO only if someone is known
                # - If Unknown, show only DEBUG (to avoid console spam)
                if speaker_name != "Unknown":
                    self.get_logger().info(
                        f'🗣️ Speaker identified: {speaker_name} '
                        f'(score={match.best_score:.3f}, reason={match.reason})'
                    )
                else:
                    margin = (
                        match.best_score - match.second_best_score
                        if match.second_best_score > -1.0
                        else -1.0
                    )
                    if match.best_name != 'Unknown':
                        self.get_logger().debug(
                            f'👤 Unidentified speaker (reason={match.reason}, '
                            f'top={match.best_name}:{match.best_score:.3f}, '
                            f'margin={margin:.3f})'
                        )
                    else:
                        self.get_logger().debug(
                            f'👤 Unidentified speaker (reason={match.reason})'
                        )

            except Exception as e:
                self.get_logger().error(f'❌ Identification error: {e}')
                speaker_name = "Unknown"
        else:
            self.get_logger().debug(
                '⏭️ No database — publishing "Unknown"',
                throttle_duration_sec=10.0
            )

        # ─────────────────────────────────────────────────────────
        # Publish result
        # ─────────────────────────────────────────────────────────
        result_msg = String()
        result_msg.data = speaker_name
        self.speaker_pub.publish(result_msg)

        if speaker_name != "Unknown":
            self.get_logger().info(
                f'📤 /speaker_id: "{speaker_name}" (segment: {duration:.2f}s)'
            )
        else:
            self.get_logger().debug(
                f'📤 /speaker_id: "Unknown" (segment: {duration:.2f}s)'
            )

    def _enrollment_request_callback(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except Exception as exc:
            self.get_logger().error(f'❌ Invalid enrollment request payload: {exc}')
            return

        request_id = str(payload.get('request_id', '') or '')
        preferred_name = str(payload.get('preferred_name', '') or '').strip()
        preferred_language = str(payload.get('preferred_language', '') or '').strip()
        facts = list(payload.get('facts', []) or [])
        source_wav = str(payload.get('source_wav', '') or '').strip()
        target_voice_label = str(payload.get('target_voice_label', '') or '').strip()

        if not preferred_name:
            self._publish_enrollment_status(
                success=False,
                reason='missing_preferred_name',
                request_id=request_id,
                preferred_name=preferred_name,
                preferred_language=preferred_language,
                facts=facts,
                source_wav=source_wav,
            )
            return

        if not source_wav or not os.path.exists(source_wav):
            self._publish_enrollment_status(
                success=False,
                reason='missing_source_wav',
                request_id=request_id,
                preferred_name=preferred_name,
                preferred_language=preferred_language,
                facts=facts,
                source_wav=source_wav,
            )
            return

        if not SPEAKER_MANAGER_AVAILABLE:
            self._publish_enrollment_status(
                success=False,
                reason=f'speaker_manager_unavailable:{_SPEAKER_MANAGER_IMPORT_ERROR}',
                request_id=request_id,
                preferred_name=preferred_name,
                preferred_language=preferred_language,
                facts=facts,
                source_wav=source_wav,
            )
            return

        try:
            os.makedirs(self.enrollment_dir, exist_ok=True)
            existing_labels = set(self.speaker_manager.get_speakers()) if self.speaker_manager else set()
            existing_labels.update(
                os.path.splitext(name)[0]
                for name in os.listdir(self.enrollment_dir)
                if name.endswith('.wav')
            )
            matched_existing_label = ''
            if not target_voice_label and self.speaker_manager is not None:
                try:
                    matched_existing_label = self.speaker_manager.identify_file(
                        source_wav,
                        threshold=self.enrollment_reuse_threshold,
                        min_margin=self.enrollment_reuse_margin,
                    )
                except Exception as exc:
                    self.get_logger().warning(
                        f'Could not match enrollment audio to existing speaker: {exc}'
                    )
            if matched_existing_label and matched_existing_label != 'Unknown':
                voice_label = matched_existing_label
                self.get_logger().info(
                    f'Reusing existing speaker label {voice_label} for "{preferred_name}"'
                )
            else:
                voice_label = target_voice_label or build_unique_speaker_label(existing_labels)
            target_wav = os.path.join(self.enrollment_dir, f'{voice_label}.wav')
            shutil.copy2(source_wav, target_wav)

            if self.speaker_manager is None:
                self.speaker_manager = SpeakerManager(
                    self.enrollment_dir,
                    threshold=self.similarity_threshold,
                    min_margin=self.similarity_margin,
                )
            else:
                self.speaker_manager.reload()

            loaded_speakers = self.speaker_manager.get_speakers()
            self.db_loaded = bool(loaded_speakers)
            if not self.db_loaded:
                raise RuntimeError('speaker_database_empty_after_reload')

            self.get_logger().info(
                f'✅ Auto-enrolled speaker "{preferred_name}" as {voice_label}'
            )
            result_msg = String()
            result_msg.data = voice_label
            self.speaker_pub.publish(result_msg)
            self._publish_enrollment_status(
                success=True,
                reason='',
                request_id=request_id,
                preferred_name=preferred_name,
                preferred_language=preferred_language,
                facts=facts,
                source_wav=source_wav,
                voice_label=voice_label,
                target_wav=target_wav,
            )
        except Exception as exc:
            self.get_logger().error(f'❌ Failed to auto-enroll speaker "{preferred_name}": {exc}')
            self._publish_enrollment_status(
                success=False,
                reason=str(exc),
                request_id=request_id,
                preferred_name=preferred_name,
                preferred_language=preferred_language,
                facts=facts,
                source_wav=source_wav,
            )
        finally:
            try:
                if source_wav and os.path.exists(source_wav):
                    os.remove(source_wav)
            except Exception:
                pass

    def _publish_enrollment_status(
        self,
        *,
        success: bool,
        reason: str,
        request_id: str,
        preferred_name: str,
        preferred_language: str,
        facts,
        source_wav: str,
        voice_label: str = '',
        target_wav: str = '',
    ):
        payload = {
            'success': bool(success),
            'reason': reason,
            'request_id': request_id,
            'preferred_name': preferred_name or default_preferred_name_for_voice_label(voice_label),
            'preferred_language': preferred_language,
            'facts': list(facts or []),
            'source_wav': source_wav,
            'voice_label': voice_label,
            'target_wav': target_wav,
        }
        out = String()
        out.data = json.dumps(payload, separators=(',', ':'))
        self.enrollment_status_pub.publish(out)

    def _migrate_legacy_auto_enrollment(self):
        try:
            if os.path.exists(self.memory_file):
                with open(self.memory_file, 'r', encoding='utf-8') as handle:
                    memory = json.load(handle)
            else:
                memory = {'people': {}}
        except Exception as exc:
            self.get_logger().warning(f'Failed to read person memory for migration: {exc}')
            memory = {'people': {}}

        normalized_memory, changed, mapping = migrate_legacy_auto_voice_labels(
            memory,
            self.enrollment_dir,
        )
        if not changed:
            return

        memory_dir = os.path.dirname(self.memory_file)
        if memory_dir:
            os.makedirs(memory_dir, exist_ok=True)
        with open(self.memory_file, 'w', encoding='utf-8') as handle:
            json.dump(normalized_memory, handle, indent=2, ensure_ascii=False)

        if mapping:
            self.get_logger().info(
                f'Migrated {len(mapping)} legacy auto-enrolled speaker labels to neutral IDs'
            )


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main(args=None):
    rclpy.init(args=args)
    node = None

    try:
        node = SpeakerIdNode()
        rclpy.spin(node)
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
