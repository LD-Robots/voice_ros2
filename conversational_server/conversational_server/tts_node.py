#!/usr/bin/env python3
"""
TTS Node - Text to Speech using Edge TTS.

Subscribes to: /llm_response (Transcription)
Publishes to: /audio_out (Audio)
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Transcription, Audio
import sys
import os
import asyncio
import tempfile
import numpy as np

# Adaugă path-ul către proiectul existent
sys.path.insert(0, os.path.expanduser('~/Conversational_Robot/Conversational_Bot'))

try:
    import edge_tts
    import soundfile as sf
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False


class TTSNode(Node):
    def __init__(self):
        super().__init__('tts_node')
        
        # Parametri configurabili
        self.declare_parameter('voice_en', 'en-GB-SoniaNeural')
        self.declare_parameter('voice_ro', 'ro-RO-EmilNeural')
        self.declare_parameter('rate', '+0%')
        self.declare_parameter('pitch', '+0Hz')
        self.declare_parameter('sample_rate', 24000)
        
        self.voice_en = self.get_parameter('voice_en').value
        self.voice_ro = self.get_parameter('voice_ro').value
        self.rate = self.get_parameter('rate').value
        self.pitch = self.get_parameter('pitch').value
        self.sample_rate = self.get_parameter('sample_rate').value
        
        if not EDGE_TTS_AVAILABLE:
            self.get_logger().error('edge-tts not installed! Run: pip install edge-tts')
            raise RuntimeError('edge-tts not available')
        
        self.get_logger().info(f'TTS initialized: EN={self.voice_en}, RO={self.voice_ro}')
        
        # Subscriber pentru răspunsul LLM
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
        
        self.get_logger().info('TTS Node started! Listening on /llm_response')
    
    def _pick_voice(self, lang: str) -> str:
        """Alege vocea în funcție de limbă."""
        if lang.lower().startswith('ro'):
            return self.voice_ro
        return self.voice_en
    
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
    
    def _synthesize(self, text: str, voice: str) -> np.ndarray:
        """Wrapper sincron pentru sinteză."""
        # Edge TTS returnează MP3, trebuie convertit
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
            # Citește audio
            audio_data, sr = sf.read(temp_path, dtype='int16')
            return audio_data, sr
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    
    def response_callback(self, msg: Transcription):
        """Procesează răspunsul LLM și publică audio."""
        text = msg.text.strip()
        lang = msg.language
        
        if not text:
            self.get_logger().warn('Empty text received, skipping')
            return
        
        self.get_logger().info(f'🔊 Synthesizing [{lang}]: {text[:50]}...')
        
        try:
            voice = self._pick_voice(lang)
            audio_data, sample_rate = self._synthesize(text, voice)
            
            # Asigură-te că e mono
            if len(audio_data.shape) > 1:
                audio_data = audio_data[:, 0]
            
            self.get_logger().info(f'✅ Synthesized {len(audio_data)} samples at {sample_rate}Hz')
            
            # Publică audio
            out = Audio()
            out.data = audio_data.tolist()
            out.sample_rate = sample_rate
            out.channels = 1
            self.audio_pub.publish(out)
            
        except Exception as e:
            self.get_logger().error(f'TTS error: {e}')


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
