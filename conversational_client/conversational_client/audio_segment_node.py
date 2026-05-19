#!/usr/bin/env python3
"""
audio_segment_node.py
Buffers audio and sends complete segments when the user finishes speaking.

EXPLANATION:
- Listens to /audio_raw from audio_capture_node
- Listens to /voice_activity from vad_node
- When VAD detects the user finished speaking, publishes the full segment on /audio_segment
- The server (asr_node) receives complete segments, not a continuous stream

This reduces bandwidth for client-server setups on separate machines.
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════════════════════

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import Bool
import numpy as np
import time


# ═══════════════════════════════════════════════════════════════════
# NODE CLASS
# ═══════════════════════════════════════════════════════════════════

class AudioSegmentNode(Node):
    """
    ROS2 node that buffers audio and sends complete segments.
    
    Operation:
    1. Receives audio on /audio_raw (from audio_capture_node)
    2. Receives VAD state on /voice_activity (from vad_node)
    3. When the user speaks, buffers audio
    4. When the user finishes speaking, publishes the full segment on /audio_segment
    """
    
    def __init__(self):
        super().__init__('audio_segment_node')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETERS
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('min_segment_seconds', 0.5)   # Minimum segment to send
        self.declare_parameter('max_segment_seconds', 30.0)  # Maximum segment (protection)
        self.declare_parameter('pre_buffer_seconds', 0.3)    # Audio before voice detection
        self.declare_parameter('capture_during_playback', False)
        self.declare_parameter('stop_playback_on_voice_start', True)
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.min_segment_seconds = self.get_parameter('min_segment_seconds').value
        self.max_segment_seconds = self.get_parameter('max_segment_seconds').value
        self.pre_buffer_seconds = self.get_parameter('pre_buffer_seconds').value
        self.capture_during_playback = self.get_parameter('capture_during_playback').value
        self.stop_playback_on_voice_start = bool(
            self.get_parameter('stop_playback_on_voice_start').value
        )
        
        # Compute sizes in samples
        self.min_samples = int(self.min_segment_seconds * self.sample_rate)
        self.max_samples = int(self.max_segment_seconds * self.sample_rate)
        self.pre_buffer_samples = int(self.pre_buffer_seconds * self.sample_rate)
        
        # ─────────────────────────────────────────────────────────
        # STATE
        # ─────────────────────────────────────────────────────────
        self.audio_buffer = []          # Buffer for audio during speech
        self.pre_buffer = []            # Pre-buffer for context before speech
        self.is_speaking = False        # True when VAD detects voice
        self.was_speaking = False       # Previous state
        self.session_active = False     # True when session is active (after wake word)
        self.is_robot_speaking = False  # True when the robot is speaking (TTS playback)
        self.ignore_segment = False     # Flag to ignore segment sending
        self.channels = 1
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBERS
        # ─────────────────────────────────────────────────────────
        
        # Microphone audio
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # VAD state
        self.vad_sub = self.create_subscription(
            Bool,
            '/voice_activity',
            self.vad_callback,
            10
        )
        
        # Session state (from wake_word_node)
        self.session_sub = self.create_subscription(
            Bool,
            '/session_active',
            self.session_callback,
            10
        )
        
        # TTS state - when the robot is speaking, ignore input
        self.robot_speaking_sub = self.create_subscription(
            Bool,
            '/is_speaking',
            self.robot_speaking_callback,
            10
        )
        
        # Barge-in event - clear current buffer to avoid transcribing "Stop"
        self.barge_in_sub = self.create_subscription(
            Bool,
            '/barge_in',
            self.barge_in_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHER - send complete segments to the server
        # ─────────────────────────────────────────────────────────
        self.segment_pub = self.create_publisher(Audio, '/audio_segment', 10)
        self.stop_playback_pub = self.create_publisher(Bool, '/stop_playback', 10)
        self.legacy_stop_pub = self.create_publisher(Bool, '/tts_stop', 10)
        
        self.get_logger().debug('📦 Audio Segment Node started')
    
    # ═══════════════════════════════════════════════════════════════════
    # CALLBACKS
    # ═══════════════════════════════════════════════════════════════════
    
    def session_callback(self, msg: Bool):
        """Callback for session state."""
        self.session_active = msg.data
        if msg.data:
            self.get_logger().debug('🟢 Session active - ready to capture speech')
        else:
            # Reset buffers when session ends
            self.audio_buffer = []
            self.pre_buffer = []
            self.get_logger().debug('🔴 Session ended - buffers cleared')
    
    def robot_speaking_callback(self, msg: Bool):
        """Callback for TTS playback state."""
        was_speaking = self.is_robot_speaking
        self.is_robot_speaking = msg.data

        if self.capture_during_playback:
            return  # Allow full duplex listening
        
        if msg.data and not was_speaking:
            self.get_logger().info('🔇 Robot speaking - muting input')
            # Clear buffer immediately when robot starts speaking to remove any leak
            self.audio_buffer = []
            self.ignore_segment = True  # Ignore any pending segment as it might be echo
        elif not msg.data and was_speaking:
            self.get_logger().info('🔊 Robot stopped - listening again')

    def barge_in_callback(self, msg: Bool):
        """Callback pentru evenimentul de barge-in."""
        if msg.data:
            self.get_logger().warn('🚫 Barge-in detected - clearing audio buffer to prevent transcription')
            self.audio_buffer = []
            self.pre_buffer = []
            self.is_speaking = False  # Reset VAD state locally
            self.ignore_segment = True # Flag to ignore sending segment if VAD triggers later

    def vad_callback(self, msg: Bool):
        """Callback for VAD state (voice activity)."""
        self.was_speaking = self.is_speaking
        self.is_speaking = msg.data
        
        # Do not process if session is not active or if the robot is speaking
        if not self.session_active:
            self.get_logger().debug(f'⏸️ VAD ignored - session not active', throttle_duration_sec=5.0)
            return
        if self.is_robot_speaking and not self.capture_during_playback:
            self.get_logger().debug(f'⏸️ VAD ignored - robot speaking', throttle_duration_sec=5.0)
            return
        
        # When the user starts speaking, add pre-buffer
        if msg.data and not self.was_speaking:
            self._stop_playback_for_user_speech()
            # Add the pre-buffer at the start of recording
            self.audio_buffer = list(self.pre_buffer)
            self.get_logger().info('🎤 Voice started - capturing...')
        
        # When the user finishes speaking, send the segment
        if not msg.data and self.was_speaking:
            self.get_logger().debug(f'📤 VAD speech end - sending segment (session_active={self.session_active})')
            self._send_segment()
    
    def audio_callback(self, msg: Audio):
        """Buffer audio."""
        self.sample_rate = msg.sample_rate
        self.channels = msg.channels
        
        # In half-duplex mode, do not buffer robot playback. In full-duplex mode,
        # keep buffering VAD-confirmed user speech so barge-in captures the turn.
        if self.session_active and (not self.is_robot_speaking or self.capture_during_playback):
            if self.is_speaking:
                # User speaking - add to main buffer
                self.audio_buffer.extend(msg.data)
                
                # Protection for overly long segments
                if len(self.audio_buffer) >= self.max_samples:
                    self.get_logger().warn('⚠️ Max segment length reached, sending...')
                    self._send_segment()
            else:
                # User not speaking - keep pre-buffer for context, but avoid filling
                # it with robot echo while playback is active.
                if not self.is_robot_speaking:
                    self.pre_buffer.extend(msg.data)
                    # Keep only the last pre_buffer_samples
                    if len(self.pre_buffer) > self.pre_buffer_samples:
                        self.pre_buffer = self.pre_buffer[-self.pre_buffer_samples:]
        elif self.session_active and self.is_robot_speaking:
            if self.is_speaking and len(self.audio_buffer) > 0:
                 self.get_logger().debug(f'DROPPING audio because RobotSpeaking=True (Buffer len: {len(self.audio_buffer)})')
                 self.audio_buffer = [] # Enforce empty buffer
    
    # ═══════════════════════════════════════════════════════════════════
    # SEND SEGMENT
    # ═══════════════════════════════════════════════════════════════════
    
    def _send_segment(self):
        """Send the audio segment to the server."""
        
        # CRITICAL: Don't send if session ended (race condition fix)
        if not self.session_active:
            self.get_logger().warn(f'🚫 Segment BLOCKED - session not active (had {len(self.audio_buffer)} samples)')
            self.audio_buffer = []
            return
            
        if getattr(self, 'ignore_segment', False):
            self.get_logger().warn('🚫 Segment BLOCKED - ignore flag set (barge-in)')
            self.audio_buffer = []
            self.ignore_segment = False
            return
        
        if not self.audio_buffer:
            self.get_logger().warn('Empty buffer, skipping')
            return
        
        # Check minimum length
        if len(self.audio_buffer) < self.min_samples:
            duration = len(self.audio_buffer) / self.sample_rate
            self.get_logger().warn(
                f'Segment too short ({duration:.2f}s < {self.min_segment_seconds}s), skipping'
            )
            self.audio_buffer = []
            return
        
        # Compute duration
        duration = len(self.audio_buffer) / self.sample_rate
        
        self.get_logger().debug(f'📤 Sending audio segment: {duration:.2f}s ({len(self.audio_buffer)} samples)')
        
        # Create and publish the message
        msg = Audio()
        msg.sample_rate = self.sample_rate
        msg.channels = self.channels
        msg.data = self.audio_buffer
        
        self.segment_pub.publish(msg)
        
        # Reset buffer
        self.audio_buffer = []
        self.get_logger().debug('✅ Segment sent to server')

    def _stop_playback_for_user_speech(self):
        """Stop any previous robot answer as soon as the user starts talking."""
        if not self.stop_playback_on_voice_start:
            return
        msg = Bool()
        msg.data = True
        self.stop_playback_pub.publish(msg)
        self.legacy_stop_pub.publish(msg)
        if self.is_robot_speaking:
            self.get_logger().info('⏹️ User started speaking - stopping robot playback')


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
