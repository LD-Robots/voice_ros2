#!/usr/bin/env python3
"""
TTS Node - Text to Speech with MULTIPLE BACKENDS, STREAMING and DOUBLE BUFFER.

BACKENDS:
  - edge-tts (default) - Microsoft Edge TTS, requires internet
  - pyttsx3 - Offline TTS fallback

DOUBLE BUFFER: Sintetizează next chunk în paralel cu playback-ul curent.

Subscribes to: 
  - /llm_stream (TextChunk) - streaming chunks (PREFERRED)
  - /llm_response (Transcription) - complete response (fallback)
  - /tts_command (String) - comenzi pentru cache playback
Publishes to: /audio_out (Audio)
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription, TextChunk, Audio
from std_msgs.msg import Bool, String
import asyncio
import tempfile
import os
import numpy as np
import threading
import queue
import time

# Edge TTS pentru sinteză vocală (online)
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    print("⚠️ edge-tts not installed. Run: pip install edge-tts")

# pyttsx3 pentru sinteză vocală (offline fallback)
try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except ImportError:
    PYTTSX3_AVAILABLE = False
    print("⚠️ pyttsx3 not installed. Run: pip install pyttsx3")

# Soundfile pentru citirea audio
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
        self.declare_parameter('backend', 'edge')  # 'edge' sau 'pyttsx3'
        self.declare_parameter('voice_en', 'en-IE-EmilyNeural')
        self.declare_parameter('voice_ro', 'ro-RO-AlinaNeural')
        self.declare_parameter('rate', '+0%')
        self.declare_parameter('pitch', '+0Hz')
        self.declare_parameter('buffer_size', 2)  # Double buffer (2 chunks ahead)
        
        self.backend = self.get_parameter('backend').value
        self.voice_en = self.get_parameter('voice_en').value
        self.voice_ro = self.get_parameter('voice_ro').value
        self.rate = self.get_parameter('rate').value
        self.pitch = self.get_parameter('pitch').value
        self.buffer_size = self.get_parameter('buffer_size').value
        
        # Selectează backend-ul
        if self.backend == 'edge':
            if not EDGE_TTS_AVAILABLE:
                self.get_logger().warn('edge-tts not available, falling back to pyttsx3')
                self.backend = 'pyttsx3'
            elif not SOUNDFILE_AVAILABLE:
                self.get_logger().warn('soundfile not available for edge-tts, falling back to pyttsx3')
                self.backend = 'pyttsx3'
        
        if self.backend == 'pyttsx3':
            if not PYTTSX3_AVAILABLE:
                self.get_logger().error('No TTS backend available!')
                raise RuntimeError('No TTS backend available')
            # Inițializează pyttsx3
            self.pyttsx3_engine = pyttsx3.init()
            self.pyttsx3_engine.setProperty('rate', 170)
            self.get_logger().info('✅ TTS initialized with pyttsx3 (offline)')
        else:
            # Edge TTS necesită soundfile pt MP3
            if not SOUNDFILE_AVAILABLE:
                self.get_logger().error('soundfile not installed - required for edge-tts!')
                raise RuntimeError('soundfile not available')
            self.get_logger().info(f'✅ TTS initialized with edge-tts: EN={self.voice_en}, RO={self.voice_ro}')
        
        # === WAV CACHE - fraze comune pre-generate ===
        self.cache_dir = '/tmp/tts_cache'
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Fraze comune pentru cache
        self.cache_phrases = {
            'ack_en': ('Yes, I am listening.', 'en'),
            'ack_ro': ('Da, te ascult.', 'ro'),
            'filler_en': ('One moment please...', 'en'),
            'filler_ro': ('Un moment...', 'ro'),
            'goodbye_en': ('Goodbye! Have a great day!', 'en'),
            'goodbye_ro': ('La revedere! O zi frumoasă!', 'ro'),
            'error_en': ('Sorry, I encountered an error.', 'en'),
            'error_ro': ('Scuze, am întâlnit o eroare.', 'ro'),
        }
        self.audio_cache = {}  # key -> (audio_data, sample_rate)
        
        # Pre-generează cache-ul în background
        self.cache_thread = threading.Thread(target=self._precache, daemon=True, name="TTS-Cache")
        self.cache_thread.start()
        
        # === DOUBLE BUFFER QUEUES ===
        # Queue pentru text chunks incoming
        self.text_queue = queue.Queue()
        # Queue pentru audio pre-sintetizat (double buffer) - max 2 chunks pre-sintetizate
        self.audio_queue = queue.Queue(maxsize=self.buffer_size)
        
        self.is_speaking = False
        self.current_session = None
        self.stop_requested = False
        self.stop_epoch = 0  # Epoch counter - increments on stop(), chunks with old epoch are skipped
        
        # Subscriber pentru comenzi cache (ack, goodbye, etc)
        from std_msgs.msg import String
        self.command_sub = self.create_subscription(
            String,
            '/tts_command',
            self.command_callback,
            10
        )
        
        # Subscriber pentru STREAMING chunks (PREFERRED)
        self.stream_sub = self.create_subscription(
            TextChunk,
            '/llm_stream',
            self.stream_callback,
            10
        )
        
        # Subscriber pentru răspunsul complet (FALLBACK)
        self.response_sub = self.create_subscription(
            Transcription,
            '/llm_response',
            self.response_callback,
            10
        )
        
        # Publisher pentru audio sintetizat
        self.audio_pub = self.create_publisher(
            Audio,
            '/audio_out',
            10
        )
        
        # Publisher pentru status speaking
        from std_msgs.msg import Bool
        self.speaking_pub = self.create_publisher(
            Bool,
            '/tts_speaking',  # Renamed for clarity
            10
        )
        
        # Subscriber pentru stop TTS
        self.stop_sub = self.create_subscription(
            Bool,
            '/tts_stop',
            self.stop_callback,
            10
        )
        
        # Timer pentru a publica starea is_speaking periodic
        self.speaking_timer = self.create_timer(0.2, self._publish_speaking_status)
        
        # === DOUBLE BUFFER THREADS ===
        self.running = True
        
        # Thread PRODUCER: citeste text chunks, sintetizează audio, pune în audio_queue
        self.producer_thread = threading.Thread(target=self._producer_loop, daemon=True, name="TTS-Producer")
        self.producer_thread.start()
        
        # Thread CONSUMER: citeste din audio_queue, publică pe /audio_out
        self.consumer_thread = threading.Thread(target=self._consumer_loop, daemon=True, name="TTS-Consumer")
        self.consumer_thread.start()
        
        self.get_logger().info('TTS Node started with DOUBLE BUFFER + CACHE! Listening on /llm_stream')
    
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
        """Pre-generează audio pentru frazele comune."""
        self.get_logger().info('🔄 Pre-generating cached phrases...')
        for key, (text, lang) in self.cache_phrases.items():
            try:
                voice = self._pick_voice(lang)
                audio_data, sample_rate = self._synthesize(text, voice)
                if len(audio_data.shape) > 1:
                    audio_data = audio_data[:, 0]
                self.audio_cache[key] = (audio_data, sample_rate)
                self.get_logger().debug(f'  ✓ Cached: {key}')
            except Exception as e:
                self.get_logger().warn(f'  ✗ Failed to cache {key}: {e}')
        self.get_logger().info(f'✅ Cached {len(self.audio_cache)} phrases')
    
    def say_cached(self, key: str) -> bool:
        """Redă o frază din cache. Returnează True dacă a reușit."""
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
        
        self.get_logger().info(f'🎵 Playing cached: {key}')
        return True
    
    def command_callback(self, msg):
        """Procesează comenzi pentru TTS (play cached phrases)."""
        command = msg.data.strip()
        
        # Dacă e un key din cache, îl redă
        if command in self.audio_cache or command in self.cache_phrases:
            self.say_cached(command)
        else:
            self.get_logger().warn(f'Unknown TTS command: {command}')
    
    def _pick_voice(self, lang: str) -> str:
        """Alege vocea - română sau engleză (default pentru orice altceva)."""
        lang = lang.lower() if lang else 'en'
        if lang.startswith('ro'):
            return self.voice_ro
        # Orice altă limbă -> engleză
        return self.voice_en
    
    def stream_callback(self, msg: TextChunk):
        """Procesează chunk-uri de text streaming."""
        # Sesiune nouă - resetează
        if self.current_session and msg.session_id != self.current_session:
            if not msg.is_final:
                self.current_session = msg.session_id
                self.stop_requested = True  # Oprește ce e în curs
                # Golim queue-urile
                self._clear_queues()
                self.stop_requested = False
        
        if not self.current_session:
            self.current_session = msg.session_id
        
        if msg.text.strip():
            self.get_logger().info(f'📥 Stream chunk: "{msg.text[:40]}..." (final={msg.is_final})')
            self.text_queue.put((msg.text, msg.language, msg.is_final, msg.session_id))
        elif msg.is_final:
            # Mesaj gol cu is_final - semnalizează sfârșitul
            self.text_queue.put(("", "", True, msg.session_id))
    
    def response_callback(self, msg: Transcription):
        """Fallback pentru răspunsuri complete (non-streaming)."""
        pass  # Dezactivat - folosim doar streaming
    
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
        PRODUCER: Citește text din text_queue, sintetizează, pune în audio_queue.
        Rulează în paralel - mereu încearcă să aibă 1-2 chunks pre-sintetizate.
        """
        while self.running:
            try:
                text, lang, is_final, session_id = self.text_queue.get(timeout=0.1)
                
                if self.stop_requested:
                    continue
                
                if text:
                    voice = self._pick_voice(lang)
                    self.get_logger().info(f'🔧 Pre-synthesizing: "{text[:30]}..."')
                    
                    try:
                        audio_data, sample_rate = self._synthesize(text, voice)
                        
                        # Asigură-te că e mono
                        if len(audio_data.shape) > 1:
                            audio_data = audio_data[:, 0]
                        
                        # Pune în audio_queue CU EPOCH (va bloca dacă e plin = double buffer full)
                        current_epoch = self.stop_epoch
                        if not self.stop_requested:
                            self.audio_queue.put((audio_data, sample_rate, is_final, session_id, current_epoch), timeout=5.0)
                            self.get_logger().debug(f'📦 Buffered audio ({len(audio_data)} samples, epoch={current_epoch})')
                    
                    except Exception as e:
                        self.get_logger().error(f'Synthesis error: {e}')
                
                elif is_final:
                    # Semnalizează sfârșitul în audio_queue (cu epoch)
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
                
                if audio_data is not None:
                    # Marchează că vorbim
                    self.is_speaking = True
                    speaking_msg = Bool()
                    speaking_msg.data = True
                    self.speaking_pub.publish(speaking_msg)
                    
                    # Publică audio
                    out = Audio()
                    out.data = audio_data.tolist()
                    out.sample_rate = sample_rate
                    out.channels = 1
                    self.audio_pub.publish(out)
                    
                    self.get_logger().info(f'📤 Published {len(audio_data)} samples at {sample_rate}Hz')
                    
                    # Marchează că am terminat acest chunk
                    self.is_speaking = False
                    speaking_msg.data = False
                    self.speaking_pub.publish(speaking_msg)
                
                if is_final:
                    self.current_session = None
                    self.get_logger().info('✅ Stream complete')
                    
            except queue.Empty:
                continue
            except Exception as e:
                self.get_logger().error(f'Consumer error: {e}')
    
    async def _synthesize_async(self, text: str, voice: str) -> bytes:
        """Sintetizează text în audio folosind Edge TTS."""
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
        """Sintetizează text în audio folosind backend-ul selectat."""
        if self.backend == 'pyttsx3':
            return self._synthesize_pyttsx3(text)
        else:
            return self._synthesize_edge(text, voice)
    
    def _synthesize_edge(self, text: str, voice: str):
        """Sintetizează cu Edge TTS (online)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            audio_bytes = loop.run_until_complete(self._synthesize_async(text, voice))
        finally:
            loop.close()
        
        # Salvează temporar și citește cu soundfile
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            temp_path = f.name
            f.write(audio_bytes)
        
        try:
            audio_data, sample_rate = sf.read(temp_path, dtype='int16')
            return audio_data, sample_rate
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    
    def _synthesize_pyttsx3(self, text: str):
        """Sintetizează cu pyttsx3 (offline)."""
        # pyttsx3 salvează în fișier WAV
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            temp_path = f.name
        
        try:
            self.pyttsx3_engine.save_to_file(text, temp_path)
            self.pyttsx3_engine.runAndWait()
            
            # Citește WAV
            audio_data, sample_rate = sf.read(temp_path, dtype='int16')
            return audio_data, sample_rate
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    
    def stop(self):
        """Oprește TTS-ul curent (pentru barge-in)."""
        # INCREMENT EPOCH FIRST - all queued chunks become invalid
        self.stop_epoch += 1
        self.stop_requested = True
        self._clear_queues()
        self.is_speaking = False
        self.get_logger().info(f'⏹️ TTS stopped (epoch now {self.stop_epoch})')
        self.stop_requested = False
    
    def destroy_node(self):
        """Cleanup la închidere."""
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
        rclpy.shutdown()


if __name__ == '__main__':
    main()
