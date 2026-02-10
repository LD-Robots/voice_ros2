#!/usr/bin/env python3
"""
audio_segment_node.py
Bufferează audio și trimite segmente complete când utilizatorul termină de vorbit.

EXPLICAȚIE:
- Ascultă /audio_raw de la audio_capture_node
- Ascultă /voice_activity de la vad_node
- Când VAD detectează că user-ul a terminat de vorbit, publică segmentul complet pe /audio_segment
- Serverul (asr_node) primește segmente complete, nu stream continuu

Aceasta reduce bandwidth-ul pentru arhitecturi client-server pe mașini separate.
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTURI
# ═══════════════════════════════════════════════════════════════════

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import Bool
import numpy as np
import time


# ═══════════════════════════════════════════════════════════════════
# CLASA NODULUI
# ═══════════════════════════════════════════════════════════════════

class AudioSegmentNode(Node):
    """
    Nod ROS2 care bufferează audio și trimite segmente complete.
    
    Funcționare:
    1. Primește audio pe /audio_raw (de la audio_capture_node)
    2. Primește starea VAD pe /voice_activity (de la vad_node)
    3. Când user-ul vorbește, bufferează audio-ul
    4. Când user-ul termină de vorbit, publică segmentul complet pe /audio_segment
    """
    
    def __init__(self):
        super().__init__('audio_segment_node')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETRI
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('min_segment_seconds', 0.5)   # Segment minim pentru a trimite
        self.declare_parameter('max_segment_seconds', 30.0)  # Segment maxim (protecție)
        self.declare_parameter('pre_buffer_seconds', 0.3)    # Audio înainte de detecția vocii
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.min_segment_seconds = self.get_parameter('min_segment_seconds').value
        self.max_segment_seconds = self.get_parameter('max_segment_seconds').value
        self.pre_buffer_seconds = self.get_parameter('pre_buffer_seconds').value
        
        # Calculează dimensiuni în samples
        self.min_samples = int(self.min_segment_seconds * self.sample_rate)
        self.max_samples = int(self.max_segment_seconds * self.sample_rate)
        self.pre_buffer_samples = int(self.pre_buffer_seconds * self.sample_rate)
        
        # ─────────────────────────────────────────────────────────
        # STARE
        # ─────────────────────────────────────────────────────────
        self.audio_buffer = []          # Buffer pentru audio în timpul vorbirii
        self.pre_buffer = []            # Pre-buffer pentru context înainte de vorbire
        self.is_speaking = False        # True când VAD detectează voce
        self.was_speaking = False       # Starea anterioară
        self.session_active = False     # True când sesiunea e activă (după wake word)
        self.is_robot_speaking = False  # True când robotul vorbește (TTS playback)
        self.channels = 1
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBERS
        # ─────────────────────────────────────────────────────────
        
        # Audio de la microfon
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # Starea VAD
        self.vad_sub = self.create_subscription(
            Bool,
            '/voice_activity',
            self.vad_callback,
            10
        )
        
        # Starea sesiunii (de la wake_word_node)
        self.session_sub = self.create_subscription(
            Bool,
            '/session_active',
            self.session_callback,
            10
        )
        
        # Starea TTS - când robotul vorbește, ignorăm input-ul
        self.robot_speaking_sub = self.create_subscription(
            Bool,
            '/is_speaking',
            self.robot_speaking_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHER - trimite segmente complete la server
        # ─────────────────────────────────────────────────────────
        self.segment_pub = self.create_publisher(Audio, '/audio_segment', 10)
        
        self.get_logger().debug('📦 Audio Segment Node started')
    
    # ═══════════════════════════════════════════════════════════════════
    # CALLBACKS
    # ═══════════════════════════════════════════════════════════════════
    
    def session_callback(self, msg: Bool):
        """Callback pentru starea sesiunii."""
        self.session_active = msg.data
        if msg.data:
            self.get_logger().debug('🟢 Session active - ready to capture speech')
        else:
            # Resetează buffer-ele când sesiunea se termină
            self.audio_buffer = []
            self.pre_buffer = []
            self.get_logger().debug('🔴 Session ended - buffers cleared')
    
    def robot_speaking_callback(self, msg: Bool):
        """Callback pentru starea TTS playback."""
        was_speaking = self.is_robot_speaking
        self.is_robot_speaking = msg.data
        
        if msg.data and not was_speaking:
            self.get_logger().debug('🔇 Robot speaking - muting input')
        elif not msg.data and was_speaking:
            self.get_logger().debug('🔊 Robot stopped - listening again')
    
    def vad_callback(self, msg: Bool):
        """Callback pentru starea VAD (voice activity)."""
        self.was_speaking = self.is_speaking
        self.is_speaking = msg.data
        
        # Nu procesa dacă sesiunea nu e activă sau dacă robotul vorbește
        if not self.session_active:
            self.get_logger().debug(f'⏸️ VAD ignored - session not active', throttle_duration_sec=5.0)
            return
        if self.is_robot_speaking:
            self.get_logger().debug(f'⏸️ VAD ignored - robot speaking', throttle_duration_sec=5.0)
            return
        
        # Când user-ul începe să vorbească, adaugă pre-buffer
        if msg.data and not self.was_speaking:
            # Adaugă pre-buffer-ul la începutul înregistrării
            self.audio_buffer = list(self.pre_buffer)
            self.get_logger().info('🎤 Voice started - capturing...')
        
        # Când user-ul termină de vorbit, trimite segmentul
        if not msg.data and self.was_speaking:
            self.get_logger().debug(f'📤 VAD speech end - sending segment (session_active={self.session_active})')
            self._send_segment()
    
    def audio_callback(self, msg: Audio):
        """Bufferează audio."""
        self.sample_rate = msg.sample_rate
        self.channels = msg.channels
        
        # Nu buffera audio când robotul vorbește (previne feedback loop)
        if self.session_active and not self.is_robot_speaking:
            if self.is_speaking:
                # User vorbește - adaugă în buffer principal
                self.audio_buffer.extend(msg.data)
                
                # Protecție pentru segmente prea lungi
                if len(self.audio_buffer) >= self.max_samples:
                    self.get_logger().warn('⚠️ Max segment length reached, sending...')
                    self._send_segment()
            else:
                # User nu vorbește - menține pre-buffer pentru context
                self.pre_buffer.extend(msg.data)
                # Păstrează doar ultimele pre_buffer_samples
                if len(self.pre_buffer) > self.pre_buffer_samples:
                    self.pre_buffer = self.pre_buffer[-self.pre_buffer_samples:]
    
    # ═══════════════════════════════════════════════════════════════════
    # TRIMITE SEGMENT
    # ═══════════════════════════════════════════════════════════════════
    
    def _send_segment(self):
        """Trimite segmentul audio la server."""
        
        # CRITICAL: Don't send if session ended (race condition fix)
        if not self.session_active:
            self.get_logger().warn(f'🚫 Segment BLOCKED - session not active (had {len(self.audio_buffer)} samples)')
            self.audio_buffer = []
            return
        
        if not self.audio_buffer:
            self.get_logger().warn('Empty buffer, skipping')
            return
        
        # Verifică lungimea minimă
        if len(self.audio_buffer) < self.min_samples:
            duration = len(self.audio_buffer) / self.sample_rate
            self.get_logger().warn(
                f'Segment too short ({duration:.2f}s < {self.min_segment_seconds}s), skipping'
            )
            self.audio_buffer = []
            return
        
        # Calculează durata
        duration = len(self.audio_buffer) / self.sample_rate
        
        self.get_logger().debug(f'📤 Sending audio segment: {duration:.2f}s ({len(self.audio_buffer)} samples)')
        
        # Creează și publică mesajul
        msg = Audio()
        msg.sample_rate = self.sample_rate
        msg.channels = self.channels
        msg.data = self.audio_buffer
        
        self.segment_pub.publish(msg)
        
        # Resetează buffer-ul
        self.audio_buffer = []
        self.get_logger().debug('✅ Segment sent to server')


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main(args=None):
    rclpy.init(args=args)
    node = AudioSegmentNode()
    
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
