#!/usr/bin/env python3
"""
TTS Node - Text to Speech with MULTIPLE BACKENDS, STREAMING and DOUBLE BUFFER.

BACKENDS:
  - edge-tts (default) - Microsoft Edge TTS, requires internet
  - piper - Offline TTS fallback (high quality, ONNX models)

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
import os
import numpy as np
import threading
import queue
import time
import scipy.signal  # For resampling
import re

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

# Piper TTS for voice synthesis (offline fallback)
try:
    from piper import PiperVoice
    PIPER_AVAILABLE = True
except ImportError:
    PIPER_AVAILABLE = False
    print("⚠️ piper-tts not installed. Run: pip install piper-tts")

# Soundfile for audio reading
try:
    import soundfile as sf
    SOUNDFILE_AVAILABLE = True
except ImportError:
    SOUNDFILE_AVAILABLE = False
    print("⚠️ soundfile not installed. Run: pip install soundfile")


class TTSNode(Node):
    def __init__(self):
        super().__init__('tts_node')
        
        # Parametri configurabili
        self.declare_parameter('backend', 'edge')  # 'edge' sau 'piper'
        self.declare_parameter('voice_en', 'en-IE-EmilyNeural')
        self.declare_parameter('voice_ro', 'ro-RO-AlinaNeural')
        self.declare_parameter('rate', '+0%')
        self.declare_parameter('pitch', '+0Hz')
        self.declare_parameter('buffer_size', 2)  # Double buffer (2 chunks ahead)
        
        # Piper model paths
        self.declare_parameter('piper_model_en', '')
        self.declare_parameter('piper_model_ro', '')
        
        self.backend = self.get_parameter('backend').value
        self.voice_en = self.get_parameter('voice_en').value
        self.voice_ro = self.get_parameter('voice_ro').value
        self.rate = self.get_parameter('rate').value
        self.pitch = self.get_parameter('pitch').value
        self.buffer_size = self.get_parameter('buffer_size').value
        self.piper_model_en_path = self.get_parameter('piper_model_en').value
        self.piper_model_ro_path = self.get_parameter('piper_model_ro').value
        
        # Piper voices (pre-loaded)
        self.piper_voice_en = None
        self.piper_voice_ro = None
        
        # Selectează backend-ul
        if self.backend == 'edge':
            if not EDGE_TTS_AVAILABLE:
                self.get_logger().warn('edge-tts not available, falling back to piper')
                self.backend = 'piper'
            elif not SOUNDFILE_AVAILABLE:
                self.get_logger().warn('soundfile not available for edge-tts, falling back to piper')
                self.backend = 'piper'
        
        if self.backend == 'piper':
            if not PIPER_AVAILABLE:
                self.get_logger().error('No TTS backend available! Install piper-tts.')
                raise RuntimeError('No TTS backend available')
            self._load_piper_models()
            self.get_logger().info('✅ TTS initialized with Piper (offline)')
        else:
            # Edge TTS necesită soundfile pt MP3
            if not SOUNDFILE_AVAILABLE:
                self.get_logger().error('soundfile not installed - required for edge-tts!')
                raise RuntimeError('soundfile not available')
            # Pre-load Piper models for fallback if available
            if PIPER_AVAILABLE:
                self._load_piper_models()
                self.get_logger().info(f'✅ TTS initialized with edge-tts (Piper fallback ready): EN={self.voice_en}, RO={self.voice_ro}')
            else:
                self.get_logger().info(f'✅ TTS initialized with edge-tts (no offline fallback): EN={self.voice_en}, RO={self.voice_ro}')
        
        # Target sample rate (fix "horror voice" issues by standardizing on 16kHz)
        self.target_sample_rate = 16000
        
        # === WAV CACHE - fraze comune pre-generate ===
        self.cache_dir = '/tmp/tts_cache'
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Common phrases for cache
        self.cache_phrases = {
            'ack_en': ('Hello. I am here and listening.', 'en'),
            'ack_ro': ('Salut. Sunt aici si te ascult.', 'ro'),
            'filler_en': ('One moment please...', 'en'),
            'filler_ro': ('Un moment...', 'ro'),
            'goodbye_en': ('Goodbye. I will be here when you need me again.', 'en'),
            'goodbye_ro': ('La revedere. Raman aici daca mai ai nevoie de mine.', 'ro'),
            'error_en': ('Sorry, I encountered an error.', 'en'),
            'error_ro': ('Sorry, I encountered an error.', 'ro'),
            'confirm_en': ('Are you sure? Please say yes or no.', 'en'),
            'confirm_ro': ('Ești sigur? Te rog confirmă cu da sau nu.', 'ro'),
        }
        self.system_commands = {
            'ack_en',
            'ack_ro',
            'goodbye_en',
            'goodbye_ro',
            'error_en',
            'error_ro',
            'confirm_en',
            'confirm_ro',
            'filler_en',
            'filler_ro',
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
        
        # Thread CONSUMER: citeste din audio_queue, publică pe /audio_out
        self.consumer_thread = threading.Thread(target=self._consumer_loop, daemon=True, name="TTS-Consumer")
        self.consumer_thread.start()
        
        self.get_logger().debug('TTS Node started with DOUBLE BUFFER + CACHE! Listening on /llm_stream')
    
    def _publish_speaking_status(self):
        """Publică periodic starea is_speaking pe /tts_speaking."""
        from std_msgs.msg import Bool
        msg = Bool()
        msg.data = self.is_speaking
        self.speaking_pub.publish(msg)
    
    def stop_callback(self, msg: Bool):
        """Oprește TTS când primim True pe /tts_stop."""
        if msg.data:
            self.stop()
    
    def _precache(self):
        """Pre-generate audio for common phrases."""
        self.get_logger().debug('🔄 Pre-generating cached phrases...')
        for key, (text, lang) in self.cache_phrases.items():
            try:
                voice = self._pick_voice(lang)
                audio_data, sample_rate = self._synthesize(text, voice)
                
                # Resample immediately for cache
                if sample_rate != self.target_sample_rate:
                     audio_data = self._resample(audio_data, sample_rate, self.target_sample_rate)
                     sample_rate = self.target_sample_rate
                
                if len(audio_data.shape) > 1:
                    audio_data = audio_data[:, 0]
                self.audio_cache[key] = (audio_data, sample_rate)
                self.get_logger().debug(f'  ✓ Cached: {key}')
            except Exception as e:
                self.get_logger().warn(f'  ✗ Failed to cache {key}: {e}')
        self.get_logger().debug(f'✅ Cached {len(self.audio_cache)} phrases')
    
    def say_cached(self, key: str) -> bool:
        """Play a cached phrase. Returns True if successful."""
        if key not in self.audio_cache:
            self.get_logger().warn(f'Cache miss: {key}')
            return False
        
        audio_data, sample_rate = self.audio_cache[key]
        
        # Publică audio
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
        if self.current_backend != 'legacy' and command not in self.system_commands:
            return
        
        # Dacă e un key din cache, îl redă
        if command in self.audio_cache or command in self.cache_phrases:
            self.say_cached(command)
        else:
            self.get_logger().warn(f'Unknown TTS command: {command}')
    
    def _pick_voice(self, lang: str) -> str:
        """Choose the voice - Romanian or English (default for anything else)."""
        lang = lang.lower() if lang else 'en'
        if lang.startswith('ro'):
            return self.voice_ro
        # Orice altă limbă -> engleză
        return self.voice_en
    
    def _preprocess_numbers(self, text: str, lang: str) -> str:
        """Convert numbers to words and pronounce math symbols."""
        if not NUM2WORDS_AVAILABLE:
            return text

        base_lang = lang.lower()[:2] if lang else 'en'
        if base_lang not in ['en', 'ro']:
            base_lang = 'en'

        # 1. Traducem semnele matematice uzuale (cu spații în jur)
        math_symbols = {
            'en': {' + ': ' plus ', ' - ': ' minus ', ' = ': ' equals ', ' * ': ' times ', ' / ': ' divided by '},
            'ro': {' + ': ' plus ', ' - ': ' minus ', ' = ': ' egal ', ' * ': ' înmulțit cu ', ' / ': ' împărțit la '}
        }
        
        for symbol, word in math_symbols[base_lang].items():
            text = text.replace(symbol, word)
            
        # Tratăm și cazul în care minusul e lipit direct de număr (ex: -50 -> minus 50)
        text = re.sub(r'(?<!\w)-(?=\d)', 'minus ', text)

        # 2. Transformăm numerele în cuvinte
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

        # Regex care prinde numere complexe: 3.14, 3,14, 100,000, 1.000.000 etc.
        return re.sub(r'\b\d+(?:[.,]\d+)*\b', replace_match, text) 

        # Regex nou care prinde și numere cu virgulă/punct (ex: 100,000.50, 3.14, 4520)
        return re.sub(r'\b(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\b', replace_match, text)
    
    def stream_callback(self, msg: TextChunk):
        """Procesează chunk-uri de text streaming."""
        if self.current_backend != 'legacy':
            return
        # Sesiune nouă - resetează
        if self.current_session and msg.session_id != self.current_session:
            if not msg.is_final:
                self.current_session = msg.session_id
                self.stop_requested = True  # Stop what is currently playing
                # Golim queue-urile
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
        if backend != 'legacy':
            self.stop()
    
    def _clear_queues(self):
        """Golește toate queue-urile."""
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
                
                if self.stop_requested or self.current_backend != 'legacy':
                    continue
                
                if text:
                    voice = self._pick_voice(lang)
                    
                    # ---> NOU: Transformăm numerele în cuvinte aici <---
                    original_text = text
                    text = self._preprocess_numbers(text, lang)
                    if text != original_text:
                        self.get_logger().info(f'🔢 Numbers replaced: "{original_text}" -> "{text}"')
                    # ----------------------------------------------------
                    
                    self.get_logger().debug(f'🔧 Pre-synthesizing: "{text[:30]}..."')
                    
                    try:
                        audio_data, sample_rate = self._synthesize(text, voice)
                        
                        # Resample to target rate (16kHz)
                        if sample_rate != self.target_sample_rate:
                            audio_data = self._resample(audio_data, sample_rate, self.target_sample_rate)
                            sample_rate = self.target_sample_rate
                        
                        # Asigură-te că e mono
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
                if self.current_backend != 'legacy':
                    continue
                
                if audio_data is not None:
                    # Publică audio - audio_playback_node va gestiona is_speaking
                    out = Audio()
                    out.data = audio_data.tolist()
                    out.sample_rate = sample_rate
                    out.channels = 1
                    self.audio_pub.publish(out)
                    
                    self.get_logger().debug(f'📤 Published {len(audio_data)} samples at {sample_rate}Hz')
                    # NU facem sleep - audio_playback_node gestionează starea is_speaking
                
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
    
    def _load_piper_models(self):
        """Pre-load Piper ONNX models for EN and RO."""
        if self.piper_model_en_path and os.path.exists(self.piper_model_en_path):
            try:
                self.piper_voice_en = PiperVoice.load(self.piper_model_en_path)
                self.get_logger().info(f'🔊 Piper EN loaded: {os.path.basename(self.piper_model_en_path)}')
            except Exception as e:
                self.get_logger().error(f'❌ Failed to load Piper EN: {e}')
        else:
            self.get_logger().warn(f'⚠️ Piper EN model not found: {self.piper_model_en_path}')
        
        if self.piper_model_ro_path and os.path.exists(self.piper_model_ro_path):
            try:
                self.piper_voice_ro = PiperVoice.load(self.piper_model_ro_path)
                self.get_logger().info(f'🔊 Piper RO loaded: {os.path.basename(self.piper_model_ro_path)}')
            except Exception as e:
                self.get_logger().error(f'❌ Failed to load Piper RO: {e}')
        else:
            self.get_logger().warn(f'⚠️ Piper RO model not found: {self.piper_model_ro_path}')
    
    def _synthesize(self, text: str, voice: str):
        """Synthesize text to audio using the selected backend."""
        if self.backend == 'piper':
            return self._synthesize_piper(text, voice)
        else:
            try:
                return self._synthesize_edge(text, voice)
            except Exception as e:
                # Fallback to Piper if edge-tts fails (e.g., no internet)
                if self.piper_voice_en or self.piper_voice_ro:
                    self.get_logger().warn(f'⚠️ Edge TTS failed ({e}), falling back to Piper')
                    return self._synthesize_piper(text, voice)
                raise
    
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
        
        # Citește MP3 direct din buffer-ul din memorie
        audio_data, sample_rate = sf.read(mp3_io, dtype='int16')
        return audio_data, sample_rate
    
    def _synthesize_piper(self, text: str, voice: str):
        """Synthesize with Piper TTS (offline, entirely in RAM)."""
        # Alege modelul Piper bazat pe limba vocii
        lang = voice.lower() if voice else 'en'
        if lang.startswith('ro') or 'ro-' in lang.lower():
            piper_voice = self.piper_voice_ro or self.piper_voice_en
        else:
            piper_voice = self.piper_voice_en or self.piper_voice_ro
        
        if piper_voice is None:
            raise RuntimeError('No Piper voice loaded!')
        
        # Sintetizează — returnează chunks de audio int16
        audio_chunks = list(piper_voice.synthesize(text))
        
        if not audio_chunks:
            raise RuntimeError('Piper returned no audio')
        
        # Concatenate all chunks into a single array
        all_audio = b''.join(chunk.audio_int16_bytes for chunk in audio_chunks)
        audio_data = np.frombuffer(all_audio, dtype=np.int16)
        sample_rate = audio_chunks[0].sample_rate
        
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
