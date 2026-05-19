#!/usr/bin/env python3
"""
wake_word_node.py - Multi-Keyword Wake Word Detection

FEATURES (synced with Conversational_Robot Python):
  - Support for MULTIPLE ONNX models (wake + stop)
  - `kind` field: 'wake' or 'stop' for each keyword
  - Goodbye/stop detection to end the session
  - Cooldown per keyword

EXPLANATION:
- This node LISTENS on /audio_raw (microphone audio)
- When it detects "hello robot", it PUBLISHES on /wake_detected
- When it detects "goodbye robot" or "stop", it PUBLISHES on /end_session
- The server or other nodes can then know whether the session is active/inactive
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════════════════════

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio, WakeWord
from std_msgs.msg import Bool, String
import numpy as np
import time
import os
from pathlib import Path

# Try to import OpenWakeWord
try:
    import logging
    import warnings
    
    # Suppress "Tried to import the tflite runtime" and other model-loading noise
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        logging.getLogger().setLevel(logging.ERROR)
        from openwakeword.model import Model as OWWModel
        logging.getLogger().setLevel(logging.INFO)  # Restore
        
    OPENWAKEWORD_AVAILABLE = True
except ImportError:
    OPENWAKEWORD_AVAILABLE = False
    print("⚠️ OpenWakeWord not installed. Run: pip install openwakeword")


# ═══════════════════════════════════════════════════════════════════
# NODE CLASS
# ═══════════════════════════════════════════════════════════════════

class WakeWordNode(Node):
    """
    ROS2 node that detects multiple wake/stop words.
    
    Operation:
    1. Receives audio on /audio_raw (from audio_capture_node)
    2. Processes with OpenWakeWord (multiple ONNX models)
    3. When it detects a "wake" keyword, publishes True on /wake_detected
    4. When it detects a "stop" keyword, publishes True on /end_session
    """
    
    def __init__(self):
        super().__init__('wake_word_node')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETERS
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('threshold', 0.5)     # Default detection threshold
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('cooldown_ms', 1500)  # Cooldown between detections
        
        # Custom ONNX models - can be set from launch/YAML
        # Format: "path1:kind1,path2:kind2" (e.g., "/path/hello.onnx:wake,/path/goodbye.onnx:stop")
        self.declare_parameter('custom_models', '')
        
        # Per-model thresholds (JSON-like format)
        # Format: "label1:threshold1,label2:threshold2"
        self.declare_parameter('model_thresholds', '')
        
        self.threshold = self.get_parameter('threshold').value
        self.sample_rate = self.get_parameter('sample_rate').value
        self.cooldown_ms = self.get_parameter('cooldown_ms').value
        custom_models_str = self.get_parameter('custom_models').value
        model_thresholds_str = self.get_parameter('model_thresholds').value
        
        # ─────────────────────────────────────────────────────────
        # PARSE CUSTOM MODELS
        # ─────────────────────────────────────────────────────────
        self.keywords = {}  # label -> {path, kind, threshold, last_hit}
        
        # Parse custom model paths
        if custom_models_str:
            self.get_logger().debug(f'📦 Parsing custom_models: {custom_models_str}')
            for entry in custom_models_str.split(','):
                entry = entry.strip()
                self.get_logger().debug(f'  → Entry: {entry}')
                if ':' in entry:
                    parts = entry.rsplit(':', 1)
                    path_str = parts[0]
                    kind = parts[1].lower() if len(parts) > 1 else 'wake'
                    path = Path(path_str).expanduser()
                    if path.exists():
                        label = path.stem
                        self.keywords[label] = {
                            'path': str(path),
                            'kind': kind,
                            'threshold': self.threshold,
                            'last_hit': 0.0
                        }
                        self.get_logger().debug(f'  ✓ Loaded model: {label} (kind={kind})')
                    else:
                        self.get_logger().warn(f'  ✗ Model not found: {path}')
        
        # Parse individual thresholds
        if model_thresholds_str:
            for entry in model_thresholds_str.split(','):
                entry = entry.strip()
                if ':' in entry:
                    parts = entry.split(':')
                    if len(parts) == 2:
                        label, thr = parts
                        if label in self.keywords:
                            try:
                                self.keywords[label]['threshold'] = float(thr)
                            except ValueError:
                                pass
        
        # ─────────────────────────────────────────────────────────
        # STATE
        # ─────────────────────────────────────────────────────────
        self.session_active = False  # True when the session is active
        self.audio_buffer = []       # Buffer for audio accumulation
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER - receive microphone audio
        # ─────────────────────────────────────────────────────────
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHERS
        # ─────────────────────────────────────────────────────────
        self.wake_pub = self.create_publisher(Bool, '/wake_detected', 10)
        self.wake_word_pub = self.create_publisher(WakeWord, '/wake_word', 10)
        self.session_pub = self.create_publisher(Bool, '/session_active', 10)
        self.end_session_pub = self.create_publisher(Bool, '/end_session', 10)
        self.tts_stop_pub = self.create_publisher(Bool, '/tts_stop', 10)
        
        # Publisher for TTS commands (cache playback)
        self.tts_cmd_pub = self.create_publisher(String, '/tts_command', 10)
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER for external session end
        # ─────────────────────────────────────────────────────────
        self.external_end_sub = self.create_subscription(
            Bool,
            '/end_session_external',
            self.external_end_session_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # OPENWAKEWORD INITIALIZATION
        # ─────────────────────────────────────────────────────────
        self.oww_model = None
        if OPENWAKEWORD_AVAILABLE:
            try:
                model_paths = [kw['path'] for kw in self.keywords.values()] if self.keywords else None
                
                if model_paths:
                    # Load custom models
                    self.oww_model = OWWModel(wakeword_models=model_paths)
                    self.get_logger().debug(f'🔔 Wake Word Node started with {len(model_paths)} custom models')
                else:
                    # Use default built-in models
                    self.oww_model = OWWModel()
                    self.get_logger().debug('🔔 Wake Word Node started with default models')
                
                self.get_logger().debug(f'   threshold={self.threshold}, cooldown={self.cooldown_ms}ms')
                
            except Exception as e:
                self.get_logger().error(f'❌ Failed to load OpenWakeWord: {e}')
                self.get_logger().warn('⚠️ Running in dummy mode (no wake word detection)')
                self.oww_model = None
        else:
            self.get_logger().warn('⚠️ OpenWakeWord not available - using dummy mode')
            self.get_logger().info('🔔 Wake Word Node started (dummy mode)')
    
    # ═══════════════════════════════════════════════════════════════════
    # AUDIO CALLBACK - process each audio chunk
    # ═══════════════════════════════════════════════════════════════════
    def audio_callback(self, msg: Audio):
        """Process audio for wake/stop word detection."""
        
        # Convert to numpy array
        audio = np.array(msg.data, dtype=np.int16)
        
        # Add to buffer
        self.audio_buffer.extend(audio.tolist())

        # Track chunk count for periodic logging
        if not hasattr(self, 'audio_debug_count'): self.audio_debug_count = 0
        self.audio_debug_count += 1
        
        # OpenWakeWord typically expects chunks of 1280 samples (80ms at 16kHz)
        # for optimal performance (though it handles streaming internally).
        MIN_SAMPLES = 1280
        
        if len(self.audio_buffer) >= MIN_SAMPLES:
            # Extract exactly MIN_SAMPLES
            audio_chunk = np.array(self.audio_buffer[:MIN_SAMPLES], dtype=np.int16)
            
            # --- DEBUG: Save Audio to WAV for verification (DISABLED) ---
            # if not hasattr(self, 'debug_wav_buffer'):
            #     self.debug_wav_buffer = []

            # # Capture first 3 seconds (16000 * 3 = 48000 samples)
            # if len(self.debug_wav_buffer) < 48000:
            #     self.debug_wav_buffer.extend(audio_chunk.tolist())
            #     # self.get_logger().info(f'🎤 Debug buffer filling: {len(self.debug_wav_buffer)}/48000')
                
            #     if len(self.debug_wav_buffer) >= 48000:
            #         try:
            #             import soundfile as sf
            #             wav_path = '/home/delia/ros2_ws/debug_wake_audio.wav'
                        
            #             # Convert to numpy int16 array explicitly
            #             wav_data = np.array(self.debug_wav_buffer, dtype=np.int16)
                        
            #             # Verify it's not silence
            #             rms_debug = np.sqrt(np.mean(wav_data.astype(np.float32)**2))
            #             self.get_logger().info(f'🎤 Debug WAV RMS: {rms_debug:.2f}')
                        
            #             # Write with explicit subtype
            #             sf.write(wav_path, wav_data, 16000, subtype='PCM_16')
                        
            #             self.get_logger().warn(f'💾 DEBUG WAV SAVED: {wav_path}')
            #             self.get_logger().warn('👉 Please play this file to verify audio quality!')
            #         except ImportError:
            #             self.get_logger().error("Cannot save debug WAV: soundfile not installed")
            #         except Exception as e:
            #             self.get_logger().error(f"Error saving WAV: {e}")
            # -------------------------------------------------

            # Slide window: In standard OWW streaming, we usually feed chunks of 1280.
            # We can either consume all 1280 (no overlap) or slide by a smaller step.
            # The standard OWW `predict` method is stateful, so we just feed it sequential chunks.
            # We will consume the whole chunk to keep it real-time and simple.
            self.audio_buffer = self.audio_buffer[MIN_SAMPLES:]
            
            if self.oww_model is not None:
                try:
                    # ✅ OpenWakeWord expects int16 audio directly!
                    # Do NOT convert to float32 - that was causing near-zero scores
                    prediction = self.oww_model.predict(audio_chunk)
                    
                    # Check scores for all models
                    self._check_predictions(prediction)


                    if self.audio_debug_count % 25 == 0:
                        scores_str = " | ".join([f"{k}: {v:.3f}" for k, v in prediction.items()])
                        self.get_logger().debug(f'👀 Scores: {scores_str}')
                    
                except Exception as e:
                    self.get_logger().error(f'OpenWakeWord prediction error: {e}')
            else:
                # Model is None
                if not hasattr(self, 'model_none_warned'):
                    self.get_logger().error('❌ oww_model is NONE! Initialization failed?')
                    self.model_none_warned = True
    
    def _check_predictions(self, prediction: dict):
        """Check predictions and trigger actions."""
        now_ms = time.time() * 1000
        
        for model_name, scores in prediction.items():
            if isinstance(scores, dict):
                score = max(scores.values()) if scores else 0.0
            else:
                score = float(scores) if scores else 0.0
            
            # Find configuration for this model (or use default)
            if model_name in self.keywords:
                kw_cfg = self.keywords[model_name]
                threshold = kw_cfg['threshold']
                kind = kw_cfg['kind']
                last_hit = kw_cfg.get('last_hit', 0.0)
            else:
                threshold = self.threshold
                kind = 'wake'
                last_hit = 0.0
            
            # Check threshold and cooldown
            cooldown_passed = (now_ms - last_hit) > self.cooldown_ms
            
            if score >= threshold and cooldown_passed:
                # Update last_hit
                if model_name in self.keywords:
                    self.keywords[model_name]['last_hit'] = now_ms
                
                self.get_logger().debug(f'🔔 Detected "{model_name}" (kind={kind}, score={score:.2f})')
                
                if kind == 'stop':
                    # STOP total + End Session (ex: "goodbye robot")
                    self._end_session(model_name, score)
                elif kind == 'barge_in':
                    # STOP TTS only, session remains active (e.g., "stop robot")
                    self._trigger_barge_in(model_name, score)
                else:  # wake
                    if not self.session_active:
                        self._activate_session(model_name, score)
                    else:
                        # If already active, we can do an optional re-activate/ack
                        self.get_logger().debug('ℹ️ Session already active (wake word ignored)')
    
    # ═══════════════════════════════════════════════════════════════════
    # SESSION ACTIVATION (wake word)
    # ═══════════════════════════════════════════════════════════════════
    def _activate_session(self, model_name: str, score: float):
        """Activate the session when a wake word is detected."""
        self.session_active = True
        
        self.get_logger().info(f'🟢 Session ACTIVE via "{model_name}" (score={score:.2f})')

        wake_event = WakeWord()
        wake_event.header.stamp = self.get_clock().now().to_msg()
        wake_event.word = model_name
        wake_event.score = float(score)
        self.wake_word_pub.publish(wake_event)
        
        # Publish to /wake_detected
        wake_msg = Bool()
        wake_msg.data = True
        self.wake_pub.publish(wake_msg)
        
        # Publish session state
        session_msg = Bool()
        session_msg.data = True
        self.session_pub.publish(session_msg)
        
        # Send acknowledgement command to TTS
        tts_cmd = String()
        tts_cmd.data = 'ack_en'
        self.tts_cmd_pub.publish(tts_cmd)
    
    # ═══════════════════════════════════════════════════════════════════
    # TTS STOP (BARGE-IN ONLY)
    # ═══════════════════════════════════════════════════════════════════
    def _trigger_barge_in(self, model_name: str, score: float):
        """Stop only TTS, keep the session active."""
        self.get_logger().debug(f'✋ BARGE-IN via "{model_name}" (score={score:.2f}) - Stopping TTS only')
        
        # Stop TTS immediately
        stop_msg = Bool()
        stop_msg.data = True
        self.tts_stop_pub.publish(stop_msg)
        
        # OPTIONAL: Reset VAD if we want to be safe
        # But VAD already listens while session_active=True


    # ═══════════════════════════════════════════════════════════════════
    # END SESSION (stop/goodbye word)
    # ═══════════════════════════════════════════════════════════════════
    def _end_session(self, model_name: str, score: float):
        """End the session when a stop/goodbye word is detected."""
        self.get_logger().info(f'🔴 Session ENDED via "{model_name}" (score={score:.2f})')
        
        # Stop TTS immediately
        stop_msg = Bool()
        stop_msg.data = True
        self.tts_stop_pub.publish(stop_msg)
        
        # Publish to /end_session
        self.end_session_pub.publish(stop_msg)
        
        if self.session_active:
            self.session_active = False
            
            # Publish session state
            session_msg = Bool()
            session_msg.data = False
            self.session_pub.publish(session_msg)
            
            # Send goodbye to TTS
            tts_cmd = String()
            tts_cmd.data = 'goodbye_en'
            self.tts_cmd_pub.publish(tts_cmd)
    
    def external_end_session_callback(self, msg: Bool):
        """Callback for ending the session externally."""
        if msg.data and self.session_active:
            self.session_active = False
            self.get_logger().info('🔴 Session ENDED externally')
            
            session_msg = Bool()
            session_msg.data = False
            self.session_pub.publish(session_msg)
    
    def reset_session(self):
        """Reset the session to standby."""
        self.session_active = False
        self.get_logger().info('⏳ Standby')


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
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()
