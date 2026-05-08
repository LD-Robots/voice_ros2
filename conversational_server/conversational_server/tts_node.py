#!/usr/bin/env python3
"""
TTS Node - Text to Speech with MULTIPLE BACKENDS, STREAMING and DOUBLE BUFFER.

BACKENDS:
  - edge-tts (default) - Microsoft Edge TTS, requires internet

DOUBLE BUFFER: Synthesize next chunk in parallel with current playback.

Subscribes to: 
  - /llm_stream (TextChunk) - streaming chunks (PREFERRED)
  - /llm_response (Transcription) - complete response (fallback)
  - /tts_command (String) - commands for cache playback
Publishes to: /audio_out (Audio)
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription, TextChunk, Audio
from std_msgs.msg import Bool, String
import asyncio
import base64
import io
import json
import os
import numpy as np
import threading
import queue
import time
import scipy.signal  # For resampling
import re
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

def _find_workspace_root():
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None

try:
    from num2words import num2words
    NUM2WORDS_AVAILABLE = True
except ImportError:
    NUM2WORDS_AVAILABLE = False
    print("⚠️ num2words not installed. Number verbalization disabled.")

# Edge TTS for voice synthesis (online)
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    print("⚠️ edge-tts not installed. Run: pip install edge-tts")

# Soundfile for audio reading
try:
    import soundfile as sf
    SOUNDFILE_AVAILABLE = True
except ImportError:
    SOUNDFILE_AVAILABLE = False
    print("⚠️ soundfile not installed. Run: pip install soundfile")

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False


class TTSNode(Node):
    def __init__(self):
        super().__init__('tts_node')

        if DOTENV_AVAILABLE:
            workspace_root = _find_workspace_root()
            env_path = workspace_root / '.env' if workspace_root else None
            if env_path and env_path.exists():
                load_dotenv(dotenv_path=env_path)
        
        # Configurable parameters
        self.declare_parameter('provider', 'edge')
        self.declare_parameter('provider_fallback_edge', True)
        self.declare_parameter('voice_en', 'en-IE-EmilyNeural')
        self.declare_parameter('voice_ro', 'ro-RO-AlinaNeural')
        self.declare_parameter('rate', '+0%')
        self.declare_parameter('pitch', '+0Hz')
        self.declare_parameter('buffer_size', 2)  # Double buffer (2 chunks ahead)
        self.declare_parameter('voxtral_api_url', 'https://api.mistral.ai/v1/audio/speech')
        self.declare_parameter('voxtral_api_key_env', 'VOXTRAL_API_KEY')
        self.declare_parameter('voxtral_model', 'voxtral-mini-tts-2603')
        self.declare_parameter('voxtral_voice_id_en', '')
        self.declare_parameter('voxtral_voice_id_ro', '')
        self.declare_parameter('voxtral_ref_audio_en_path', '')
        self.declare_parameter('voxtral_ref_audio_ro_path', '')
        self.declare_parameter('voxtral_use_ref_audio_if_no_voice', True)
        self.declare_parameter('voxtral_response_format', 'wav')
        self.declare_parameter('voxtral_timeout_s', 20.0)
        
        self.provider = str(self.get_parameter('provider').value).strip().lower()
        if self.provider not in {'edge', 'voxtral'}:
            self.get_logger().warning(
                f"Unknown TTS provider '{self.provider}', falling back to 'edge'"
            )
            self.provider = 'edge'
        self.provider_fallback_edge = bool(
            self.get_parameter('provider_fallback_edge').value
        )
        self.voice_en = self.get_parameter('voice_en').value
        self.voice_ro = self.get_parameter('voice_ro').value
        self.rate = self.get_parameter('rate').value
        self.pitch = self.get_parameter('pitch').value
        self.buffer_size = self.get_parameter('buffer_size').value
        self.voxtral_api_url = str(self.get_parameter('voxtral_api_url').value).strip()
        self.voxtral_api_key_env = str(
            self.get_parameter('voxtral_api_key_env').value
        ).strip() or 'VOXTRAL_API_KEY'
        self.voxtral_model = str(self.get_parameter('voxtral_model').value).strip()
        self.voxtral_voice_id_en = str(
            self.get_parameter('voxtral_voice_id_en').value
        ).strip()
        self.voxtral_voice_id_ro = str(
            self.get_parameter('voxtral_voice_id_ro').value
        ).strip()
        self.voxtral_ref_audio_en_path = str(
            self.get_parameter('voxtral_ref_audio_en_path').value
        ).strip()
        self.voxtral_ref_audio_ro_path = str(
            self.get_parameter('voxtral_ref_audio_ro_path').value
        ).strip()
        self.voxtral_use_ref_audio_if_no_voice = bool(
            self.get_parameter('voxtral_use_ref_audio_if_no_voice').value
        )
        self.voxtral_response_format = str(
            self.get_parameter('voxtral_response_format').value
        ).strip().lower()
        self.voxtral_timeout_s = max(
            1.0,
            float(self.get_parameter('voxtral_timeout_s').value),
        )
        if self.voxtral_response_format not in {'pcm', 'wav', 'mp3', 'flac', 'opus'}:
            self.get_logger().warning(
                f"Unsupported Voxtral response format '{self.voxtral_response_format}', using 'wav'"
            )
            self.voxtral_response_format = 'wav'
        self.voxtral_api_key = os.environ.get(self.voxtral_api_key_env) or ''
        
        # Decoding requires soundfile (Edge MP3 and Voxtral WAV/MP3/FLAC/OPUS)
        if not SOUNDFILE_AVAILABLE:
            self.get_logger().error('soundfile not installed - required for edge-tts!')
            raise RuntimeError('soundfile not available')

        if self.provider == 'edge' and not EDGE_TTS_AVAILABLE:
            self.get_logger().error('edge-tts not installed!')
            raise RuntimeError('edge-tts not available')

        if self.provider == 'voxtral' and not self.voxtral_api_key:
            if self.provider_fallback_edge and EDGE_TTS_AVAILABLE:
                self.get_logger().warning(
                    f'{self.voxtral_api_key_env} is missing; switching provider to edge-tts fallback'
                )
                self.provider = 'edge'
            else:
                self.get_logger().error(
                    f'{self.voxtral_api_key_env} is missing and edge fallback is unavailable'
                )
                raise RuntimeError('voxtral api key not available')

        if self.provider == 'voxtral':
            self.get_logger().info(
                '✅ TTS initialized with provider=voxtral '
                f'(model={self.voxtral_model}, format={self.voxtral_response_format})'
            )
            if self.voxtral_use_ref_audio_if_no_voice:
                self.get_logger().info('ℹ️ Voxtral will use ref_audio fallback when voice_id is missing')
        else:
            self.get_logger().info(
                f'✅ TTS initialized with provider=edge: EN={self.voice_en}, RO={self.voice_ro}'
            )
        
        # Target sample rate (fix "horror voice" issues by standardizing on 16kHz)
        self.target_sample_rate = 16000
        
        # === WAV CACHE - pre-generated common phrases ===
        self.cache_dir = '/tmp/tts_cache'
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Common phrases for cache
        self.cache_phrases = {
            'ack_en': ('Hello. I am here and listening.', 'en'),
            'ack_ro': ('Salut. Sunt aici și te ascult.', 'ro'),
            'filler_en': ('One moment please...', 'en'),
            'filler_ro': ('Un moment, te rog...', 'ro'),
            'goodbye_en': ('Goodbye. I will be here when you need me again.', 'en'),
            'goodbye_ro': ('La revedere. Sunt aici când ai nevoie de mine din nou.', 'ro'),
            'error_en': ('Sorry, I encountered an error.', 'en'),
            'error_ro': ('Îmi pare rău, am întâmpinat o eroare.', 'ro'),
            'confirm_en': ('Are you sure? Please say yes or no.', 'en'),
            'confirm_ro': ('Ești sigur? Te rog spune da sau nu.', 'ro'),
        }
        self.system_commands = {
            'ack_en',
            'ack_ro',
            'goodbye_ro',
            'error_ro',
            'confirm_ro',
            'filler_ro',
            'goodbye_en',
            'error_en',
            'confirm_en',
            'filler_en',
        }
        self.audio_cache = {}  # key -> (audio_data, sample_rate)
        
        # Pre-generate the cache in the background
        self.cache_thread = threading.Thread(target=self._precache, daemon=True, name="TTS-Cache")
        self.cache_thread.start()
        
        # === DOUBLE BUFFER QUEUES ===
        # Queue for incoming text chunks
        self.text_queue = queue.Queue()
        # Queue for pre-synthesized audio (double buffer) - max 2 pre-synthesized chunks
        self.audio_queue = queue.Queue(maxsize=self.buffer_size)
        
        self.is_speaking = False
        self.current_session = None
        self.stop_requested = False
        self.stop_epoch = 0  # Epoch counter - increments on stop(), chunks with old epoch are skipped
        self.current_backend = 'legacy'
        self._voxtral_fallback_warned = False
        
        # Subscriber for cache commands (ack, goodbye, etc)
        from std_msgs.msg import String
        self.command_sub = self.create_subscription(
            String,
            '/tts_command',
            self.command_callback,
            10
        )
        self.backend_sub = self.create_subscription(
            String,
            '/conversation_backend',
            self.backend_callback,
            10
        )
        
        # Subscriber for STREAMING chunks (PREFERRED)
        self.stream_sub = self.create_subscription(
            TextChunk,
            '/llm_stream',
            self.stream_callback,
            10
        )
        
        # Subscriber for complete response (FALLBACK)
        self.response_sub = self.create_subscription(
            Transcription,
            '/llm_response',
            self.response_callback,
            10
        )
        
        # Publisher for synthesized audio
        self.audio_pub = self.create_publisher(
            Audio,
            '/audio_out',
            10
        )
        
        # Publisher for speaking status
        from std_msgs.msg import Bool
        self.speaking_pub = self.create_publisher(
            Bool,
            '/tts_speaking',  # Renamed for clarity
            10
        )
        
        # Subscriber for stop TTS
        self.stop_sub = self.create_subscription(
            Bool,
            '/tts_stop',
            self.stop_callback,
            10
        )
        
        # Timer to periodically publish the is_speaking state
        self.speaking_timer = self.create_timer(0.2, self._publish_speaking_status)
        
        # === DOUBLE BUFFER THREADS ===
        self.running = True
        
        # PRODUCER Thread: read text chunks, synthesize audio, put in audio_queue
        self.producer_thread = threading.Thread(target=self._producer_loop, daemon=True, name="TTS-Producer")
        self.producer_thread.start()
        
        # CONSUMER thread: reads from audio_queue, publishes to /audio_out
        self.consumer_thread = threading.Thread(target=self._consumer_loop, daemon=True, name="TTS-Consumer")
        self.consumer_thread.start()
        
        self.get_logger().debug('TTS Node started with DOUBLE BUFFER + CACHE! Listening on /llm_stream')
    
    def _publish_speaking_status(self):
        """Periodically publish the is_speaking state to /tts_speaking."""
        from std_msgs.msg import Bool
        msg = Bool()
        msg.data = self.is_speaking
        self.speaking_pub.publish(msg)
    
    def stop_callback(self, msg: Bool):
        """Stop TTS when True is received on /tts_stop."""
        if msg.data:
            self.stop()
    
    def _precache(self):
        """Pre-generate audio for common phrases or load static files."""
        self.get_logger().info('🔄 Initializing TTS cache (prioritizing OpenAI static voices)...')
        
        workspace_root = _find_workspace_root()
        static_dir = None
        if workspace_root:
            static_dir = workspace_root / 'conversational_server' / 'resources' / 'static_audio'
            if not static_dir.exists():
                static_dir = None

        for key, (text, lang) in self.cache_phrases.items():
            try:
                # 1. Check if a static version exists (OpenAI Cedar)
                static_file = None
                if static_dir:
                    static_file = static_dir / f"{key}.wav"
                
                if static_file and static_file.exists():
                    # Load directly from WAV
                    audio_data, sample_rate = sf.read(str(static_file), dtype='int16')
                    self.get_logger().info(f'  ✓ Loaded static voice: {key} (OpenAI)')
                else:
                    # 2. Fall back to dynamic synthesis
                    audio_data, sample_rate = self._synthesize(text, lang)
                
                # Resample immediately for cache
                if sample_rate != self.target_sample_rate:
                     audio_data = self._resample(audio_data, sample_rate, self.target_sample_rate)
                     sample_rate = self.target_sample_rate
                
                if len(audio_data.shape) > 1:
                    audio_data = audio_data[:, 0]
                self.audio_cache[key] = (audio_data, sample_rate)
                if not (static_file and static_file.exists()):
                    self.get_logger().debug(f'  ✓ Cached (dynamic): {key}')
            except Exception as e:
                self.get_logger().warn(f'  ✗ Failed to cache {key}: {e}')
        self.get_logger().info(f'✅ TTS cache complete: {len(self.audio_cache)} phrases ready')
    
    def say_cached(self, key: str) -> bool:
        """Play a cached phrase. Returns True if successful."""
        if key not in self.audio_cache:
            self.get_logger().warn(f'Cache miss: {key}')
            return False
        
        audio_data, sample_rate = self.audio_cache[key]
        
        # Publish audio
        out = Audio()
        out.data = audio_data.tolist()
        out.sample_rate = sample_rate
        out.channels = 1
        self.audio_pub.publish(out)
        
        self.get_logger().debug(f'🎵 Playing cached: {key}')
        return True
    
    def command_callback(self, msg):
        """Process TTS commands (play cached phrases)."""
        command = msg.data.strip()
        if self.current_backend not in ('legacy', 'mistral_realtime') and command not in self.system_commands:
            return
        
        # If it's a cache key, play it
        if command in self.audio_cache or command in self.cache_phrases:
            self.say_cached(command)
        else:
            self.get_logger().warn(f'Unknown TTS command: {command}')
    
    def _pick_voice(self, lang: str) -> str:
        """Choose the voice - Romanian or English (default for anything else)."""
        lang = lang.lower() if lang else 'en'
        if lang.startswith('ro'):
            return self.voice_ro
        # Any other language -> English
        return self.voice_en

    def _pick_voxtral_voice_id(self, lang: str) -> str:
        """Choose the configured Voxtral voice_id by language (optional)."""
        lang = lang.lower() if lang else 'en'
        if lang.startswith('ro'):
            return self.voxtral_voice_id_ro
        return self.voxtral_voice_id_en

    @staticmethod
    def _file_to_base64(path: Path) -> str:
        data = path.read_bytes()
        if not data:
            return ''
        return base64.b64encode(data).decode('ascii')

    def _resolve_workspace_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            return candidate
        workspace_root = _find_workspace_root()
        if workspace_root:
            return (workspace_root / candidate).resolve()
        return candidate.resolve()

    def _pick_voxtral_ref_audio_b64(self, lang: str) -> str:
        """Load base64 ref_audio from configured path (optional)."""
        if not self.voxtral_use_ref_audio_if_no_voice:
            return ''
        lang = lang.lower() if lang else 'en'
        raw_path = self.voxtral_ref_audio_ro_path if lang.startswith('ro') else self.voxtral_ref_audio_en_path
        if not raw_path:
            return ''
        path = self._resolve_workspace_path(raw_path)
        if not path.exists():
            self.get_logger().warning(f'Voxtral ref_audio file not found: {path}')
            return ''
        try:
            return self._file_to_base64(path)
        except Exception as exc:
            self.get_logger().warning(f'Failed to read Voxtral ref_audio {path}: {exc}')
            return ''
    
    def _preprocess_numbers(self, text: str, lang: str) -> str:
        """Convert numbers to words and pronounce math symbols."""
        if not NUM2WORDS_AVAILABLE:
            return text

        base_lang = lang.lower()[:2] if lang else 'en'
        if base_lang not in ['en', 'ro']:
            base_lang = 'en'

        # 1. Replace common math symbols (with surrounding spaces)
        math_symbols = {
            'en': {' + ': ' plus ', ' - ': ' minus ', ' = ': ' equals ', ' * ': ' times ', ' / ': ' divided by '},
            'ro': {' + ': ' plus ', ' - ': ' minus ', ' = ': ' egal ', ' * ': ' înmulțit cu ', ' / ': ' împărțit la '}
        }
        
        for symbol, word in math_symbols[base_lang].items():
            text = text.replace(symbol, word)
            
        # Also handle minus sign directly attached to a number (e.g. -50 -> minus 50)
        text = re.sub(r'(?<!\w)-(?=\d)', 'minus ', text)

        # 2. Convert numbers to words
        def replace_match(match):
            s = match.group(0)
            try:
                if ',' in s and '.' not in s:
                    if len(s.split(',')[-1]) != 3:
                        s = s.replace(',', '.')
                    else:
                        s = s.replace(',', '')
                elif '.' in s and ',' not in s and len(s.split('.')[-1]) == 3 and base_lang == 'ro':
                    s = s.replace('.', '')
                else:
                    s = s.replace(',', '')

                if '.' in s:
                    return num2words(float(s), lang=base_lang)
                else:
                    return num2words(int(s), lang=base_lang)
            except Exception as e:
                self.get_logger().warn(f"Failed to convert number {match.group(0)}: {e}")
                return match.group(0)

        return re.sub(r'\b\d+(?:[.,]\d+)*\b', replace_match, text)
    
    def stream_callback(self, msg: TextChunk):
        """Process streaming text chunks."""
        if self.current_backend not in ('legacy', 'mistral_realtime'):
            return
        # New session - reset
        if self.current_session and msg.session_id != self.current_session:
            if not msg.is_final:
                self.current_session = msg.session_id
                self.stop_requested = True  # Stop what is currently playing
                # Clear the queues
                self._clear_queues()
                self.stop_requested = False
        
        if not self.current_session:
            self.current_session = msg.session_id
        
        if msg.text.strip():
            self.get_logger().debug(f'📥 Stream chunk: "{msg.text[:40]}..." (final={msg.is_final})')
            self.text_queue.put((msg.text, msg.language, msg.is_final, msg.session_id))
        elif msg.is_final:
            # Empty message with is_final - signals the end
            self.text_queue.put(("", "", True, msg.session_id))
    
    def response_callback(self, msg: Transcription):
        """Fallback for complete responses (non-streaming)."""
        pass  # Dezactivat - folosim doar streaming

    def backend_callback(self, msg: String):
        backend = msg.data.strip() or 'legacy'
        if backend == self.current_backend:
            return
        self.current_backend = backend
        if backend not in ('legacy', 'mistral_realtime'):
            self.stop()
    
    def _clear_queues(self):
        """Clear all queues."""
        while not self.text_queue.empty():
            try:
                self.text_queue.get_nowait()
            except queue.Empty:
                break
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
    
    def _producer_loop(self):
        """
        PRODUCER: Read text from text_queue, synthesize, put in audio_queue.
        Run in parallel - always try to have 1-2 pre-synthesized chunks.
        """
        while self.running:
            try:
                text, lang, is_final, session_id = self.text_queue.get(timeout=0.1)
                
                if self.stop_requested or self.current_backend not in ('legacy', 'mistral_realtime'):
                    continue
                
                if text:
                    # Convert numbers to words before synthesis
                    original_text = text
                    text = self._preprocess_numbers(text, lang)
                    if text != original_text:
                        self.get_logger().info(f'🔢 Numbers replaced: "{original_text}" -> "{text}"')
                    # ----------------------------------------------------
                    
                    self.get_logger().debug(f'🔧 Pre-synthesizing: "{text[:30]}..."')
                    
                    try:
                        audio_data, sample_rate = self._synthesize(text, lang)
                        
                        # Resample to target rate (16kHz)
                        if sample_rate != self.target_sample_rate:
                            audio_data = self._resample(audio_data, sample_rate, self.target_sample_rate)
                            sample_rate = self.target_sample_rate
                        
                        # Ensure mono audio
                        if len(audio_data.shape) > 1:
                            audio_data = audio_data[:, 0]
                        
                        # Put in audio_queue WITH EPOCH (will block if full = double buffer full)
                        current_epoch = self.stop_epoch
                        if not self.stop_requested:
                            self.audio_queue.put((audio_data, sample_rate, is_final, session_id, current_epoch), timeout=5.0)
                            self.get_logger().debug(f'📦 Buffered audio ({len(audio_data)} samples, epoch={current_epoch})')
                    
                    except Exception as e:
                        self.get_logger().error(f'Synthesis error: {e}')
                
                elif is_final:
                    # Signal the end in audio_queue (with epoch)
                    try:
                        self.audio_queue.put((None, 0, True, session_id, self.stop_epoch), timeout=1.0)
                    except queue.Full:
                        pass
                    
            except queue.Empty:
                continue
            except Exception as e:
                self.get_logger().error(f'Producer error: {e}')
    
    def _consumer_loop(self):
        """
        CONSUMER: Citește audio din audio_queue, publică pe /audio_out.
        """
        from std_msgs.msg import Bool
        
        while self.running:
            try:
                audio_data, sample_rate, is_final, session_id, chunk_epoch = self.audio_queue.get(timeout=0.1)
                
                # EPOCH CHECK: Skip chunks from before stop() was called
                if chunk_epoch != self.stop_epoch:
                    self.get_logger().debug(f'🚫 Skipping old chunk (epoch {chunk_epoch} != current {self.stop_epoch})')
                    continue
                
                if self.stop_requested:
                    continue
                if self.current_backend not in ('legacy', 'mistral_realtime'):
                    continue
                
                if audio_data is not None:
                    # Publish audio - audio_playback_node manages is_speaking state
                    out = Audio()
                    out.data = audio_data.tolist()
                    out.sample_rate = sample_rate
                    out.channels = 1
                    self.audio_pub.publish(out)
                    
                    self.get_logger().debug(f'📤 Published {len(audio_data)} samples at {sample_rate}Hz')
                    # No sleep needed - audio_playback_node manages the is_speaking state
                
                if is_final:
                    self.current_session = None
                    self.get_logger().debug('✅ Stream complete')
                    
            except queue.Empty:
                continue
            except Exception as e:
                self.get_logger().error(f'Consumer error: {e}')
    
    async def _synthesize_async(self, text: str, voice: str) -> bytes:
        """Synthesize text to audio using Edge TTS."""
        communicate = edge_tts.Communicate(
            text,
            voice,
            rate=self.rate,
            pitch=self.pitch
        )
        
        audio_data = b''
        async for chunk in communicate.stream():
            if chunk['type'] == 'audio':
                audio_data += chunk['data']
        
        return audio_data
    
    def _synthesize(self, text: str, lang: str):
        """Synthesize text to audio using the configured provider."""
        if self.provider == 'voxtral':
            try:
                audio_data, sample_rate = self._synthesize_voxtral(text, lang)
                self._voxtral_fallback_warned = False
                return audio_data, sample_rate
            except Exception as exc:
                if self.provider_fallback_edge and EDGE_TTS_AVAILABLE:
                    if not self._voxtral_fallback_warned:
                        self.get_logger().warning(
                            f'Voxtral synthesis failed ({exc}). Falling back to edge-tts.'
                        )
                        self._voxtral_fallback_warned = True
                else:
                    raise
        return self._synthesize_edge(text, self._pick_voice(lang))

    def _synthesize_voxtral(self, text: str, lang: str):
        """Synthesize text with Mistral Voxtral API."""
        if not self.voxtral_api_key:
            raise RuntimeError(f'{self.voxtral_api_key_env} is not set')

        payload = {
            'model': self.voxtral_model,
            'input': text,
            'response_format': self.voxtral_response_format,
            'stream': False,
        }
        voice_id = self._pick_voxtral_voice_id(lang)
        if voice_id:
            payload['voice_id'] = voice_id
            # Some API versions refer to this field as "voice".
            payload['voice'] = voice_id
        else:
            ref_audio = self._pick_voxtral_ref_audio_b64(lang)
            if ref_audio:
                payload['ref_audio'] = ref_audio

        if 'voice_id' not in payload and 'ref_audio' not in payload:
            raise RuntimeError(
                'Voxtral requires voice_id or ref_audio. '
                'Set voxtral_voice_id_* or voxtral_ref_audio_*_path.'
            )

        req = urlrequest.Request(
            self.voxtral_api_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Authorization': f'Bearer {self.voxtral_api_key}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )

        try:
            with urlrequest.urlopen(req, timeout=self.voxtral_timeout_s) as resp:
                body = resp.read().decode('utf-8')
        except urlerror.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='ignore')
            raise RuntimeError(
                f'Voxtral HTTP {exc.code}: {detail[:200]}'
            ) from exc
        except urlerror.URLError as exc:
            raise RuntimeError(f'Voxtral connection error: {exc}') from exc

        try:
            data = json.loads(body)
            audio_b64 = data.get('audio_data')
            if not audio_b64:
                raise ValueError('audio_data missing')
            audio_bytes = base64.b64decode(audio_b64)
        except Exception as exc:
            raise RuntimeError(f'Invalid Voxtral response: {exc}') from exc

        if self.voxtral_response_format == 'pcm':
            # PCM format is float32 little-endian according to Mistral docs.
            pcm_f32 = np.frombuffer(audio_bytes, dtype='<f4')
            if pcm_f32.size == 0:
                raise RuntimeError('Empty PCM audio from Voxtral')
            audio_i16 = np.clip(np.round(pcm_f32 * 32767.0), -32768, 32767).astype(np.int16)
            return audio_i16, self.target_sample_rate

        audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype='int16')
        return audio_data, int(sample_rate)
    
    def _synthesize_edge(self, text: str, voice: str):
        """Synthesize with Edge TTS (online, entirely in RAM)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            audio_bytes = loop.run_until_complete(self._synthesize_async(text, voice))
        finally:
            loop.close()
        
        # Use io.BytesIO directly in memory
        import io
        mp3_io = io.BytesIO(audio_bytes)
        
        # Read MP3 directly from in-memory buffer
        audio_data, sample_rate = sf.read(mp3_io, dtype='int16')
        return audio_data, sample_rate

    def _resample(self, audio_data, original_rate, target_rate):
        """Resample audio data to target rate."""
        if original_rate == target_rate:
            return audio_data
        
        # Calculate number of samples
        number_of_samples = round(len(audio_data) * float(target_rate) / original_rate)
        
        # Resample using FFT (good for speech)
        resampled_data = scipy.signal.resample(audio_data, number_of_samples)
        
        return resampled_data.astype(np.int16)
    
    def stop(self):
        """Stop current TTS (for barge-in)."""
        # INCREMENT EPOCH FIRST - all queued chunks become invalid
        self.stop_epoch += 1
        self.stop_requested = True
        self._clear_queues()
        self.is_speaking = False
        self.get_logger().debug(f'⏹️ TTS stopped (epoch now {self.stop_epoch})')
        self.stop_requested = False
    
    def destroy_node(self):
        """Cleanup on exit."""
        self.running = False
        try:
            if hasattr(self, 'producer_thread') and self.producer_thread.is_alive():
                self.producer_thread.join(timeout=1.0)
            if hasattr(self, 'consumer_thread') and self.consumer_thread.is_alive():
                self.consumer_thread.join(timeout=1.0)
            if hasattr(self, 'cache_thread') and self.cache_thread.is_alive():
                self.cache_thread.join(timeout=1.0)
        except Exception:
            pass
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    
    try:
        node = TTSNode()
        rclpy.spin(node)
    except RuntimeError as e:
        print(f'Failed to start TTS node: {e}')
    except KeyboardInterrupt:
        pass
    finally:
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()
