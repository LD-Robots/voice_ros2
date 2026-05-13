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

class EchoCancellerNode(Node):
    def __init__(self):
        super().__init__('echo_canceller_node')
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('filter_length_ms', 150)
        self.declare_parameter('step_size', 0.08)
        self.declare_parameter('max_delay_ms', 3000)
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.filter_length = int(self.get_parameter('filter_length_ms').value * self.sample_rate / 1000)
        self.mu = self.get_parameter('step_size').value
        self.max_delay_samples = int(self.get_parameter('max_delay_ms').value * self.sample_rate / 1000)
        
        # Buffer Circular AEC (Banda de timp real)
        self.buffer_size = self.max_delay_samples + self.sample_rate * 5
        self.ref_circle = np.zeros(self.buffer_size, dtype=np.float32)
        self.ref_ptr = 0
        
        # Coada de asteptare pentru sunetul de la server (care vine in rafale)
        self.ref_queue = collections.deque()
        
        self.mic_history = collections.deque(maxlen=int(0.5 * self.sample_rate))
        self.total_ref_samples = 0
        self.w = np.zeros(self.filter_length)
        self.current_delay = 0
        self.robot_speaking = False
        
        self.raw_sub = self.create_subscription(Audio, '/audio_raw', self.raw_callback, 10)
        self.out_sub = self.create_subscription(Audio, '/audio_out', self.out_callback, 10)
        self.is_speaking_sub = self.create_subscription(Bool, '/is_speaking', self.is_speaking_callback, 10)
        self.clean_pub = self.create_publisher(Audio, '/audio_clean', 10)
        
        # Debug Recordings
        self.wav_raw = self.open_wav("/home/valee/voice_ros2/aec_raw.wav", 16000)
        self.wav_ref_aligned = self.open_wav("/home/valee/voice_ros2/aec_reference.wav", 16000)
        self.wav_clean = self.open_wav("/home/valee/voice_ros2/aec_cleaned.wav", 16000)
        
        print(f"🚀 [AEC] Time-Locked Node Started. Rate: {self.sample_rate}Hz", flush=True)

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
        # Cand vine sunetul de la server, il punem in coada, NU in bufferul circular inca
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
        d = d_i16.astype(np.float32) / 32768.0
        n = len(d)
        
        # --- SINCRONIZARE PE BANDA ---
        # Extragem din coada exact n eșantioane pentru a tine pasul cu microfonul
        ref_chunk = np.zeros(n, dtype=np.float32)
        for i in range(n):
            if self.ref_queue:
                ref_chunk[i] = self.ref_queue.popleft()
            else:
                ref_chunk[i] = 0.0 # Silent daca nu mai avem date de la server
        
        # Punem in bufferul circular (care acum se misca sincron cu microfonul)
        if self.ref_ptr + n <= self.buffer_size:
            self.ref_circle[self.ref_ptr : self.ref_ptr + n] = ref_chunk
        else:
            rem = self.buffer_size - self.ref_ptr
            self.ref_circle[self.ref_ptr :] = ref_chunk[:rem]
            self.ref_circle[: n - rem] = ref_chunk[rem:]
        self.ref_ptr = (self.ref_ptr + n) % self.buffer_size
        self.total_ref_samples += n
        
        # Restul procesarii ramane la fel
        self.mic_history.extend(d)
        if self.wav_raw: self.wav_raw.writeframes(d_i16.tobytes())
            
        if not self.robot_speaking:
            self.current_delay = 0
            # Optional: golim coada daca robotul a tacut de mult sa nu avem lag
            if len(self.ref_queue) > self.sample_rate: # Mai mult de 1 secunda ramasa
                 self.ref_queue.clear()
        
        if self.robot_speaking and self.current_delay == 0:
            footprint = np.array(self.mic_history)
            if len(footprint) >= self.sample_rate * 0.3:
                slen = self.max_delay_samples + len(footprint)
                area = self.get_ref_slice(slen, slen)
                corr = signal.correlate(area, footprint, mode='valid')
                peak = np.argmax(corr)
                delay = slen - peak - len(footprint)
                if 50 < delay < self.max_delay_samples: # delay minim redus
                    self.current_delay = delay
                    self.get_logger().info(f"🔒 DELAY BLOCAT: {self.current_delay/self.sample_rate*1000:.1f}ms")

        if self.current_delay > 0:
            x_hist = self.get_ref_slice(self.current_delay + n + self.filter_length, n + self.filter_length)
            if len(x_hist) >= n + self.filter_length:
                if self.wav_ref_aligned:
                    ref = x_hist[self.filter_length : self.filter_length + n]
                    self.wav_ref_aligned.writeframes((np.clip(ref, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes())
                
                # NLMS Loop
                e = np.zeros(n)
                for i in range(n):
                    x_vec = x_hist[i : i + self.filter_length][::-1]
                    y = np.dot(x_vec, self.w)
                    e[i] = d[i] - y
                    norm = np.dot(x_vec, x_vec) + 1e-4
                    self.w += self.mu * e[i] * x_vec / norm
            else: e = d
        else:
            e = d
            if self.wav_ref_aligned: self.wav_ref_aligned.writeframes(np.zeros(n, dtype=np.int16).tobytes())
        
        clean_i16 = (np.clip(e, -1.0, 1.0) * 32767.0).astype(np.int16)
        msg.data = clean_i16.tolist()
        self.clean_pub.publish(msg)
        if self.wav_clean: self.wav_clean.writeframes(clean_i16.tobytes())

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
