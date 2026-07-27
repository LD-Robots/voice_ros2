#!/usr/bin/env python3
from typing import Any, TYPE_CHECKING
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import Bool, String
import numpy as np
import json
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
    if TYPE_CHECKING:
        from aec_audio_processing import AudioProcessor

# Import DeepFilterNet
try:
    import sys
    import types
    import torch
    import torchaudio
    # ─────────────────────────────────────────────────────────────────
    # FIX: torchaudio 2.x removed torchaudio.backend.common.AudioMetaData,
    # but DeepFilterNet 0.5.x still imports it at module load. Provide a
    # minimal stub so `df` imports; the node feeds tensors straight to
    # enhance(), so file-metadata handling is never exercised.
    # ─────────────────────────────────────────────────────────────────
    if 'torchaudio.backend.common' not in sys.modules:
        _ta_backend = types.ModuleType('torchaudio.backend')
        _ta_common = types.ModuleType('torchaudio.backend.common')

        class AudioMetaData:  # noqa: D401 - compatibility stub
            def __init__(self, sample_rate=0, num_frames=0, num_channels=0,
                         bits_per_sample=0, encoding='UNKNOWN'):
                self.sample_rate = sample_rate
                self.num_frames = num_frames
                self.num_channels = num_channels
                self.bits_per_sample = bits_per_sample
                self.encoding = encoding

        setattr(_ta_common, 'AudioMetaData', AudioMetaData)
        setattr(_ta_backend, 'common', _ta_common)
        sys.modules.setdefault('torchaudio.backend', _ta_backend)
        sys.modules['torchaudio.backend.common'] = _ta_common
        if not hasattr(torchaudio, 'backend'):
            setattr(torchaudio, 'backend', _ta_backend)

    from df.enhance import init_df, enhance
    DF_AVAILABLE = True
except ImportError:
    DF_AVAILABLE = False

class EchoCancellerNode(Node):
    def __init__(self):
        super().__init__('echo_canceller_node')
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('max_delay_ms', 400)
        # Gain applied by AudioPlaybackNode – reference must match what actually
        # comes out of the speakers so the AEC subtraction is correct.
        self.declare_parameter('playback_gain', 0.5)
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.max_delay_samples = int(self.get_parameter('max_delay_ms').value * self.sample_rate / 1000)
        self.playback_gain = float(self.get_parameter('playback_gain').value)
        
        self.declare_parameter('mic_gain', 1.0)  # Amplificare software post-AEC (1.0 = fara amplificare extra)
        self.mic_gain = float(self.get_parameter('mic_gain').value)
        # capture_gain trebuie sincronizat cu audio_capture_node.gain din YAML
        # Necesar pentru a scala referinta AEC la amplitudinea reala captata de microfon
        self.declare_parameter('capture_gain', 1.0)  # Sincronizat cu audio_capture_node.gain
        self.capture_gain = float(self.get_parameter('capture_gain').value)
        self.declare_parameter('webrtc_ns_level', 2) # Noise Suppression level (0-3)
        self.webrtc_ns_level = int(self.get_parameter('webrtc_ns_level').value)
        self.base_webrtc_ns_level = self.webrtc_ns_level
        self.declare_parameter('webrtc_ns_enabled', True)
        self.webrtc_ns_enabled = bool(self.get_parameter('webrtc_ns_enabled').value)
        # Residual gate: during/after robot playback, zero out AEC output if it
        # is below the RMS threshold — eliminates residual echo fragments.
        # Human barge-in voice is loud enough to exceed the threshold and pass.
        self.declare_parameter('residual_gate_enabled', True)
        self.residual_gate_enabled = bool(self.get_parameter('residual_gate_enabled').value)
        self.declare_parameter('residual_gate_rms_threshold', 800)  # int16 RMS (~-32 dBFS)
        self.residual_gate_rms_threshold = int(self.get_parameter('residual_gate_rms_threshold').value)
        self.base_residual_gate_rms_threshold = self.residual_gate_rms_threshold
        
        self.declare_parameter('raw_wav_path', '')
        self.declare_parameter('ref_wav_path', '')
        self.declare_parameter('clean_wav_path', '')
        
        raw_path = self.get_parameter('raw_wav_path').value
        ref_path = self.get_parameter('ref_wav_path').value
        clean_path = self.get_parameter('clean_wav_path').value
        
        # WebRTC strictly works on 10ms frames
        self.frame_size_10ms = self.sample_rate // 100 # 160 samples @ 16kHz
        
        # Initialize WebRTC Audio Processor
        self.apm: AudioProcessor | None = None
        if WEBRTC_AVAILABLE:
            # AGC disabled per user request to avoid over-amplification
            self.apm = AudioProcessor(
                enable_aec=True,
                enable_ns=self.webrtc_ns_enabled,
                ns_level=self.webrtc_ns_level,
                enable_agc=False
            )
            self.apm.set_stream_format(16000, 1)
            self.apm.set_reverse_stream_format(16000, 1)
            self.get_logger().info("🚀 [AEC] WebRTC Engine Started (AEC+NS, AGC Disabled).")
        else:
            self.get_logger().error("❌ [AEC] WebRTC Library NOT FOUND!")

        # Initialize DeepFilterNet
        self.declare_parameter('deep_filter_enabled', True)
        self.declare_parameter('deep_filter_post_filter', True)
        self.declare_parameter('deep_filter_atten_lim_db', 25.0)
        self.declare_parameter('deep_filter_dynamic_adaptation', True)

        self.deep_filter_enabled = self.get_parameter('deep_filter_enabled').value and DF_AVAILABLE
        self.df_post_filter = bool(self.get_parameter('deep_filter_post_filter').value)
        self.base_df_atten_lim_db = float(self.get_parameter('deep_filter_atten_lim_db').value)
        self.df_atten_lim_db = self.base_df_atten_lim_db
        self.df_dynamic_adaptation = bool(self.get_parameter('deep_filter_dynamic_adaptation').value)
        self.df_model: Any = None

        if self.deep_filter_enabled:
            self.get_logger().info("🧠 [DF] Loading DeepFilterNet model... (this may take a few seconds)")
            start_t = time.time()
            self.df_model, self.df_state, _ = init_df(post_filter=self.df_post_filter)  # type: ignore[name-defined]
            self.df_sr: int = self.df_state.sr() # Usually 48000
            self.df_hop: int = self.df_state.hop_size() # Usually 480
            
            # Resamplers for DF (16kHz <-> 48kHz)
            self.resampler_16to48: Any = torchaudio.transforms.Resample(16000, self.df_sr)  # type: ignore[name-defined]
            self.resampler_48to16: Any = torchaudio.transforms.Resample(self.df_sr, 16000)  # type: ignore[name-defined]
            
            self.get_logger().info(
                f"✅ [DF] Model Loaded in {time.time()-start_t:.2f}s ({self.df_sr}Hz). "
                f"post_filter={self.df_post_filter}, atten_lim={self.df_atten_lim_db}dB, "
                f"dynamic_adapt={self.df_dynamic_adaptation}"
            )
        else:
            if not DF_AVAILABLE:
                self.get_logger().warn("⚠️ [DF] DeepFilterNet NOT INSTALLED. Skipping AI enhancement.")

        self.buffer_size = self.max_delay_samples + self.sample_rate * 5
        self.ref_circle = np.zeros(self.buffer_size, dtype=np.float32)
        self.ref_ptr = 0
        self.ref_queue = collections.deque()
        self.mic_history = collections.deque(maxlen=int(0.5 * self.sample_rate))
        
        self.current_delay = 0
        self.robot_speaking = False
        self.tail_samples = 0
        self._lock_count = 0
        self._drift_count = 0
        self.total_ref_samples = 0
        self.residual_gate_hold_samples = 0
        self.residual_gate_hold_limit = int(0.25 * self.sample_rate) # 250ms hold time
        
        
        self.raw_sub = self.create_subscription(Audio, 'audio_raw', self.raw_callback, 10)
        self.out_sub = self.create_subscription(Audio, 'audio_out', self.out_callback, 10)
        self.is_speaking_sub = self.create_subscription(Bool, 'is_speaking', self.is_speaking_callback, 10)
        self.env_sub = self.create_subscription(String, 'acoustic_environment', self.env_callback, 10)
        self.clean_pub = self.create_publisher(Audio, 'audio_clean', 10)
        
        # Debug files (only open if path is provided)
        self.wav_raw = self.open_wav(raw_path, 16000) if raw_path else None
        self.wav_ref_aligned = self.open_wav(ref_path, 16000) if ref_path else None
        self.wav_clean = self.open_wav(clean_path, 16000) if clean_path else None

    def is_speaking_callback(self, msg):
        self.robot_speaking = msg.data

    def open_wav(self, path, rate):
        try:
            import os
            path = os.path.expanduser(path)
            dir_name = os.path.dirname(os.path.abspath(path))
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            with open(path, 'wb') as f:
                pass
            w = wave.open(path, 'wb')
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            return w
        except Exception as e:
            self.get_logger().warn(f"⚠️ [AEC] Could not open debug WAV file at {path}: {e}")
            return None

    def out_callback(self, msg):
        """Receive the audio that will be played on speakers and enqueue it as
        the AEC reference signal.  The audio may arrive at a different sample
        rate than the AEC processing rate (e.g. Gemini sends 24 kHz, AEC works
        at 16 kHz), so we resample here.

        SCALING LOGIC:
        The reference must reflect what the microphone actually captures.
        The acoustic echo path is:
            API audio → playback_gain (audio_playback_node) → speaker → room → mic → capture_gain (audio_capture_node)
        So the reference must be scaled by: playback_gain × capture_gain
        Both values are read from YAML parameters and must be kept in sync
        with audio_playback_node.gain and audio_capture_node.gain respectively.
        """
        if len(msg.data) == 0:
            return

        src_rate = int(getattr(msg, 'sample_rate', None) or self.sample_rate)
        audio_f32 = np.array(msg.data, dtype=np.int16).astype(np.float32) / 32768.0

        # Scale reference to match what the microphone actually captures:
        # - playback_gain: applied by audio_playback_node before speaker output
        # - capture_gain: applied by audio_capture_node on microphone signal
        ref_scale = self.playback_gain * self.capture_gain
        if ref_scale != 1.0:
            audio_f32 = audio_f32 * ref_scale

        # Resample to AEC processing rate if the source rate differs.
        if src_rate != self.sample_rate:
            from math import gcd
            g = gcd(src_rate, self.sample_rate)
            up = self.sample_rate // g
            down = src_rate // g
            audio_f32 = signal.resample_poly(audio_f32, up, down).astype(np.float32)

        self.ref_queue.extend(audio_f32)

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
            
        if self.robot_speaking:
            self.tail_samples = int(0.5 * self.sample_rate) # 500ms tail
        else:
            if self.tail_samples > 0:
                self.tail_samples -= n
            else:
                self.current_delay = 0
                if len(self.ref_queue) > self.sample_rate: self.ref_queue.clear()
                self._lock_count = 0
                self._drift_count = 0
        
        # Delay Estimation
        if (self.robot_speaking or self.tail_samples > 0):
            footprint = np.array(self.mic_history)
            if len(footprint) >= self.sample_rate * 0.5:
                slen = self.max_delay_samples + len(footprint)
                area = self.get_ref_slice(slen, slen)
                f_norm = (footprint - float(np.mean(footprint))) / (float(np.std(footprint)) + 1e-6)
                a_norm = (area - float(np.mean(area))) / (float(np.std(area)) + 1e-6)
                corr = signal.correlate(a_norm, f_norm, mode='valid')
                peak = np.argmax(corr)
                score = corr[peak] / len(f_norm)
                delay = slen - peak - len(f_norm)
                
                # Compute RMS to ignore pure silence/noise
                ref_rms = np.std(area)
                if ref_rms > 0.005 and score > 0.3:  # Mai strict: ignoram daca e zgomot sau corelatia e slaba
                    if self._lock_count < 5:
                        if abs(delay - self.current_delay) < 50:
                            self._lock_count += 1
                        else:
                            self.current_delay = int(delay)
                            self._lock_count = 1
                        if self._lock_count == 5:
                            self.get_logger().info(f"🔒 AEC LOCKED: {self.current_delay/self.sample_rate*1000:.1f}ms")
                    else:
                        # Once locked, only resync if latency drifted massively (> 50ms / 800 samples)
                        # This prevents micro-jitter ("rushing") while still handling real desyncs
                        if abs(delay - self.current_delay) > 800:
                            self._drift_count += 1
                            if self._drift_count >= 15:  # Must persist for 15 consecutive frames (~300ms)
                                self.get_logger().warn(f"⚠️ AEC Drift! Resyncing delay: {self.current_delay} -> {int(delay)}")
                                self.current_delay = int(delay)
                                self._lock_count = 1
                                self._drift_count = 0
                        else:
                            self._drift_count = 0

        # Process through WebRTC
        # Activate as soon as we have at least 1 consistent delay estimate
        # (inspired by voice-cancellation/OpenAI branch which activated at current_delay > 0).
        # _lock_count >= 1 means at least one correlated estimate — good enough to start;
        # the full 5-frame lock is still tracked for the log message.
        final_clean = d_i16
        if self.apm and (self._lock_count >= 5 or (self.current_delay > 0 and self._lock_count >= 1)):
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
                
                # WebRTC API
                self.apm.set_stream_delay(0)
                self.apm.process_reverse_stream(r_frame.tobytes())
                res_bytes = self.apm.process_stream(m_frame.tobytes())
                processed_frame = np.frombuffer(res_bytes, dtype=np.int16)
                clean_out.append(processed_frame)
            
            if clean_out:
                final_clean = np.concatenate(clean_out)
                
            # --- DeepFilterNet Stage ---
            if self.df_model and len(final_clean) > 0:
                try:
                    # Convert to torch tensor (normalized float32)
                    clean_f32 = final_clean.astype(np.float32) / 32768.0
                    clean_t = torch.from_numpy(clean_f32).unsqueeze(0)
                    
                    # Resample 16kHz -> 48kHz
                    clean_48k = self.resampler_16to48(clean_t)
                    
                    # Enhance with DeepFilterNet
                    # We process the whole chunk, DF handles internal state persistence via self.df_state
                    enhanced_48k = enhance(
                        self.df_model,
                        self.df_state,
                        clean_48k,
                        atten_lim_db=self.df_atten_lim_db
                    )
                    
                    # Resample 48kHz -> 16kHz
                    enhanced_16k = self.resampler_48to16(enhanced_48k)
                    
                    # Convert back to int16
                    enhanced_np = (torch.clamp(enhanced_16k.squeeze(0), -1.0, 1.0).numpy() * 32767.0).astype(np.int16)
                    final_clean = enhanced_np
                except Exception as e:
                    self.get_logger().error(f"❌ [DF] Enhancement Error: {e}")
        else:
            if self.wav_ref_aligned: self.wav_ref_aligned.writeframes(np.zeros(n, dtype=np.int16).tobytes())

        # ── Residual Gate ─────────────────────────────────────────────────────
        # Applied only during/after robot playback (when AEC was active).
        # AEC residuals are low-amplitude; real human voice (barge-in) is higher.
        # Uses a hangover (hold) timer to prevent rapid frame-by-frame chattering/cutting.
        if self.residual_gate_enabled and (self.robot_speaking or self.tail_samples > 0):
            rms = float(np.sqrt(np.mean(final_clean.astype(np.float32) ** 2)))
            if rms >= self.residual_gate_rms_threshold:
                # Vocal activity detected: reset/hold the gate open
                self.residual_gate_hold_samples = self.residual_gate_hold_limit
            else:
                # No activity: decay the hold samples count
                if self.residual_gate_hold_samples > 0:
                    self.residual_gate_hold_samples -= len(final_clean)
            
            # If hangover expired, mute the frame to clean residual echo
            if self.residual_gate_hold_samples <= 0:
                final_clean = np.zeros(len(final_clean), dtype=np.int16)

        if self.mic_gain != 1.0:
            final_clean = np.clip(final_clean.astype(np.float32) * self.mic_gain, -32768, 32767).astype(np.int16)

        msg.data = final_clean.tolist()
        self.clean_pub.publish(msg)
        if self.wav_clean: self.wav_clean.writeframes(final_clean.tobytes())

    def env_callback(self, msg: String):
        try:
            data = json.loads(msg.data)
            state = data.get('state', 'moderate')
            ns_level_changed = False
            
            if state == 'quiet':
                self.residual_gate_rms_threshold = int(self.base_residual_gate_rms_threshold * 0.5)
                new_ns_level = 1
                new_df_atten = 15.0
            elif state == 'noisy':
                self.residual_gate_rms_threshold = int(self.base_residual_gate_rms_threshold * 2.3)
                new_ns_level = 3
                new_df_atten = 30.0
            else:
                self.residual_gate_rms_threshold = self.base_residual_gate_rms_threshold
                new_ns_level = self.base_webrtc_ns_level
                new_df_atten = 22.0

            if self.df_dynamic_adaptation and abs(self.df_atten_lim_db - new_df_atten) > 1.0:
                self.df_atten_lim_db = new_df_atten
                self.get_logger().info(
                    f'🧠 [DF] DeepFilterNet atten_lim_db dynamically set to {self.df_atten_lim_db:.1f} dB (env: {state.upper()})'
                )
                
            if new_ns_level != self.webrtc_ns_level:
                self.webrtc_ns_level = new_ns_level
                ns_level_changed = True
                
            self.get_logger().debug(
                f'Acoustic state: {state} | Threshold: {self.residual_gate_rms_threshold} | '
                f'NS Level: {self.webrtc_ns_level} | DF Atten: {self.df_atten_lim_db}dB'
            )
            
            # Recreate APM if noise suppression level changed
            if ns_level_changed and WEBRTC_AVAILABLE and self.apm is not None:
                self.apm = AudioProcessor(
                    enable_aec=True,
                    enable_ns=self.webrtc_ns_enabled,
                    ns_level=self.webrtc_ns_level,
                    enable_agc=False
                )
                self.apm.set_stream_format(16000, 1)
                self.apm.set_reverse_stream_format(16000, 1)
                self.get_logger().info(f'🔄 [AEC] WebRTC NS Level dynamically updated to {self.webrtc_ns_level}')
        except Exception as e:
            self.get_logger().error(f'Error parsing acoustic environment in AEC: {e}')

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
        try:
            rclpy.shutdown()
        except Exception:
            pass

if __name__ == '__main__':
    main()
