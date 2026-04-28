#!/usr/bin/env python3
"""
audio_capture_node.py
Capture audio from microphone and publish to /audio_raw using sounddevice.
Refactored to match legacy project configuration for better hardware compatibility.
"""

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
import numpy as np
import sys

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
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.channels = self.get_parameter('channels').value
        self.chunk_ms = self.get_parameter('chunk_ms').value
        self.device_index = self.get_parameter('device_index').value
        self.respeaker_mode = self.get_parameter('respeaker_mode').value
        self.respeaker_channel = self.get_parameter('respeaker_channel').value
        self.gain = self.get_parameter('gain').value
        
        # Calculate block size (frames per chunk)
        self.block_size = int(self.sample_rate * self.chunk_ms / 1000)
        
        self.audio_pub = self.create_publisher(Audio, '/audio_raw', 10)
        
        self.stream = None
        self.frame_count = 0
        self.running = True
        
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
            
            # Handle multi-channel extraction for ReSpeaker
            if self.respeaker_mode:
                # indata shape is (frames, 6)
                # Extract only the desired channel (5 = processed AEC)
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
            if self.stream:
                self.stream.stop()
                self.stream.close()
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
