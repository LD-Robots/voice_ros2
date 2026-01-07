#!/usr/bin/env python3
"""
TTS Node - Text to Speech using Edge TTS with DOUBLE BUFFERING.

Subscribes to: 
  - /llm_stream (TextChunk) - streaming chunks (PREFERRED)
  - /llm_response (Transcription) - complete response (fallback)
Publishes to: /audio_out (Audio)

DOUBLE BUFFER ARCHITECTURE:
- ThreadPoolExecutor synthesizes next chunk WHILE current chunk plays
- Pre-synthesis queue ensures audio is ready before needed
- Eliminates dead time between phrases
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription, TextChunk, Audio
from std_msgs.msg import Bool
import asyncio
import tempfile
import os
import numpy as np
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, Future
from collections import OrderedDict

# Edge TTS pentru sinteză vocală
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    print("⚠️ edge-tts not installed. Run: pip install edge-tts")

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
        self.declare_parameter('voice_en', 'en-IE-EmilyNeural')
        self.declare_parameter('voice_ro', 'ro-RO-AlinaNeural')
        self.declare_parameter('rate', '+0%')
        self.declare_parameter('pitch', '+0Hz')
        self.declare_parameter('prefetch_count', 2)  # Câte chunks să sintetizeze în avans
        
        self.voice_en = self.get_parameter('voice_en').value
        self.voice_ro = self.get_parameter('voice_ro').value
        self.rate = self.get_parameter('rate').value
        self.pitch = self.get_parameter('pitch').value
        self.prefetch_count = self.get_parameter('prefetch_count').value
        
        if not EDGE_TTS_AVAILABLE:
            self.get_logger().error('edge-tts not installed!')
            raise RuntimeError('edge-tts not available')
        
        if not SOUNDFILE_AVAILABLE:
            self.get_logger().error('soundfile not installed!')
            raise RuntimeError('soundfile not available')
        
        self.get_logger().info(f'✅ TTS initialized: EN={self.voice_en}, RO={self.voice_ro}')
        
        # ═══════════════════════════════════════════════════════════════
        # DOUBLE BUFFER SYSTEM
        # ═══════════════════════════════════════════════════════════════
        
        # Queue pentru chunks primite (text)
        self.text_queue = queue.Queue()
        
        # Queue pentru audio sintetizat (gata de redare)
        self.audio_queue = queue.Queue()
        
        # Thread pool pentru sinteză paralelă
        self.executor = ThreadPoolExecutor(max_workers=self.prefetch_count)
        
        # Tracking pentru ordine
        self.pending_futures = OrderedDict()  # sequence_id -> Future
        self.next_sequence = 0
        self.next_to_publish = 0
        
        self.is_speaking = False
        self.current_session = None
        self.running = True
        
        # ═══════════════════════════════════════════════════════════════
        # SUBSCRIBERS
        # ═══════════════════════════════════════════════════════════════
        
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
        
        # ═══════════════════════════════════════════════════════════════
        # PUBLISHERS
        # ═══════════════════════════════════════════════════════════════
        
        self.audio_pub = self.create_publisher(Audio, '/audio_out', 10)
        self.speaking_pub = self.create_publisher(Bool, '/playback_active', 10)
        
        # ═══════════════════════════════════════════════════════════════
        # WORKER THREADS
        # ═══════════════════════════════════════════════════════════════
        
        # Thread pentru dispatch sinteze
        self.dispatch_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self.dispatch_thread.start()
        
        # Thread pentru publicare audio în ordine
        self.publish_thread = threading.Thread(target=self._publish_loop, daemon=True)
        self.publish_thread.start()
        
        self.get_logger().info(f'🔊 TTS Node started with DOUBLE BUFFER (prefetch={self.prefetch_count})')
    
    def _pick_voice(self, lang: str) -> str:
        """Alege vocea în funcție de limbă."""
        if lang.lower().startswith('ro'):
            return self.voice_ro
        return self.voice_en
    
    # ═══════════════════════════════════════════════════════════════════
    # CALLBACKS
    # ═══════════════════════════════════════════════════════════════════
    
    def stream_callback(self, msg: TextChunk):
        """Procesează chunk-uri de text streaming."""
        # Sesiune nouă - resetează
        if self.current_session and msg.session_id != self.current_session:
            self._reset_session()
        
        if not self.current_session:
            self.current_session = msg.session_id
            self.next_sequence = 0
            self.next_to_publish = 0
        
        if msg.text.strip():
            self.get_logger().info(f'📥 Stream chunk [{self.next_sequence}]: "{msg.text[:30]}..."')
            self.text_queue.put((self.next_sequence, msg.text, msg.language, msg.is_final))
            self.next_sequence += 1
        elif msg.is_final:
            self.text_queue.put((self.next_sequence, "", "", True))
            self.next_sequence += 1
    
    def response_callback(self, msg: Transcription):
        """Fallback pentru răspunsuri complete (non-streaming)."""
        pass  # Dezactivat - folosim doar streaming
    
    def _reset_session(self):
        """Resetează sesiunea curentă."""
        self.current_session = None
        self.next_sequence = 0
        self.next_to_publish = 0
        
        # Golește queue-urile
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
        
        # Anulează futures în așteptare
        for future in self.pending_futures.values():
            future.cancel()
        self.pending_futures.clear()
    
    # ═══════════════════════════════════════════════════════════════════
    # DISPATCH LOOP - Trimite chunks la ThreadPool pentru sinteză
    # ═══════════════════════════════════════════════════════════════════
    
    def _dispatch_loop(self):
        """Dispatch chunks pentru sinteză în paralel."""
        while self.running:
            try:
                seq, text, lang, is_final = self.text_queue.get(timeout=0.1)
                
                if text:
                    voice = self._pick_voice(lang)
                    # Trimite la ThreadPool
                    future = self.executor.submit(self._synthesize_chunk, seq, text, voice)
                    self.pending_futures[seq] = future
                    self.get_logger().debug(f'🔄 Dispatched chunk {seq} for synthesis')
                
                if is_final and not text:
                    # Marker de final
                    self.audio_queue.put((seq, None, None, True))
                    
            except queue.Empty:
                continue
            except Exception as e:
                self.get_logger().error(f'Dispatch error: {e}')
    
    def _synthesize_chunk(self, seq: int, text: str, voice: str):
        """Sintetizează un chunk (rulează în ThreadPool)."""
        try:
            audio_data, sample_rate = self._synthesize(text, voice)
            
            # Asigură-te că e mono
            if len(audio_data.shape) > 1:
                audio_data = audio_data[:, 0]
            
            # Pune în queue de audio
            self.audio_queue.put((seq, audio_data, sample_rate, False))
            self.get_logger().debug(f'✅ Chunk {seq} synthesized ({len(audio_data)} samples)')
            
        except Exception as e:
            self.get_logger().error(f'Synthesis error for chunk {seq}: {e}')
            # Pune un marker de eroare
            self.audio_queue.put((seq, None, None, False))
    
    # ═══════════════════════════════════════════════════════════════════
    # PUBLISH LOOP - Publică audio ÎN ORDINE
    # ═══════════════════════════════════════════════════════════════════
    
    def _publish_loop(self):
        """Publică audio chunks în ordinea corectă."""
        pending_audio = {}  # seq -> (audio_data, sample_rate)
        
        while self.running:
            try:
                seq, audio_data, sample_rate, is_final = self.audio_queue.get(timeout=0.1)
                
                if is_final and audio_data is None:
                    # Așteaptă toate chunk-urile anterioare
                    while self.next_to_publish < seq:
                        if self.next_to_publish in pending_audio:
                            ad, sr = pending_audio.pop(self.next_to_publish)
                            if ad is not None:
                                self._publish_audio(ad, sr)
                            self.next_to_publish += 1
                        else:
                            break
                    
                    self.current_session = None
                    self.get_logger().info('✅ Stream complete')
                    continue
                
                # Stochează audio-ul
                pending_audio[seq] = (audio_data, sample_rate)
                
                # Publică în ordine
                while self.next_to_publish in pending_audio:
                    ad, sr = pending_audio.pop(self.next_to_publish)
                    if ad is not None:
                        self._publish_audio(ad, sr)
                    self.next_to_publish += 1
                    
            except queue.Empty:
                continue
            except Exception as e:
                self.get_logger().error(f'Publish error: {e}')
    
    def _publish_audio(self, audio_data: np.ndarray, sample_rate: int):
        """Publică un chunk de audio."""
        # Marchează că vorbim
        speaking_msg = Bool()
        speaking_msg.data = True
        self.speaking_pub.publish(speaking_msg)
        
        # Publică audio
        out = Audio()
        out.data = audio_data.tolist()
        out.sample_rate = sample_rate
        out.channels = 1
        self.audio_pub.publish(out)
        
        self.get_logger().info(f'📤 Published audio: {len(audio_data)/sample_rate:.2f}s')
    
    # ═══════════════════════════════════════════════════════════════════
    # SYNTHESIS
    # ═══════════════════════════════════════════════════════════════════
    
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
        """Wrapper sincron pentru sinteză."""
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
    
    def destroy_node(self):
        """Cleanup la închidere."""
        self.running = False
        self.executor.shutdown(wait=False)
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
