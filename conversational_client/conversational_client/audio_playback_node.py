#!/usr/bin/env python3
import os
import contextlib
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import Bool, String
import numpy as np
import pyaudio
import threading
from collections import deque
import time
import json

@contextlib.contextmanager
def ignore_stderr():
    """Suppress C-level stderr output (ALSA/JACK noise)."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    old_stderr = os.dup(2)
    os.dup2(devnull, 2)
    try:
        yield
    finally:
        os.dup2(old_stderr, 2)
        os.close(devnull)
        os.close(old_stderr)

class AudioPlaybackNode(Node):
    def __init__(self):
        super().__init__('audio_playback_node')
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('channels', 1)
        self.declare_parameter('gain', 0.5)
        self.declare_parameter('enable_dynamic_volume', True)
        self.declare_parameter('min_playback_gain', 0.15)
        self.declare_parameter('max_playback_gain', 1.0)
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.channels = self.get_parameter('channels').value
        self.gain = self.get_parameter('gain').value
        self.base_gain = self.gain
        self.enable_dynamic_volume = bool(self.get_parameter('enable_dynamic_volume').value)
        self.min_playback_gain = float(self.get_parameter('min_playback_gain').value)
        self.max_playback_gain = float(self.get_parameter('max_playback_gain').value)
        self.target_gain = self.gain
        self._last_env_state = ''
        
        self.speaking_pub = self.create_publisher(Bool, 'is_speaking', 10)
        self.progress_pub = self.create_publisher(String, 'audio_playback_progress', 10)
        self.volume_pub = self.create_publisher(String, 'playback_volume', 10)

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
        
        self.audio_sub = self.create_subscription(Audio, 'audio_out', self.audio_callback, 10)
        self.stop_sub = self.create_subscription(Bool, 'stop_playback', self.stop_callback, 10)
        self.env_sub = self.create_subscription(String, 'acoustic_environment', self.env_callback, 10)
        
        # PyAudio Setup
        with ignore_stderr():
            self.audio_p = pyaudio.PyAudio()
            self.stream: pyaudio.Stream | None = None
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
                    with ignore_stderr():
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
            
            # Smoothly ramp gain towards target_gain to avoid click/pop artifacts
            self.gain = float(0.80 * self.gain + 0.20 * self.target_gain)

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
                    try:
                        self.progress_pub.publish(String(data=json.dumps(prog)))
                    except Exception:
                        pass

            if self._stop_requested:
                self._write_buffer = np.array([], dtype=np.int16)

    def env_callback(self, msg: String):
        try:
            data = json.loads(msg.data)
            state = str(data.get('state', 'moderate') or 'moderate')
            noise_dbfs = float(data.get('noise_dbfs', -45.0) or -45.0)

            if not self.enable_dynamic_volume:
                return

            # Continuous linear interpolation:
            # -60.0 dBFS (or quieter) -> min_playback_gain (0.35)
            # -25.0 dBFS (or noisier) -> max_playback_gain (1.00)
            raw_target = np.interp(
                noise_dbfs,
                [-60.0, -25.0],
                [self.min_playback_gain, self.max_playback_gain]
            )
            old_target = self.target_gain
            self.target_gain = float(np.clip(raw_target, self.min_playback_gain, self.max_playback_gain))

            if abs(self.target_gain - old_target) >= 0.05 or state != self._last_env_state:
                self._last_env_state = state
                pct = int(self.target_gain * 100)
                self.get_logger().debug(
                    f'🔊 Dynamic Playback Volume: {pct}% (Env: {state.upper()}, Noise: {noise_dbfs:.1f} dBFS)'
                )
                vol_msg = {
                    'volume_pct': pct,
                    'target_gain': round(self.target_gain, 3),
                    'state': state,
                    'noise_dbfs': round(noise_dbfs, 1),
                }
                self.volume_pub.publish(String(data=json.dumps(vol_msg)))
        except Exception as e:
            self.get_logger().error(f'Error parsing acoustic environment in playback: {e}')

    def destroy_node(self):
        self.running = False
        if hasattr(self, 'playback_thread') and self.playback_thread.is_alive():
            self.playback_thread.join(timeout=1.0)
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
        try:
            self.audio_p.terminate()
        except Exception:
            pass
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = AudioPlaybackNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt: pass
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass

if __name__ == '__main__':
    main()
