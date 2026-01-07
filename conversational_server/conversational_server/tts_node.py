#!/usr/bin/env python3
"""
TTS Node - Text to Speech using Edge TTS with STREAMING support.

Subscribes to: 
  - /llm_stream (TextChunk) - streaming chunks (PREFERRED)
  - /llm_response (Transcription) - complete response (fallback)
Publishes to: /audio_out (Audio)
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription, TextChunk, Audio
import asyncio
import tempfile
import os
import numpy as np
import threading
import queue

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
        
        self.voice_en = self.get_parameter('voice_en').value
        self.voice_ro = self.get_parameter('voice_ro').value
        self.rate = self.get_parameter('rate').value
        self.pitch = self.get_parameter('pitch').value
        
        if not EDGE_TTS_AVAILABLE:
            self.get_logger().error('edge-tts not installed!')
            raise RuntimeError('edge-tts not available')
        
        if not SOUNDFILE_AVAILABLE:
            self.get_logger().error('soundfile not installed!')
            raise RuntimeError('soundfile not available')
        
        self.get_logger().info(f'✅ TTS initialized: EN={self.voice_en}, RO={self.voice_ro}')
        
        # Queue pentru procesare chunks în ordine
        self.chunk_queue = queue.Queue()
        self.is_speaking = False
        self.current_session = None
        
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
            '/playback_active',
            10
        )
        
        # Thread pentru procesare queue
        self.running = True
        self.worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.worker_thread.start()
        
        self.get_logger().info('TTS Node started with STREAMING! Listening on /llm_stream')
    
    def _pick_voice(self, lang: str) -> str:
        """Alege vocea în funcție de limbă."""
        if lang.lower().startswith('ro'):
            return self.voice_ro
        return self.voice_en
    
    def stream_callback(self, msg: TextChunk):
        """Procesează chunk-uri de text streaming."""
        # Ignoră mesaje din sesiuni vechi dacă avem o sesiune nouă
        if self.current_session and msg.session_id != self.current_session:
            if not msg.is_final:
                # Sesiune nouă, resetăm
                self.current_session = msg.session_id
                # Golim queue-ul vechi
                while not self.chunk_queue.empty():
                    try:
                        self.chunk_queue.get_nowait()
                    except queue.Empty:
                        break
        
        if not self.current_session:
            self.current_session = msg.session_id
        
        if msg.text.strip():
            self.get_logger().info(f'📥 Stream chunk: "{msg.text[:40]}..." (final={msg.is_final})')
            self.chunk_queue.put((msg.text, msg.language, msg.is_final))
        elif msg.is_final:
            # Mesaj gol cu is_final - semnalizează sfârșitul
            self.chunk_queue.put(("", "", True))
    
    def response_callback(self, msg: Transcription):
        """Fallback pentru răspunsuri complete (non-streaming)."""
        # Dacă primim pe /llm_response, înseamnă că LLM-ul nu face streaming
        # sau e un mesaj de compatibilitate - îl ignorăm dacă avem deja chunks
        pass  # Dezactivat - folosim doar streaming
    
    def _process_queue(self):
        """Procesează queue-ul de chunks în background."""
        while self.running:
            try:
                text, lang, is_final = self.chunk_queue.get(timeout=0.1)
                
                if text:
                    self._synthesize_and_publish(text, lang)
                
                if is_final:
                    self.current_session = None
                    self.get_logger().info('✅ Stream complete')
                    
            except queue.Empty:
                continue
            except Exception as e:
                self.get_logger().error(f'Queue processing error: {e}')
    
    def _synthesize_and_publish(self, text: str, lang: str):
        """Sintetizează și publică audio pentru un chunk."""
        from std_msgs.msg import Bool
        
        try:
            voice = self._pick_voice(lang)
            self.get_logger().info(f'🔊 Synthesizing: "{text[:30]}..."')
            
            # Marchează că vorbim
            self.is_speaking = True
            speaking_msg = Bool()
            speaking_msg.data = True
            self.speaking_pub.publish(speaking_msg)
            
            # Sintetizează
            audio_data, sample_rate = self._synthesize(text, voice)
            
            # Asigură-te că e mono
            if len(audio_data.shape) > 1:
                audio_data = audio_data[:, 0]
            
            # Publică audio
            out = Audio()
            out.data = audio_data.tolist()
            out.sample_rate = sample_rate
            out.channels = 1
            self.audio_pub.publish(out)
            
            self.get_logger().debug(f'📤 Published {len(audio_data)} samples at {sample_rate}Hz')
            
        except Exception as e:
            self.get_logger().error(f'TTS error: {e}')
        finally:
            # Marchează că am terminat acest chunk
            self.is_speaking = False
            speaking_msg = Bool()
            speaking_msg.data = False
            self.speaking_pub.publish(speaking_msg)
    
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
