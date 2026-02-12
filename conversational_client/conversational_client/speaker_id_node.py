#!/usr/bin/env python3
"""
speaker_id_node.py
Identifică vorbitorul (cine vorbește) pe baza segmentelor audio de la VAD.

EXPLICAȚIE:
- Ascultă /audio_segment de la audio_segment_node (audio deja segmentat de VAD)
- Folosește SpeakerManager (de la Developer A) pentru a compara vocea cu baza de date
- Publică numele vorbitorului pe /speaker_id ("Vale", "Delia", "Unknown")

Dacă baza de date de enrollment e goală, publicat mereu "Unknown".
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTURI
# ═══════════════════════════════════════════════════════════════════

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import String
import numpy as np
import os
import sys

# ─────────────────────────────────────────────────────────────────
# Import SpeakerManager de la Developer A
# Calea: ~/voice_ros2/speaker_id/speaker_manager.py
# ─────────────────────────────────────────────────────────────────
SPEAKER_ID_DIR = os.path.expanduser('~/voice_ros2/speaker_id')
sys.path.insert(0, SPEAKER_ID_DIR)

try:
    from speaker_manager import SpeakerManager
    SPEAKER_MANAGER_AVAILABLE = True
except ImportError:
    SPEAKER_MANAGER_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════
# CLASA NODULUI
# ═══════════════════════════════════════════════════════════════════

class SpeakerIdNode(Node):
    """
    Nod ROS2 care identifică vorbitorul pe baza segmentelor audio.

    Funcționare:
    1. Primește segmente audio pe /audio_segment (de la audio_segment_node)
    2. Convertește audio-ul în format compatibil cu SpeechBrain
    3. Apelează speaker_manager.identify() pentru identificare
    4. Publică rezultatul pe /speaker_id
    """

    def __init__(self):
        super().__init__('speaker_id_node')

        # ─────────────────────────────────────────────────────────
        # PARAMETRI
        # ─────────────────────────────────────────────────────────
        self.declare_parameter(
            'enrollment_dir',
            os.path.expanduser('~/voice_ros2/voices/enrollment/')
        )
        self.declare_parameter('similarity_threshold', 0.25)
        self.declare_parameter('sample_rate', 16000)

        self.enrollment_dir = self.get_parameter('enrollment_dir').value
        self.similarity_threshold = self.get_parameter('similarity_threshold').value
        self.sample_rate = self.get_parameter('sample_rate').value

        # ─────────────────────────────────────────────────────────
        # SPEAKER MANAGER (de la Developer A)
        # ─────────────────────────────────────────────────────────
        self.speaker_manager = None
        self.db_loaded = False

        self._init_speaker_manager()

        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER — primește segmente audio de la VAD
        # ─────────────────────────────────────────────────────────
        self.segment_sub = self.create_subscription(
            Audio,
            '/audio_segment',
            self.segment_callback,
            10
        )

        # ─────────────────────────────────────────────────────────
        # PUBLISHER — publică numele vorbitorului
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
    # INIȚIALIZARE SPEAKER MANAGER
    # ═══════════════════════════════════════════════════════════════════

    def _init_speaker_manager(self):
        """Încearcă să inițializeze SpeakerManager de la Developer A."""

        # Verifică dacă modulul e disponibil
        if not SPEAKER_MANAGER_AVAILABLE:
            self.get_logger().warn(
                '⚠️ speaker_manager.py nu a fost găsit în '
                f'{SPEAKER_ID_DIR}. Nodul funcționează în modul "Unknown".'
            )
            return

        # Verifică dacă folderul de enrollment există și are fișiere
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

        # Inițializează SpeakerManager
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
    # CALLBACK — PROCESARE SEGMENT AUDIO
    # ═══════════════════════════════════════════════════════════════════

    def segment_callback(self, msg: Audio):
        """
        Primește un segment audio complet (de la audio_segment_node)
        și identifică vorbitorul.
        """
        if not msg.data:
            return

        # ─────────────────────────────────────────────────────────
        # Convertește int16[] → float32 numpy (normalizat [-1, 1])
        # ─────────────────────────────────────────────────────────
        audio_int16 = np.array(msg.data, dtype=np.int16)
        audio_float = audio_int16.astype(np.float32) / 32768.0
        
        duration = len(audio_float) / msg.sample_rate
        
        # Ignoră segmentele foarte scurte (zgomote, click-uri) pentru identificare
        if duration < 0.8:
            self.get_logger().debug(f'🔇 Segment prea scurt pentru ID ({duration:.2f}s) - Ignorat')
            return

        self.get_logger().debug(
            f'🔊 Segment primit: {duration:.2f}s ({len(audio_float)} samples)'
        )

        # ─────────────────────────────────────────────────────────
        # Identificare vorbitor
        # ─────────────────────────────────────────────────────────
        speaker_name = "Unknown"

        if self.db_loaded and self.speaker_manager is not None:
            try:
                # Identifică vorbitorul
                speaker_name = self.speaker_manager.identify(audio_float)
                
                # LOGGING INTELIGENT:
                # - Afișează INFO doar dacă e cineva cunoscut
                # - Dacă e Unknown, afișează doar DEBUG (ca să nu spammeze consola)
                if speaker_name != "Unknown":
                    self.get_logger().info(f'UNKNOWN -> 🗣️ SPEAKER IDENTIFICAT: {speaker_name}')
                else:
                    self.get_logger().debug(f'👤 Speaker neidentificat (Unknown)')

            except Exception as e:
                self.get_logger().error(f'❌ Eroare la identificare: {e}')
                speaker_name = "Unknown"
        else:
            self.get_logger().debug(
                '⏭️ Fără bază de date — publicăm "Unknown"',
                throttle_duration_sec=10.0
            )

        # ─────────────────────────────────────────────────────────
        # Publică rezultatul
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
