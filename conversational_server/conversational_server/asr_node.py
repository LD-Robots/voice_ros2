#!/usr/bin/env python3
"""
ASR Node - Speech to Text using Faster Whisper (Standalone).

FEATURES (synced with Conversational_Robot Python):
  - Warmup at start for full model load
  - RO/EN detection with best score selection
  - Fallback without VAD on errors

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
import re
import unicodedata
import soundfile as sf

# RapidFuzz for textual anti-echo
try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    print("⚠️ rapidfuzz not installed. Anti-echo disabled. Run: pip install rapidfuzz")

# Faster Whisper for ASR
try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    print("⚠️ faster-whisper not installed. Run: pip install faster-whisper")


class ASRNode(Node):
    def __init__(self):
        super().__init__('asr_node')
        
        # Configurable parameters
        self.declare_parameter('model_size', 'small')
        self.declare_parameter('device', 'cpu')
        self.declare_parameter('compute_type', 'int8')
        self.declare_parameter('model_fallbacks', 'small,base,tiny')
        self.declare_parameter('min_audio_length', 0.5)  # Minimum seconds to transcribe
        self.declare_parameter('language', '')  # Empty = auto-detect, 'ro_en' = detect best
        self.declare_parameter('beam_size', 5)
        self.declare_parameter('vad_min_silence_ms', 300)
        self.declare_parameter('warmup_enabled', True)
        
        # Anti-echo textual parameters
        self.declare_parameter('echo_threshold', 85)  # Similarity % to consider as echo
        self.declare_parameter('echo_min_length', 8)  # Min chars to check for echo
        self.declare_parameter('echo_enabled', True)  # Enable/disable anti-echo
        
        model_size = self.get_parameter('model_size').value
        device = self.get_parameter('device').value
        compute_type = self.get_parameter('compute_type').value
        model_fallbacks = self.get_parameter('model_fallbacks').value
        self.min_audio_length = self.get_parameter('min_audio_length').value
        self.language = self.get_parameter('language').value or None
        self.beam_size = self.get_parameter('beam_size').value
        self.vad_min_silence_ms = self.get_parameter('vad_min_silence_ms').value
        self.warmup_enabled = self.get_parameter('warmup_enabled').value
        
        # Anti-echo settings
        self.echo_threshold = self.get_parameter('echo_threshold').value
        self.echo_min_length = self.get_parameter('echo_min_length').value
        self.echo_enabled = self.get_parameter('echo_enabled').value and RAPIDFUZZ_AVAILABLE
        
        if not WHISPER_AVAILABLE:
            self.get_logger().error('faster-whisper not installed!')
            raise RuntimeError('faster-whisper not available')
        
        # Initialize Whisper model with fallback sizes
        self.model = self._load_model_with_fallbacks(
            preferred_model=model_size,
            fallback_models=model_fallbacks,
            device=device,
            compute_type=compute_type,
        )
        
        # Warmup at start
        self._warmed_up = False
        self._ensure_warm()
        
        # Audio buffer
        self.audio_buffer = []
        self.sample_rate = 16000
        self.channels = 1
        self.is_speaking = False
        self.was_speaking = False
        
        # Anti-echo: last bot reply
        self.last_bot_reply = ""
        
        # Subscriber for audio
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # Subscriber for VAD
        self.vad_sub = self.create_subscription(
            Bool,
            '/voice_activity',
            self.vad_callback,
            10
        )
        
        # Subscriber for LLM response (anti-echo)
        self.llm_response_sub = self.create_subscription(
            Transcription,
            '/llm_response',
            self.llm_response_callback,
            10
        )
        
        # Publisher for transcription
        self.transcription_pub = self.create_publisher(
            Transcription,
            '/transcription',
            10
        )
        
        if self.echo_enabled:
            self.get_logger().debug(f'🔇 Anti-echo ENABLED (threshold={self.echo_threshold}%, min_len={self.echo_min_length})')
        else:
            self.get_logger().debug('🔇 Anti-echo DISABLED')
        
        self.get_logger().debug('ASR Node started! Listening on /audio_raw, /voice_activity, /llm_response')

    def _load_model_with_fallbacks(self, preferred_model: str, fallback_models: str, device: str, compute_type: str):
        """Load Whisper model, trying fallback sizes if needed."""
        candidates = [preferred_model]
        if fallback_models:
            candidates.extend([m.strip() for m in str(fallback_models).split(',') if m.strip()])

        # Keep order, drop duplicates
        seen = set()
        ordered = []
        for cand in candidates:
            if cand not in seen:
                ordered.append(cand)
                seen.add(cand)

        last_error = None
        for cand in ordered:
            try:
                self.get_logger().debug(f'Loading Whisper model: {cand} on {device}...')
                model = WhisperModel(
                    cand,
                    device=device,
                    compute_type=compute_type
                )
                self.get_logger().debug(f'✅ Whisper model loaded: {cand}')
                return model
            except Exception as e:
                last_error = e
                self.get_logger().warn(f'Failed to load model "{cand}": {e}')

        raise RuntimeError(f'No ASR model could be loaded. Last error: {last_error}')
    
    def _ensure_warm(self):
        """Fully load the model via a dummy transcription."""
        if not self.warmup_enabled or self._warmed_up:
            return
        try:
            self.get_logger().debug("🔥 ASR warm-up start...")
            start = time.perf_counter()
            
            # Create a short audio file (0.5s silence)
            fd, temp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                silence = np.zeros(8000, dtype=np.float32)  # 0.5s @ 16kHz
                sf.write(temp_path, silence, 16000)
                
                # Dummy transcription
                self.model.transcribe(temp_path, language="en", beam_size=1)
            finally:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            
            elapsed = time.perf_counter() - start
            self._warmed_up = True
            self.get_logger().debug(f"✅ ASR warm-up gata ({elapsed:.2f}s)")
        except Exception as e:
            self.get_logger().warning(f"ASR warm-up eșuat: {e}")
    
    def _run_once(self, wav_path: str, language, use_vad: bool):
        """
        Returns: (text, lang_out, lang_prob, score)
        score = average(avg_logprob over segments) + 0.01 * len(text)
        """
        segments, info = self.model.transcribe(
            str(wav_path),
            language=language,
            beam_size=self.beam_size,
            temperature=0.0,
            vad_filter=use_vad,
            vad_parameters={"min_silence_duration_ms": self.vad_min_silence_ms} if use_vad else None,
            no_speech_threshold=0.5,  # Lower = less likely to skip valid speech
            log_prob_threshold=-0.7,  # Lower = accept lower confidence segments
            condition_on_previous_text=False,
        )
        segs = list(segments)
        text = "".join(s.text for s in segs).strip()
        
        # Simple, robust score
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
        Strict EN/RO transcription -> choose the best.
        Run EN & RO with internal VAD; if it errors, retry without VAD.
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
    
    def _normalize_text(self, text: str) -> str:
        """
        Normalize text for anti-echo comparison.
        Removes diacritics, punctuation, extra spaces, and lowercases.
        """
        if not text:
            return ""
        # Lowercase
        text = text.lower()
        # Remove diacritics (ă->a, î->i, etc.)
        text = unicodedata.normalize('NFD', text)
        text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
        # Remove punctuation and special characters
        text = re.sub(r'[^a-z0-9\s]', '', text)
        # Normalize spaces
        text = ' '.join(text.split())
        return text.strip()
    
    def _is_echo(self, transcription: str) -> bool:
        """
        Check whether the transcription is TTS echo.
        Return True if it should be ignored.
        """
        if not self.echo_enabled or not self.last_bot_reply:
            return False
        
        user_norm = self._normalize_text(transcription)
        bot_norm = self._normalize_text(self.last_bot_reply)
        
        # Check only if both are long enough
        if len(user_norm) < self.echo_min_length or len(bot_norm) < self.echo_min_length:
            return False
        
        # Compute similarity
        similarity = fuzz.partial_ratio(user_norm, bot_norm)
        
        if similarity >= self.echo_threshold:
            self.get_logger().debug(f'🔇 Ignor input (echo TTS) sim={similarity}% > {self.echo_threshold}%')
            return True
        
        return False
    
    def llm_response_callback(self, msg: Transcription):
        """Store the last bot reply for anti-echo."""
        if msg.text:
            self.last_bot_reply = msg.text
            self.get_logger().debug(f'📝 Stored bot reply for anti-echo: {msg.text[:50]}...')
    
    def vad_callback(self, msg: Bool):
        """Receive VAD status (speaking/not speaking)."""
        self.was_speaking = self.is_speaking
        self.is_speaking = msg.data
        
        # When the user finishes speaking, transcribe
        if self.was_speaking and not self.is_speaking:
            self.get_logger().debug(f'🔚 Speech ended, processing {len(self.audio_buffer)} frames...')
            self._process_buffer()
    
    def audio_callback(self, msg: Audio):
        """Buffer audio during speech."""
        self.sample_rate = msg.sample_rate
        self.channels = msg.channels
        
        # Buffer audio when the user speaks (or slightly before)
        if self.is_speaking:
            self.audio_buffer.extend(msg.data)
        else:
            # Keep the last 0.5 seconds for context
            max_pre_buffer = int(self.sample_rate * 0.5)
            self.audio_buffer.extend(msg.data)
            if len(self.audio_buffer) > max_pre_buffer:
                self.audio_buffer = self.audio_buffer[-max_pre_buffer:]
    
    def _process_buffer(self):
        """Process buffered audio and publish transcription."""
        if not self.audio_buffer:
            self.get_logger().warn('Empty audio buffer, skipping')
            self.audio_buffer = []
            return
        
        # Check minimum length
        audio_length = len(self.audio_buffer) / self.sample_rate
        if audio_length < self.min_audio_length:
            self.get_logger().warn(f'Audio too short ({audio_length:.2f}s < {self.min_audio_length}s), skipping')
            self.audio_buffer = []
            return
        
        self.get_logger().info(f'🎤 Processing {audio_length:.2f}s of audio...')
        
        # Convert to numpy array
        audio_data = np.array(self.audio_buffer, dtype=np.int16)
        
        # Save temporarily as WAV
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            temp_path = f.name
            with wave.open(f.name, 'wb') as wav:
                wav.setnchannels(self.channels)
                wav.setsampwidth(2)  # 16-bit = 2 bytes
                wav.setframerate(self.sample_rate)
                wav.writeframes(audio_data.tobytes())
        
        try:
            # Use RO/EN detection if set
            if self.language == 'ro_en':
                result = self._transcribe_ro_en(temp_path)
                text = result["text"]
                lang = result["lang"]
                confidence = result["language_probability"]
            else:
                # Transcribe with Faster Whisper - with fallback without VAD
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
                
                # Anti-echo: check if it's echo from TTS
                if self._is_echo(text):
                    self.audio_buffer = []
                    return
                
                # Publish result
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
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()
