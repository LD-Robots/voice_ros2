#!/usr/bin/env python3
"""
audio_capture_node.py
Capturează audio de la microfon și publică pe /audio_raw

Acest nod:
1. Deschide microfonul cu PyAudio
2. Citește audio în chunks de 20ms
3. Publică fiecare chunk pe topicul /audio_raw
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
import numpy as np

# Încercăm să importăm PyAudio
try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    print("⚠️ PyAudio not installed. Run: pip install pyaudio")


class AudioCaptureNode(Node):
    """Nod ROS2 care capturează audio de la microfon."""
    
    def __init__(self):
        super().__init__('audio_capture_node')
        
        # Declară parametri configurabili
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('channels', 1)
        self.declare_parameter('chunk_ms', 20)  # 20ms per chunk
        
        # Citește parametrii
        self.sample_rate = self.get_parameter('sample_rate').value
        self.channels = self.get_parameter('channels').value
        chunk_ms = self.get_parameter('chunk_ms').value
        
        # Calculează chunk size în samples
        self.chunk_size = int(self.sample_rate * chunk_ms / 1000)
        
        # Publisher pentru audio
        self.audio_pub = self.create_publisher(
            Audio,
            '/audio_raw',
            10  # QoS queue size
        )
        
        # Setup PyAudio (dacă e disponibil)
        self.stream = None
        if PYAUDIO_AVAILABLE:
            try:
                self.audio = pyaudio.PyAudio()
                self.stream = self.audio.open(
                    format=pyaudio.paInt16,
                    channels=self.channels,
                    rate=self.sample_rate,
                    input=True,
                    frames_per_buffer=self.chunk_size
                )
                self.get_logger().info(
                    f'🎤 Audio Capture started: {self.sample_rate}Hz, '
                    f'{self.channels}ch, chunk={self.chunk_size} samples ({chunk_ms}ms)'
                )
            except Exception as e:
                self.get_logger().error(f'❌ Failed to open microphone: {e}')
                self.stream = None
        else:
            self.get_logger().warn('⚠️ PyAudio not available - running in dummy mode')
        
        # Timer pentru captură (la fiecare chunk_ms milisecunde)
        timer_period = chunk_ms / 1000.0  # conversie în secunde
        self.timer = self.create_timer(timer_period, self.capture_callback)
        
        self.frame_count = 0
    
    def capture_callback(self):
        """Citește audio de la microfon și publică pe topic."""
        if self.stream is None:
            # Mod dummy - publică silence pentru testare
            if self.frame_count % 50 == 0:  # log la fiecare secundă
                self.get_logger().info('📢 Publishing silence (no microphone)')
            audio_data = [0] * self.chunk_size
        else:
            try:
                # Citește chunk de audio de la microfon
                data = self.stream.read(self.chunk_size, exception_on_overflow=False)
                # Convertește bytes la numpy array de int16
                audio_array = np.frombuffer(data, dtype=np.int16)
                audio_data = audio_array.tolist()
            except Exception as e:
                self.get_logger().error(f'❌ Audio read error: {e}')
                return
        
        # Creează mesajul ROS2
        msg = Audio()
        msg.sample_rate = self.sample_rate
        msg.channels = self.channels
        msg.data = audio_data
        
        # Publică
        self.audio_pub.publish(msg)
        self.frame_count += 1
        
        # Log periodic (la fiecare 5 secunde)
        if self.frame_count % (50 * 5) == 0:
            self.get_logger().info(f'📊 Published {self.frame_count} audio frames')
    
    def destroy_node(self):
        """Cleanup la închiderea nodului."""
        self.get_logger().info('🛑 Shutting down audio capture...')
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
            self.audio.terminate()
        super().destroy_node()


def main(args=None):
    """Entry point pentru nodul ROS2."""
    rclpy.init(args=args)
    
    node = AudioCaptureNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
