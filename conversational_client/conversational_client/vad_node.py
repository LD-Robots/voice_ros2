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

ENGINES (selectable via 'vad_engine' parameter):
  - "silero"  : Deep Learning VAD (best accuracy, robust to noise)
  - "webrtc"  : WebRTC VAD (legacy, frequency-based)
  - "hardware": ReSpeaker on-board DSP VAD
  - "energy"  : Simple RMS energy threshold (last resort)
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════════════════════

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio, WakeWord
from std_msgs.msg import Bool, String
import numpy as np
import json
import struct
import usb.core
import usb.util

# Try to import WebRTC VAD (legacy fallback)
try:
    import webrtcvad
    WEBRTCVAD_AVAILABLE = True
except ImportError:
    WEBRTCVAD_AVAILABLE = False

# Try to import Silero VAD via torch.hub
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════
# SILERO VAD WRAPPER
# ═══════════════════════════════════════════════════════════════════

class SileroVADEngine:
    """Wraps the Silero VAD model for frame-by-frame inference."""

    def __init__(self, threshold: float = 0.5,
                 min_speech_duration_ms: int = 50,
                 min_silence_duration_ms: int = 550,
                 sample_rate: int = 16000,
                 logger=None):
        self.threshold = threshold
        self.min_speech_duration_ms = min_speech_duration_ms
        self.min_silence_duration_ms = min_silence_duration_ms
        self.sample_rate = sample_rate
        self.logger = logger

        # Silero works on 512-sample windows at 16kHz (32ms)
        self.window_size = 512 if sample_rate == 16000 else 256

        # State machine
        self._is_speech = False
        self._speech_ms = 0
        self._silence_ms = 0
        self._frame_ms = (self.window_size / self.sample_rate) * 1000.0

        # Load the model
        self.model, _ = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            trust_repo=True
        )
        self.model.eval()
        if logger:
            logger.info('🧠 Silero VAD model loaded successfully')

    def reset_states(self):
        """Reset the model's internal RNN states."""
        self.model.reset_states()
        self._is_speech = False
        self._speech_ms = 0
        self._silence_ms = 0

    def process_frame(self, audio: np.ndarray) -> bool:
        """
        Process an audio frame and return True if speech is active.
        Applies min_speech_duration and min_silence_duration logic.
        """
        # Split into window_size chunks and process each
        speech_detected_in_any = False

        for i in range(0, len(audio), self.window_size):
            chunk = audio[i:i + self.window_size]
            if len(chunk) < self.window_size:
                # Pad short final chunk with zeros
                chunk = np.pad(chunk, (0, self.window_size - len(chunk)))

            # Convert to float32 tensor normalised to [-1, 1]
            tensor = torch.from_numpy(chunk.astype(np.float32) / 32768.0)

            # Run inference
            with torch.no_grad():
                prob = self.model(tensor, self.sample_rate).item()

            if prob >= self.threshold:
                speech_detected_in_any = True

        # State machine with hangover logic
        if speech_detected_in_any:
            self._speech_ms += self._frame_ms * max(1, len(audio) // self.window_size)
            self._silence_ms = 0

            if not self._is_speech and self._speech_ms >= self.min_speech_duration_ms:
                self._is_speech = True
        else:
            self._silence_ms += self._frame_ms * max(1, len(audio) // self.window_size)
            self._speech_ms = 0

            if self._is_speech and self._silence_ms >= self.min_silence_duration_ms:
                self._is_speech = False

        return self._is_speech


# ═══════════════════════════════════════════════════════════════════
# RESPEAKER TUNING HELPER (Simplified)
# ═══════════════════════════════════════════════════════════════════

class ReSpeakerVAD:
    """Helper to read VOICEACTIVITY from ReSpeaker hardware via USB."""
    def __init__(self, vid=0x2886, pid=0x0018):
        self.dev = usb.core.find(idVendor=vid, idProduct=pid)
        self.TIMEOUT = 1000

    def is_connected(self):
        return self.dev is not None

    def read_vad(self):
        if not self.dev:
            return False
        try:
            # VOICEACTIVITY register: id=19, offset=32, type=int
            # Control transfer parameters for reading:
            # request=0, request_type=IN|VENDOR|DEVICE, value=0x80|offset, index=id
            cmd = 0x80 | 32 | 0x40 # 0x40 is for int type
            response = self.dev.ctrl_transfer(
                usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
                0, cmd, 19, 8, self.TIMEOUT)
            if response:
                # Use tostring() for older pyusb or memoryview for newer
                try:
                    data = response.tobytes()
                except AttributeError:
                    data = response.tostring()
                return struct.unpack(b'ii', data)[0] == 1
        except Exception:
            pass
        return False


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
        self.declare_parameter('vad_engine', 'silero')  # "silero" | "webrtc" | "hardware" | "energy"

        # Silero VAD parameters
        self.declare_parameter('silero_activation_threshold', 0.5)
        self.declare_parameter('silero_min_speech_duration_ms', 50)
        self.declare_parameter('silero_min_silence_duration_ms', 550)

        # WebRTC VAD parameters (legacy)
        self.declare_parameter('aggressiveness', 2)  # 0-3, 3 = more aggressive
        self.declare_parameter('quiet_vad_aggressiveness', 1)   # More permissive in silence — catches whispers
        self.declare_parameter('noisy_vad_aggressiveness', 3)   # More strict in noise — rejects non-speech

        # General parameters
        self.declare_parameter('energy_threshold', 500)  # RMS energy threshold
        self.declare_parameter('wake_word_enabled', True)
        self.declare_parameter('session_timeout', 8.0)
        self.declare_parameter('min_speech_frames', 5)
        self.declare_parameter('min_silence_frames', 14)
        self.declare_parameter('capture_during_playback', False)
        self.declare_parameter('use_hardware_vad', False)

        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.vad_engine = str(self.get_parameter('vad_engine').value).strip().lower()
        self.aggressiveness = self.get_parameter('aggressiveness').value
        self.base_aggressiveness = self.aggressiveness  # Preserved as the neutral/moderate baseline
        self.quiet_vad_aggressiveness = max(0, min(3, int(self.get_parameter('quiet_vad_aggressiveness').value)))
        self.noisy_vad_aggressiveness = max(0, min(3, int(self.get_parameter('noisy_vad_aggressiveness').value)))
        self.energy_threshold = self.get_parameter('energy_threshold').value
        self.base_energy_threshold = self.energy_threshold
        self.wake_word_enabled = self.get_parameter('wake_word_enabled').value
        self.session_timeout = self.get_parameter('session_timeout').value
        self.min_speech_frames = max(1, int(self.get_parameter('min_speech_frames').value))
        self.min_silence_frames = max(1, int(self.get_parameter('min_silence_frames').value))
        self.capture_during_playback = self.get_parameter('capture_during_playback').value
        self.use_hardware_vad = self.get_parameter('use_hardware_vad').value

        # Legacy override: use_hardware_vad=true forces hardware engine
        if self.use_hardware_vad:
            self.vad_engine = 'hardware'
        
        # ─────────────────────────────────────────────────────────
        # STATE
        # ─────────────────────────────────────────────────────────
        self.is_speaking = False          # Current state
        self.speech_frames = 0            # Consecutive frames with voice
        self.silence_frames = 0           # Consecutive frames without voice
        self.is_robot_speaking = False    # True when the robot is speaking (TTS playback)
        self.is_gate_open = not self.wake_word_enabled
        self.session_timer = None
        self.conversation_paused = False
        
        # ─────────────────────────────────────────────────────────
        # ENGINE INITIALIZATION
        # ─────────────────────────────────────────────────────────
        self.silero_engine = None
        self.hw_vad = None
        self.vad = None  # WebRTC VAD instance

        if self.vad_engine == 'silero':
            if TORCH_AVAILABLE:
                try:
                    self.silero_engine = SileroVADEngine(
                        threshold=float(self.get_parameter('silero_activation_threshold').value),
                        min_speech_duration_ms=int(self.get_parameter('silero_min_speech_duration_ms').value),
                        min_silence_duration_ms=int(self.get_parameter('silero_min_silence_duration_ms').value),
                        sample_rate=self.sample_rate,
                        logger=self.get_logger(),
                    )
                    self.get_logger().info(
                        f'🧠 VAD Node started (Silero Deep Learning, '
                        f'threshold={self.silero_engine.threshold}, '
                        f'min_speech={self.silero_engine.min_speech_duration_ms}ms, '
                        f'min_silence={self.silero_engine.min_silence_duration_ms}ms)'
                    )
                except Exception as e:
                    self.get_logger().error(f'❌ Failed to load Silero VAD: {e}')
                    self.get_logger().warn('⚠️ Falling back to WebRTC VAD')
                    self.vad_engine = 'webrtc'
            else:
                self.get_logger().warn('⚠️ PyTorch not installed — cannot use Silero VAD. Falling back to WebRTC.')
                self.vad_engine = 'webrtc'

        if self.vad_engine == 'hardware':
            self.hw_vad = ReSpeakerVAD()
            if self.hw_vad.is_connected():
                self.get_logger().info('⚡ Hardware VAD: ReSpeaker detected and enabled!')
            else:
                self.get_logger().warn('⚠️ Hardware VAD: ReSpeaker NOT FOUND. Falling back to WebRTC.')
                self.vad_engine = 'webrtc'

        if self.vad_engine == 'webrtc':
            if WEBRTCVAD_AVAILABLE:
                try:
                    self.vad = webrtcvad.Vad(self.aggressiveness)
                    self.get_logger().info(
                        f'🎯 VAD Node started (WebRTC, aggressiveness={self.aggressiveness})'
                    )
                except Exception as e:
                    self.get_logger().error(f'❌ Failed to init WebRTC VAD: {e}')
                    self.vad_engine = 'energy'
            else:
                self.get_logger().warn('⚠️ WebRTC VAD not available — falling back to energy-based detection')
                self.vad_engine = 'energy'

        if self.vad_engine == 'energy':
            self.get_logger().info(f'🎯 VAD Node started (energy threshold={self.energy_threshold})')

        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER
        # ─────────────────────────────────────────────────────────
        self.audio_sub = self.create_subscription(
            Audio,
            'audio_raw',
            self.audio_callback,
            10
        )
        
        # TTS state - when the robot is speaking, ignore VAD
        self.robot_speaking_sub = self.create_subscription(
            Bool,
            'is_speaking',
            self.robot_speaking_callback,
            10
        )

        self.wake_word_sub = self.create_subscription(
            WakeWord,
            'wake_word',
            self.wake_word_callback,
            10
        )
        
        # Subscriber for session state (from wake_word_node)
        self.session_sub = self.create_subscription(
            Bool,
            'session_active',
            self.session_callback,
            10
        )
        self.pause_sub = self.create_subscription(
            Bool,
            'conversation_pause',
            self.pause_callback,
            10
        )
        self.env_sub = self.create_subscription(
            String,
            'acoustic_environment',
            self.env_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHER
        # ─────────────────────────────────────────────────────────
        self.vad_pub = self.create_publisher(Bool, 'voice_activity', 10)
        self.end_session_pub = self.create_publisher(Bool, 'end_session_external', 10)
        
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
        self.get_logger().debug('🎤 READY TO LISTEN - speak now!')
    
    def _stop_reminder_timer(self):
        """Stop the reminder timer."""
        if self.reminder_timer:
            self.reminder_timer.cancel()
            self.reminder_timer = None
    
    def _on_reminder(self):
        """Callback for the periodic reminder."""
        # Only if the gate is open and the robot is not speaking
        if self.is_gate_open and not self.is_robot_speaking and not self.is_speaking:
            self.get_logger().debug('🎤 READY TO LISTEN - speak now!')
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
            self.get_logger().debug('🤖 Robot is SPEAKING - please wait...')
        
        # When the robot finishes speaking, restart the timer and reminder
        elif not self.is_robot_speaking and was_speaking:
            # Reset Silero internal states after robot finishes speaking
            # to avoid stale RNN state from echo residuals
            if self.silero_engine is not None:
                self.silero_engine.reset_states()
            if self.is_gate_open:
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
                # Reset Silero states for a fresh session
                if self.silero_engine is not None:
                    self.silero_engine.reset_states()
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

    def pause_callback(self, msg: Bool):
        self.conversation_paused = bool(msg.data)

    def _on_session_timeout(self):
        if self.conversation_paused:
            self.get_logger().debug('⏳ Session timeout skipped (conversation is paused)')
            return
            
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
        if self.is_robot_speaking and not self.capture_during_playback:
            return
        
        audio = np.array(msg.data, dtype=np.int16)
        
        # Detect voice
        has_voice = self._detect_voice(audio)
        
        # For Silero, the engine handles its own debounce internally,
        # so we can use simplified state logic
        if self.vad_engine == 'silero' and self.silero_engine is not None:
            old_state = self.is_speaking
            self.is_speaking = has_voice

            if self.is_speaking and not old_state:
                self._stop_reminder_timer()
                self.get_logger().debug('🗣️ Voice DETECTED - user is speaking')
                if self.is_gate_open:
                    self._reset_session_timer()
            elif not self.is_speaking and old_state:
                self.get_logger().debug('🤫 Voice ENDED - silence detected')
                if self.is_gate_open and not self.is_robot_speaking:
                    self._start_reminder_timer()

        else:
            # Legacy debounce logic for WebRTC / energy / hardware
            if has_voice:
                self.speech_frames += 1
                self.silence_frames = 0
                if self.is_gate_open:
                    self._reset_session_timer()
            else:
                self.silence_frames += 1
                self.speech_frames = 0
            
            old_state = self.is_speaking
            
            if not self.is_speaking and self.speech_frames >= self.min_speech_frames:
                self.is_speaking = True
                self._stop_reminder_timer()
                self.get_logger().debug('🗣️ Voice DETECTED - user is speaking')
            elif self.is_speaking and self.silence_frames >= self.min_silence_frames:
                self.is_speaking = False
                self.get_logger().debug('🤫 Voice ENDED - silence detected')
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
        Dispatches to the configured engine.
        """
        
        # ─────────────────────────────────────────────────────
        # ENGINE: Silero Deep Learning VAD (best accuracy)
        # ─────────────────────────────────────────────────────
        if self.vad_engine == 'silero' and self.silero_engine is not None:
            try:
                return self.silero_engine.process_frame(audio)
            except Exception as e:
                self.get_logger().error(f'Silero VAD error: {e}', throttle_duration_sec=5.0)
                return self._energy_based_detection(audio)

        # ─────────────────────────────────────────────────────
        # ENGINE: Hardware VAD (ReSpeaker DSP)
        # ─────────────────────────────────────────────────────
        if self.vad_engine == 'hardware' and self.hw_vad:
            return self.hw_vad.read_vad()

        # ─────────────────────────────────────────────────────
        # ENGINE: WebRTC VAD (legacy)
        # ─────────────────────────────────────────────────────
        if self.vad_engine == 'webrtc' and self.vad is not None:
            try:
                # WebRTC VAD requires exactly 10, 20, or 30ms of audio (160, 320, or 480 samples at 16kHz)
                # Split the input frame into 20ms (320 samples) sub-frames to handle arbitrary chunk sizes.
                sub_frame_len = 320
                if len(audio) % sub_frame_len == 0 and len(audio) > 0:
                    for i in range(0, len(audio), sub_frame_len):
                        sub_frame = audio[i:i+sub_frame_len]
                        if self.vad.is_speech(sub_frame.tobytes(), self.sample_rate):
                            return True
                    return False
                
                # If we cannot split evenly into 20ms frames, check if single frame matches standard sizes
                frame_len = len(audio)
                if frame_len == 320:  # 20ms at 16kHz
                    return self.vad.is_speech(audio.tobytes(), self.sample_rate)
                else:
                    # Energy fallback for non-standard lengths
                    return self._energy_based_detection(audio)
            except Exception:
                return self._energy_based_detection(audio)

        # ─────────────────────────────────────────────────────
        # ENGINE: RMS Energy (simple fallback)
        # ─────────────────────────────────────────────────────
        return self._energy_based_detection(audio)
    
    def _energy_based_detection(self, audio: np.ndarray) -> bool:
        """Simple detection based on audio energy (RMS)."""
        # Compute RMS (Root Mean Square) = average energy
        rms = np.sqrt(np.mean(audio.astype(np.float32) ** 2))
        return rms > self.energy_threshold

    def env_callback(self, msg: String):
        try:
            data = json.loads(msg.data)
            state = data.get('state', 'moderate')

            # ── Energy threshold adjustment (existing logic, unchanged) ──
            if state == 'quiet':
                self.energy_threshold = self.base_energy_threshold * 0.5
            elif state == 'noisy':
                self.energy_threshold = self.base_energy_threshold * 1.9
            else:
                self.energy_threshold = self.base_energy_threshold

            # ── Silero VAD: adjust activation threshold based on environment ──
            if self.silero_engine is not None:
                if state == 'quiet':
                    self.silero_engine.threshold = max(0.15, float(
                        self.get_parameter('silero_activation_threshold').value) - 0.15)
                elif state == 'noisy':
                    self.silero_engine.threshold = min(0.85, float(
                        self.get_parameter('silero_activation_threshold').value) + 0.15)
                else:
                    self.silero_engine.threshold = float(
                        self.get_parameter('silero_activation_threshold').value)
                self.get_logger().debug(
                    f'🎚️ [VAD] Silero threshold adjusted for {state.upper()} environment: '
                    f'{self.silero_engine.threshold:.2f}'
                )

            # ── WebRTC VAD aggressiveness adjustment (legacy) ──
            # Only applies when WebRTC VAD is active (not hardware VAD, not energy-only fallback)
            if self.vad is not None:
                if state == 'quiet':
                    new_aggressiveness = self.quiet_vad_aggressiveness
                elif state == 'noisy':
                    new_aggressiveness = self.noisy_vad_aggressiveness
                else:
                    new_aggressiveness = self.base_aggressiveness

                if new_aggressiveness != self.aggressiveness:
                    self.vad.set_mode(new_aggressiveness)
                    self.aggressiveness = new_aggressiveness
                    self.get_logger().debug(
                        f'🎚️ [VAD] Aggressiveness adjusted for {state.upper()} environment: {new_aggressiveness}'
                    )

            self.get_logger().debug(
                f'VAD env update: state={state}, energy_threshold={self.energy_threshold:.1f}, aggressiveness={self.aggressiveness}'
            )
        except Exception as e:
            self.get_logger().error(f'Error parsing acoustic environment in VAD: {e}')


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
