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
import os
import numpy as np
import threading
import queue
import time
import scipy.signal  # For resampling
import re
import wave
from pathlib import Path

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


class TTSNode(Node):
    def __init__(self):
        super().__init__('tts_node')
        
        # Configurable parameters
        self.declare_parameter('voice_en', 'en-IE-EmilyNeural')
        self.declare_parameter('voice_ro', 'ro-RO-AlinaNeural')
        self.declare_parameter('rate', '+0%')
        self.declare_parameter('pitch', '+0Hz')
        self.declare_parameter('buffer_size', 2)  # Double buffer (2 chunks ahead)
        
        self.voice_en = self.get_parameter('voice_en').value
        self.voice_ro = self.get_parameter('voice_ro').value
        self.rate = self.get_parameter('rate').value
        self.pitch = self.get_parameter('pitch').value
        self.buffer_size = self.get_parameter('buffer_size').value
        
        # Edge TTS requires soundfile for MP3 decoding
        if not SOUNDFILE_AVAILABLE:
            self.get_logger().error('soundfile not installed - required for edge-tts!')
            raise RuntimeError('soundfile not available')
        
        if not EDGE_TTS_AVAILABLE:
            self.get_logger().error('edge-tts not installed!')
            raise RuntimeError('edge-tts not available')

        self.get_logger().info(f'✅ TTS initialized with edge-tts: EN={self.voice_en}, RO={self.voice_ro}')
        
        # Target sample rate (fix "horror voice" issues by standardizing on 16kHz)
        self.target_sample_rate = 16000
        
        # === WAV CACHE - pre-generated common phrases ===
        self.cache_dir = '/tmp/tts_cache'
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Common phrases for cache
        self.cache_phrases = {
            'ack_en': ('Hello. I am here and listening.', 'en'),
            'filler_en': ('One moment please...', 'en'),
            'goodbye_en': ('Goodbye. I will be here when you need me again.', 'en'),
            'error_en': ('Sorry, I encountered an error.', 'en'),
            'confirm_en': ('Are you sure? Please say yes or no.', 'en'),
        }
        self.system_commands = {
            'ack_en',
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
        self.speaking_pub = self.create_publisher(
            Bool,
            '/tts_speaking',
            10
        )
        
        # Subscriber for stop TTS
        self.stop_sub = self.create_subscription(
            Bool,
            '/stop_playback',
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
        self.get_logger().debug('🔄 Initializing TTS cache (prioritizing OpenAI static voices)...')
        
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
                    self.get_logger().debug(f'  ✓ Loaded static voice: {key} (OpenAI)')
                else:
                    # 2. Fall back to Edge-TTS
                    voice = self._pick_voice(lang)
                    audio_data, sample_rate = self._synthesize(text, voice)
                
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
        self.get_logger().info(f'✅ TTS cache ready ({len(self.audio_cache)} phrases)')
    
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
        if self.current_backend != 'legacy' and command not in self.system_commands:
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
        if self.current_backend != 'legacy':
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
        if backend != 'legacy':
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
                
                if self.stop_requested or self.current_backend != 'legacy':
                    continue
                
                if text:
                    voice = self._pick_voice(lang)
                    
                    # Convert numbers to words before synthesis
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
                if self.current_backend != 'legacy':
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
    
    def _synthesize(self, text: str, voice: str):
        """Synthesize text to audio using Edge TTS."""
        return self._synthesize_edge(text, voice)
    
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
