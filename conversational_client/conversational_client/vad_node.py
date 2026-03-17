#!/usr/bin/env python3
"""
vad_node.py
Voice Activity Detection - detects when the user is speaking.

EXPLANATION:
- Receives audio on /audio_raw
- Analyzes whether the audio contains voice (not just noise/silence)
- Publishes True/False on /voice_activity
- Used by other nodes to:
  - Know when to send audio to the server (only when speaking)
  - Barge-in (stop TTS when the user speaks)
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════════════════════

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio, WakeWord
from std_msgs.msg import Bool
import numpy as np

# Try to import WebRTC VAD (simple variant)
try:
    import webrtcvad
    WEBRTCVAD_AVAILABLE = True
except ImportError:
    WEBRTCVAD_AVAILABLE = False
    print("⚠️ webrtcvad not installed. Run: pip install webrtcvad")


# ═══════════════════════════════════════════════════════════════════
# NODE CLASS
# ═══════════════════════════════════════════════════════════════════

class VADNode(Node):
    """
    ROS2 node for Voice Activity Detection.
    
    Operation:
    1. Receives audio on /audio_raw
    2. Analyzes whether it contains voice (energy + typical voice frequencies)
    3. Publishes True/False on /voice_activity
    """
    
    def __init__(self):
        super().__init__('vad_node')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETERS
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('aggressiveness', 2)  # 0-3, 3 = more aggressive
        self.declare_parameter('energy_threshold', 500)  # RMS energy threshold
        self.declare_parameter('wake_word_enabled', True)
        self.declare_parameter('session_timeout', 8.0)

        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.aggressiveness = self.get_parameter('aggressiveness').value
        self.energy_threshold = self.get_parameter('energy_threshold').value
        self.wake_word_enabled = self.get_parameter('wake_word_enabled').value
        self.session_timeout = self.get_parameter('session_timeout').value
        
        # ─────────────────────────────────────────────────────────
        # STATE
        # ─────────────────────────────────────────────────────────
        self.is_speaking = False          # Current state
        self.speech_frames = 0            # Consecutive frames with voice
        self.silence_frames = 0           # Consecutive frames without voice
        self.min_speech_frames = 5        # Frames to confirm voice (raised to reduce false positives)
        self.min_silence_frames = 10      # Frames to confirm silence
        self.is_robot_speaking = False    # True when the robot is speaking (TTS playback)
        self.is_gate_open = not self.wake_word_enabled
        self.session_timer = None
        self.is_speaking = False
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER
        # ─────────────────────────────────────────────────────────
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # TTS state - when the robot is speaking, ignore VAD
        self.robot_speaking_sub = self.create_subscription(
            Bool,
            '/is_speaking',
            self.robot_speaking_callback,
            10
        )

        self.wake_word_sub = self.create_subscription(
            WakeWord,
            '/wake_word',
            self.wake_word_callback,
            10
        )
        
        # Subscriber for session state (from wake_word_node)
        self.session_sub = self.create_subscription(
            Bool,
            '/session_active',
            self.session_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHER
        # ─────────────────────────────────────────────────────────
        self.vad_pub = self.create_publisher(Bool, '/voice_activity', 10)
        self.end_session_pub = self.create_publisher(Bool, '/end_session_external', 10)
        
        # ─────────────────────────────────────────────────────────
        # WEBRTC VAD INITIALIZATION
        # ─────────────────────────────────────────────────────────
        self.vad = None
        if WEBRTCVAD_AVAILABLE:
            try:
                self.vad = webrtcvad.Vad(self.aggressiveness)
                self.get_logger().info(
                    f'🎯 VAD Node started (WebRTC, aggressiveness={self.aggressiveness})'
                )
            except Exception as e:
                self.get_logger().error(f'❌ Failed to init WebRTC VAD: {e}')
        else:
            self.get_logger().warn('⚠️ WebRTC VAD not available - using energy-based detection')
            self.get_logger().info(f'🎯 VAD Node started (energy threshold={self.energy_threshold})')
        
        self.frame_count = 0
        
        # Timer for the "READY TO LISTEN" reminder
        self.reminder_timer = None
    
    def _start_reminder_timer(self):
        """Start the periodic reminder timer."""
        if self.reminder_timer:
            self.reminder_timer.cancel()
        # Reminder every 3 seconds
        self.reminder_timer = self.create_timer(3.0, self._on_reminder)
        # Log immediately the first time
        self.get_logger().info('🎤 READY TO LISTEN - speak now!')
    
    def _stop_reminder_timer(self):
        """Stop the reminder timer."""
        if self.reminder_timer:
            self.reminder_timer.cancel()
            self.reminder_timer = None
    
    def _on_reminder(self):
        """Callback for the periodic reminder."""
        # Only if the gate is open and the robot is not speaking
        if self.is_gate_open and not self.is_robot_speaking and not self.is_speaking:
            self.get_logger().info('🎤 READY TO LISTEN - speak now!')
        else:
            # Stop the timer if conditions are no longer met
            self._stop_reminder_timer()
    
    # ═══════════════════════════════════════════════════════════════════
    # AUDIO CALLBACKS
    # ═══════════════════════════════════════════════════════════════════
    
    def robot_speaking_callback(self, msg: Bool):
        """Callback for TTS playback state."""
        was_speaking = self.is_robot_speaking
        self.is_robot_speaking = msg.data
        
        # When the robot starts speaking, stop the timeout timer and reminder
        if self.is_robot_speaking and not was_speaking:
            self._stop_reminder_timer()
            if self.session_timer:
                self.session_timer.cancel()
                self.session_timer = None
            self.get_logger().info('🤖 Robot is SPEAKING - VAD paused to avoid echo')
        
        # When the robot finishes speaking, restart the timer and reminder
        elif not self.is_robot_speaking and was_speaking:
            if self.is_gate_open:
                self.get_logger().info('👂 Robot stopped - VAD resumed')
                self._reset_session_timer()
                self._start_reminder_timer()

    def session_callback(self, msg: Bool):
        """Callback for session state (from wake_word_node)."""
        if msg.data:
            # Session active - open gate
            if not self.is_gate_open:
                self.get_logger().debug('🔓 Session started - Opening Gate!')
                self.is_gate_open = True
                self._reset_session_timer()
        else:
            # Session ended - close gate
            if self.is_gate_open:
                self.get_logger().debug('🔒 Session ended - Closing Gate!')
                self.is_gate_open = False
                self.is_speaking = False
                self.speech_frames = 0
                self.silence_frames = 0
                
                # Stop session timer
                if self.session_timer:
                    self.session_timer.cancel()
                    self.session_timer = None
                
                # Publish voice_activity=False to stop ASR
                msg_out = Bool()
                msg_out.data = False
                self.vad_pub.publish(msg_out)

    def wake_word_callback(self, msg: WakeWord):
        # Open the gate when "hello robot" is detected
        self.get_logger().debug(f"🔓 Wake Word Detected: '{msg.word}' - Opening Gate!")
        self.is_gate_open = True
        self._reset_session_timer()

    def _reset_session_timer(self):
        # Reset the session timer
        if self.session_timer:
            self.session_timer.cancel()
        self.session_timer = self.create_timer(self.session_timeout, self._on_session_timeout)

    def _on_session_timeout(self):
        # Do not close the gate if the robot is speaking
        if self.is_robot_speaking:
            self.get_logger().debug('⏳ Session timeout skipped (robot still speaking)')
            return
            
        # Close the gate when time expires
        self.get_logger().info("🔒 Session Timeout - Closing Gate.")
        self.is_gate_open = False
        self.is_speaking = False

        # Notify that speech ended
        msg = Bool()
        msg.data = False
        self.vad_pub.publish(msg)

        # Notify wake_word_node to reset session_active
        end_msg = Bool()
        end_msg.data = True
        self.end_session_pub.publish(end_msg)

        if self.session_timer:
            self.session_timer.cancel()
            self.session_timer = None
    
    def audio_callback(self, msg: Audio):
        """Analyze each audio chunk for voice activity."""

        # If the gate is closed, ignore everything
        if not self.is_gate_open:
            return
        
        # Do not process VAD when the robot is speaking (prevents false positives)
        if self.is_robot_speaking:
            return
        
        audio = np.array(msg.data, dtype=np.int16)
        
        # Detect voice
        has_voice = self._detect_voice(audio)
        
        # Debounce logic (avoid flickering)
        if has_voice:
            self.speech_frames += 1
            self.silence_frames = 0

            # If speaking, reset the timer
            if self.is_gate_open:
                self._reset_session_timer()
        else:
            self.silence_frames += 1
            self.speech_frames = 0
        
        # Change state only after a few consecutive frames
        old_state = self.is_speaking
        
        if not self.is_speaking and self.speech_frames >= self.min_speech_frames:
            self.is_speaking = True
            self._stop_reminder_timer()  # Stop reminder when the user speaks
            self.get_logger().debug('🗣️ Voice DETECTED - user is speaking')
        elif self.is_speaking and self.silence_frames >= self.min_silence_frames:
            self.is_speaking = False
            self.get_logger().debug('🤫 Voice ENDED - silence detected')
            # Restart reminder if still in listening mode
            if self.is_gate_open and not self.is_robot_speaking:
                self._start_reminder_timer()
        
        # Publish state
        msg_out = Bool()
        msg_out.data = self.is_speaking
        self.vad_pub.publish(msg_out)
        
        self.frame_count += 1
    
    # ═══════════════════════════════════════════════════════════════════
    # VOICE DETECTION
    # ═══════════════════════════════════════════════════════════════════
    def _detect_voice(self, audio: np.ndarray) -> bool:
        """
        Detect whether audio contains voice.
        Uses WebRTC VAD or falls back to energy.
        """
        
        if self.vad is not None:
            # ─────────────────────────────────────────────────────
            # METHOD 1: WebRTC VAD (more accurate)
            # ─────────────────────────────────────────────────────
            try:
                # WebRTC VAD requires exactly 10, 20, or 30ms of audio
                # At 16kHz: 160, 320, or 480 samples
                audio_bytes = audio.tobytes()
                
                # Adjust length if needed
                frame_len = len(audio)
                if frame_len == 320:  # 20ms la 16kHz
                    return self.vad.is_speech(audio_bytes, self.sample_rate)
                else:
                    # Energy fallback for non-standard lengths
                    return self._energy_based_detection(audio)
            except Exception:
                return self._energy_based_detection(audio)
        else:
            # ─────────────────────────────────────────────────────
            # METHOD 2: RMS Energy (simple fallback)
            # ─────────────────────────────────────────────────────
            return self._energy_based_detection(audio)
    
    def _energy_based_detection(self, audio: np.ndarray) -> bool:
        """Simple detection based on audio energy (RMS)."""
        # Compute RMS (Root Mean Square) = average energy
        rms = np.sqrt(np.mean(audio.astype(np.float32) ** 2))
        return rms > self.energy_threshold


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main(args=None):
    rclpy.init(args=args)
    node = VADNode()
    
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
