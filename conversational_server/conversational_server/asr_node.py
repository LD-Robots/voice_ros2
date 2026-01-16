#!/usr/bin/env python3
"""
ASR Node - Speech to Text using Faster Whisper (Standalone).

FEATURES (sincronizat cu Conversational_Robot Python):
  - Warmup la start pentru încărcare completă model
  - Detecție RO/EN cu alegere best score
  - Fallback fără VAD pentru erori

Subscribes to: 
  - /audio_raw (Audio) - audio frames
  - /voice_activity (Bool) - VAD status
Publishes to: /transcription (Transcription)

Buffers audio while user is speaking, then transcribes when speech ends.
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio, Transcription
from std_msgs.msg import Bool
import numpy as np
import tempfile
import wave
import os
import time
import soundfile as sf

# Faster Whisper pentru ASR
try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    print("⚠️ faster-whisper not installed. Run: pip install faster-whisper")


class ASRNode(Node):
    def __init__(self):
        super().__init__('asr_node')
        
        # Parametri configurabili
        self.declare_parameter('model_size', 'small')
        self.declare_parameter('device', 'cpu')
        self.declare_parameter('compute_type', 'int8')
        self.declare_parameter('min_audio_length', 0.5)  # Minimum seconds to transcribe
        self.declare_parameter('language', '')  # Empty = auto-detect, 'ro_en' = detect best
        self.declare_parameter('beam_size', 5)
        self.declare_parameter('vad_min_silence_ms', 300)
        self.declare_parameter('warmup_enabled', True)
        
        model_size = self.get_parameter('model_size').value
        device = self.get_parameter('device').value
        compute_type = self.get_parameter('compute_type').value
        self.min_audio_length = self.get_parameter('min_audio_length').value
        self.language = self.get_parameter('language').value or None
        self.beam_size = self.get_parameter('beam_size').value
        self.vad_min_silence_ms = self.get_parameter('vad_min_silence_ms').value
        self.warmup_enabled = self.get_parameter('warmup_enabled').value
        
        if not WHISPER_AVAILABLE:
            self.get_logger().error('faster-whisper not installed!')
            raise RuntimeError('faster-whisper not available')
        
        # Inițializează Whisper model
        self.get_logger().info(f'Loading Whisper model: {model_size} on {device}...')
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type
        )
        self.get_logger().info('✅ Whisper model loaded!')
        
        # Warmup la start
        self._warmed_up = False
        self._ensure_warm()
        
        # Buffer pentru audio
        self.audio_buffer = []
        self.sample_rate = 16000
        self.channels = 1
        self.is_speaking = False
        self.was_speaking = False
        
        # Subscriber pentru audio
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # Subscriber pentru VAD
        self.vad_sub = self.create_subscription(
            Bool,
            '/voice_activity',
            self.vad_callback,
            10
        )
        
        # Publisher pentru transcriere
        self.transcription_pub = self.create_publisher(
            Transcription,
            '/transcription',
            10
        )
        
        self.get_logger().info('ASR Node started! Listening on /audio_raw and /voice_activity')
    
    def _ensure_warm(self):
        """Încarcă complet modelul prin transcriere dummy."""
        if not self.warmup_enabled or self._warmed_up:
            return
        try:
            self.get_logger().info("🔥 ASR warm-up start...")
            start = time.perf_counter()
            
            # Creează fișier audio scurt (0.5s tăcere)
            fd, temp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                silence = np.zeros(8000, dtype=np.float32)  # 0.5s @ 16kHz
                sf.write(temp_path, silence, 16000)
                
                # Transcriere dummy
                self.model.transcribe(temp_path, language="en", beam_size=1)
            finally:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            
            elapsed = time.perf_counter() - start
            self._warmed_up = True
            self.get_logger().info(f"✅ ASR warm-up gata ({elapsed:.2f}s)")
        except Exception as e:
            self.get_logger().warning(f"ASR warm-up eșuat: {e}")
    
    def _run_once(self, wav_path: str, language, use_vad: bool):
        """
        Returnează: (text, lang_out, lang_prob, score)
        score = medie(avg_logprob pe segmente) + 0.01 * len(text)
        """
        segments, info = self.model.transcribe(
            str(wav_path),
            language=language,
            beam_size=self.beam_size,
            temperature=0.0,
            vad_filter=use_vad,
            vad_parameters={"min_silence_duration_ms": self.vad_min_silence_ms} if use_vad else None,
            no_speech_threshold=0.6,
            log_prob_threshold=-0.5,
            condition_on_previous_text=False,
        )
        segs = list(segments)
        text = "".join(s.text for s in segs).strip()
        
        # scor simplu și robust
        if segs:
            vals = [getattr(s, "avg_logprob", -5.0) if getattr(s, "avg_logprob", None) is not None else -5.0 for s in segs]
            avg_lp = sum(vals) / len(vals)
        else:
            avg_lp = -9.0
        score = avg_lp + 0.01 * len(text)
        out_lang = info.language or (language or "en")
        prob = float(getattr(info, "language_probability", 0.0) or 0.0)
        return text, out_lang, prob, score
    
    def _transcribe_ro_en(self, wav_path: str):
        """
        Transcriere strict EN/RO -> alegem cea mai bună.
        Rulăm EN & RO cu VAD intern; dacă dă eroare, retry fără VAD.
        """
        def safe(lang):
            try:
                return self._run_once(wav_path, lang, use_vad=True)
            except ValueError as e:
                if "max() iterable argument is empty" in str(e):
                    return self._run_once(wav_path, lang, use_vad=False)
                raise
        
        en_text, _, _, en_score = safe("en")
        ro_text, _, _, ro_score = safe("ro")
        
        if (ro_score > en_score) and ro_text:
            return {"text": ro_text, "lang": "ro", "language_probability": 1.0}
        else:
            return {"text": en_text, "lang": "en", "language_probability": 1.0}
    
    def vad_callback(self, msg: Bool):
        """Primește statusul VAD (vorbește/nu vorbește)."""
        self.was_speaking = self.is_speaking
        self.is_speaking = msg.data
        
        # Când userul termină de vorbit, transcrie
        if self.was_speaking and not self.is_speaking:
            self.get_logger().info(f'🔚 Speech ended, processing {len(self.audio_buffer)} frames...')
            self._process_buffer()
    
    def audio_callback(self, msg: Audio):
        """Bufferează audio în timpul vorbirii."""
        self.sample_rate = msg.sample_rate
        self.channels = msg.channels
        
        # Bufferează audio când userul vorbește (sau puțin înainte)
        if self.is_speaking:
            self.audio_buffer.extend(msg.data)
        else:
            # Păstrează ultimele 0.5 secunde pentru context
            max_pre_buffer = int(self.sample_rate * 0.5)
            self.audio_buffer.extend(msg.data)
            if len(self.audio_buffer) > max_pre_buffer:
                self.audio_buffer = self.audio_buffer[-max_pre_buffer:]
    
    def _process_buffer(self):
        """Procesează audio-ul bufferat și publică transcrierea."""
        if not self.audio_buffer:
            self.get_logger().warn('Empty audio buffer, skipping')
            self.audio_buffer = []
            return
        
        # Verifică lungimea minimă
        audio_length = len(self.audio_buffer) / self.sample_rate
        if audio_length < self.min_audio_length:
            self.get_logger().warn(f'Audio too short ({audio_length:.2f}s < {self.min_audio_length}s), skipping')
            self.audio_buffer = []
            return
        
        self.get_logger().info(f'🎤 Processing {audio_length:.2f}s of audio...')
        
        # Convertește în numpy array
        audio_data = np.array(self.audio_buffer, dtype=np.int16)
        
        # Salvează temporar ca WAV
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            temp_path = f.name
            with wave.open(f.name, 'wb') as wav:
                wav.setnchannels(self.channels)
                wav.setsampwidth(2)  # 16-bit = 2 bytes
                wav.setframerate(self.sample_rate)
                wav.writeframes(audio_data.tobytes())
        
        try:
            # Folosește detecție RO/EN dacă setat
            if self.language == 'ro_en':
                result = self._transcribe_ro_en(temp_path)
                text = result["text"]
                lang = result["lang"]
                confidence = result["language_probability"]
            else:
                # Transcrie cu Faster Whisper - cu fallback fără VAD
                try:
                    text, lang, confidence, _ = self._run_once(temp_path, self.language, use_vad=True)
                except ValueError as e:
                    if "max() iterable argument is empty" in str(e):
                        self.get_logger().warn("VAD error, retrying without VAD filter...")
                        fallback_lang = self.language or "en"
                        text, lang, confidence, _ = self._run_once(temp_path, fallback_lang, use_vad=False)
                    else:
                        raise
            
            if text:
                self.get_logger().info(f'🧏 [{lang}] {text}')
                
                # Publică rezultat
                out = Transcription()
                out.text = text
                out.language = lang
                out.confidence = float(confidence)
                self.transcription_pub.publish(out)
            else:
                self.get_logger().warn('Empty transcription, skipping')
                
        except Exception as e:
            self.get_logger().error(f'ASR error: {e}')
        finally:
            # Cleanup
            if os.path.exists(temp_path):
                os.remove(temp_path)
            self.audio_buffer = []


def main(args=None):
    rclpy.init(args=args)
    
    try:
        node = ASRNode()
        rclpy.spin(node)
    except RuntimeError as e:
        print(f'Failed to start ASR node: {e}')
    except KeyboardInterrupt:
        pass
    finally:
        rclpy.shutdown()


if __name__ == '__main__':
    main()
