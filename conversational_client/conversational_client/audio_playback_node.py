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

        # No maxlen – Gemini streams audio faster than real-time, so a bounded
        # deque would silently drop the oldest (next-to-play) chunks, causing
        # the characteristic "rushing to finish" jump artifacts.
        self.audio_buffer = deque()

        self.is_playing = False
        self._stop_requested = False
        # Write exactly this many samples per PyAudio call → smooth, jitter-free
        # playback regardless of how large/small each incoming ROS chunk is.
        self._playback_chunk_size = 2400   # 100 ms @ 24 kHz (Gemini output rate)
        self._write_buffer = np.array([], dtype=np.int16)  # accumulator
        self._last_audio_time = 0.0
        self._speaking_grace_period = 1.5
        self._current_item_id = ''
        self._current_stream_id = ''
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
        incoming_rate = getattr(msg, 'sample_rate', self.sample_rate) or self.sample_rate
        audio_data = np.array(msg.data, dtype=np.int16)
        if audio_data.size == 0: return

        incoming_stream_id = getattr(msg, 'stream_id', '') or getattr(msg, 'item_id', '')
        incoming_item_id = getattr(msg, 'item_id', '')

        # If this is a NEW stream/turn, flush buffered audio from the previous one
        # to prevent two voices playing simultaneously
        if incoming_stream_id and incoming_stream_id != self._current_stream_id:
            self.audio_buffer.clear()
            self._played_samples_current_item = 0
            self._current_stream_id = incoming_stream_id
            self.get_logger().debug(f'New audio stream: {incoming_stream_id}')

        # Only clear stop flag when we receive audio (don't resume stopped old stream)
        self._stop_requested = False
        self._last_audio_time = time.time()
        self.audio_buffer.append((audio_data, incoming_item_id, incoming_rate))
        self.is_playing = True

    def stop_callback(self, msg):
        if msg.data:
            self._stop_requested = True
            self.audio_buffer.clear()
            self._write_buffer = np.array([], dtype=np.int16)
            self._current_stream_id = ''
            self.is_playing = False
            self.speaking_pub.publish(Bool(data=False))

    def _flush_write_buffer(self):
        """Write any remaining samples in the accumulator (end-of-stream tail)."""
        if len(self._write_buffer) > 0 and self.stream and not self._stop_requested:
            self.stream.write(self._write_buffer.tobytes())
            self._played_samples_current_item += len(self._write_buffer)
        self._write_buffer = np.array([], dtype=np.int16)

    def _playback_loop(self):
        while self.running:
            if not self.audio_buffer:
                # Flush the write accumulator once we drain all queued chunks
                # so the last few samples of a turn are not held back.
                if len(self._write_buffer) > 0 and self.is_playing:
                    self._flush_write_buffer()

                if self.is_playing and (time.time() - self._last_audio_time > self._speaking_grace_period):
                    self.is_playing = False
                    self.speaking_pub.publish(Bool(data=False))
                time.sleep(0.01)
                continue

            chunk_data, item_id, chunk_rate = self.audio_buffer.popleft()
            
            if chunk_rate != self.sample_rate:
                # Flush accumulator before reopening the stream to avoid mixing
                # samples from different sample rates in a single write call.
                self._flush_write_buffer()
                if self.stream:
                    self.stream.stop_stream()
                    self.stream.close()
                try:
                    self.stream = self.audio_p.open(
                        format=pyaudio.paInt16,
                        channels=self.channels,
                        rate=chunk_rate,
                        output=True,
                        frames_per_buffer=self._playback_chunk_size
                    )
                    self.sample_rate = chunk_rate
                    self.get_logger().info(f'🔊 Playback stream switched to {chunk_rate}Hz')
                except Exception as e:
                    self.get_logger().error(f'❌ Failed to open speaker for {chunk_rate}Hz: {e}')
                    self.stream = None

            if item_id != self._current_item_id:
                self._current_item_id = item_id
                self._played_samples_current_item = 0
            
            self.is_playing = True
            self.speaking_pub.publish(Bool(data=True))
            
            if self.gain != 1.0:
                chunk_data = (chunk_data.astype(np.float32) * self.gain).astype(np.int16)

            # Accumulate incoming samples into the write buffer, then drain in
            # fixed-size chunks.  This guarantees every PyAudio write call is
            # exactly _playback_chunk_size samples, eliminating the timing jitter
            # that variable-size Gemini chunks would otherwise introduce.
            self._write_buffer = np.concatenate([self._write_buffer, chunk_data])

            while len(self._write_buffer) >= self._playback_chunk_size and not self._stop_requested:
                sub = self._write_buffer[:self._playback_chunk_size]
                self._write_buffer = self._write_buffer[self._playback_chunk_size:]
                if self.stream:
                    self.stream.write(sub.tobytes())
                self._played_samples_current_item += self._playback_chunk_size
                if self._current_item_id:
                    prog = {
                        "event": "audio_progress",
                        "item_id": self._current_item_id,
                        "played_samples": self._played_samples_current_item,
                    }
                    self.progress_pub.publish(String(data=json.dumps(prog)))

            if self._stop_requested:
                self._write_buffer = np.array([], dtype=np.int16)

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
