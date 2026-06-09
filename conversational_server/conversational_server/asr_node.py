#!/usr/bin/env python3
"""
ASR Node - Speech to Text using Faster Whisper (Standalone).

FEATURES (synced with Conversational_Robot Python):
  - Warmup at start for full model loading
  - RO/EN detection with best-score selection
  - Fallback without VAD for errors

Subscribes to: 
  - /audio_raw (Audio) - audio frames
  - /voice_activity (Bool) - VAD status
Publishes to: /transcription (Transcription)

Buffers audio while user is speaking, then transcribes when speech ends.
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio, Transcription
from std_msgs.msg import Bool, String
import numpy as np
import tempfile
import wave
import os
import time
import io
import json
import re
import unicodedata
import soundfile as sf
from pathlib import Path

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("⚠️ requests not installed. Run: pip install requests")

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

        self._load_env()
        
        # Configurable parameters
        self.declare_parameter('provider', 'whisper')
        self.declare_parameter('model_size', 'small')
        self.declare_parameter('device', 'cpu')
        self.declare_parameter('compute_type', 'int8')
        self.declare_parameter('min_audio_length', 0.5)  # Minimum seconds to transcribe
        self.declare_parameter('language', '')  # Empty = auto-detect, 'ro_en' = detect best
        self.declare_parameter('beam_size', 5)
        self.declare_parameter('vad_min_silence_ms', 300)
        self.declare_parameter('warmup_enabled', True)
        self.declare_parameter('eleven_api_key_env', 'ELEVENLABS_API_KEY')
        self.declare_parameter('eleven_model_id', 'scribe_v2')
        self.declare_parameter('eleven_language_code', '')
        self.declare_parameter('eleven_diarize', True)
        self.declare_parameter('eleven_num_speakers', 0)
        self.declare_parameter('eleven_diarization_threshold', 0.0)
        self.declare_parameter('eleven_tag_audio_events', False)
        self.declare_parameter('eleven_timeout_s', 30.0)
        self.declare_parameter('eleven_prefer_raw_pcm', True)
        self.declare_parameter('eleven_min_diarized_words', 2)
        self.declare_parameter('eleven_allowed_language_codes', 'en,eng,ro,ron,rum')
        
        # Anti-echo textual parameters
        self.declare_parameter('echo_threshold', 85)  # Similarity % to consider as echo
        self.declare_parameter('echo_min_length', 8)  # Min chars to check for echo
        self.declare_parameter('echo_enabled', True)  # Enable/disable anti-echo
        
        self.provider = str(self.get_parameter('provider').value or 'whisper').strip().lower()
        model_size = self.get_parameter('model_size').value
        device = self.get_parameter('device').value
        compute_type = self.get_parameter('compute_type').value
        self.min_audio_length = self.get_parameter('min_audio_length').value
        self.language = self.get_parameter('language').value or None
        self.beam_size = self.get_parameter('beam_size').value
        self.vad_min_silence_ms = self.get_parameter('vad_min_silence_ms').value
        self.warmup_enabled = self.get_parameter('warmup_enabled').value
        self.eleven_api_key_env = str(self.get_parameter('eleven_api_key_env').value)
        self.eleven_model_id = str(self.get_parameter('eleven_model_id').value)
        self.eleven_language_code = str(self.get_parameter('eleven_language_code').value or '')
        self.eleven_diarize = bool(self.get_parameter('eleven_diarize').value)
        self.eleven_num_speakers = int(self.get_parameter('eleven_num_speakers').value or 0)
        self.eleven_diarization_threshold = float(
            self.get_parameter('eleven_diarization_threshold').value or 0.0
        )
        self.eleven_tag_audio_events = bool(
            self.get_parameter('eleven_tag_audio_events').value
        )
        self.eleven_timeout_s = float(self.get_parameter('eleven_timeout_s').value)
        self.eleven_prefer_raw_pcm = bool(
            self.get_parameter('eleven_prefer_raw_pcm').value
        )
        self.eleven_min_diarized_words = max(
            1,
            int(self.get_parameter('eleven_min_diarized_words').value),
        )
        self.eleven_allowed_language_codes = {
            code.strip().lower()
            for code in str(self.get_parameter('eleven_allowed_language_codes').value or '').split(',')
            if code.strip()
        }
        
        # Anti-echo settings
        self.echo_threshold = self.get_parameter('echo_threshold').value
        self.echo_min_length = self.get_parameter('echo_min_length').value
        self.echo_enabled = self.get_parameter('echo_enabled').value and RAPIDFUZZ_AVAILABLE
        
        self.model = None
        if self.provider == 'elevenlabs':
            if not REQUESTS_AVAILABLE:
                self.get_logger().error('requests not installed - required for ElevenLabs STT!')
                raise RuntimeError('requests not available')
            self.eleven_api_key = self._read_elevenlabs_api_key()
            if not self.eleven_api_key:
                self.get_logger().error(
                    f'{self.eleven_api_key_env} or ELEVENLABS_API_KEY environment variable not set!'
                )
                raise RuntimeError(f'{self.eleven_api_key_env} not set')
            self._warmed_up = True
            self.get_logger().info(
                f'✅ ASR initialized with ElevenLabs STT: model={self.eleven_model_id}, '
                f'diarize={self.eleven_diarize}'
            )
        elif not WHISPER_AVAILABLE:
            self.get_logger().error('faster-whisper not installed!')
            raise RuntimeError('faster-whisper not available')
        else:
            # Initialize Whisper model
            self.get_logger().debug(f'Loading Whisper model: {model_size} on {device}...')
            self.model = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type
            )
            self.get_logger().debug('✅ Whisper model loaded!')

            # Warmup on startup
            self._warmed_up = False
            self._ensure_warm()
        
        # Buffer for audio
        self.audio_buffer = []
        self.sample_rate = 16000
        self.channels = 1
        self.is_speaking = False
        self.was_speaking = False
        
        # Anti-echo: last robot response
        self.last_bot_reply = ""
        self.current_backend = 'legacy'
        
        # Audio subscriber
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # VAD subscriber
        self.vad_sub = self.create_subscription(
            Bool,
            '/voice_activity',
            self.vad_callback,
            10
        )
        self.backend_sub = self.create_subscription(
            String,
            '/conversation_backend',
            self.backend_callback,
            10
        )
        
        # LLM response subscriber (anti-echo)
        self.llm_response_sub = self.create_subscription(
            Transcription,
            '/llm_response',
            self.llm_response_callback,
            10
        )
        
        # Transcription publisher
        self.transcription_pub = self.create_publisher(
            Transcription,
            '/transcription',
            10
        )
        self.diarization_pub = self.create_publisher(
            String,
            '/elevenlabs_diarization',
            10,
        )
        
        if self.echo_enabled:
            self.get_logger().debug(f'🔇 Anti-echo ENABLED (threshold={self.echo_threshold}%, min_len={self.echo_min_length})')
        else:
            self.get_logger().debug('🔇 Anti-echo DISABLED')
        
        self.get_logger().debug(
            f'ASR Node started with provider={self.provider}! '
            'Listening on /audio_raw, /voice_activity, /llm_response'
        )

    def _load_env(self):
        if not DOTENV_AVAILABLE:
            return
        for base in (Path(__file__).resolve(), Path.cwd().resolve()):
            for parent in [base] + list(base.parents):
                if parent.name == 'voice_ros2':
                    env_path = parent / '.env'
                    if env_path.exists():
                        load_dotenv(dotenv_path=env_path)
                    return

    def _read_elevenlabs_api_key(self) -> str:
        """Read API key using the configured name, with common aliases."""
        names = [
            self.eleven_api_key_env,
            'ELEVENLABS_API_KEY',
            'ELEVEN_API_KEY',
        ]
        for name in names:
            value = os.environ.get(str(name or '').strip(), '')
            if value:
                return value
        return ''
    
    def audio_callback(self, msg: Audio):
        """Buffers audio during speech."""
        if self.current_backend != 'legacy':
            return
        self.sample_rate = msg.sample_rate
        self.channels = msg.channels
        
        # Buffers audio when the user is speaking (or slightly before)
        if self.is_speaking:
            self.audio_buffer.extend(msg.data)
        else:
            # Keep the last 0.5 seconds for context
            max_pre_buffer = int(self.sample_rate * 0.5)
            self.audio_buffer.extend(msg.data)
            if len(self.audio_buffer) > max_pre_buffer:
                self.audio_buffer = self.audio_buffer[-max_pre_buffer:]

    def vad_callback(self, msg: Bool):
        """Receive VAD status (speaking/not speaking)."""
        if self.current_backend != 'legacy':
            self.was_speaking = False
            self.is_speaking = False
            self.audio_buffer = []
            return
        self.was_speaking = self.is_speaking
        self.is_speaking = msg.data
        
        # When the user finishes speaking, transcribe
        if self.was_speaking and not self.is_speaking:
            self.get_logger().debug(f'🔚 Speech ended, processing {len(self.audio_buffer)} frames...')
            self._process_buffer()

    def llm_response_callback(self, msg: Transcription):
        """Store the last robot response for anti-echo."""
        if msg.text:
            self.last_bot_reply = msg.text
            self.get_logger().debug(f'📝 Stored bot reply for anti-echo: {msg.text[:50]}...')

    def backend_callback(self, msg: String):
        backend = msg.data.strip() or 'legacy'
        if backend != self.current_backend:
            self.current_backend = backend
            self.audio_buffer = []
            self.is_speaking = False
            self.was_speaking = False

    def _normalize_text(self, text: str) -> str:
        """
        Normalize text for anti-echo comparison.
        Remove diacritics, punctuation, extra spaces and make lowercase.
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
        Check if the transcription is an echo from TTS.
        Return True if it should be ignored.
        """
        if not self.echo_enabled or not self.last_bot_reply:
            return False
        
        user_norm = self._normalize_text(transcription)
        bot_norm = self._normalize_text(self.last_bot_reply)
        
        # Only check if both are long enough
        if len(user_norm) < self.echo_min_length or len(bot_norm) < self.echo_min_length:
            return False
        
        # Calculate similarity
        similarity = fuzz.partial_ratio(user_norm, bot_norm)
        
        if similarity >= self.echo_threshold:
            self.get_logger().debug(f'🔇 Ignor input (echo TTS) sim={similarity}% > {self.echo_threshold}%')
            return True
        
        return False

    def _process_buffer(self):
        """Process the buffered audio and publish the transcription."""
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
        
        # Save in memory (BytesIO) as WAV
        wav_io = io.BytesIO()
        try:
            with wave.open(wav_io, 'wb') as wav:
                wav.setnchannels(self.channels)
                wav.setsampwidth(2)  # 16-bit = 2 bytes
                wav.setframerate(self.sample_rate)
                wav.writeframes(audio_data.tobytes())
            
            # Reset cursor to the beginning of the buffer
            wav_io.seek(0)
            
            if self.provider == 'elevenlabs':
                result = self._run_elevenlabs(wav_io, audio_data)
                text = result["text"]
                lang = result["lang"]
                confidence = result["language_probability"]
                self._publish_diarization(result.get("diarization", {}))
            # Use RO/EN detection if set
            elif self.language == 'ro_en':
                # For ro_en we need to read twice, so BytesIO is perfect (seek(0))
                result = self._transcribe_ro_en(wav_io)
                text = result["text"]
                lang = result["lang"]
                confidence = result["language_probability"]
            else:
                # Transcribe with Faster Whisper - with fallback without VAD
                try:
                    text, lang, confidence, _ = self._run_once(wav_io, self.language, use_vad=True)
                except ValueError as e:
                    if "max() iterable argument is empty" in str(e):
                        self.get_logger().warn("VAD error, retrying without VAD filter...")
                        wav_io.seek(0) # Reset for the second attempt
                        fallback_lang = self.language or "en"
                        text, lang, confidence, _ = self._run_once(wav_io, fallback_lang, use_vad=False)
                    else:
                        raise
            
            if text:
                self.get_logger().info(f'🧏 [{lang}] {text}')

                if self.provider == 'elevenlabs' and not self._is_allowed_eleven_language(lang):
                    self.get_logger().warn(
                        f'Ignoring ElevenLabs transcription in unsupported language "{lang}": {text}'
                    )
                    self.audio_buffer = []
                    return
                
                # Anti-echo: check if it is an echo from TTS
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
            self.audio_buffer = []

    def _ensure_warm(self):
        """Fully load the model via a dummy transcription."""
        if not self.warmup_enabled or self._warmed_up:
            return
        try:
            self.get_logger().debug("🔥 ASR warm-up start...")
            start = time.perf_counter()
            
            # Create short WAV in memory
            wav_io = io.BytesIO()
            silence = np.zeros(8000, dtype=np.int16)  # 0.5s @ 16kHz
            with wave.open(wav_io, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(silence.tobytes())
            wav_io.seek(0)

            # Dummy transcription to force full model load
            self.model.transcribe(wav_io, language="en", beam_size=1)
            
            elapsed = time.perf_counter() - start
            self._warmed_up = True
            self.get_logger().debug(f"✅ ASR warm-up complete ({elapsed:.2f}s)")
        except Exception as e:
            self.get_logger().warning(f"ASR warm-up failed: {e}")

    def _run_once(self, audio_source, language, use_vad: bool):
        """
        Audio source can be a path (str) or file-like object (BytesIO).
        Returns: (text, lang_out, lang_prob, score)
        """
        # If it is a stream, make sure it is at the beginning
        if hasattr(audio_source, 'seek'):
            audio_source.seek(0)

        segments, info = self.model.transcribe(
            audio_source,
            language=language,
            beam_size=self.beam_size,
            temperature=0.0,
            vad_filter=use_vad,
            vad_parameters={"min_silence_duration_ms": self.vad_min_silence_ms} if use_vad else None,
            no_speech_threshold=0.5,
            log_prob_threshold=-0.7,
            condition_on_previous_text=False,
        )
        segs = list(segments)
        text = "".join(s.text for s in segs).strip()
        
        if segs:
            vals = [getattr(s, "avg_logprob", -5.0) if getattr(s, "avg_logprob", None) is not None else -5.0 for s in segs]
            avg_lp = sum(vals) / len(vals)
        else:
            avg_lp = -9.0
        score = avg_lp + 0.01 * len(text)
        out_lang = info.language or (language or "en")
        prob = float(getattr(info, "language_probability", 0.0) or 0.0)
        return text, out_lang, prob, score

    def _run_elevenlabs(self, audio_source, audio_pcm: np.ndarray | None = None):
        """Transcribe one utterance with ElevenLabs Scribe v2."""
        if hasattr(audio_source, 'seek'):
            audio_source.seek(0)

        file_bytes = audio_source.read()
        filename = 'speech.wav'
        content_type = 'audio/wav'
        file_format = 'other'
        if (
            self.eleven_prefer_raw_pcm
            and audio_pcm is not None
            and self.sample_rate == 16000
            and self.channels == 1
        ):
            file_bytes = np.asarray(audio_pcm, dtype=np.int16).tobytes()
            filename = 'speech.pcm'
            content_type = 'application/octet-stream'
            file_format = 'pcm_s16le_16'

        data = {
            'model_id': self.eleven_model_id,
            'diarize': 'true' if self.eleven_diarize else 'false',
            'tag_audio_events': 'true' if self.eleven_tag_audio_events else 'false',
            'timestamps_granularity': 'word',
            'file_format': file_format,
        }
        language_code = self.eleven_language_code.strip()
        if not language_code and self.language and self.language != 'ro_en':
            language_code = str(self.language)
        if language_code:
            data['language_code'] = language_code
        if self.eleven_num_speakers > 0:
            data['num_speakers'] = str(max(1, min(32, self.eleven_num_speakers)))
        elif self.eleven_diarization_threshold > 0.0:
            data['diarization_threshold'] = str(
                max(0.1, min(0.4, self.eleven_diarization_threshold))
            )

        files = {
            'file': (filename, file_bytes, content_type),
        }
        response = requests.post(
            'https://api.elevenlabs.io/v1/speech-to-text',
            headers={'xi-api-key': self.eleven_api_key},
            data=data,
            files=files,
            timeout=max(1.0, self.eleven_timeout_s),
        )
        if not response.ok:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text.strip()
            raise RuntimeError(f'ElevenLabs STT HTTP {response.status_code}: {payload}')

        payload = response.json()
        text = str(payload.get('text', '') or '').strip()
        lang = str(payload.get('language_code', '') or 'en')
        confidence = float(payload.get('language_probability', 0.0) or 0.0)
        diarization = self._build_diarization_payload(payload)
        return {
            'text': text,
            'lang': lang,
            'language_probability': confidence,
            'diarization': diarization,
        }

    def _is_allowed_eleven_language(self, language_code: str) -> bool:
        if not self.eleven_allowed_language_codes:
            return True
        code = str(language_code or '').strip().lower()
        return not code or code in self.eleven_allowed_language_codes

    def _build_diarization_payload(self, payload: dict) -> dict:
        words = list(payload.get('words', []) or [])
        spans = []
        current = None
        speaker_counts = {}
        for word in words:
            if str(word.get('type', 'word') or 'word') != 'word':
                continue
            speaker = str(word.get('speaker_id', '') or 'unknown')
            token = str(word.get('text', '') or '')
            if speaker:
                speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1
            start = float(word.get('start', 0.0) or 0.0)
            end = float(word.get('end', start) or start)
            if current is None or current['speaker_id'] != speaker:
                if current:
                    current['text'] = current['text'].strip()
                    spans.append(current)
                current = {
                    'speaker_id': speaker,
                    'start': start,
                    'end': end,
                    'text': token,
                }
            else:
                current['end'] = end
                if token:
                    if current['text'] and not token.startswith("'"):
                        current['text'] += ' '
                    current['text'] += token
        if current:
            current['text'] = current['text'].strip()
            spans.append(current)

        filtered_counts = {
            speaker: count
            for speaker, count in speaker_counts.items()
            if int(count or 0) >= self.eleven_min_diarized_words
        }
        dominant = ''
        dominant_share = 0.0
        if filtered_counts:
            dominant, dominant_count = max(filtered_counts.items(), key=lambda item: item[1])
            total_words = sum(filtered_counts.values())
            dominant_share = float(dominant_count) / float(total_words or 1)
        return {
            'provider': 'elevenlabs',
            'model_id': self.eleven_model_id,
            'language_code': str(payload.get('language_code', '') or ''),
            'language_probability': float(payload.get('language_probability', 0.0) or 0.0),
            'dominant_speaker_id': dominant,
            'dominant_speaker_share': dominant_share,
            'speaker_word_counts': filtered_counts,
            'raw_speaker_word_counts': speaker_counts,
            'speaker_count': len(filtered_counts),
            'multi_speaker': len(filtered_counts) > 1,
            'spans': spans,
            'segments': spans,
        }

    def _publish_diarization(self, payload: dict):
        if not payload:
            return
        msg = String()
        msg.data = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        self.diarization_pub.publish(msg)
        dominant = payload.get('dominant_speaker_id', '') or 'unknown'
        speaker_count = int(payload.get('speaker_count', 0) or 0)
        dominant_share = float(payload.get('dominant_speaker_share', 0.0) or 0.0)
        self.get_logger().info(
            f'🗣️ ElevenLabs diarization: dominant={dominant}, '
            f'speakers={speaker_count}, share={dominant_share:.2f}'
        )

    def _transcribe_ro_en(self, audio_source):
        """
        Strict EN/RO transcription -> select the best result.
        Audio source must be seekable (BytesIO).
        """
        def safe(lang):
            try:
                return self._run_once(audio_source, lang, use_vad=True)
            except ValueError as e:
                # Retry without VAD
                if "max() iterable argument is empty" in str(e):
                    return self._run_once(audio_source, lang, use_vad=False)
                raise
        
        en_text, _, _, en_score = safe("en")
        ro_text, _, _, ro_score = safe("ro")
        
        if (ro_score > en_score) and ro_text:
            return {"text": ro_text, "lang": "ro", "language_probability": 1.0}
        else:
            return {"text": en_text, "lang": "en", "language_probability": 1.0}


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
