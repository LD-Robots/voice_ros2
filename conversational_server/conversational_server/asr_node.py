#!/usr/bin/env python3
"""
ASR Node - Speech to Text using Faster Whisper.

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
import sys
import os

# Adaugă path-ul către proiectul existent
sys.path.insert(0, os.path.expanduser('~/Conversational_Robot/Conversational_Bot'))

from src.asr.engine_faster import ASREngine


class ASRNode(Node):
    def __init__(self):
        super().__init__('asr_node')
        
        # Parametri configurabili
        self.declare_parameter('model_size', 'small')
        self.declare_parameter('device', 'cpu')
        self.declare_parameter('compute_type', 'int8')
        self.declare_parameter('min_audio_length', 0.5)  # Minimum seconds to transcribe
        
        model_size = self.get_parameter('model_size').value
        device = self.get_parameter('device').value
        compute_type = self.get_parameter('compute_type').value
        self.min_audio_length = self.get_parameter('min_audio_length').value
        
        # Inițializează ASR engine
        self.get_logger().info(f'Initializing ASR: model={model_size}, device={device}')
        self.engine = ASREngine(
            model_size=model_size,
            device=device,
            compute_type=compute_type,
            logger=self.get_logger(),
        )
        
        # Buffer pentru audio
        self.audio_buffer = []
        self.sample_rate = 16000
        self.channels = 1
        self.is_speaking = False
        self.was_speaking = False
        
        # Subscriber pentru audio
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # Subscriber pentru VAD
        self.vad_sub = self.create_subscription(
            Bool,
            '/voice_activity',
            self.vad_callback,
            10
        )
        
        # Publisher pentru transcriere
        self.transcription_pub = self.create_publisher(
            Transcription,
            '/transcription',
            10
        )
        
        self.get_logger().info('ASR Node started! Listening on /audio_raw and /voice_activity')
    
    def vad_callback(self, msg: Bool):
        """Primește statusul VAD (vorbește/nu vorbește)."""
        self.was_speaking = self.is_speaking
        self.is_speaking = msg.data
        
        # Când userul termină de vorbit, transcrie
        if self.was_speaking and not self.is_speaking:
            self.get_logger().info(f'🔚 Speech ended, processing {len(self.audio_buffer)} frames...')
            self._process_buffer()
    
    def audio_callback(self, msg: Audio):
        """Bufferează audio în timpul vorbirii."""
        self.sample_rate = msg.sample_rate
        self.channels = msg.channels
        
        # Bufferează audio când userul vorbește (sau puțin înainte)
        if self.is_speaking:
            self.audio_buffer.extend(msg.data)
        else:
            # Păstrează ultimele 0.5 secunde pentru context
            max_pre_buffer = int(self.sample_rate * 0.5)
            self.audio_buffer.extend(msg.data)
            if len(self.audio_buffer) > max_pre_buffer:
                self.audio_buffer = self.audio_buffer[-max_pre_buffer:]
    
    def _process_buffer(self):
        """Procesează audio-ul bufferat și publică transcrierea."""
        if not self.audio_buffer:
            self.get_logger().warn('Empty audio buffer, skipping')
            self.audio_buffer = []
            return
        
        # Verifică lungimea minimă
        audio_length = len(self.audio_buffer) / self.sample_rate
        if audio_length < self.min_audio_length:
            self.get_logger().warn(f'Audio too short ({audio_length:.2f}s < {self.min_audio_length}s), skipping')
            self.audio_buffer = []
            return
        
        self.get_logger().info(f'🎤 Processing {audio_length:.2f}s of audio...')
        
        # Convertește în numpy array
        audio_data = np.array(self.audio_buffer, dtype=np.int16)
        
        # Salvează temporar ca WAV
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            temp_path = f.name
            with wave.open(f.name, 'wb') as wav:
                wav.setnchannels(self.channels)
                wav.setsampwidth(2)  # 16-bit = 2 bytes
                wav.setframerate(self.sample_rate)
                wav.writeframes(audio_data.tobytes())
        
        try:
            # Transcrie
            result = self.engine.transcribe(temp_path)
            
            text = result.get('text', '').strip()
            lang = result.get('lang', 'en')
            confidence = float(result.get('language_probability', 0.0))
            
            if text:
                self.get_logger().info(f'🧏 [{lang}] {text}')
                
                # Publică rezultat
                out = Transcription()
                out.text = text
                out.language = lang
                out.confidence = confidence
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
    node = ASRNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
