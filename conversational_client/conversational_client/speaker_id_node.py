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

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import String
import numpy as np
import os
from pathlib import Path

try:
    from conversational_client.speaker_manager import SpeakerManager
    SPEAKER_MANAGER_AVAILABLE = True
except ImportError:
    SPEAKER_MANAGER_AVAILABLE = False


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
        workspace_root = _find_workspace_root()
        voices_dir = os.path.join(
            str(workspace_root) if workspace_root else os.getcwd(),
            'voices'
        )
        self.declare_parameter(
            'enrollment_dir',
            os.path.join(voices_dir, 'enrollment')
        )
        self.declare_parameter('similarity_threshold', 0.25)
        self.declare_parameter('sample_rate', 16000)

        self.enrollment_dir = self.get_parameter('enrollment_dir').value
        self.similarity_threshold = self.get_parameter('similarity_threshold').value
        self.sample_rate = self.get_parameter('sample_rate').value

        # ─────────────────────────────────────────────────────────
        # SPEAKER MANAGER (from Developer A)
        # ─────────────────────────────────────────────────────────
        self.speaker_manager = None
        self.db_loaded = False

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

        # ─────────────────────────────────────────────────────────
        # PUBLISHER — publishes speaker name
        # ─────────────────────────────────────────────────────────
        self.speaker_pub = self.create_publisher(String, '/speaker_id', 10)

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
                '⚠️ speaker_manager nu a fost importat. '
                'Nodul funcționează în modul "Unknown".'
            )
            return

        # Check if enrollment folder exists and has files
        if not os.path.isdir(self.enrollment_dir):
            self.get_logger().warn(
                f'⚠️ Folderul de enrollment nu există: {self.enrollment_dir}'
            )
            return

        wav_files = [f for f in os.listdir(self.enrollment_dir) if f.endswith('.wav')]
        if not wav_files:
            self.get_logger().warn(
                f'⚠️ Folderul de enrollment e gol: {self.enrollment_dir}'
            )
            return

        # Initialize SpeakerManager
        try:
            self.speaker_manager = SpeakerManager(self.enrollment_dir)
            self.db_loaded = True
            self.get_logger().info(
                f'✅ Speaker database loaded: {len(wav_files)} voci '
                f'({", ".join(f.replace(".wav", "") for f in wav_files)})'
            )
        except Exception as e:
            self.get_logger().error(f'❌ Eroare la inițializarea SpeakerManager: {e}')

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
        self.get_logger().debug(
            f'🔊 Segment primit: {duration:.2f}s ({len(audio_float)} samples)'
        )

        # ─────────────────────────────────────────────────────────
        # Speaker identification
        # ─────────────────────────────────────────────────────────
        speaker_name = "Unknown"

        if self.db_loaded and self.speaker_manager is not None:
            try:
                speaker_name = self.speaker_manager.identify(audio_float)
                self.get_logger().info(f'🗣️ Speaker identificat: {speaker_name}')
            except Exception as e:
                self.get_logger().error(f'❌ Eroare la identificare: {e}')
                speaker_name = "Unknown"
        else:
            self.get_logger().debug(
                '⏭️ Fără bază de date — publicăm "Unknown"',
                throttle_duration_sec=10.0
            )

        # ─────────────────────────────────────────────────────────
        # Publish result
        # ─────────────────────────────────────────────────────────
        result_msg = String()
        result_msg.data = speaker_name
        self.speaker_pub.publish(result_msg)

        self.get_logger().info(
            f'📤 /speaker_id: "{speaker_name}" (segment: {duration:.2f}s)'
        )


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main(args=None):
    rclpy.init(args=args)
    node = SpeakerIdNode()

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
