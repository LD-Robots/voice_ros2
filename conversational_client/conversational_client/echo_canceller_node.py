#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import Bool
import numpy as np
from scipy import signal
import wave
import collections
import time

# Import WebRTC Audio Processing
try:
    from aec_audio_processing import AudioProcessor
    WEBRTC_AVAILABLE = True
except ImportError:
    WEBRTC_AVAILABLE = False

class EchoCancellerNode(Node):
    def __init__(self):
        super().__init__('echo_canceller_node')
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('max_delay_ms', 3000)
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.max_delay_samples = int(self.get_parameter('max_delay_ms').value * self.sample_rate / 1000)
        
        # WebRTC strictly works on 10ms frames
        self.frame_size_10ms = self.sample_rate // 100 # 160 samples @ 16kHz
        
        # Initialize WebRTC Audio Processor
        if WEBRTC_AVAILABLE:
            # Default init enables AEC, NS, and AGC
            self.apm = AudioProcessor(enable_aec=True, enable_ns=True, ns_level=2, enable_agc=True)
            self.apm.set_stream_format(16000, 1)
            self.apm.set_reverse_stream_format(16000, 1)
            self.get_logger().info("🚀 [AEC] WebRTC Professional Engine Started (AEC+NS+AGC).")
        else:
            self.get_logger().error("❌ [AEC] WebRTC Library NOT FOUND! Install with: pip install aec-audio-processing")
            self.apm = None

        self.buffer_size = self.max_delay_samples + self.sample_rate * 5
        self.ref_circle = np.zeros(self.buffer_size, dtype=np.float32)
        self.ref_ptr = 0
        self.ref_queue = collections.deque()
        self.mic_history = collections.deque(maxlen=int(0.5 * self.sample_rate))
        
        self.current_delay = 0
        self.robot_speaking = False
        self._lock_count = 0
        self.total_ref_samples = 0
        
        self.raw_sub = self.create_subscription(Audio, '/audio_raw', self.raw_callback, 10)
        self.out_sub = self.create_subscription(Audio, '/audio_out', self.out_callback, 10)
        self.is_speaking_sub = self.create_subscription(Bool, '/is_speaking', self.is_speaking_callback, 10)
        self.clean_pub = self.create_publisher(Audio, '/audio_clean', 10)
        
        # Debug files
        self.wav_raw = self.open_wav("/home/valee/voice_ros2/aec_raw.wav", 16000)
        self.wav_ref_aligned = self.open_wav("/home/valee/voice_ros2/aec_reference.wav", 16000)
        self.wav_clean = self.open_wav("/home/valee/voice_ros2/aec_cleaned.wav", 16000)

    def is_speaking_callback(self, msg):
        self.robot_speaking = msg.data

    def open_wav(self, path, rate):
        try:
            w = wave.open(path, 'wb')
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            return w
        except: return None

    def out_callback(self, msg):
        if len(msg.data) == 0: return
        audio_data = np.array(msg.data, dtype=np.int16).astype(np.float32) / 32768.0
        self.ref_queue.extend(audio_data)

    def get_ref_slice(self, offset, length):
        idx = (self.ref_ptr - offset) % self.buffer_size
        if idx + length <= self.buffer_size:
            return self.ref_circle[idx : idx + length]
        else:
            p1 = self.ref_circle[idx:]
            p2 = self.ref_circle[: length - len(p1)]
            return np.concatenate([p1, p2])

    def raw_callback(self, msg):
        if len(msg.data) == 0: return
        d_i16 = np.array(msg.data, dtype=np.int16)
        d_f32 = d_i16.astype(np.float32) / 32768.0
        n = len(d_f32)
        
        # Sync reference queue
        ref_chunk = np.zeros(n, dtype=np.float32)
        for i in range(n):
            if self.ref_queue: ref_chunk[i] = self.ref_queue.popleft()
        
        # Write to circular buffer
        if self.ref_ptr + n <= self.buffer_size:
            self.ref_circle[self.ref_ptr : self.ref_ptr + n] = ref_chunk
        else:
            rem = self.buffer_size - self.ref_ptr
            self.ref_circle[self.ref_ptr :] = ref_chunk[:rem]
            self.ref_circle[: n - rem] = ref_chunk[rem:]
        self.ref_ptr = (self.ref_ptr + n) % self.buffer_size
        self.total_ref_samples += n
        
        self.mic_history.extend(d_f32)
        if self.wav_raw: self.wav_raw.writeframes(d_i16.tobytes())
            
        if not self.robot_speaking:
            self.current_delay = 0
            if len(self.ref_queue) > self.sample_rate: self.ref_queue.clear()
            self._lock_count = 0
        
        # Delay Estimation
        if self.robot_speaking and self._lock_count < 10:
            footprint = np.array(self.mic_history)
            if len(footprint) >= self.sample_rate * 0.5:
                slen = self.max_delay_samples + len(footprint)
                area = self.get_ref_slice(slen, slen)
                f_norm = (footprint - np.mean(footprint)) / (np.std(footprint) + 1e-6)
                a_norm = (area - np.mean(area)) / (np.std(area) + 1e-6)
                corr = signal.correlate(a_norm, f_norm, mode='valid')
                peak = np.argmax(corr)
                score = corr[peak] / len(f_norm)
                delay = slen - peak - len(f_norm)
                
                if score > 0.15:
                    if abs(delay - self.current_delay) < 50:
                        self._lock_count += 1
                    else:
                        self.current_delay = delay
                        self._lock_count = 1
                    if self._lock_count == 5:
                        self.get_logger().info(f"🔒 AEC LOCKED: {self.current_delay/self.sample_rate*1000:.1f}ms")

        # Process through WebRTC
        final_clean = d_i16
        if self.apm and self.current_delay > 0:
            # Extract aligned reference
            ref_aligned = self.get_ref_slice(self.current_delay + n, n)
            if self.wav_ref_aligned:
                self.wav_ref_aligned.writeframes((np.clip(ref_aligned, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes())
            
            # Split into 10ms frames for WebRTC
            clean_out = []
            mic_i16 = d_i16
            ref_i16 = (np.clip(ref_aligned, -1.0, 1.0) * 32767.0).astype(np.int16)
            
            for i in range(0, n, self.frame_size_10ms):
                if i + self.frame_size_10ms > n: break
                
                m_frame = mic_i16[i : i + self.frame_size_10ms]
                r_frame = ref_i16[i : i + self.frame_size_10ms]
                
                # WebRTC API (expects bytes)
                self.apm.process_reverse_stream(r_frame.tobytes())
                res_bytes = self.apm.process_stream(m_frame.tobytes())
                processed_frame = np.frombuffer(res_bytes, dtype=np.int16)
                clean_out.append(processed_frame)
            
            if clean_out:
                final_clean = np.concatenate(clean_out)
        else:
            if self.wav_ref_aligned: self.wav_ref_aligned.writeframes(np.zeros(n, dtype=np.int16).tobytes())

        msg.data = final_clean.tolist()
        self.clean_pub.publish(msg)
        if self.wav_clean: self.wav_clean.writeframes(final_clean.tobytes())

    def destroy_node(self):
        for f in [self.wav_raw, self.wav_ref_aligned, self.wav_clean]:
            if f: f.close()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = EchoCancellerNode()
    try: rclpy.spin(node)
    except KeyboardInterrupt: pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
