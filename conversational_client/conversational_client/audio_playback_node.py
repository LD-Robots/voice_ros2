#!/usr/bin/env python3
"""
audio_playback_node.py
Receives audio from the server (TTS) and plays it on the speaker.

EXPLANATION:
- This node RECEIVES audio on topic /audio_out (from the server)
- And PLAYS it on the robot's speaker
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTS - libraries we need
# ═══════════════════════════════════════════════════════════════════

import rclpy                              # Main ROS2 library for Python
from rclpy.node import Node               # Base class - all nodes inherit from it
from conversational_interfaces.msg import Audio  # Audio message type we defined
from std_msgs.msg import Bool, String      # For stop commands and playback progress
import numpy as np                         # For working with numeric arrays
from collections import deque              # Queue for audio buffer
import threading                           # Run playback in parallel
import json

# Try to import PyAudio (for audio playback)
try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    print("⚠️ PyAudio not installed. Run: pip install pyaudio")

import os
import sys
import ctypes

# Context manager to suppress stderr from C libraries (ALSA/Jack)
class RedirectStderr:
    def __init__(self):
        self._err_pipe_r, self._err_pipe_w = os.pipe()
        self._original_stderr_fd = sys.stderr.fileno()
        self._dup_stderr_fd = None

    def __enter__(self):
        self._dup_stderr_fd = os.dup(self._original_stderr_fd)
        sys.stderr.flush()
        os.dup2(self._err_pipe_w, self._original_stderr_fd)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        sys.stderr.flush()
        os.dup2(self._dup_stderr_fd, self._original_stderr_fd)
        os.close(self._dup_stderr_fd)
        os.close(self._err_pipe_r)
        os.close(self._err_pipe_w)


# ═══════════════════════════════════════════════════════════════════
# NODE CLASS - main logic here
# ═══════════════════════════════════════════════════════════════════

class AudioPlaybackNode(Node):
    """
    ROS2 node that plays audio received from the server.
    
    Operation:
    1. Listens on /audio_out
    2. When audio arrives, it pushes it into a buffer
    3. A separate thread plays buffered audio on the speaker
    """
    
    def __init__(self):
        # Call the parent class constructor (Node)
        # 'audio_playback_node' = node name (shows in ros2 node list)
        super().__init__('audio_playback_node')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETERS - configurable values
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('sample_rate', 16000)   # Audio sample rate
        self.declare_parameter('channels', 1)          # 1 = mono, 2 = stereo
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.channels = self.get_parameter('channels').value
        
        # ─────────────────────────────────────────────────────────
        # BUFFER - audio queue (accumulate before playback)
        # ─────────────────────────────────────────────────────────
        self.audio_buffer = deque(maxlen=100)  # Max 100 chunks (~2 seconds)
        self.is_playing = False
        self._stream_lock = threading.Lock()  # Lock for thread-safety on stop
        self._ignore_until = 0.0               # Timestamp until which to ignore new audio (for barge-in)
        self._stop_requested = False           # Flag for immediate stop during playback
        self._playback_chunk_size = 1024       # Small chunks for responsive stop (~42ms at 24kHz)
        self._last_audio_time = 0.0            # Timestamp of last audio received (for grace period)
        self._speaking_grace_period = 2.0      # Keep is_speaking True for 2s after last audio
        self._current_stream_id = ''
        self._current_item_id = ''
        self._played_samples_current_item = 0
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER - listen on /audio_out
        # ─────────────────────────────────────────────────────────
        # When the server sends TTS audio, it arrives here
        self.audio_sub = self.create_subscription(
            Audio,              # Message type (defined in conversational_interfaces)
            '/audio_out',       # Topic name to listen on
            self.audio_callback,  # Callback when a message arrives
            10                  # Queue size
        )
        
        # ─────────────────────────────────────────────────────────
        # SETUP PYAUDIO - speaker playback
        # ─────────────────────────────────────────────────────────
        self.stream = None
        if PYAUDIO_AVAILABLE:
            try:
                # Suppress ALSA/Jack error spam
                with RedirectStderr():
                    self.audio = pyaudio.PyAudio()
                    
                # Suppress ALSA/Jack error spam
                with RedirectStderr():
                    self.stream = self.audio.open(
                        format=pyaudio.paInt16,    # Format: 16-bit integer
                        channels=self.channels,     # Mono or stereo
                        rate=self.sample_rate,      # 16000 Hz
                        output=True,                # OUTPUT (not input) = speaker
                        frames_per_buffer=1024      # Buffer size
                    )
                self.get_logger().info(f'🔊 Audio Playback ready: {self.sample_rate}Hz')
            except Exception as e:
                self.get_logger().error(f'❌ Failed to open speaker: {e}')
                self.stream = None
        else:
            self.get_logger().warn('⚠️ PyAudio not available - audio will not play')
        
        # ─────────────────────────────────────────────────────────
        # PLAYBACK THREAD - runs in parallel
        # ─────────────────────────────────────────────────────────
        self.running = True
        self.playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self.playback_thread.start()
        
        # ─────────────────────────────────────────────────────────
        # STOP SUBSCRIBER - allows barge_in_node to stop playback
        # ─────────────────────────────────────────────────────────
        self.stop_sub = self.create_subscription(
            Bool,
            '/stop_playback',
            self.stop_callback,
            10
        )
        
        # ALSO listen on /tts_stop (from wake_word_node barge-in)
        self.tts_stop_sub = self.create_subscription(
            Bool,
            '/tts_stop',
            self.stop_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHER FOR is_speaking - publishes TTS state
        # ─────────────────────────────────────────────────────────
        self.speaking_pub = self.create_publisher(Bool, '/is_speaking', 10)
        self.playback_progress_pub = self.create_publisher(String, '/audio_playback_progress', 10)
        self._last_speaking_state = False
        
        # Timer to publish state (every 100ms)
        self.speaking_timer = self.create_timer(0.1, self._publish_speaking_state)
        
        self.get_logger().info('🔊 Audio Playback Node started - waiting for audio on /audio_out')

    def _emit_speaking_state(self, state: bool):
        """Publish speaking state immediately when playback is changed out-of-band."""
        msg = Bool()
        msg.data = state
        self.speaking_pub.publish(msg)
        self._last_speaking_state = state
    
    # ═══════════════════════════════════════════════════════════════════
    # CALLBACK - called when a message arrives on /audio_out
    # ═══════════════════════════════════════════════════════════════════
    def audio_callback(self, msg: Audio):
        """
        This function is called AUTOMATICALLY by ROS2
        whenever we receive a message on /audio_out.
        
        Args:
            msg: Received Audio message (contains sample_rate, channels, data)
        """
        # BARGE-IN: Ignore new audio if in cooldown (after stop)
        import time
        if time.time() < self._ignore_until:
            self.get_logger().debug('🚫 Ignoring audio (barge-in cooldown active)')
            return
        
        # Convert int16 list to numpy array
        audio_data = np.array(msg.data, dtype=np.int16)
        if msg.sample_rate != self.sample_rate:
            audio_data = self._resample_pcm16(audio_data, int(msg.sample_rate), int(self.sample_rate))
        stream_id = getattr(msg, 'stream_id', '') or ''
        item_id = getattr(msg, 'item_id', '') or ''
        
        # Clear stop flag - new audio means we should play again
        self._stop_requested = False
        
        # Track last audio time for grace period
        import time
        self._last_audio_time = time.time()
        
        # Push chunk into buffer
        self.audio_buffer.append((audio_data, stream_id, item_id))
        self.is_playing = True
        
        # Log (only occasionally to avoid flooding)
        if len(self.audio_buffer) == 1:
            self.get_logger().info(f'🎵 Received audio ({self.sample_rate}Hz), starting playback...')

    @staticmethod
    def _resample_pcm16(audio: np.ndarray, original_rate: int, target_rate: int) -> np.ndarray:
        if audio.size == 0 or original_rate == target_rate:
            return audio.astype(np.int16, copy=False)

        duration = audio.size / float(original_rate)
        target_samples = max(1, int(round(duration * target_rate)))
        source_positions = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
        target_positions = np.linspace(0.0, 1.0, num=target_samples, endpoint=False)
        resampled = np.interp(target_positions, source_positions, audio.astype(np.float32))
        return np.clip(np.round(resampled), -32768, 32767).astype(np.int16)
    
    # ═══════════════════════════════════════════════════════════════════
    # PLAYBACK LOOP - runs continuously in a separate thread
    # ═══════════════════════════════════════════════════════════════════
    def _playback_loop(self):
        """
        This loop runs in a separate thread.
        It takes audio from the buffer and plays it in SMALL CHUNKS for instant stop.
        """
        import time
        
        while self.running:
            # Check stop flag first
            if self._stop_requested:
                time.sleep(0.01)
                continue
            
            chunk = None
            with self._stream_lock:
                if self.audio_buffer and self.stream is not None:
                    # Take the first chunk from the buffer
                    chunk = self.audio_buffer.popleft()
                else:
                    # Empty buffer - wait a bit
                    self.is_playing = False
            
            if chunk is not None:
                chunk_data, stream_id, item_id = chunk

                if item_id:
                    if item_id != self._current_item_id:
                        self._current_item_id = item_id
                        self._current_stream_id = stream_id
                        self._played_samples_current_item = 0

                # Play the chunk in SMALL PIECES to allow instant stop
                chunk_bytes = chunk_data.tobytes()
                bytes_per_sample = 2  # int16
                piece_size = self._playback_chunk_size * bytes_per_sample  # ~1024 samples = 42ms
                offset = 0
                
                while offset < len(chunk_bytes):
                    # Check stop flag between each small piece
                    if self._stop_requested:
                        self.get_logger().debug('⏹️ Playback interrupted mid-chunk!')
                        break
                    
                    # Get next small piece
                    piece = chunk_bytes[offset:offset + piece_size]
                    offset += piece_size
                    
                    # Play this small piece
                    try:
                        with self._stream_lock:
                            if self.stream is not None:
                                self.stream.write(piece)
                                if item_id:
                                    self._played_samples_current_item += len(piece) // bytes_per_sample
                                    self._publish_playback_progress()
                    except Exception:
                        break  # Stream might have been stopped
            else:
                time.sleep(0.001)  # 1ms pause when buffer empty
    
    # ═══════════════════════════════════════════════════════════════════
    # STOP PLAYBACK - stop playback when the user speaks over it (barge-in)
    # ═══════════════════════════════════════════════════════════════════
    def stop_playback(self):
        """Stop current playback IMMEDIATELY (for barge-in)."""
        # 0. Set stop flag FIRST - interrupts playback loop immediately
        self._stop_requested = True
        self._publish_playback_progress(stopped=True)

        # 1. Clear the buffer
        self.audio_buffer.clear()
        self.is_playing = False
        self._last_audio_time = 0.0
        
        # 2. Stop the stream immediately (abort - don't wait for current chunk)
        with self._stream_lock:
            if self.stream is not None and PYAUDIO_AVAILABLE:
                try:
                    self.stream.stop_stream()
                    self.stream.close()
                    
                    # 3. Recreate the stream for future playback
                    # Suppress ALSA logs
                    with RedirectStderr():
                        self.stream = self.audio.open(
                            format=pyaudio.paInt16,
                            channels=self.channels,
                            rate=self.sample_rate,
                            output=True,
                            frames_per_buffer=1024
                        )
                except Exception as e:
                    self.get_logger().error(f'❌ Error stopping stream: {e}')
        
        # 4. Set cooldown - ignore new audio for 1.5s (avoid race condition with TTS double-buffer)
        import time
        self._ignore_until = time.time() + 1.5

        self._current_stream_id = ''
        self._current_item_id = ''
        self._played_samples_current_item = 0
        self._emit_speaking_state(False)
        self.get_logger().debug('⏹️ Playback stopped immediately (ignoring new audio for 1.5s)')
    
    def stop_callback(self, msg: Bool):
        """Callback for stop command (from barge_in_node)."""
        if msg.data:
            self.stop_playback()
    
    # ═══════════════════════════════════════════════════════════════════
    # PUBLISH SPEAKING STATE - informs other nodes when the robot is speaking
    # ═══════════════════════════════════════════════════════════════════
    def _publish_speaking_state(self):
        """Publish is_speaking state on the topic (only when it changes)."""
        import time
        
        # Calculate current state with grace period
        # Stay "speaking" for grace period after last audio (covers gaps between chunks)
        in_grace_period = (time.time() - self._last_audio_time) < self._speaking_grace_period
        current_state = self.is_playing or (in_grace_period and self._last_audio_time > 0)
        
        # Publish only when state changes (optimization)
        if current_state != self._last_speaking_state:
            self._emit_speaking_state(current_state)
            
            if current_state:
                self.get_logger().debug('🔊 Speaking: True')
            else:
                self.get_logger().debug('🔇 Speaking: False')

    def _publish_playback_progress(self, stopped: bool = False):
        """Publish the latest playback position for interruption-aware backends."""
        if not self._current_item_id:
            return

        played_ms = int(round(self._played_samples_current_item * 1000.0 / self.sample_rate))
        payload = {
            'stream_id': self._current_stream_id,
            'item_id': self._current_item_id,
            'played_ms': played_ms,
            'stopped': bool(stopped),
        }
        msg = String()
        msg.data = json.dumps(payload, separators=(',', ':'))
        self.playback_progress_pub.publish(msg)
    
    # ═══════════════════════════════════════════════════════════════════
    # CLEANUP - on node shutdown
    # ═══════════════════════════════════════════════════════════════════
    def destroy_node(self):
        """Cleanup when stopping the node."""
        print('🛑 Shutting down audio playback...')
        
        # 1. Stop the playback loop
        self.running = False
        
        # 2. Wait for the thread to finish (avoids segfault)
        if hasattr(self, 'playback_thread') and self.playback_thread.is_alive():
            self.playback_thread.join(timeout=1.0)
            
        # 3. Close the PyAudio stream
        if self.stream is not None:
            try:
                if self.stream.is_active():
                    self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                print(f'Error closing stream: {e}')
        
        # 4. Terminate PyAudio
        if hasattr(self, 'audio'):
            try:
                self.audio.terminate()
            except Exception as e:
                print(f'Error terminating PyAudio: {e}')
                
        super().destroy_node()


# ═══════════════════════════════════════════════════════════════════
# MAIN - entry point
# ═══════════════════════════════════════════════════════════════════

def main(args=None):
    """Main function - starts the node."""
    
    rclpy.init(args=args)           # Initialize ROS2
    
    node = AudioPlaybackNode()       # Create our node
    
    try:
        rclpy.spin(node)             # Run the node (wait for messages)
    except KeyboardInterrupt:
        pass                         # Ctrl+C - normal exit
    finally:
        node.destroy_node()          # Cleanup
        try:
            rclpy.shutdown()             # Shutdown ROS2
        except Exception:
            pass


if __name__ == '__main__':
    main()
