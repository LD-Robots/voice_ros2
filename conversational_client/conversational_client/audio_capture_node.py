#!/usr/bin/env python3
"""
audio_capture_node.py
Capture audio from microphone and publish to /audio_raw using sounddevice.
Refactored to match legacy project configuration for better hardware compatibility.
"""

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import Bool, Int32
import numpy as np
import sys
import time

try:
    from conversational_client.respeaker_tuning import (
        ReSpeakerTuning,
        USB_AVAILABLE as RESPEAKER_USB_AVAILABLE,
        parse_tuning_overrides,
        resolve_profile,
    )
except Exception:
    ReSpeakerTuning = None
    RESPEAKER_USB_AVAILABLE = False

    def parse_tuning_overrides(_raw):
        return {}

    def resolve_profile(_name):
        return {}

# Replace PyAudio with sounddevice
try:
    import sounddevice as sd
    SD_AVAILABLE = True
except ImportError:
    SD_AVAILABLE = False
    print("⚠️ sounddevice not installed. Please install: pip install sounddevice")

class AudioCaptureNode(Node):
    def __init__(self):
        super().__init__('audio_capture_node')
        
        # Parameters
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('channels', 1)
        self.declare_parameter('chunk_ms', 20)
        self.declare_parameter('device_index', -1)
        self.declare_parameter('respeaker_mode', False)  # Use ReSpeaker 6-ch special mode
        self.declare_parameter('respeaker_channel', 0)   # 0=processed ASR audio, 5=playback reference
        self.declare_parameter('gain', 1.0)              # Digital gain multiplier
        self.declare_parameter('respeaker_tuning_enabled', False)
        self.declare_parameter('respeaker_vendor_id', 0x2886)
        self.declare_parameter('respeaker_product_id', 0x0018)
        self.declare_parameter('respeaker_tuning_profile', 'voice_assistant')
        self.declare_parameter('respeaker_tuning_overrides', '')
        self.declare_parameter('respeaker_doa_poll_interval_s', 1.0)
        self.declare_parameter('output_guard_enabled', True)
        self.declare_parameter('output_guard_duck_gain', 0.0)
        self.declare_parameter('output_guard_post_duck_gain', 0.35)
        self.declare_parameter('output_guard_hold_ms', 1200)
        self.declare_parameter('respeaker_ref_guard_enabled', True)
        self.declare_parameter('respeaker_ref_guard_reference_channel', 5)
        self.declare_parameter('respeaker_ref_guard_min_ref_dbfs', -38.0)
        self.declare_parameter('respeaker_ref_guard_corr_threshold', 0.80)
        self.declare_parameter('respeaker_ref_guard_user_ratio', 1.35)
        self.declare_parameter('respeaker_ref_guard_duck_gain', 0.15)
        self.declare_parameter('respeaker_ref_guard_log_interval_ms', 3000)
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.channels = self.get_parameter('channels').value
        self.chunk_ms = self.get_parameter('chunk_ms').value
        self.device_index = self.get_parameter('device_index').value
        self.respeaker_mode = self.get_parameter('respeaker_mode').value
        self.respeaker_channel = self.get_parameter('respeaker_channel').value
        self.gain = self.get_parameter('gain').value
        self.respeaker_tuning_enabled = self.get_parameter('respeaker_tuning_enabled').value
        self.respeaker_vendor_id = int(self.get_parameter('respeaker_vendor_id').value)
        self.respeaker_product_id = int(self.get_parameter('respeaker_product_id').value)
        self.respeaker_tuning_profile = str(self.get_parameter('respeaker_tuning_profile').value)
        self.respeaker_tuning_overrides = str(self.get_parameter('respeaker_tuning_overrides').value)
        self.respeaker_doa_poll_interval_s = float(self.get_parameter('respeaker_doa_poll_interval_s').value)
        self.output_guard_enabled = bool(self.get_parameter('output_guard_enabled').value)
        self.output_guard_duck_gain = float(self.get_parameter('output_guard_duck_gain').value)
        self.output_guard_post_duck_gain = float(
            self.get_parameter('output_guard_post_duck_gain').value
        )
        self.output_guard_hold_ms = int(self.get_parameter('output_guard_hold_ms').value)
        self.respeaker_ref_guard_enabled = bool(
            self.get_parameter('respeaker_ref_guard_enabled').value
        )
        self.respeaker_ref_guard_reference_channel = int(
            self.get_parameter('respeaker_ref_guard_reference_channel').value
        )
        self.respeaker_ref_guard_min_ref_dbfs = float(
            self.get_parameter('respeaker_ref_guard_min_ref_dbfs').value
        )
        self.respeaker_ref_guard_corr_threshold = float(
            self.get_parameter('respeaker_ref_guard_corr_threshold').value
        )
        self.respeaker_ref_guard_user_ratio = float(
            self.get_parameter('respeaker_ref_guard_user_ratio').value
        )
        self.respeaker_ref_guard_duck_gain = float(
            self.get_parameter('respeaker_ref_guard_duck_gain').value
        )
        self.respeaker_ref_guard_log_interval_ms = int(
            self.get_parameter('respeaker_ref_guard_log_interval_ms').value
        )
        
        # Calculate block size (frames per chunk)
        self.block_size = int(self.sample_rate * self.chunk_ms / 1000)
        
        self.audio_pub = self.create_publisher(Audio, '/audio_raw', 10)
        
        self.stream = None
        self.frame_count = 0
        self.running = True
        self.respeaker_tuning = None
        self.respeaker_doa_pub = self.create_publisher(Int32, '/respeaker/doa_angle', 10)
        self.respeaker_voice_pub = self.create_publisher(Bool, '/respeaker/voice_activity', 10)
        self.respeaker_poll_timer = None
        self._respeaker_poll_errors = 0
        self.is_robot_speaking = False
        self._last_robot_speaking_end_ms = 0
        self._last_ref_guard_log_ms = 0

        self.robot_speaking_sub = self.create_subscription(
            Bool,
            '/is_speaking',
            self.robot_speaking_callback,
            10,
        )
        
        self._setup_respeaker_tuning()
        if self.output_guard_enabled:
            self.get_logger().info(
                "🛡️ Output guard enabled: "
                f"playback_duck={self.output_guard_duck_gain:.3f}, "
                f"post_duck={self.output_guard_post_duck_gain:.3f}, "
                f"hold={self.output_guard_hold_ms}ms"
            )
        if self.respeaker_mode and self.respeaker_ref_guard_enabled:
            self.get_logger().info(
                "🧱 ReSpeaker ref guard enabled: "
                f"ref_ch={self.respeaker_ref_guard_reference_channel}, "
                f"ref_min={self.respeaker_ref_guard_min_ref_dbfs:.1f}dBFS, "
                f"corr>={self.respeaker_ref_guard_corr_threshold:.2f}, "
                f"user_ratio>{self.respeaker_ref_guard_user_ratio:.2f}, "
                f"duck={self.respeaker_ref_guard_duck_gain:.2f}"
            )

        if SD_AVAILABLE:
            self.start_capture()
        else:
            self.get_logger().error("sounddevice library missing! Cannot capture audio.")

    def start_capture(self):
        try:
            if self.respeaker_mode and not (0 <= int(self.respeaker_channel) <= 5):
                raise ValueError(f"respeaker_channel must be in [0..5], got {self.respeaker_channel}")

            # Device selection
            if self.device_index >= 0:
                device = self.device_index
                self.get_logger().info(f"🎤 Using explicit device index: {device}")
            else:
                # Search for ReSpeaker if mode is enabled
                if self.respeaker_mode:
                    devices = sd.query_devices()
                    for i, d in enumerate(devices):
                        # Name match for ReSpeaker hardware or common driver strings
                        if ('ReSpeaker' in d['name'] or 'ArrayUAC10' in d['name']) and d['max_input_channels'] >= 6:
                            self.device_index = i
                            self.get_logger().info(f"🎤 Found ReSpeaker hardware at index {i}: {d['name']}")
                            break
                    
                    if self.device_index < 0:
                        # If not found by name, try to use default if it has enough channels
                        default_dev = sd.query_devices(kind='input')
                        if default_dev['max_input_channels'] >= 6:
                             self.device_index = default_dev['index']
                             self.get_logger().info(f"🎤 ReSpeaker name not found, but default input supports 6+ channels. Using index {self.device_index}")
                
                device = self.device_index if self.device_index >= 0 else None
                if device is None:
                    self.get_logger().info("🎤 Using OS Default Input Device (Pipewire/Pulse bridge)")
                
                # Debug: show which device sounddevice considers default
                try:
                    default_dev = sd.query_devices(kind='input')
                    self.get_logger().info(f"ℹ️ Default device info: {default_dev['name']}")
                except:
                    pass

            # If ReSpeaker mode, we MUST force 6 channels
            capture_channels = 6 if self.respeaker_mode else self.channels
            
            self.get_logger().info(
                f"✅ Starting Capture: {self.sample_rate}Hz, {capture_channels}ch (publish={self.channels}ch), "
                f"block={self.block_size} ({self.chunk_ms}ms), device={device}"
            )

            # Start Input Stream with Callback
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                device=device,
                channels=capture_channels,
                dtype='float32',  # sounddevice native is float32 usually
                callback=self.audio_callback
            )
            self.stream.start()
            
        except Exception as e:
            self.get_logger().error(f"❌ Failed to start sounddevice stream: {e}")
            self.stream = None

    def robot_speaking_callback(self, msg: Bool):
        was_speaking = self.is_robot_speaking
        self.is_robot_speaking = bool(msg.data)
        if was_speaking and not self.is_robot_speaking:
            self._last_robot_speaking_end_ms = int(time.time() * 1000)

    def _output_guard_gain(self, now_ms: int) -> float:
        if not self.output_guard_enabled:
            return 1.0
        if self.is_robot_speaking:
            return float(self.output_guard_duck_gain)
        if self._last_robot_speaking_end_ms <= 0:
            return 1.0
        if (now_ms - self._last_robot_speaking_end_ms) <= max(0, self.output_guard_hold_ms):
            return float(self.output_guard_post_duck_gain)
        return 1.0

    def _setup_respeaker_tuning(self):
        if not self.respeaker_mode:
            return

        if int(self.respeaker_channel) == 5:
            self.get_logger().warn(
                "⚠️ ReSpeaker channel 5 is usually playback reference. "
                "Use channel 0 for processed ASR audio to avoid self-hearing loops."
            )

        if not self.respeaker_tuning_enabled:
            self.get_logger().info(
                "ℹ️ ReSpeaker tuning mode disabled (set audio_capture_node.respeaker_tuning_enabled=true to enable)"
            )
            return

        if not RESPEAKER_USB_AVAILABLE or ReSpeakerTuning is None:
            self.get_logger().warn(
                "⚠️ ReSpeaker tuning requested but PyUSB is unavailable. Install with: pip install pyusb"
            )
            return

        try:
            self.respeaker_tuning = ReSpeakerTuning.find(
                vid=self.respeaker_vendor_id,
                pid=self.respeaker_product_id,
            )
            if self.respeaker_tuning is None:
                self.get_logger().warn(
                    "⚠️ ReSpeaker tuning device not found "
                    f"(vid=0x{self.respeaker_vendor_id:04x}, pid=0x{self.respeaker_product_id:04x})"
                )
                return

            profile_values = resolve_profile(self.respeaker_tuning_profile)
            override_values = parse_tuning_overrides(self.respeaker_tuning_overrides)
            profile_values.update(override_values)

            if profile_values:
                self.respeaker_tuning.apply(profile_values)
                applied = ', '.join(f'{k}={v}' for k, v in profile_values.items())
                self.get_logger().info(
                    f"🎛️ ReSpeaker tuning applied ({self.respeaker_tuning_profile}): {applied}"
                )
            else:
                self.get_logger().info("🎛️ ReSpeaker tuning connected (no profile writes)")

            poll_interval = max(0.1, float(self.respeaker_doa_poll_interval_s))
            self.respeaker_poll_timer = self.create_timer(poll_interval, self._poll_respeaker_state)
            self._poll_respeaker_state()
        except Exception as e:
            self.get_logger().warn(f"⚠️ ReSpeaker tuning init failed: {e}")
            self.respeaker_tuning = None

    def _poll_respeaker_state(self):
        if self.respeaker_tuning is None:
            return
        try:
            doa = int(self.respeaker_tuning.read('DOAANGLE'))
            doa_msg = Int32()
            doa_msg.data = doa
            self.respeaker_doa_pub.publish(doa_msg)

            voice = bool(int(self.respeaker_tuning.read('VOICEACTIVITY')))
            voice_msg = Bool()
            voice_msg.data = voice
            self.respeaker_voice_pub.publish(voice_msg)

            self._respeaker_poll_errors = 0
        except Exception as e:
            self._respeaker_poll_errors += 1
            if self._respeaker_poll_errors <= 3 or self._respeaker_poll_errors % 20 == 0:
                self.get_logger().warn(f"⚠️ ReSpeaker tuning poll failed: {e}")

    @staticmethod
    def _rms_dbfs(sig: np.ndarray) -> float:
        if sig.size == 0:
            return -120.0
        rms = float(np.sqrt(np.mean(np.square(sig, dtype=np.float32)) + 1e-12))
        return 20.0 * np.log10(rms + 1e-12)

    def _apply_respeaker_reference_guard(self, audio_f32: np.ndarray, now_ms: int) -> np.ndarray:
        """
        Suppress frames that strongly match playback reference (ReSpeaker channel 5),
        while allowing likely user barge-in when mic energy rises over reference.
        """
        if not (self.respeaker_mode and self.respeaker_ref_guard_enabled):
            return audio_f32
        if audio_f32.ndim != 2 or audio_f32.shape[1] < 2:
            return audio_f32

        mic_ch = int(self.respeaker_channel)
        ref_ch = int(self.respeaker_ref_guard_reference_channel)
        if mic_ch < 0 or ref_ch < 0:
            return audio_f32
        if mic_ch >= audio_f32.shape[1] or ref_ch >= audio_f32.shape[1]:
            return audio_f32
        if mic_ch == ref_ch:
            return audio_f32

        mic = audio_f32[:, mic_ch].astype(np.float32, copy=False)
        ref = audio_f32[:, ref_ch].astype(np.float32, copy=False)

        ref_dbfs = self._rms_dbfs(ref)
        if ref_dbfs < self.respeaker_ref_guard_min_ref_dbfs:
            return audio_f32

        mic_norm = float(np.linalg.norm(mic))
        ref_norm = float(np.linalg.norm(ref))
        denom = mic_norm * ref_norm
        if denom <= 1e-9:
            return audio_f32

        corr = abs(float(np.dot(mic, ref) / denom))
        if corr < self.respeaker_ref_guard_corr_threshold:
            return audio_f32

        mic_rms = float(np.sqrt(np.mean(np.square(mic, dtype=np.float32)) + 1e-12))
        ref_rms = float(np.sqrt(np.mean(np.square(ref, dtype=np.float32)) + 1e-12))
        user_override = mic_rms > (ref_rms * max(1.0, self.respeaker_ref_guard_user_ratio))
        if user_override:
            return audio_f32

        guarded = audio_f32.copy()
        guarded[:, mic_ch] *= float(self.respeaker_ref_guard_duck_gain)

        if (now_ms - self._last_ref_guard_log_ms) >= max(0, self.respeaker_ref_guard_log_interval_ms):
            self._last_ref_guard_log_ms = now_ms
            try:
                self.get_logger().info(
                    "🧱 ReSpeaker ref guard suppressed playback-like frame: "
                    f"corr={corr:.2f}, ref={ref_dbfs:.1f}dBFS"
                )
            except Exception:
                pass

        return guarded

    def audio_callback(self, indata, frames, time_info, status):
        """
        Callback called by sounddevice audio thread.
        indata is numpy array of shape (frames, channels) float32
        """
        if not self.running or not rclpy.ok():
            return
            
        if status:
            self.get_logger().warn(f"Audio Status: {status}")
            
        try:
            # Convert float32 [-1, 1] to int16 [-32768, 32767]
            # Apply digital gain and clip to be safe
            now_ms = int(time.time() * 1000)
            audio_f32 = indata * self.gain
            guard_gain = self._output_guard_gain(now_ms)
            if guard_gain < 0.999:
                audio_f32 = audio_f32 * guard_gain
            audio_f32 = np.clip(audio_f32, -1.0, 1.0)
            audio_f32 = self._apply_respeaker_reference_guard(audio_f32, now_ms)
            
            # Handle multi-channel extraction for ReSpeaker
            if self.respeaker_mode:
                # indata shape is (frames, 6)
                # Extract selected channel: 0=processed ASR, 1-4=raw mics, 5=playback reference
                audio_f32 = audio_f32[:, self.respeaker_channel]
            
            # Scale and cast
            audio_i16 = (audio_f32 * 32767.0).astype(np.int16)
            
            # Flatten to list
            audio_data = audio_i16.flatten().tolist()

            # Publish
            msg = Audio()
            msg.sample_rate = self.sample_rate
            msg.channels = self.channels
            msg.data = audio_data
            self.audio_pub.publish(msg)
            
            self.frame_count += 1
            
            # Periodic logging
            if self.frame_count % 500 == 0:  # Log every ~10 seconds
                rms = np.sqrt(np.mean(audio_f32**2)) * 32767.0 # Scale RMS to int16 range for readable logs
                self.get_logger().info(f"📊 Audio Level (RMS): {rms:.2f} (Frames: {self.frame_count})")
                
        except Exception as e:
            if self.running and rclpy.ok():
                try:
                    self.get_logger().error(f"Callback error: {e}")
                except Exception:
                    pass

    def destroy_node(self):
        self.running = False
        print("🛑 Shutting down audio capture...")
        try:
            if self.respeaker_poll_timer:
                self.respeaker_poll_timer.cancel()
            if self.stream:
                self.stream.stop()
                self.stream.close()
            if self.respeaker_tuning is not None:
                self.respeaker_tuning.close()
        except Exception as e:
            print(f"Error closing audio stream: {e}")
            
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = AudioCaptureNode()
    
    try:
        # We just need to keep the node alive; audio is driven by sounddevice thread
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
