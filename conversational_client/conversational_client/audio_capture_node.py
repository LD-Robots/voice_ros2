#!/usr/bin/env python3
"""
audio_capture_node.py
Capture audio from microphone and publish to /audio_raw using sounddevice.
Refactored to match legacy project configuration for better hardware compatibility.
"""

import os
import tempfile
os.environ['PA_ALSA_PLUGHW'] = '1'  # Force PortAudio to use ALSA plughw (handles format/rate mismatch)

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import Bool
import numpy as np
import wave
import contextlib

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
        self.declare_parameter('respeaker_channel', 5)   # Which channel to extract (5 = AEC for this device)
        self.declare_parameter('gain', 1.0)              # Digital gain multiplier
        self.declare_parameter('stereo_mono_extract', False)
        self.declare_parameter('debug_recording', False) # Save to local WAV file
        # Launch injects an absolute path; this default only has to be somewhere
        # writable rather than a fixed /tmp location.
        self.declare_parameter(
            'debug_wav_path',
            os.path.join(tempfile.gettempdir(), 'debug_mic_capture.wav'),
        )
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.channels = self.get_parameter('channels').value
        self.chunk_ms = self.get_parameter('chunk_ms').value
        self.device_index = self.get_parameter('device_index').value
        self.respeaker_mode = self.get_parameter('respeaker_mode').value
        self.respeaker_channel = self.get_parameter('respeaker_channel').value
        self.gain = self.get_parameter('gain').value
        self.stereo_mono_extract = self.get_parameter('stereo_mono_extract').value
        self.debug_recording = self.get_parameter('debug_recording').value
        self.debug_wav_path = self.get_parameter('debug_wav_path').value
        
        # Calculate block size (frames per chunk)
        self.block_size = int(self.sample_rate * self.chunk_ms / 1000)
        
        self.audio_pub = self.create_publisher(Audio, 'audio_raw', 10)
        
        self.stream = None
        self.frame_count = 0
        self.running = True
        self.sample_buffer = []  # Accumulator for exact chunk publishing
        
        # Debug recording setup
        self.debug_wav = None
        if self.debug_recording:
            try:
                path = os.path.expanduser(self.debug_wav_path)
                dir_name = os.path.dirname(os.path.abspath(path))
                if dir_name:
                    os.makedirs(dir_name, exist_ok=True)
                with open(path, 'wb') as f:
                    pass
                self.debug_wav = wave.open(path, 'wb')
                self.debug_wav.setnchannels(self.channels)
                self.debug_wav.setsampwidth(2) # 16-bit
                self.debug_wav.setframerate(self.sample_rate)
                self.get_logger().info(f"🔴 DEBUG RECORDING ENABLED: Saving to {path}")
            except Exception as e:
                self.get_logger().error(f"❌ Failed to open debug WAV file at {self.debug_wav_path}: {e}")

        
        if SD_AVAILABLE:
            self.start_capture()
        else:
            self.get_logger().error("sounddevice library missing! Cannot capture audio.")

    def start_capture(self):
        try:
            # Device selection
            if self.device_index >= 0:
                device = self.device_index
                self.get_logger().info(f"🎤 Using explicit device index: {device}")
            else:
                # Auto-detect ReSpeaker if present, regardless of respeaker_mode
                devices = sd.query_devices()
                detected_index = -1
                for i, d in enumerate(devices):
                    if ('ReSpeaker' in d['name'] or 'ArrayUAC10' in d['name']) and d['max_input_channels'] >= 1:
                        detected_index = i
                        self.get_logger().info(f"🎤 Auto-detected ReSpeaker hardware at index {i}: {d['name']}")
                        break

                if detected_index >= 0:
                    device = detected_index
                    self.device_index = detected_index
                else:
                    self.get_logger().info("🎤 ReSpeaker not found or busy. Using PulseAudio Default ('pulse')")
                    device = 'pulse'

                # Debug: show which device sounddevice considers default
                try:
                    default_dev = sd.query_devices(kind='input')
                    self.get_logger().info(f"ℹ️ Default device info: {default_dev['name']}")
                except:
                    pass

            # Channel selection logic:
            # - respeaker_mode: open 6ch, extract respeaker_channel
            # - stereo_mono_extract: open 2ch (hardware minimum), extract channel 0 (left = AEC processed output)
            # - default: use declared channels count
            if self.respeaker_mode:
                capture_channels = 6
            elif self.stereo_mono_extract:
                capture_channels = 2
            else:
                capture_channels = self.channels

            # Safe hardware bounds fallback to prevent PaErrorCode -9998:
            selected_device = device if device is not None else sd.default.device[0]
            if selected_device is not None:
                try:
                    dev_info = sd.query_devices(selected_device)
                    max_input_ch = dev_info.get('max_input_channels', capture_channels)
                    if max_input_ch < capture_channels:
                        self.get_logger().warn(
                            f"⚠️ Device {selected_device} only supports {max_input_ch} input channels, "
                            f"but {capture_channels} were requested. Falling back to {max_input_ch} channels."
                        )
                        capture_channels = max_input_ch
                        if capture_channels < 2:
                            self.stereo_mono_extract = False
                        if capture_channels < 6:
                            self.respeaker_mode = False
                except Exception as e:
                    self.get_logger().warn(f"⚠️ Could not query device channels: {e}")

            self.get_logger().info(
                f"✅ Starting Capture: {self.sample_rate}Hz, {capture_channels}ch (publish={self.channels}ch), "
                f"block={self.block_size} ({self.chunk_ms}ms), device={device}"
            )

            # Start Input Stream with Callback
            # blocksize=0 lets ALSA choose optimal size, preventing overrun crashes
            @contextlib.contextmanager
            def ignore_stderr():
                devnull = os.open(os.devnull, os.O_WRONLY)
                old_stderr = os.dup(2)
                os.dup2(devnull, 2)
                try:
                    yield
                finally:
                    os.dup2(old_stderr, 2)
                    os.close(devnull)
                    os.close(old_stderr)

            with ignore_stderr():
                self.stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    blocksize=0,
                    device=device,
                    channels=capture_channels,
                    dtype='float32',  # sounddevice native is float32 usually
                    callback=self.audio_callback
                )
            self.stream.start()
            
        except Exception as e:
            self.get_logger().error(f"❌ Failed to start sounddevice stream: {e}")
            self.stream = None

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
            audio_f32 = indata * self.gain
            audio_f32 = np.clip(audio_f32, -1.0, 1.0)
            
            # Handle multi-channel extraction
            if self.respeaker_mode:
                # indata shape is (frames, 6) — extract desired channel
                audio_f32 = audio_f32[:, self.respeaker_channel]
            elif self.stereo_mono_extract:
                # indata shape is (frames, 2) — extract channel 0 (left = AEC processed output)
                audio_f32 = audio_f32[:, 0]
            else:
                # Fallback: if multi-channel but neither is active, default to first channel
                if audio_f32.ndim > 1:
                    audio_f32 = audio_f32[:, 0]
            
            # Scale and cast to int16
            audio_i16 = (audio_f32 * 32767.0).astype(np.int16)
            
            # Add to buffer
            self.sample_buffer.extend(audio_i16.flatten().tolist())

            # Emit chunks of exactly block_size (fixed latency)
            while len(self.sample_buffer) >= self.block_size:
                chunk_data = self.sample_buffer[:self.block_size]
                self.sample_buffer = self.sample_buffer[self.block_size:]

                # Publish
                msg = Audio()
                msg.sample_rate = self.sample_rate
                msg.channels = self.channels
                msg.data = chunk_data
                self.audio_pub.publish(msg)

                # Save to debug WAV
                if self.debug_wav:
                    self.debug_wav.writeframes(np.array(chunk_data, dtype=np.int16).tobytes())

                self.frame_count += 1

                # Periodic logging
                if self.frame_count % 50 == 0:  # Log every ~3 seconds
                    chunk_f32 = np.array(chunk_data, dtype=np.float32)
                    rms = np.sqrt(np.mean(chunk_f32**2))
                    self.get_logger().info(f"📊 Audio Level (RMS): {rms:.2f}")
                
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
            if self.stream:
                self.stream.stop()
                self.stream.close()
            
            if self.debug_wav:
                self.debug_wav.close()
                print(f"📄 Debug recording saved to {self.debug_wav_path}")
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
