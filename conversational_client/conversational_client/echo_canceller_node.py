#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
import numpy as np
from scipy import signal
import wave
import collections
import time

class EchoCancellerNode(Node):
    def __init__(self):
        super().__init__('echo_canceller_node')
        
        # Parametri
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('filter_length_ms', 150)
        self.declare_parameter('step_size', 0.08)
        self.declare_parameter('max_delay_ms', 3000)
        self.declare_parameter('debug_recording', True)
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.filter_length = int(self.get_parameter('filter_length_ms').value * self.sample_rate / 1000)
        self.mu = self.get_parameter('step_size').value
        self.max_delay_ms = self.get_parameter('max_delay_ms').value
        self.max_delay_samples = int(self.max_delay_ms * self.sample_rate / 1000)
        self.debug_recording = self.get_parameter('debug_recording').value
        
        # Buffer Circular Rapid (NumPy)
        self.buffer_size = self.max_delay_samples + self.sample_rate * 3 # 3 secunde extra
        self.ref_circle = np.zeros(self.buffer_size, dtype=np.float32)
        self.mic_history = collections.deque(maxlen=int(0.5 * self.sample_rate)) # 500ms istoric microfon pt corelatie
        self.ref_ptr = 0 # Arata unde vom scrie urmatorul pachet
        self.total_ref_samples = 0
        
        # State pentru Resampling (pentru a evita drift-ul)
        self.resample_remainder = 0.0
        
        self.w = np.zeros(self.filter_length) # Ponderile filtrului NLMS
        self.current_delay = 0
        self.last_sync_time = 0
        self.frame_count = 0
        
        # Topicuri
        self.raw_sub = self.create_subscription(Audio, '/audio_raw', self.raw_callback, 10)
        self.out_sub = self.create_subscription(Audio, '/audio_out', self.out_callback, 10)
        self.clean_pub = self.create_publisher(Audio, '/audio_clean', 10)
        
        # Debug Recordings
        if self.debug_recording:
            self.wav_raw = self.open_wav("/home/valee/voice_ros2/aec_raw.wav")
            self.wav_ref = self.open_wav("/home/valee/voice_ros2/aec_reference.wav")
            self.wav_clean = self.open_wav("/home/valee/voice_ros2/aec_cleaned.wav")
            self.wav_ref_rx = self.open_wav("/home/valee/voice_ros2/aec_ref_received.wav")
        else:
            self.wav_raw = self.wav_ref = self.wav_clean = self.wav_ref_rx = None
        
        print(f"🚀 [AEC] Stable Node Started. Max Delay: {self.max_delay_ms}ms", flush=True)

    def open_wav(self, path):
        try:
            w = wave.open(path, 'wb')
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            return w
        except Exception as e:
            self.get_logger().error(f"Failed to open WAV {path}: {e}")
            return None

    def out_callback(self, msg):
        if len(msg.data) == 0: return
        audio_data = np.array(msg.data, dtype=np.int16)
        
        # FOLOSIM EXACT ACEEASI LOGICA CA IN AUDIO_PLAYBACK_NODE
        if msg.sample_rate != self.sample_rate:
            audio_data = self._resample_pcm16(audio_data, int(msg.sample_rate), int(self.sample_rate))
        
        # Debug RX
        if self.wav_ref_rx: self.wav_ref_rx.writeframes(audio_data.tobytes())
            
        # Adaugare in Buffer Circular
        data_f32 = audio_data.astype(np.float32) / 32768.0
        n = len(data_f32)
        
        if self.ref_ptr + n <= self.buffer_size:
            self.ref_circle[self.ref_ptr : self.ref_ptr + n] = data_f32
        else:
            remaining = self.buffer_size - self.ref_ptr
            self.ref_circle[self.ref_ptr :] = data_f32[:remaining]
            self.ref_circle[: n - remaining] = data_f32[remaining:]
            
        self.ref_ptr = (self.ref_ptr + n) % self.buffer_size
        self.total_ref_samples += n

    def _resample_pcm16(self, audio: np.ndarray, original_rate: int, target_rate: int) -> np.ndarray:
        # COPIAT EXACT DIN AUDIO_PLAYBACK_NODE
        if audio.size == 0 or original_rate == target_rate:
            return audio.astype(np.int16, copy=False)
        duration = audio.size / float(original_rate)
        target_samples = max(1, int(round(duration * target_rate)))
        source_positions = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
        target_positions = np.linspace(0.0, 1.0, num=target_samples, endpoint=False)
        resampled = np.interp(target_positions, source_positions, audio.astype(np.float32))
        return np.clip(np.round(resampled), -32768, 32767).astype(np.int16)

    def get_ref_slice(self, start_offset_from_head, length):
        # Extrage un segment continuu din bufferul circular
        # start_offset_from_head: cate samples in urma fata de self.ref_ptr
        start_idx = (self.ref_ptr - start_offset_from_head) % self.buffer_size
        
        if start_idx + length <= self.buffer_size:
            return self.ref_circle[start_idx : start_idx + length]
        else:
            part1 = self.ref_circle[start_idx:]
            part2 = self.ref_circle[: length - len(part1)]
            return np.concatenate([part1, part2])

    def raw_callback(self, msg):
        if len(msg.data) == 0: return
        self.frame_count += 1
        
        d_i16 = np.array(msg.data, dtype=np.int16)
        d = d_i16.astype(np.float32) / 32768.0
        n = len(d)
        
        # Adaugam in istoricul pt delay
        self.mic_history.extend(d)
        
        if self.wav_raw:
            self.wav_raw.writeframes(d_i16.tobytes())

        # Avem destula referinta? (asteptam 1s extra pt siguranta)
        if self.total_ref_samples < self.max_delay_samples + self.sample_rate:
            self.clean_pub.publish(msg)
            if self.wav_clean: self.wav_clean.writeframes(d_i16.tobytes())
            if self.wav_ref: self.wav_ref.writeframes(np.zeros(n, dtype=np.int16).tobytes())
            return

        # 1. Sincronizare Delay cu LOCK
        now = time.time()
        
        # Resetam lock-ul daca robotul a taciut recent
        if not self.robot_speaking:
            if self.current_delay > 0:
                self.get_logger().info("🔓 Delay deblocat (robotul a tăcut)")
            self.current_delay = 0
            self.last_sync_time = 0
        
        # Daca robotul vorbeste si nu avem delay-ul blocat, il cautam
        if self.robot_speaking and self.current_delay == 0:
            mic_footprint = np.array(self.mic_history)
            # Asteptam 300ms de audio pentru o corelatie buna
            if len(mic_footprint) >= self.sample_rate * 0.3:
                search_len = self.max_delay_samples + len(mic_footprint)
                search_area = self.get_ref_slice(search_len, search_len)
                
                corr = signal.correlate(search_area, mic_footprint, mode='valid')
                peak_idx = np.argmax(corr)
                found_delay = search_len - peak_idx - len(mic_footprint)
                
                # Debug: sa vedem ce gaseste
                if self.frame_count % 20 == 0:
                    self.get_logger().info(f"🔍 Caut delay... găsit: {found_delay/self.sample_rate*1000:.1f}ms")

                if 100 < found_delay < self.max_delay_samples:
                    self.current_delay = found_delay
                    self.get_logger().info(f"🔒 DELAY BLOCAT: {self.current_delay/self.sample_rate*1000:.1f}ms")

        # 2. NLMS AEC
        # Folosim delay-ul blocat SAU unul estimat temporar pentru debug
        temp_delay = self.current_delay if self.current_delay > 0 else 0
        
        if temp_delay > 0:
            x_with_history = self.get_ref_slice(temp_delay + n + self.filter_length, n + self.filter_length)
            
            if len(x_with_history) >= n + self.filter_length:
                y_vec = np.convolve(x_with_history, self.w[::-1], mode='valid')[:n]

                if self.wav_ref:
                    ref_only = x_with_history[self.filter_length:]
                    self.wav_ref.writeframes((np.clip(ref_only, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes())

                e = np.zeros(n)
                for i in range(n):
                    y = y_vec[i]
                    e[i] = d[i] - y
                    x_vec = x_with_history[i : i + self.filter_length][::-1]
                    norm_x = np.dot(x_vec, x_vec) + 1e-6
                    self.w += self.mu * e[i] * x_vec / norm_x
            else:
                e = d
        else:
            e = d
            # Chiar daca n-avem delay, scriem tacere in ref ca sa pastram sincronizarea fisierului
            if self.wav_ref:
                self.wav_ref.writeframes(np.zeros(n, dtype=np.int16).tobytes())
        
        # 3. Publicare
        clean_i16 = (np.clip(e, -1.0, 1.0) * 32767.0).astype(np.int16)
        clean_msg = Audio()
        clean_msg.sample_rate = msg.sample_rate
        clean_msg.channels = msg.channels
        clean_msg.data = clean_i16.tolist()
        self.clean_pub.publish(clean_msg)
        
        if self.wav_clean:
            self.wav_clean.writeframes(clean_i16.tobytes())

    def destroy_node(self):
        print("💾 Inchidere fisiere debug...", flush=True)
        for f in [self.wav_raw, self.wav_ref, self.wav_clean, self.wav_ref_rx]:
            if f: f.close()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = EchoCancellerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()

if __name__ == '__main__':
    main()
