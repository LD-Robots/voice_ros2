#!/usr/bin/env python3
"""
barge_in_node.py - INTELLIGENT Barge-in Detection

Features (ca în proiectul original):
- RMS dBFS - măsoară volumul pentru a ignora sunetele slabe
- High-pass filter - elimină zgomote joase (bătăi în masă)
- Zero-Crossing Rate (ZCR) - detectează voce umană vs zgomot impulsiv
- Anti-echo (leak baseline) - ignoră ecoul TTS/difuzor
- Voice hold - menține detecția pentru drop-uri scurte
- Timere: min_voice_ms, debounce, cooldown, arm_after

Subscribes to:
  - /audio_raw (Audio) - pentru analiză directă
  - /tts_speaking (Bool) - starea TTS

Publishes to:
  - /barge_in (Bool) - semnal de întrerupere
  - /tts_stop (Bool) - comandă stop TTS
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from conversational_interfaces.msg import Audio
import numpy as np
import time

# ═══════════════════════════════════════════════════════════════════
# FUNCȚII HELPER
# ═══════════════════════════════════════════════════════════════════

def _rms_dbfs(pcm_i16: np.ndarray) -> float:
    """Calculează RMS în dBFS."""
    if pcm_i16.size == 0:
        return -120.0
    xf = pcm_i16.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(xf * xf) + 1e-12))
    return 20.0 * np.log10(rms + 1e-12)

def _highpass_filter(pcm_i16: np.ndarray, cutoff_hz: float, sr: int) -> np.ndarray:
    """
    Filtru high-pass simplu (first-order IIR) pentru a tăia frecvențele joase.
    Elimină zgomotele de tip bătăi în masă (~50-200 Hz).
    """
    if cutoff_hz <= 0:
        return pcm_i16
    
    rc = 1.0 / (2.0 * np.pi * cutoff_hz)
    dt = 1.0 / sr
    alpha = rc / (rc + dt)
    
    xf = pcm_i16.astype(np.float32)
    y = np.zeros_like(xf)
    y_prev = 0.0
    x_prev = 0.0
    
    for i in range(len(xf)):
        y[i] = alpha * (y_prev + xf[i] - x_prev)
        y_prev = y[i]
        x_prev = xf[i]
    
    return np.clip(y, -32768, 32767).astype(np.int16)

def _zero_crossing_rate(pcm_i16: np.ndarray) -> float:
    """
    Calculează rata de treceri prin zero (ZCR).
    Vocea umană: ZCR moderat (~0.05-0.3)
    Zgomote impulsive: ZCR foarte mare (>0.4)
    Zgomote joase constante: ZCR foarte mic (<0.02)
    """
    if len(pcm_i16) < 2:
        return 0.0
    signs = np.sign(pcm_i16)
    crossings = np.sum(np.abs(np.diff(signs))) / 2.0
    return crossings / (len(pcm_i16) - 1)


# ═══════════════════════════════════════════════════════════════════
# CLASA NODULUI
# ═══════════════════════════════════════════════════════════════════

class BargeInNode(Node):
    """
    Nod ROS2 pentru barge-in detection INTELIGENT.
    
    Nu detectează doar că există sunet, ci verifică dacă e VOCE UMANĂ:
    - Nu bătăi în masă
    - Nu ecoul robotului
    - Nu zgomote impulsive
    """
    
    def __init__(self):
        super().__init__('barge_in_node')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETRI
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('min_voice_ms', 600)      # Cât să vorbească pentru barge-in
        self.declare_parameter('debounce_ms', 150)       # Debounce între verificări
        self.declare_parameter('cooldown_ms', 800)       # Cooldown după barge-in
        self.declare_parameter('arm_after_ms', 400)      # Delay inițial (anti-scurgeri)
        self.declare_parameter('voice_hold_ms', 200)     # Menține detecție pentru drop-uri
        self.declare_parameter('voice_drop_ms', 20)      # Cât se pierde per frame fără voce
        
        # Praguri audio
        self.declare_parameter('min_rms_dbfs', -28.0)    # Prag volum minim
        self.declare_parameter('highpass_hz', 300.0)     # Filtru pentru bătăi joase
        self.declare_parameter('zcr_min', 0.05)          # ZCR minim pentru voce
        self.declare_parameter('zcr_max', 0.35)          # ZCR maxim pentru voce
        
        # Anti-echo
        self.declare_parameter('leak_margin_db', 3.0)    # Marjă peste ecou
        self.declare_parameter('leak_decay_ms', 1200)    # Cât durează până expiră baseline
        
        self.sr = self.get_parameter('sample_rate').value
        self.min_voice_ms = self.get_parameter('min_voice_ms').value
        self.debounce_ms = self.get_parameter('debounce_ms').value
        self.cooldown_ms = self.get_parameter('cooldown_ms').value
        self.arm_after_ms = self.get_parameter('arm_after_ms').value
        self.voice_hold_ms = self.get_parameter('voice_hold_ms').value
        self.voice_drop_ms = self.get_parameter('voice_drop_ms').value
        
        self.min_rms_dbfs = self.get_parameter('min_rms_dbfs').value
        self.highpass_hz = self.get_parameter('highpass_hz').value
        self.zcr_min = self.get_parameter('zcr_min').value
        self.zcr_max = self.get_parameter('zcr_max').value
        
        self.leak_margin_db = self.get_parameter('leak_margin_db').value
        self.leak_decay_ms = self.get_parameter('leak_decay_ms').value
        
        # ─────────────────────────────────────────────────────────
        # STARE
        # ─────────────────────────────────────────────────────────
        self.is_tts_speaking = False
        self.voiced_ms = 0  # Acumulare voce continuă
        self.last_voice_ms = 0  # Ultimul moment cu voce
        self.last_trigger_ms = 0  # Ultimul barge-in
        self.start_ms = int(time.time() * 1000)
        
        # Anti-echo baseline
        self.leak_baseline_dbfs = None
        self.last_leak_update_ms = 0
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBERS
        # ─────────────────────────────────────────────────────────
        
        # Audio raw pentru analiză proprieteară
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # Starea TTS
        self.tts_sub = self.create_subscription(
            Bool,
            '/tts_speaking',
            self.tts_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHERS
        # ─────────────────────────────────────────────────────────
        self.barge_pub = self.create_publisher(Bool, '/barge_in', 10)
        self.stop_pub = self.create_publisher(Bool, '/tts_stop', 10)
        
        self.get_logger().info(
            f'🎯 Intelligent Barge-in started: min_voice={self.min_voice_ms}ms, '
            f'rms>{self.min_rms_dbfs}dB, hp={self.highpass_hz}Hz, '
            f'zcr=[{self.zcr_min},{self.zcr_max}]'
        )
    
    # ═══════════════════════════════════════════════════════════════════
    # CALLBACKS
    # ═══════════════════════════════════════════════════════════════════
    
    def tts_callback(self, msg: Bool):
        """Actualizează starea TTS."""
        was_speaking = self.is_tts_speaking
        self.is_tts_speaking = msg.data
        
        # Când TTS începe să vorbească, resetăm baseline pentru anti-echo
        if msg.data and not was_speaking:
            self.leak_baseline_dbfs = None
    
    def audio_callback(self, msg: Audio):
        """Procesează audio pentru detectare voce umană."""
        now_ms = int(time.time() * 1000)
        
        # Arm delay - ignoră la început
        if (now_ms - self.start_ms) < self.arm_after_ms:
            return
        
        # Nu verificăm dacă TTS nu vorbește (nu are sens barge-in)
        if not self.is_tts_speaking:
            # Resetăm acumularea și actualizăm baseline
            self.voiced_ms = 0
            pcm = np.array(msg.data, dtype=np.int16)
            self._update_leak_baseline(_rms_dbfs(pcm), now_ms, fast=True)
            return
        
        # Debounce
        if (now_ms - self.last_trigger_ms) < self.debounce_ms:
            return
        
        # Convertește la int16
        pcm = np.array(msg.data, dtype=np.int16)
        
        # Verifică dacă e voce umană
        if self._is_human_voice(pcm, now_ms):
            # Acumulează timp de voce (max min_voice_ms)
            block_ms = len(pcm) * 1000 // self.sr
            self.voiced_ms = min(self.voiced_ms + block_ms, self.min_voice_ms)
            self.last_voice_ms = now_ms
        else:
            # Pierde voce gradual (pentru drop-uri scurte)
            self.voiced_ms = max(0, self.voiced_ms - self.voice_drop_ms)
        
        # Trigger barge-in dacă voce continuă suficientă
        if self.voiced_ms >= self.min_voice_ms:
            if (now_ms - self.last_trigger_ms) >= self.cooldown_ms:
                self._trigger_barge_in()
            self.voiced_ms = 0
    
    # ═══════════════════════════════════════════════════════════════════
    # DETECȚIE VOCE UMANĂ
    # ═══════════════════════════════════════════════════════════════════
    
    def _is_human_voice(self, pcm_i16: np.ndarray, now_ms: int) -> bool:
        """
        Verifică dacă PCM-ul conține voce umană (nu zgomot/eco):
        1. RMS peste prag (vocea e mai tare decât TTS leak)
        2. High-pass filter (elimină bătăi joase)
        3. Zero-crossing rate în interval vocii umane
        """
        # Decay leak baseline
        self._maybe_decay_leak(now_ms)
        
        # 1) RMS check
        rms = _rms_dbfs(pcm_i16)
        
        # Prag dinamic bazat pe ecou
        rms_threshold = self.min_rms_dbfs
        if self.leak_baseline_dbfs is not None:
            rms_threshold = max(rms_threshold, self.leak_baseline_dbfs + self.leak_margin_db)
        
        if rms < rms_threshold:
            self._update_leak_baseline(rms, now_ms, fast=False)
            return False
        
        # 2) High-pass filtering (anti-zgomot jos-frecvent)
        pcm_filtered = _highpass_filter(pcm_i16, self.highpass_hz, self.sr)
        
        # 3) Zero-crossing rate (anti-zgomot impulsiv)
        zcr = _zero_crossing_rate(pcm_filtered)
        if not (self.zcr_min <= zcr <= self.zcr_max):
            self._update_leak_baseline(rms, now_ms, fast=False)
            return False
        
        # Voice hold - menține detecția pentru drop-uri scurte
        if (now_ms - self.last_voice_ms) <= self.voice_hold_ms:
            return True
        
        return True
    
    def _maybe_decay_leak(self, now_ms: int):
        """Expiră leak baseline după timeout."""
        if self.leak_baseline_dbfs is None:
            return
        if (now_ms - self.last_leak_update_ms) > self.leak_decay_ms:
            self.leak_baseline_dbfs = None
            self.last_leak_update_ms = now_ms
    
    def _update_leak_baseline(self, rms_db: float, now_ms: int, fast: bool = False):
        """Actualizează baseline pentru anti-echo."""
        if not np.isfinite(rms_db) or rms_db <= -90.0:
            return
        
        if self.leak_baseline_dbfs is None:
            self.leak_baseline_dbfs = rms_db
        else:
            # Limitează spike-uri mari
            if not fast and rms_db > self.leak_baseline_dbfs + self.leak_margin_db * 2:
                rms_db = self.leak_baseline_dbfs + self.leak_margin_db * 2
            
            alpha = 0.35 if fast else 0.12
            self.leak_baseline_dbfs = (1.0 - alpha) * self.leak_baseline_dbfs + alpha * rms_db
        
        self.last_leak_update_ms = now_ms
    
    # ═══════════════════════════════════════════════════════════════════
    # TRIGGER
    # ═══════════════════════════════════════════════════════════════════
    
    def _trigger_barge_in(self):
        """Declanșează barge-in - oprește TTS."""
        now_ms = int(time.time() * 1000)
        self.last_trigger_ms = now_ms
        self.voiced_ms = 0
        
        self.get_logger().info('🛑 BARGE-IN: Voce umană detectată, opresc TTS!')
        
        # Publică pe /barge_in
        msg = Bool()
        msg.data = True
        self.barge_pub.publish(msg)
        
        # Trimite stop direct la TTS
        self.stop_pub.publish(msg)


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
