#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import Bool, String
import numpy as np
import pyaudio
import threading
from collections import deque
import time
import sys
import json
import os

class AudioPlaybackNode(Node):
    def __init__(self):
        super().__init__('audio_playback_node')
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('channels', 1)
        self.declare_parameter('gain', 0.5)
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.channels = self.get_parameter('channels').value
        self.gain = self.get_parameter('gain').value
        
        self.speaking_pub = self.create_publisher(Bool, '/is_speaking', 10)
        self.progress_pub = self.create_publisher(String, '/audio_playback_progress', 10)
        self.audio_buffer = deque(maxlen=200)
        self.is_playing = False
        self._stop_requested = False
        self._playback_chunk_size = 1024
        self._last_audio_time = 0.0
        self._speaking_grace_period = 1.5
        self._current_item_id = ''
        self._played_samples_current_item = 0
        
        self.audio_sub = self.create_subscription(Audio, '/audio_out', self.audio_callback, 10)
        self.stop_sub = self.create_subscription(Bool, '/stop_playback', self.stop_callback, 10)
        
        # PyAudio Setup
        self.audio_p = pyaudio.PyAudio()
        try:
            self.stream = self.audio_p.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                output=True,
                frames_per_buffer=self._playback_chunk_size
            )
            self.get_logger().info(f'🔊 Playback ready: {self.sample_rate}Hz')
        except Exception as e:
            self.get_logger().error(f'❌ Failed to open speaker: {e}')
            self.stream = None
        
        self.running = True
        self.playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self.playback_thread.start()

    def audio_callback(self, msg):
        # Audio is now always 16kHz from the server
        audio_data = np.array(msg.data, dtype=np.int16)
        if audio_data.size == 0: return
        
        self._stop_requested = False
        self._last_audio_time = time.time()
        self.audio_buffer.append((audio_data, getattr(msg, 'item_id', '')))
        self.is_playing = True

    def stop_callback(self, msg):
        if msg.data:
            self._stop_requested = True
            self.audio_buffer.clear()
            self.is_playing = False
            self.speaking_pub.publish(Bool(data=False))

    def _playback_loop(self):
        while self.running:
            if not self.audio_buffer:
                if self.is_playing and (time.time() - self._last_audio_time > self._speaking_grace_period):
                    self.is_playing = False
                    self.speaking_pub.publish(Bool(data=False))
                time.sleep(0.01)
                continue

            chunk_data, item_id = self.audio_buffer.popleft()
            if item_id != self._current_item_id:
                self._current_item_id = item_id
                self._played_samples_current_item = 0
            
            self.is_playing = True
            self.speaking_pub.publish(Bool(data=True))
            
            if self.gain != 1.0:
                chunk_data = (chunk_data.astype(np.float32) * self.gain).astype(np.int16)

            ptr = 0
            while ptr < len(chunk_data) and not self._stop_requested:
                sub = chunk_data[ptr : ptr + self._playback_chunk_size]
                if self.stream: self.stream.write(sub.tobytes())
                ptr += len(sub)
                self._played_samples_current_item += len(sub)
                
                if self._current_item_id:
                    prog = {"event": "audio_progress", "item_id": self._current_item_id, "played_samples": self._played_samples_current_item}
                    self.progress_pub.publish(String(data=json.dumps(prog)))

    def destroy_node(self):
        self.running = False
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        self.audio_p.terminate()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = AudioPlaybackNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt: pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
