#!/usr/bin/env python3
"""
ASR Node - Speech to Text using Faster Whisper.

Subscribes to: /audio_segment (Audio) - complete audio segments from client
Publishes to: /transcription (Transcription)

Primește segmente audio complete de la client și le transcrie cu Faster Whisper.
"""
import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio, Transcription
import numpy as np
import tempfile
import wave
import os

# Faster Whisper pentru ASR
try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    print("⚠️ faster-whisper not installed. Run: pip install faster-whisper")


class ASRNode(Node):
    def __init__(self):
        super().__init__('asr_node')
        
        # Parametri configurabili
        self.declare_parameter('model_size', 'small')
        self.declare_parameter('device', 'cpu')
        self.declare_parameter('compute_type', 'int8')
        self.declare_parameter('min_audio_length', 0.5)  # Minimum seconds to transcribe
        self.declare_parameter('language', '')  # Empty = auto-detect
        
        model_size = self.get_parameter('model_size').value
        device = self.get_parameter('device').value
        compute_type = self.get_parameter('compute_type').value
        self.min_audio_length = self.get_parameter('min_audio_length').value
        self.language = self.get_parameter('language').value or None
        
        if not WHISPER_AVAILABLE:
            self.get_logger().error('faster-whisper not installed!')
            raise RuntimeError('faster-whisper not available')
        
        # Inițializează Whisper model
        self.get_logger().info(f'Loading Whisper model: {model_size} on {device}...')
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type
        )
        self.get_logger().info('✅ Whisper model loaded!')
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER - primește segmente audio complete de la client
        # ─────────────────────────────────────────────────────────
        self.segment_sub = self.create_subscription(
            Audio,
            '/audio_segment',
            self.segment_callback,
            10
        )
        
        # Publisher pentru transcriere
        self.transcription_pub = self.create_publisher(
            Transcription,
            '/transcription',
            10
        )
        
        self.get_logger().info('🧏 ASR Node started! Listening on /audio_segment')
    
    def segment_callback(self, msg: Audio):
        """Procesează un segment audio complet."""
        
        if not msg.data:
            self.get_logger().warn('Empty audio segment, skipping')
            return
        
        sample_rate = msg.sample_rate
        channels = msg.channels
        audio_length = len(msg.data) / sample_rate
        
        # Verifică lungimea minimă
        if audio_length < self.min_audio_length:
            self.get_logger().warn(
                f'Audio too short ({audio_length:.2f}s < {self.min_audio_length}s), skipping'
            )
            return
        
        self.get_logger().info(f'📥 Received audio segment: {audio_length:.2f}s')
        
        # Convertește în numpy array
        audio_data = np.array(msg.data, dtype=np.int16)
        
        # DEBUG: arată amplitudinea audio
        audio_max = int(np.max(np.abs(audio_data)))
        audio_rms = int(np.sqrt(np.mean(audio_data.astype(np.float32)**2)))
        self.get_logger().info(f'🎵 Audio stats: max={audio_max}, rms={audio_rms}, samples={len(audio_data)}')
        
        # Salvează temporar ca WAV
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            temp_path = f.name
            with wave.open(f.name, 'wb') as wav:
                wav.setnchannels(channels)
                wav.setsampwidth(2)  # 16-bit = 2 bytes
                wav.setframerate(sample_rate)
                wav.writeframes(audio_data.tobytes())
        
        # DEBUG: salvează o copie pentru inspecție
        import shutil
        debug_path = '/tmp/asr_debug_last.wav'
        shutil.copy(temp_path, debug_path)
        self.get_logger().info(f'💾 Debug WAV saved: {debug_path}')
        
        try:
            # Transcrie cu Faster Whisper (fără VAD filter pentru debug)
            segments, info = self.model.transcribe(
                temp_path,
                language=self.language,
                beam_size=5,
                vad_filter=False,  # Dezactivat temporar pentru debug
            )
            
            # Combină segmentele
            text = " ".join(seg.text.strip() for seg in segments).strip()
            lang = info.language
            confidence = info.language_probability
            
            if text:
                self.get_logger().info(f'🧏 [{lang}] "{text}"')
                
                # Publică rezultat
                out = Transcription()
                out.text = text
                out.language = lang
                out.confidence = float(confidence)
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
    
    try:
        node = ASRNode()
        rclpy.spin(node)
    except RuntimeError as e:
        print(f'Failed to start ASR node: {e}')
    except KeyboardInterrupt:
        pass
    finally:
        rclpy.shutdown()


if __name__ == '__main__':
    main()
