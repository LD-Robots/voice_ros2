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
from conversational_interfaces.msg import Audio, Transcription, TextChunk
from std_msgs.msg import Bool, String
import numpy as np
import asyncio
import concurrent.futures
import tempfile
import wave
import os
import time
import io
import re
import threading
import unicodedata
import soundfile as sf
from pathlib import Path

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

# Mistral SDK for realtime STT
try:
    from mistralai.client.sdk import Mistral
    from mistralai.client.models import AudioFormat
    MISTRAL_AVAILABLE = True
except ImportError:
    MISTRAL_AVAILABLE = False
    print("⚠️ mistralai not installed. Run: pip install mistralai[realtime]")

# Load variables from .env (optional convenience)
try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False


def _find_workspace_root():
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


class ASRNode(Node):
    def __init__(self):
        super().__init__('asr_node')

        if DOTENV_AVAILABLE:
            workspace_root = _find_workspace_root()
            env_path = workspace_root / '.env' if workspace_root else None
            if env_path and env_path.exists():
                load_dotenv(dotenv_path=env_path)
        
        # Configurable parameters
        self.declare_parameter('provider', 'faster_whisper')  # faster_whisper | mistral_realtime
        self.declare_parameter('model_size', 'small')
        self.declare_parameter('device', 'cpu')
        self.declare_parameter('compute_type', 'int8')
        self.declare_parameter('min_audio_length', 0.5)  # Minimum seconds to transcribe
        self.declare_parameter('language', '')  # Empty = auto-detect, 'ro_en' = detect best
        self.declare_parameter('beam_size', 5)
        self.declare_parameter('vad_min_silence_ms', 300)
        self.declare_parameter('warmup_enabled', True)
        self.declare_parameter('ignore_during_playback', True)
        self.declare_parameter('playback_guard_post_ms', 1800)
        self.declare_parameter('mistral_api_key_env', 'VOXTRAL_API_KEY')
        self.declare_parameter('mistral_realtime_model', 'voxtral-mini-realtime-latest')
        self.declare_parameter('mistral_batch_fallback_model', 'voxtral-mini-latest')
        self.declare_parameter('mistral_use_batch_fallback', True)
        self.declare_parameter('mistral_timeout_ms', 25000)
        self.declare_parameter('mistral_target_streaming_delay_ms', 120)
        self.declare_parameter('mistral_chunk_ms', 80)
        
        # Anti-echo textual parameters
        self.declare_parameter('echo_threshold', 85)  # Similarity % to consider as echo
        self.declare_parameter('echo_min_length', 8)  # Min chars to check for echo
        self.declare_parameter('echo_enabled', True)  # Enable/disable anti-echo
        self.declare_parameter('echo_recent_playback_window_s', 10.0)
        self.declare_parameter('echo_recent_playback_threshold', 72)
        
        self.provider = str(self.get_parameter('provider').value).strip().lower()
        model_size = self.get_parameter('model_size').value
        device = self.get_parameter('device').value
        compute_type = self.get_parameter('compute_type').value
        self.min_audio_length = self.get_parameter('min_audio_length').value
        self.language = self.get_parameter('language').value or None
        self.beam_size = self.get_parameter('beam_size').value
        self.vad_min_silence_ms = self.get_parameter('vad_min_silence_ms').value
        self.warmup_enabled = self.get_parameter('warmup_enabled').value
        self.ignore_during_playback = bool(self.get_parameter('ignore_during_playback').value)
        self.playback_guard_post_ms = max(0, int(self.get_parameter('playback_guard_post_ms').value))
        self.mistral_api_key_env = str(self.get_parameter('mistral_api_key_env').value).strip() or 'VOXTRAL_API_KEY'
        self.mistral_realtime_model = str(self.get_parameter('mistral_realtime_model').value).strip() or 'voxtral-mini-realtime-latest'
        self.mistral_batch_fallback_model = str(
            self.get_parameter('mistral_batch_fallback_model').value
        ).strip() or 'voxtral-mini-latest'
        self.mistral_use_batch_fallback = bool(
            self.get_parameter('mistral_use_batch_fallback').value
        )
        self.mistral_timeout_ms = max(
            1000, int(self.get_parameter('mistral_timeout_ms').value)
        )
        self.mistral_target_streaming_delay_ms = max(
            0, int(self.get_parameter('mistral_target_streaming_delay_ms').value)
        )
        self.mistral_chunk_ms = max(20, int(self.get_parameter('mistral_chunk_ms').value))
        
        # Anti-echo settings
        self.echo_threshold = self.get_parameter('echo_threshold').value
        self.echo_min_length = self.get_parameter('echo_min_length').value
        self.echo_enabled = self.get_parameter('echo_enabled').value and RAPIDFUZZ_AVAILABLE
        self.echo_recent_playback_window_s = max(
            0.0, float(self.get_parameter('echo_recent_playback_window_s').value)
        )
        self.echo_recent_playback_threshold = int(
            self.get_parameter('echo_recent_playback_threshold').value
        )

        self.model = None
        self.mistral_client = None
        self._async_loop = None
        self._async_loop_thread = None

        if self.provider == 'faster_whisper':
            if not WHISPER_AVAILABLE:
                self.get_logger().error('faster-whisper not installed!')
                raise RuntimeError('faster-whisper not available')

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
            self.get_logger().info(f'✅ ASR provider initialized: faster_whisper ({model_size})')
        elif self.provider in ('mistral_realtime', 'mistral'):
            if not MISTRAL_AVAILABLE:
                self.get_logger().error('mistralai[realtime] not installed!')
                raise RuntimeError('mistralai[realtime] not available')
            api_key = os.environ.get(self.mistral_api_key_env, '').strip()
            if not api_key:
                self.get_logger().error(f'{self.mistral_api_key_env} environment variable not set!')
                raise RuntimeError(f'{self.mistral_api_key_env} not set')
            self.mistral_client = Mistral(api_key=api_key, timeout_ms=self.mistral_timeout_ms)
            self._start_async_runtime()
            self._warmed_up = True
            self.get_logger().info(
                '✅ ASR provider initialized: mistral_realtime '
                f'(realtime_model={self.mistral_realtime_model}, '
                f'batch_fallback={self.mistral_batch_fallback_model}, '
                f'use_batch_fallback={self.mistral_use_batch_fallback})'
            )
        else:
            raise RuntimeError(
                f"Unsupported ASR provider '{self.provider}'. "
                "Use 'faster_whisper' or 'mistral_realtime'."
            )
        
        # Buffer for audio
        self.audio_buffer = []
        self.sample_rate = 16000
        self.channels = 1
        self.is_speaking = False
        self.was_speaking = False
        self.robot_speaking = False
        self.last_robot_speaking_end_at = 0.0
        
        # Anti-echo: last robot response
        self.last_bot_reply = ""
        self._assistant_stream_session = ''
        self._assistant_stream_text = ''
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
        self.playback_sub = self.create_subscription(
            Bool,
            '/is_speaking',
            self.playback_callback,
            10,
        )
        self.backend_sub = self.create_subscription(
            String,
            '/conversation_backend',
            self.backend_callback,
            10
        )
        
        # LLM response subscriber (anti-echo)
        self.llm_stream_sub = self.create_subscription(
            TextChunk,
            '/llm_stream',
            self.llm_stream_callback,
            10,
        )
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
        
        if self.echo_enabled:
            self.get_logger().debug(f'🔇 Anti-echo ENABLED (threshold={self.echo_threshold}%, min_len={self.echo_min_length})')
        else:
            self.get_logger().debug('🔇 Anti-echo DISABLED')
        
        self.get_logger().debug('ASR Node started! Listening on /audio_raw, /voice_activity, /llm_response')
    
    def audio_callback(self, msg: Audio):
        """Buffers audio during speech."""
        if self.current_backend not in ('legacy', 'mistral_realtime'):
            return
        if self._is_playback_guard_active():
            # Drop any captured leak while robot is speaking (or shortly after).
            if self.audio_buffer:
                self.audio_buffer = []
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
        if self.current_backend not in ('legacy', 'mistral_realtime'):
            self.was_speaking = False
            self.is_speaking = False
            self.audio_buffer = []
            return
        self.was_speaking = self.is_speaking
        self.is_speaking = msg.data

        # Ignore end-of-speech events during playback guard to prevent
        # delayed transcriptions from robot voice leakage.
        if self._is_playback_guard_active():
            if self.was_speaking and not self.is_speaking and self.audio_buffer:
                self.get_logger().debug('🔇 Ignored VAD segment during playback guard')
            self.audio_buffer = []
            return
        
        # When the user finishes speaking, transcribe
        if self.was_speaking and not self.is_speaking:
            self.get_logger().debug(f'🔚 Speech ended, processing {len(self.audio_buffer)} frames...')
            self._process_buffer()

    def playback_callback(self, msg: Bool):
        was_speaking = self.robot_speaking
        self.robot_speaking = bool(msg.data)
        if self.robot_speaking:
            self.audio_buffer = []
        elif was_speaking and not self.robot_speaking:
            self.last_robot_speaking_end_at = time.monotonic()

    def llm_response_callback(self, msg: Transcription):
        """Store the last robot response for anti-echo."""
        if msg.text:
            self.last_bot_reply = msg.text
            self.get_logger().debug(f'📝 Stored bot reply for anti-echo: {msg.text[:50]}...')

    def llm_stream_callback(self, msg: TextChunk):
        """Continuously track assistant stream text for real-time anti-echo."""
        session_id = str(msg.session_id or '')
        if session_id and session_id != self._assistant_stream_session:
            self._assistant_stream_session = session_id
            self._assistant_stream_text = ''

        chunk = str(msg.text or '').strip()
        if chunk:
            if self._assistant_stream_text:
                self._assistant_stream_text += ' '
            self._assistant_stream_text += chunk
            # Keep only recent suffix to bound memory and comparison cost.
            if len(self._assistant_stream_text) > 2000:
                self._assistant_stream_text = self._assistant_stream_text[-2000:]
            self.last_bot_reply = self._assistant_stream_text

        if msg.is_final and self._assistant_stream_text:
            self.last_bot_reply = self._assistant_stream_text

    def backend_callback(self, msg: String):
        backend = msg.data.strip() or 'legacy'
        if backend != self.current_backend:
            self.current_backend = backend
            self.audio_buffer = []
            self.is_speaking = False
            self.was_speaking = False
            self.robot_speaking = False
            self.last_robot_speaking_end_at = 0.0

    def _is_playback_guard_active(self) -> bool:
        if not self.ignore_during_playback:
            return False
        if self.robot_speaking:
            return True
        if self.last_robot_speaking_end_at <= 0.0:
            return False
        elapsed_ms = (time.monotonic() - self.last_robot_speaking_end_at) * 1000.0
        return elapsed_ms <= float(self.playback_guard_post_ms)

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

        # Direct fragment containment often indicates leaked playback.
        if user_norm in bot_norm:
            self.get_logger().debug('🔇 Ignoring input (assistant fragment detected)')
            return True

        if self._is_recent_playback_window_active() and bot_norm in user_norm:
            self.get_logger().debug('🔇 Ignoring input (recent playback overlap detected)')
            return True

        dynamic_threshold = int(self.echo_threshold)
        if self._is_recent_playback_window_active():
            dynamic_threshold = min(
                dynamic_threshold, int(self.echo_recent_playback_threshold)
            )

        if self._is_recent_playback_window_active():
            user_words = set(user_norm.split())
            bot_words = set(bot_norm.split())
            if len(user_words) >= 3:
                overlap_ratio = len(user_words.intersection(bot_words)) / max(1, len(user_words))
                if overlap_ratio >= 0.8:
                    self.get_logger().debug(
                        f'🔇 Ignoring input (word-overlap echo, ratio={overlap_ratio:.2f})'
                    )
                    return True
        
        # Calculate similarity
        similarity = fuzz.partial_ratio(user_norm, bot_norm)
        
        if similarity >= dynamic_threshold:
            self.get_logger().debug(
                f'🔇 Ignoring input (echo TTS) sim={similarity}% >= {dynamic_threshold}%'
            )
            return True
        
        return False

    def _is_recent_playback_window_active(self) -> bool:
        if self.robot_speaking:
            return True
        if self.last_robot_speaking_end_at <= 0.0:
            return False
        elapsed_s = time.monotonic() - self.last_robot_speaking_end_at
        return elapsed_s <= float(self.echo_recent_playback_window_s)

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

        try:
            if self.provider in ('mistral_realtime', 'mistral'):
                text, lang, confidence = self._transcribe_with_mistral(audio_data)
            else:
                # Save in memory (BytesIO) as WAV
                wav_io = io.BytesIO()
                with wave.open(wav_io, 'wb') as wav:
                    wav.setnchannels(self.channels)
                    wav.setsampwidth(2)  # 16-bit = 2 bytes
                    wav.setframerate(self.sample_rate)
                    wav.writeframes(audio_data.tobytes())
                
                # Reset cursor to the beginning of the buffer
                wav_io.seek(0)
                
                # Use RO/EN detection if set
                if self.language == 'ro_en':
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

    def _run_async(self, coro):
        if self._async_loop and self._async_loop.is_running():
            timeout_s = max(2.0, (self.mistral_timeout_ms / 1000.0) + 3.0)
            future = asyncio.run_coroutine_threadsafe(coro, self._async_loop)
            try:
                return future.result(timeout=timeout_s)
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                raise RuntimeError(
                    f'Mistral realtime coroutine timed out after {timeout_s:.1f}s'
                ) from exc
        return asyncio.run(coro)

    def _start_async_runtime(self):
        if self._async_loop and self._async_loop.is_running():
            return

        self._async_loop = asyncio.new_event_loop()

        def _runner():
            asyncio.set_event_loop(self._async_loop)
            self._async_loop.run_forever()

        self._async_loop_thread = threading.Thread(
            target=_runner,
            daemon=True,
            name='ASR-MistralLoop',
        )
        self._async_loop_thread.start()

    def _stop_async_runtime(self):
        loop = self._async_loop
        if loop is None:
            return

        if loop.is_running():
            try:
                if self.mistral_client is not None:
                    close_future = asyncio.run_coroutine_threadsafe(
                        self.mistral_client.__aexit__(None, None, None),
                        loop,
                    )
                    close_future.result(timeout=2.0)
            except Exception:
                pass

            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass

            if self._async_loop_thread and self._async_loop_thread.is_alive():
                self._async_loop_thread.join(timeout=2.0)

        if not loop.is_closed():
            try:
                loop.close()
            except Exception:
                pass

        self._async_loop = None
        self._async_loop_thread = None

    def _default_language(self) -> str:
        lang = (self.language or '').strip().lower()
        if lang in ('ro', 'en'):
            return lang
        return 'en'

    def _transcribe_with_mistral(self, audio_data: np.ndarray):
        """Transcribe with Mistral realtime STT and optional batch fallback."""
        try:
            return self._run_async(self._transcribe_with_mistral_realtime_async(audio_data))
        except Exception as realtime_error:
            if not self.mistral_use_batch_fallback:
                raise RuntimeError(f'Mistral realtime failed: {realtime_error}') from realtime_error
            self.get_logger().warn(
                f'Mistral realtime STT failed ({realtime_error}). Falling back to batch transcription...'
            )
            return self._transcribe_with_mistral_batch(audio_data)

    async def _transcribe_with_mistral_realtime_async(self, audio_data: np.ndarray):
        if self.mistral_client is None:
            raise RuntimeError('Mistral client not initialized')

        pcm_bytes = audio_data.astype(np.int16, copy=False).tobytes()
        bytes_per_second = max(1, int(self.sample_rate * max(1, self.channels) * 2))
        chunk_size = max(640, int(bytes_per_second * (self.mistral_chunk_ms / 1000.0)))

        async def audio_stream():
            for offset in range(0, len(pcm_bytes), chunk_size):
                yield pcm_bytes[offset:offset + chunk_size]
                await asyncio.sleep(0)

        audio_format = AudioFormat(encoding='pcm_s16le', sample_rate=int(self.sample_rate))
        target_delay = self.mistral_target_streaming_delay_ms or None
        text_deltas = []
        final_text = ''
        detected_lang = ''

        async for event in self.mistral_client.audio.realtime.transcribe_stream(
            audio_stream(),
            model=self.mistral_realtime_model,
            audio_format=audio_format,
            target_streaming_delay_ms=target_delay,
            timeout_ms=self.mistral_timeout_ms,
        ):
            event_type = getattr(event, 'type', None)
            if event_type == 'transcription.text.delta':
                token = str(getattr(event, 'text', '') or '')
                if token:
                    text_deltas.append(token)
            elif event_type == 'transcription.done':
                final_text = str(getattr(event, 'text', '') or '').strip()
                detected_lang = str(getattr(event, 'language', '') or '').strip().lower()
                break
            elif event_type == 'error':
                err = getattr(event, 'error', None)
                err_msg = str(getattr(err, 'message', '') or '') if err is not None else ''
                raise RuntimeError(err_msg or 'Unknown realtime transcription error')

        text = final_text or ''.join(text_deltas).strip()
        if not text:
            raise RuntimeError('Mistral realtime returned empty transcription')

        if not detected_lang:
            detected_lang = self._default_language()
        return text, detected_lang, 1.0

    def _transcribe_with_mistral_batch(self, audio_data: np.ndarray):
        if self.mistral_client is None:
            raise RuntimeError('Mistral client not initialized')

        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(audio_data.astype(np.int16, copy=False).tobytes())
        wav_io.seek(0)

        kwargs = {
            'model': self.mistral_batch_fallback_model,
            'file': {
                'file_name': 'segment.wav',
                'content': wav_io,
                'content_type': 'audio/wav',
            },
            'temperature': 0.0,
            'timeout_ms': self.mistral_timeout_ms,
        }

        language_hint = (self.language or '').strip().lower()
        if language_hint in ('ro', 'en'):
            kwargs['language'] = language_hint

        resp = self.mistral_client.audio.transcriptions.complete(**kwargs)
        text = str(getattr(resp, 'text', '') or '').strip()
        lang = str(getattr(resp, 'language', '') or '').strip().lower() or self._default_language()
        if not text:
            raise RuntimeError('Mistral batch transcription returned empty text')
        return text, lang, 1.0

    def _ensure_warm(self):
        """Fully load the model via a dummy transcription."""
        if self.provider != 'faster_whisper':
            self._warmed_up = True
            return
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

    def destroy_node(self):
        self._stop_async_runtime()
        super().destroy_node()


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
