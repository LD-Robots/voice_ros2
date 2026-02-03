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
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.channels = self.get_parameter('channels').value
        self.chunk_ms = self.get_parameter('chunk_ms').value
        self.device_index = self.get_parameter('device_index').value
        
        # Calculate block size (frames per chunk)
        self.block_size = int(self.sample_rate * self.chunk_ms / 1000)
        
        self.audio_pub = self.create_publisher(Audio, '/audio_raw', 10)
        
        self.stream = None
        self.frame_count = 0
        
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
                # Folosim DISPOZITIVUL DEFAULT al sistemului (care știm că merge cu arecord)
                # Nu mai căutăm explicit 'pulse' sau 'pipewire' pentru că poate cauza probleme cu indexul
                device = None 
                self.get_logger().info("🎤 Using OS Default Input Device (sounddevice default)")
                
                # Debug: arătăm ce dispozitiv consideră sounddevice ca fiind default
                try:
                    default_dev = sd.query_devices(kind='input')
                    self.get_logger().info(f"ℹ️ Default device info: {default_dev['name']}")
                except:
                    pass

            # Determine optimal blocksize if possible, or use fixed
            # We enforce fixed block_size to match downstream expectation (20ms)
            
            self.get_logger().info(
                f"✅ Starting Capture: {self.sample_rate}Hz, {self.channels}ch, "
                f"block={self.block_size} ({self.chunk_ms}ms), device={device}"
            )

            # Start Input Stream with Callback
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                device=device,
                channels=self.channels,
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
        if status:
            self.get_logger().warn(f"Audio Status: {status}")
            
        try:
            # Convert float32 [-1, 1] to int16 [-32768, 32767]
            # Clip to be safe
            audio_f32 = np.clip(indata, -1.0, 1.0)
            # Scale and cast
            audio_i16 = (audio_f32 * 32767.0).astype(np.int16)
            
            # Flatten if needed (channel 0)
            if self.channels == 1:
                audio_data = audio_i16.flatten().tolist()
            else:
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
            self.get_logger().error(f"Callback error: {e}")

    def destroy_node(self):
        self.get_logger().info("🛑 Shutting down audio capture...")
        if self.stream:
            self.stream.stop()
            self.stream.close()
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
        rclpy.shutdown()

if __name__ == '__main__':
    main()
