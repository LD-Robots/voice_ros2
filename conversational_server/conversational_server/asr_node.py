#!/usr/bin/env python3
"""
ASR Node - Speech to Text using Faster Whisper.

Subscribes to: /audio_raw (Audio)
Publishes to: /transcription (Transcription)
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio, Transcription
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
        
        model_size = self.get_parameter('model_size').value
        device = self.get_parameter('device').value
        compute_type = self.get_parameter('compute_type').value
        
        # Inițializează ASR engine
        self.get_logger().info(f'Initializing ASR: model={model_size}, device={device}')
        self.engine = ASREngine(
            model_size=model_size,
            device=device,
            compute_type=compute_type,
            logger=self.get_logger(),
        )
        
        # Subscriber pentru audio
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # Publisher pentru transcriere
        self.transcription_pub = self.create_publisher(
            Transcription,
            '/transcription',
            10
        )
        
        self.get_logger().info('ASR Node started! Listening on /audio_raw')
    
    def audio_callback(self, msg: Audio):
        """Procesează audio și publică transcrierea."""
        self.get_logger().info(f'Received audio: {len(msg.data)} samples, {msg.sample_rate}Hz')
        
        # Convertește mesajul în array numpy
        audio_data = np.array(msg.data, dtype=np.int16)
        
        # Salvează temporar ca WAV
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            temp_path = f.name
            with wave.open(f.name, 'wb') as wav:
                wav.setnchannels(msg.channels)
                wav.setsampwidth(2)  # 16-bit = 2 bytes
                wav.setframerate(msg.sample_rate)
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
