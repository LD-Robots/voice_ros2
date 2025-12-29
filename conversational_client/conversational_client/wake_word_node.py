#!/usr/bin/env python3
"""
wake_word_node.py
Detectează cuvântul de trezire "hello robot" și activează sesiunea.

EXPLICAȚIE:
- Acest nod ASCULTĂ pe topic /audio_raw (audio de la microfon)
- Când detectează "hello robot", PUBLICĂ pe /wake_detected
- Serverul sau alte noduri pot apoi să știe că sesiunea e activă
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTURI
# ═══════════════════════════════════════════════════════════════════

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import Bool, String
import numpy as np

# Încercăm să importăm OpenWakeWord
try:
    from openwakeword.model import Model as OWWModel
    OPENWAKEWORD_AVAILABLE = True
except ImportError:
    OPENWAKEWORD_AVAILABLE = False
    print("⚠️ OpenWakeWord not installed. Run: pip install openwakeword")


# ═══════════════════════════════════════════════════════════════════
# CLASA NODULUI
# ═══════════════════════════════════════════════════════════════════

class WakeWordNode(Node):
    """
    Nod ROS2 care detectează wake word "hello robot".
    
    Funcționare:
    1. Primește audio pe /audio_raw (de la audio_capture_node)
    2. Procesează cu OpenWakeWord
    3. Când detectează "hello robot", publică True pe /wake_detected
    """
    
    def __init__(self):
        super().__init__('wake_word_node')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETRI
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('threshold', 0.5)     # Prag de detecție (0.0 - 1.0)
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('wake_phrase', 'hey_jarvis')  # Modelul OpenWakeWord
        
        self.threshold = self.get_parameter('threshold').value
        self.sample_rate = self.get_parameter('sample_rate').value
        self.wake_phrase = self.get_parameter('wake_phrase').value
        
        # ─────────────────────────────────────────────────────────
        # STARE
        # ─────────────────────────────────────────────────────────
        self.session_active = False  # True când sesiunea e activă
        self.audio_buffer = []       # Buffer pentru acumulare audio
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER - primim audio de la microfon
        # ─────────────────────────────────────────────────────────
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHERS - publicăm când detectăm wake word
        # ─────────────────────────────────────────────────────────
        self.wake_pub = self.create_publisher(Bool, '/wake_detected', 10)
        self.session_pub = self.create_publisher(Bool, '/session_active', 10)
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER pentru oprirea sesiunii (goodbye robot)
        # ─────────────────────────────────────────────────────────
        self.end_session_sub = self.create_subscription(
            Bool,
            '/end_session',
            self.end_session_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # INIȚIALIZARE OPENWAKEWORD
        # ─────────────────────────────────────────────────────────
        self.oww_model = None
        if OPENWAKEWORD_AVAILABLE:
            try:
                # Încarcă modelul pre-antrenat
                self.oww_model = OWWModel(
                    wakeword_models=[self.wake_phrase],
                    inference_framework='onnx'
                )
                self.get_logger().info(
                    f'🔔 Wake Word Node started - listening for "{self.wake_phrase}" '
                    f'(threshold={self.threshold})'
                )
            except Exception as e:
                self.get_logger().error(f'❌ Failed to load OpenWakeWord: {e}')
                self.oww_model = None
        else:
            self.get_logger().warn('⚠️ OpenWakeWord not available - using dummy mode')
            self.get_logger().info('🔔 Wake Word Node started (dummy mode)')
    
    # ═══════════════════════════════════════════════════════════════════
    # CALLBACK AUDIO - procesează fiecare chunk de audio
    # ═══════════════════════════════════════════════════════════════════
    def audio_callback(self, msg: Audio):
        """Procesează audio pentru detectare wake word."""
        
        # Convertește la numpy array
        audio = np.array(msg.data, dtype=np.int16)
        
        if self.oww_model is not None:
            # ─────────────────────────────────────────────────────
            # PROCESARE CU OPENWAKEWORD
            # ─────────────────────────────────────────────────────
            # OpenWakeWord așteaptă audio normalizat float32
            audio_float = audio.astype(np.float32) / 32768.0
            
            # Procesează chunk-ul
            prediction = self.oww_model.predict(audio_float)
            
            # Verifică scorul pentru wake phrase
            scores = prediction.get(self.wake_phrase, {})
            score = max(scores.values()) if scores else 0.0
            
            if score >= self.threshold and not self.session_active:
                self._activate_session(score)
        else:
            # ─────────────────────────────────────────────────────
            # DUMMY MODE - simulăm detecție la fiecare 10 secunde (pentru test)
            # ─────────────────────────────────────────────────────
            self.audio_buffer.append(audio)
            # În mod real, aici ai pune logica de detecție
            pass
    
    # ═══════════════════════════════════════════════════════════════════
    # ACTIVARE SESIUNE
    # ═══════════════════════════════════════════════════════════════════
    def _activate_session(self, score: float):
        """Activează sesiunea când detectăm wake word."""
        self.session_active = True
        
        self.get_logger().info(f'🔔 Wake word detected! Score: {score:.2f}')
        self.get_logger().info('🟢 Session ACTIVE - robot is listening')
        
        # Publică pe /wake_detected
        wake_msg = Bool()
        wake_msg.data = True
        self.wake_pub.publish(wake_msg)
        
        # Publică starea sesiunii
        session_msg = Bool()
        session_msg.data = True
        self.session_pub.publish(session_msg)
    
    # ═══════════════════════════════════════════════════════════════════
    # OPRIRE SESIUNE
    # ═══════════════════════════════════════════════════════════════════
    def end_session_callback(self, msg: Bool):
        """Callback pentru oprirea sesiunii (ex: "goodbye robot")."""
        if msg.data and self.session_active:
            self.session_active = False
            self.get_logger().info('🔴 Session ENDED - waiting for wake word')
            
            # Publică starea
            session_msg = Bool()
            session_msg.data = False
            self.session_pub.publish(session_msg)
    
    def reset_session(self):
        """Resetează sesiunea la standby."""
        self.session_active = False
        self.get_logger().info('⏳ Session reset to standby')


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main(args=None):
    rclpy.init(args=args)
    node = WakeWordNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
