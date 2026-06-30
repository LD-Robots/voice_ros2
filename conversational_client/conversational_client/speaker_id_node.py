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
import warnings
from pathlib import Path

# Suppress annoying library warnings (Torch, SpeechBrain, etc.)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
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
    write_speaker_profile_sidecar,
)

import time
import soundfile as sf
import torch

class SpeakerCandidate:
    """Represents a temporary speaker voice candidate for unsupervised enrollment."""

    def __init__(self, candidate_id: str, embedding: torch.Tensor, first_audio: np.ndarray, sample_rate: int):
        self.candidate_id = candidate_id
        self.embedding = embedding  # The initial embedding (torch.Tensor)
        self.audio_segments = [first_audio]  # Stored float32 arrays
        self.hits = 1
        self.last_seen = time.monotonic()

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
        self.declare_parameter('adaptation_enabled', True)
        self.declare_parameter('adaptation_threshold', 0.60)
        self.declare_parameter('adaptation_rate', 0.90)
        self.declare_parameter('unsupervised_enrollment_enabled', True)
        self.declare_parameter('unsupervised_match_threshold', 0.50)
        self.declare_parameter('min_unsupervised_hits', 3)
        self.declare_parameter('unsupervised_candidate_timeout', 600.0)

        self.enrollment_dir = self.get_parameter('enrollment_dir').value
        self.similarity_threshold = self.get_parameter('similarity_threshold').value
        self.similarity_margin = self.get_parameter('similarity_margin').value
        self.enrollment_reuse_threshold = self.get_parameter('enrollment_reuse_threshold').value
        self.enrollment_reuse_margin = self.get_parameter('enrollment_reuse_margin').value
        self.sample_rate = self.get_parameter('sample_rate').value
        self.adaptation_enabled = bool(self.get_parameter('adaptation_enabled').value)
        self.adaptation_threshold = float(self.get_parameter('adaptation_threshold').value)
        self.adaptation_rate = float(self.get_parameter('adaptation_rate').value)
        self.unsupervised_enrollment_enabled = bool(self.get_parameter('unsupervised_enrollment_enabled').value)
        self.unsupervised_match_threshold = float(self.get_parameter('unsupervised_match_threshold').value)
        self.min_unsupervised_hits = int(self.get_parameter('min_unsupervised_hits').value)
        self.unsupervised_candidate_timeout = float(self.get_parameter('unsupervised_candidate_timeout').value)
        
        self.candidates = {}
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
            try:
                os.makedirs(self.enrollment_dir, exist_ok=True)
            except Exception as e:
                self.get_logger().error(f'❌ Failed to create enrollment dir: {e}')
                return

        wav_files = [f for f in os.listdir(self.enrollment_dir) if f.endswith('.wav')]
        if not wav_files:
            self.get_logger().warn(
                f'⚠️ Enrollment folder is empty: {self.enrollment_dir}. '
                'Initializing empty SpeakerManager for dynamic/unsupervised enrollment.'
            )
            try:
                self.speaker_manager = SpeakerManager(
                    self.enrollment_dir,
                    threshold=self.similarity_threshold,
                    min_margin=self.similarity_margin,
                )
                self.db_loaded = False
            except Exception as e:
                self.get_logger().error(f'❌ Error initializing SpeakerManager: {e}')
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
        
        # Ignore very short segments (noise, clicks) for identification.
        # Reduced threshold to 0.5s to allow processing early segments for low-latency backends like Gemini Live.
        if duration < 0.5:
            self.get_logger().debug(f'🔇 Segment too short for ID ({duration:.2f}s) - skipped')
            return

        self.get_logger().debug(
            f'🔊 Segment received: {duration:.2f}s ({len(audio_float)} samples)'
        )

        # ─────────────────────────────────────────────────────────
        # Speaker identification
        # ─────────────────────────────────────────────────────────
        speaker_name = "Unknown"
        new_embedding = None

        if self.speaker_manager is not None:
            try:
                # Identify the speaker (optionally retrieval of embedding for adaptation)
                if self.db_loaded:
                    if self.adaptation_enabled or self.unsupervised_enrollment_enabled:
                        match, new_embedding = self.speaker_manager.identify_and_get_embedding(audio_float)
                    else:
                        match = self.speaker_manager.identify_with_details(audio_float)
                        new_embedding = None
                    speaker_name = match.speaker_name

                    # Adapt speaker template if confidence is high
                    if (self.adaptation_enabled and speaker_name != "Unknown" and
                            match.best_score >= self.adaptation_threshold and new_embedding is not None):
                        if self.speaker_manager.adapt_speaker(speaker_name, new_embedding, self.adaptation_rate):
                            self.get_logger().info(
                                f'🔄 Adapted voice template for {speaker_name} '
                                f'(score={match.best_score:.3f}, rate={self.adaptation_rate:.2f})'
                            )
                else:
                    # Database is empty. Compute embedding for candidate tracking
                    if self.unsupervised_enrollment_enabled:
                        new_embedding = self.speaker_manager._compute_embedding_from_array(audio_float)
                    match = None

                # Perform dynamic unsupervised enrollment if enabled and speaker is Unknown
                # Skip candidate tracking if the speaker is Unknown only due to margin conflict
                if (self.unsupervised_enrollment_enabled and speaker_name == "Unknown" and new_embedding is not None
                        and (match is None or match.reason != 'margin_too_small')):
                    # Ignore short noise segments
                    if duration >= 1.0:
                        promoted_label = self._process_unsupervised_candidate(
                            audio_float, new_embedding, msg.sample_rate
                        )
                        if promoted_label:
                            speaker_name = promoted_label

                # SMART LOGGING:
                # - Show INFO only if someone is known
                # - If Unknown, show only DEBUG (to avoid console spam)
                if speaker_name != "Unknown" and match is not None:
                    self.get_logger().info(
                        f'🗣️ Speaker identified: {speaker_name} '
                        f'(score={match.best_score:.3f}, reason={match.reason})'
                    )
                elif match is not None:
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
                self.get_logger().error(f'❌ Identification/unsupervised error: {e}')
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

            target_emb = os.path.join(self.enrollment_dir, f'{voice_label}.emb.npy')
            if os.path.exists(target_emb):
                try:
                    os.remove(target_emb)
                except Exception as exc:
                    self.get_logger().warning(f"Could not remove old cached embedding: {exc}")

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

    def _process_unsupervised_candidate(self, audio_float, new_embedding, sample_rate) -> str | None:
        # First, prune expired candidates to avoid memory leaks
        now = time.monotonic()
        expired = [
            cid for cid, cand in self.candidates.items()
            if (now - cand.last_seen) > self.unsupervised_candidate_timeout
        ]
        for cid in expired:
            del self.candidates[cid]

        # Match against active candidates
        best_candidate_id = None
        best_candidate_score = -1.0
        
        for cid, candidate in self.candidates.items():
            score = self.speaker_manager._cosine_similarity(new_embedding, candidate.embedding)
            if score > best_candidate_score:
                best_candidate_score = score
                best_candidate_id = cid

        if best_candidate_id is not None and best_candidate_score >= self.unsupervised_match_threshold:
            # Match found: update candidate
            candidate = self.candidates[best_candidate_id]
            candidate.hits += 1
            candidate.last_seen = now
            # Update embedding with moving average
            candidate.embedding = 0.8 * candidate.embedding + 0.2 * new_embedding
            candidate.embedding = candidate.embedding / torch.norm(candidate.embedding)
            
            # Save audio segment (limit to last 5 segments)
            candidate.audio_segments.append(audio_float)
            if len(candidate.audio_segments) > 5:
                candidate.audio_segments.pop(0)

            self.get_logger().info(
                f"👤 Unsupervised candidate {best_candidate_id} matched: "
                f"hits={candidate.hits}, score={best_candidate_score:.3f}"
            )

            if candidate.hits >= self.min_unsupervised_hits:
                return self._promote_candidate(candidate, sample_rate)
        else:
            # No match: create new candidate
            candidate_id = f"candidate_{len(self.candidates) + 1:03d}"
            # Ensure unique ID if somehow collision happens (e.g. after pruning)
            while candidate_id in self.candidates:
                candidate_id = f"candidate_{int(time.time() * 1000) % 1000:03d}"
                
            self.candidates[candidate_id] = SpeakerCandidate(
                candidate_id, new_embedding, audio_float, sample_rate
            )
            self.get_logger().info(
                f"👤 Created new unsupervised candidate {candidate_id} from unknown voice"
            )
        
        return None

    def _promote_candidate(self, candidate, sample_rate) -> str:
        # 1. Build unique speaker label
        existing_labels = set(self.speaker_manager.get_speakers()) if self.speaker_manager else set()
        if os.path.isdir(self.enrollment_dir):
            existing_labels.update(
                os.path.splitext(name)[0]
                for name in os.listdir(self.enrollment_dir)
                if name.endswith('.wav')
            )
        voice_label = build_unique_speaker_label(existing_labels)

        # 2. Build and save the wav file
        os.makedirs(self.enrollment_dir, exist_ok=True)
        target_wav = os.path.join(self.enrollment_dir, f"{voice_label}.wav")
        
        combined_audio = np.concatenate(candidate.audio_segments)
        # Limit total duration to 15 seconds
        max_samples = 15 * sample_rate
        if len(combined_audio) > max_samples:
            combined_audio = combined_audio[-max_samples:]
            
        # Convert float32 back to int16
        audio_int16 = (combined_audio * 32768.0).astype(np.int16)
        try:
            sf.write(target_wav, audio_int16, sample_rate, subtype='PCM_16')
        except Exception as e:
            self.get_logger().error(f"❌ Failed to write wav file during promotion: {e}")

        # 3. Save .emb.npy cache
        target_emb = os.path.join(self.enrollment_dir, f"{voice_label}.emb.npy")
        try:
            np.save(target_emb, candidate.embedding.detach().cpu().numpy())
        except Exception as e:
            self.get_logger().error(f"❌ Failed to write emb.npy file during promotion: {e}")

        # 4. Save sidecar .profile.json
        try:
            write_speaker_profile_sidecar(
                self.enrollment_dir,
                voice_label,
                {
                    'preferred_name': '',
                    'preferred_language': '',
                    'facts': [],
                    'last_seen': ''
                }
            )
        except Exception as e:
            self.get_logger().error(f"❌ Failed to write profile sidecar during promotion: {e}")

        # 5. Reload SpeakerManager
        if self.speaker_manager is None:
            self.speaker_manager = SpeakerManager(
                self.enrollment_dir,
                threshold=self.similarity_threshold,
                min_margin=self.similarity_margin,
            )
        else:
            self.speaker_manager.reload()
        self.db_loaded = True

        # 6. Publish enrollment status to notify memory store (to register in person_memory.json)
        self._publish_enrollment_status(
            success=True,
            reason='',
            request_id=f"unsupervised_{int(time.time())}",
            preferred_name='',
            preferred_language='',
            facts=[],
            source_wav='',
            voice_label=voice_label,
            target_wav=target_wav,
        )

        # Remove candidate from candidates
        if candidate.candidate_id in self.candidates:
            del self.candidates[candidate.candidate_id]

        self.get_logger().info(
            f"🎉 Promoted unsupervised candidate {candidate.candidate_id} "
            f"to registered speaker: {voice_label}!"
        )
        return voice_label


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
