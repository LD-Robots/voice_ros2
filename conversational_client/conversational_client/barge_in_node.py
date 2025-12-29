#!/usr/bin/env python3
"""
barge_in_node.py
Detectează când utilizatorul vorbește peste robot și oprește TTS.

EXPLICAȚIE:
- Ascultă pe /voice_activity (de la vad_node)
- Când user vorbește ȘI robotul redă audio, trimite comandă stop
- Publică pe /barge_in pentru a opri playback-ul

Barge-in = când user întrerupe robotul vorbind peste el.
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTURI
# ═══════════════════════════════════════════════════════════════════

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
import time


# ═══════════════════════════════════════════════════════════════════
# CLASA NODULUI
# ═══════════════════════════════════════════════════════════════════

class BargeInNode(Node):
    """
    Nod ROS2 pentru barge-in detection.
    
    Funcționare:
    1. Urmărește dacă robotul redă audio (via /playback_active)
    2. Urmărește dacă user vorbește (via /voice_activity)
    3. Dacă ambele sunt True → publică pe /barge_in pentru a opri TTS
    """
    
    def __init__(self):
        super().__init__('barge_in_node')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETRI
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('min_voice_duration_ms', 300)  # Cât de mult să vorbească
        self.declare_parameter('cooldown_ms', 500)  # Cooldown între barge-in events
        
        self.min_voice_duration = self.get_parameter('min_voice_duration_ms').value / 1000.0
        self.cooldown = self.get_parameter('cooldown_ms').value / 1000.0
        
        # ─────────────────────────────────────────────────────────
        # STARE
        # ─────────────────────────────────────────────────────────
        self.is_robot_speaking = False   # Robotul redă audio?
        self.is_user_speaking = False    # User-ul vorbește?
        self.voice_start_time = None     # Când a început user să vorbească
        self.last_barge_in_time = 0      # Ultimul barge-in (pentru cooldown)
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBERS
        # ─────────────────────────────────────────────────────────
        
        # Starea vocii user-ului (de la vad_node)
        self.voice_sub = self.create_subscription(
            Bool,
            '/voice_activity',
            self.voice_callback,
            10
        )
        
        # Starea playback-ului robotului (de la audio_playback_node)
        self.playback_sub = self.create_subscription(
            Bool,
            '/playback_active',
            self.playback_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHER
        # ─────────────────────────────────────────────────────────
        self.barge_in_pub = self.create_publisher(Bool, '/barge_in', 10)
        
        self.get_logger().info(
            f'🛑 Barge-in Node started '
            f'(min_voice={self.min_voice_duration*1000:.0f}ms, '
            f'cooldown={self.cooldown*1000:.0f}ms)'
        )
    
    # ═══════════════════════════════════════════════════════════════════
    # CALLBACKS
    # ═══════════════════════════════════════════════════════════════════
    
    def voice_callback(self, msg: Bool):
        """Callback când starea vocii user-ului se schimbă."""
        
        was_speaking = self.is_user_speaking
        self.is_user_speaking = msg.data
        
        if msg.data and not was_speaking:
            # User tocmai a început să vorbească
            self.voice_start_time = time.time()
            self._check_barge_in()
        elif not msg.data:
            # User a oprit
            self.voice_start_time = None
    
    def playback_callback(self, msg: Bool):
        """Callback când starea playback-ului se schimbă."""
        self.is_robot_speaking = msg.data
    
    # ═══════════════════════════════════════════════════════════════════
    # LOGICA BARGE-IN
    # ═══════════════════════════════════════════════════════════════════
    
    def _check_barge_in(self):
        """Verifică dacă trebuie să oprim robotul."""
        
        now = time.time()
        
        # Cooldown - nu permite barge-in prea frecvent
        if now - self.last_barge_in_time < self.cooldown:
            return
        
        # Condiții pentru barge-in:
        # 1. Robotul redă audio
        # 2. User-ul vorbește
        # 3. User-ul a vorbit suficient de mult (nu doar un "um")
        
        if self.is_robot_speaking and self.is_user_speaking:
            if self.voice_start_time is not None:
                voice_duration = now - self.voice_start_time
                
                if voice_duration >= self.min_voice_duration:
                    self._trigger_barge_in()
    
    def _trigger_barge_in(self):
        """Declanșează barge-in - oprește TTS."""
        
        self.last_barge_in_time = time.time()
        
        self.get_logger().info('🛑 BARGE-IN detected! Stopping robot speech.')
        
        # Publică pe /barge_in
        msg = Bool()
        msg.data = True
        self.barge_in_pub.publish(msg)


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main(args=None):
    rclpy.init(args=args)
    node = BargeInNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
