#!/usr/bin/env python3
"""
barge_in_node.py - INTELLIGENT Barge-in Detection

Features (as in the original project):
- RMS dBFS - measures volume to ignore weak sounds
- High-pass filter - removes low-frequency noise (desk thumps)
- Zero-Crossing Rate (ZCR) - detects human voice vs impulsive noise
- Anti-echo (leak baseline) - ignores TTS/speaker echo
- Voice hold - keeps detection during short dropouts
- Timers: min_voice_ms, debounce, cooldown, arm_after

Subscribes to:
  - /audio_raw (Audio) - direct analysis
  - /tts_speaking (Bool) - TTS state

Publishes to:
  - /barge_in (Bool) - interruption signal
  - /tts_stop (Bool) - stop TTS command
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from conversational_interfaces.msg import Audio
import numpy as np
import time
import os

# PyTorch-based stop keyword detector
try:
    from .stop_keyword_detector import StopKeywordDetector
    STOP_DETECTOR_AVAILABLE = True
except ImportError as e:
    STOP_DETECTOR_AVAILABLE = False
    _STOP_DETECTOR_ERROR = str(e)

# ═══════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def _rms_dbfs(pcm_i16: np.ndarray) -> float:
    """Compute RMS in dBFS."""
    if pcm_i16.size == 0:
        return -120.0
    xf = pcm_i16.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(xf * xf) + 1e-12))
    return 20.0 * np.log10(rms + 1e-12)

def _highpass_filter(pcm_i16: np.ndarray, cutoff_hz: float, sr: int) -> np.ndarray:
    """
    Simple high-pass filter (first-order IIR) to cut low frequencies.
    Removes desk-thump noise (~50-200 Hz).
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
    Compute the zero-crossing rate (ZCR).
    Human voice: moderate ZCR (~0.05-0.3)
    Impulsive noise: very high ZCR (>0.4)
    Steady low-frequency noise: very low ZCR (<0.02)
    """
    if len(pcm_i16) < 2:
        return 0.0
    signs = np.sign(pcm_i16)
    crossings = np.sum(np.abs(np.diff(signs))) / 2.0
    return crossings / (len(pcm_i16) - 1)


# ═══════════════════════════════════════════════════════════════════
# NODE CLASS
# ═══════════════════════════════════════════════════════════════════

class BargeInNode(Node):
    """
    ROS2 node for INTELLIGENT barge-in detection.
    
    It doesn't just detect sound, it verifies HUMAN VOICE:
    - No desk thumps
    - No robot echo
    - No impulsive noise
    """
    
    def __init__(self):
        super().__init__('barge_in_node')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETERS
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('min_voice_ms', 600)      # How long to speak for barge-in
        self.declare_parameter('debounce_ms', 150)       # Debounce between checks
        self.declare_parameter('cooldown_ms', 800)       # Cooldown after barge-in
        self.declare_parameter('arm_after_ms', 400)      # Initial delay (anti-leak)
        self.declare_parameter('voice_hold_ms', 200)     # Hold detection for dropouts
        self.declare_parameter('voice_drop_ms', 20)      # Decay per frame without voice
        
        # Audio thresholds
        self.declare_parameter('min_rms_dbfs', -28.0)    # Minimum volume threshold
        self.declare_parameter('highpass_hz', 300.0)     # Filter for low thumps
        self.declare_parameter('zcr_min', 0.05)          # Min ZCR for voice
        self.declare_parameter('zcr_max', 0.35)          # Max ZCR for voice
        
        # Anti-echo
        self.declare_parameter('leak_margin_db', 3.0)    # Margin above echo
        self.declare_parameter('leak_decay_ms', 1200)    # How long until baseline expires
        
        # Stop keyword detector (PyTorch)
        self.declare_parameter('stop_model_path', '')
        self.declare_parameter('stop_enabled', True)
        self.declare_parameter('stop_prob_threshold', 0.8)
        self.declare_parameter('stop_logit_margin', 0.5)
        self.declare_parameter('stop_hits_required', 2)
        self.declare_parameter('stop_frame_samples', 16000)  # Frame size in samples
        self.declare_parameter('stop_hop_samples', 8000)     # Hop size in samples
        self.declare_parameter('stop_requires_voice_signature', True)
        
        self.declare_parameter('voice_enabled', True)
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
        self.voice_enabled = self.get_parameter('voice_enabled').value
        
        # ─────────────────────────────────────────────────────────
        # STATE
        # ─────────────────────────────────────────────────────────
        self.is_tts_speaking = False
        self.voiced_ms = 0  # Accumulated continuous voice
        self.last_voice_ms = 0  # Last moment with voice
        self.last_trigger_ms = 0  # Last barge-in
        self.start_ms = int(time.time() * 1000)
        self.tts_started_ms = 0
        
        # Anti-echo baseline
        self.leak_baseline_dbfs = None
        self.last_leak_update_ms = 0
        
        # Initialize PyTorch stop keyword detector
        self.stop_detector = None
        stop_model_path = self.get_parameter('stop_model_path').value
        stop_enabled = self.get_parameter('stop_enabled').value
        self.stop_requires_voice_signature = bool(
            self.get_parameter('stop_requires_voice_signature').value
        )
        
        if stop_enabled and STOP_DETECTOR_AVAILABLE and stop_model_path and os.path.exists(stop_model_path):
            try:
                stop_cfg = {
                    'model_path': stop_model_path,
                    'prob_threshold': self.get_parameter('stop_prob_threshold').value,
                    'logit_margin': self.get_parameter('stop_logit_margin').value,
                    'hits_required': self.get_parameter('stop_hits_required').value,
                    'frame_samples': self.get_parameter('stop_frame_samples').value,
                    'hop_samples': self.get_parameter('stop_hop_samples').value,
                    'debug': True,  # Always show scores for debugging
                }
                self.stop_detector = StopKeywordDetector(stop_cfg, self.sr, self.get_logger())
                self.get_logger().info(f'🛑 PyTorch Stop Detector ENABLED: {os.path.basename(stop_model_path)}')
            except Exception as e:
                self.get_logger().warning(f'⚠️ Stop detector init failed: {e}')
                self.stop_detector = None
        elif stop_enabled and not STOP_DETECTOR_AVAILABLE:
            self.get_logger().warning(f'⚠️ Stop detector unavailable: {_STOP_DETECTOR_ERROR}')
        elif stop_enabled and stop_model_path and not os.path.exists(stop_model_path):
            self.get_logger().warning(f'⚠️ Stop model not found: {stop_model_path}')
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBERS
        # ─────────────────────────────────────────────────────────
        
        # Raw audio for local analysis
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # TTS state
        self.tts_sub = self.create_subscription(
            Bool,
            '/is_speaking',  # From audio_playback_node (actual playback state)
            self.tts_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHERS
        # ─────────────────────────────────────────────────────────
        self.barge_pub = self.create_publisher(Bool, '/barge_in', 10)
        self.stop_pub = self.create_publisher(Bool, '/stop_playback', 10)
        
        self.get_logger().info(
            f'🎯 Intelligent Barge-in started: min_voice={self.min_voice_ms}ms, '
            f'rms>{self.min_rms_dbfs}dB, hp={self.highpass_hz}Hz, '
            f'zcr=[{self.zcr_min},{self.zcr_max}]'
        )
    
    # ═══════════════════════════════════════════════════════════════════
    # CALLBACKS
    # ═══════════════════════════════════════════════════════════════════
    
    def tts_callback(self, msg: Bool):
        """Update TTS state."""
        was_speaking = self.is_tts_speaking
        self.is_tts_speaking = msg.data
        
        # When TTS starts speaking, reset the anti-echo baseline
        if msg.data and not was_speaking:
            self.tts_started_ms = int(time.time() * 1000)
            self.leak_baseline_dbfs = None
            self.last_voice_ms = 0
            if self.stop_detector:
                self.stop_detector.reset()
        elif not msg.data and was_speaking:
            self.last_voice_ms = 0
            if self.stop_detector:
                self.stop_detector.reset()
    
    def audio_callback(self, msg: Audio):
        """Process audio for stop keyword detection."""
        now_ms = int(time.time() * 1000)
        
        # Arm delay - ignore at the beginning
        if (now_ms - self.start_ms) < self.arm_after_ms:
            return
        
        # Do not process if TTS is not speaking
        if not self.is_tts_speaking:
            return
        
        # Debounce - ignore duplicate detections
        if (now_ms - self.last_trigger_ms) < self.cooldown_ms:
            return
        
        # Convert to int16
        pcm = np.array(msg.data, dtype=np.int16)
        has_voice_signature = self._is_human_voice(pcm, now_ms)
        
        # ══════════════════════════════════════════════════════════
        # STOP KEYWORD DETECTOR (only when TTS is speaking)
        # ══════════════════════════════════════════════════════════
        if self.stop_detector:
            try:
                stop_result = self.stop_detector.process_block(pcm)
                if stop_result:
                    if self.stop_requires_voice_signature and not has_voice_signature:
                        self.get_logger().debug(
                            'Ignoring stop keyword hit without strong human-voice signature'
                        )
                        return
                    self.get_logger().info(
                        f'🛑 STOP KEYWORD detected (p={stop_result.probability:.2f}) - Barge-in!'
                    )
                    self._trigger_barge_in()
            except Exception as e:
                self.get_logger().warning(f'Stop detector error: {e}')
        
        # ══════════════════════════════════════════════════════════
        # HUMAN VOICE (standard barge-in)
        # ══════════════════════════════════════════════════════════
        frame_ms = int(len(msg.data) / self.sr * 1000)
        if self.voice_enabled and has_voice_signature:
            self.voiced_ms += frame_ms
            self.last_voice_ms = now_ms
        else:
            self.voiced_ms = max(0, self.voiced_ms - self.voice_drop_ms)
            
        if self.voice_enabled and self.voiced_ms > self.min_voice_ms:
            self.get_logger().info(f'🗣️ Voice Barge-in detected ({self.voiced_ms}ms) - Stopping TTS')
            self._trigger_barge_in()
    
    # ═══════════════════════════════════════════════════════════════════
    # HUMAN VOICE DETECTION
    # ═══════════════════════════════════════════════════════════════════
    
    def _is_human_voice(self, pcm_i16: np.ndarray, now_ms: int) -> bool:
        """
        Check whether PCM contains human voice (not noise/echo):
        1. RMS above threshold (voice louder than TTS leak)
        2. High-pass filter (removes low thumps)
        3. Zero-crossing rate within human voice range
        """
        # Decay leak baseline
        self._maybe_decay_leak(now_ms)
        
        # 1) RMS check
        rms = _rms_dbfs(pcm_i16)
        
        # Dynamic threshold based on echo
        rms_threshold = self.min_rms_dbfs
        if self.leak_baseline_dbfs is not None:
            rms_threshold = max(rms_threshold, self.leak_baseline_dbfs + self.leak_margin_db)
        
        if rms < rms_threshold:
            self._update_leak_baseline(rms, now_ms, fast=False)
            return False
            
        # 2) High-pass filtering (low-frequency noise removal)
        pcm_filtered = _highpass_filter(pcm_i16, self.highpass_hz, self.sr)
        
        # 3) Zero-crossing rate (impulsive noise filter)
        zcr = _zero_crossing_rate(pcm_filtered)
        
        # DEBUG: Print RMS and ZCR if it passes the threshold
        self.get_logger().info(f'🎤 DEBUG BARGE-IN: RMS={rms:.2f} (thresh={rms_threshold:.2f}), ZCR={zcr:.3f} (needs [{self.zcr_min},{self.zcr_max}])')
        
        if not (self.zcr_min <= zcr <= self.zcr_max):
            self._update_leak_baseline(rms, now_ms, fast=False)
            return False
        
        # Voice hold - keep detection during short dropouts
        if (now_ms - self.last_voice_ms) <= self.voice_hold_ms:
            return True
        self.last_voice_ms = now_ms
        return True
    
    def _maybe_decay_leak(self, now_ms: int):
        """Expire the leak baseline after timeout."""
        if self.leak_baseline_dbfs is None:
            return
        if (now_ms - self.last_leak_update_ms) > self.leak_decay_ms:
            self.leak_baseline_dbfs = None
            self.last_leak_update_ms = now_ms
    
    def _update_leak_baseline(self, rms_db: float, now_ms: int, fast: bool = False):
        """Update the anti-echo baseline."""
        if not np.isfinite(rms_db) or rms_db <= -90.0:
            return
        
        if self.leak_baseline_dbfs is None:
            self.leak_baseline_dbfs = rms_db
        else:
            # Limit large spikes
            if not fast and rms_db > self.leak_baseline_dbfs + self.leak_margin_db * 2:
                rms_db = self.leak_baseline_dbfs + self.leak_margin_db * 2
            
            alpha = 0.35 if fast else 0.12
            self.leak_baseline_dbfs = (1.0 - alpha) * self.leak_baseline_dbfs + alpha * rms_db
        
        self.last_leak_update_ms = now_ms
    
    # ═══════════════════════════════════════════════════════════════════
    # TRIGGER
    # ═══════════════════════════════════════════════════════════════════
    
    def _trigger_barge_in(self):
        """Trigger barge-in - stop TTS."""
        now_ms = int(time.time() * 1000)
        self.last_trigger_ms = now_ms
        self.voiced_ms = 0
        self.last_voice_ms = 0
        if self.stop_detector:
            self.stop_detector.reset()
        
        self.get_logger().debug('_trigger_barge_in called')
        
        # Publish to /barge_in
        msg = Bool()
        msg.data = True
        self.barge_pub.publish(msg)
        
        # Send stop directly to TTS
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
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()
