#!/usr/bin/env python3
"""
Stop Keyword Detector - Detects the "stop" command during TTS.

Uses an ONNX model to detect when the user says "stop".
Runs in parallel with TTS and stops immediately when detected.

Subscribes to: /audio_raw (Audio)
Publishes to: 
  - /tts_stop (Bool) - stop TTS
  - /end_session (Bool) - end session
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import Bool
import numpy as np

# ONNX Runtime
try:
    import onnxruntime as ort
    ONNX_AVAILABLE = True
except ImportError:
    ort = None
    ONNX_AVAILABLE = False
    print("⚠️ onnxruntime not installed. Run: pip install onnxruntime")

# Torch + Torchaudio for Mel Spectrogram
try:
    import torch
    import torchaudio
    TORCH_AVAILABLE = True
except ImportError:
    torch = None
    torchaudio = None
    TORCH_AVAILABLE = False
    print("⚠️ torch/torchaudio not installed. Run: pip install torch torchaudio")


@dataclass
class StopDetectionResult:
    probability: float
    logits: Tuple[float, float]


class StopKeywordNode(Node):
    """
    ROS2 node for real-time "stop" command detection.
    """
    
    def __init__(self):
        super().__init__('stop_keyword_node')
        
        # Parameters
        self.declare_parameter('enabled', True)
        self.declare_parameter('model_path', '')
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('frame_samples', 16000)  # 1s window
        self.declare_parameter('hop_samples', 8000)     # 0.5s hop
        self.declare_parameter('logit_margin', 2.5)
        self.declare_parameter('prob_threshold', 0.95)
        self.declare_parameter('hits_required', 1)
        self.declare_parameter('debug', False)
        
        self.enabled = self.get_parameter('enabled').value
        model_path_str = self.get_parameter('model_path').value
        self.sample_rate = self.get_parameter('sample_rate').value
        self.frame = self.get_parameter('frame_samples').value
        self.hop = self.get_parameter('hop_samples').value
        self.logit_margin = self.get_parameter('logit_margin').value
        self.prob_threshold = self.get_parameter('prob_threshold').value
        self.hits_required = self.get_parameter('hits_required').value
        self.debug = self.get_parameter('debug').value
        
        if not self.enabled:
            self.get_logger().info('🛑 Stop Keyword Detector DISABLED')
            return
        
        if not ONNX_AVAILABLE:
            self.get_logger().error('onnxruntime not available!')
            self.enabled = False
            return
            
        if not TORCH_AVAILABLE:
            self.get_logger().error('torch/torchaudio not available!')
            self.enabled = False
            return
        
        # Check model path
        if not model_path_str:
            self.get_logger().warn('No model_path specified, stop keyword detector disabled')
            self.enabled = False
            return
            
        model_path = Path(model_path_str).expanduser()
        if not model_path.exists():
            self.get_logger().error(f'Model not found: {model_path}')
            self.enabled = False
            return
        
        # Initialize ONNX
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), so)
        self.input_name = self.session.get_inputs()[0].name
        
        # Mel Spectrogram transform
        self._mel = torchaudio.transforms.MelSpectrogram(
            sample_rate=self.sample_rate, 
            n_mels=40
        )
        self._db = torchaudio.transforms.AmplitudeToDB()
        
        # Buffer and state
        self._buf = np.zeros(self.frame, dtype=np.float32)
        self._buf_filled = False
        self._filled = 0
        self._stride = 0
        self._consecutive_hits = 0
        
        # Subscribers
        self.audio_sub = self.create_subscription(
            Audio,
            'audio_raw',
            self.audio_callback,
            10
        )
        
        # Publishers
        self.tts_stop_pub = self.create_publisher(Bool, 'tts_stop', 10)
        self.end_session_pub = self.create_publisher(Bool, 'end_session', 10)
        
        self.get_logger().info(
            f'🛑 Stop Keyword Detector activ: model={model_path.name}, '
            f'frame={self.frame}, hop={self.hop}, hits_required={self.hits_required}'
        )
    
    def audio_callback(self, msg: Audio):
        """Process audio for stop keyword detection."""
        if not self.enabled:
            return
        
        # Convert to float32
        pcm_i16 = np.array(msg.data, dtype=np.int16)
        if pcm_i16.size == 0:
            return
        
        chunk = pcm_i16.astype(np.float32) / 32768.0
        need = len(chunk)
        
        # Update buffer
        if need >= self.frame:
            self._buf[:] = chunk[-self.frame:]
            self._filled = self.frame
            self._buf_filled = True
        else:
            self._buf = np.roll(self._buf, -need)
            self._buf[-need:] = chunk
            self._filled = min(self.frame, self._filled + need)
            self._buf_filled = self._filled >= self.frame
        
        self._stride += len(chunk)
        
        # Run detector at each hop
        while self._stride >= self.hop:
            self._stride -= self.hop
            if not self._buf_filled:
                continue
            
            result = self._run_detector(self._buf)
            if result:
                self._on_stop_detected(result)
                break
    
    def _run_detector(self, chunk: np.ndarray) -> Optional[StopDetectionResult]:
        """Run the ONNX model on an audio chunk."""
        feats = self._featurize(chunk)
        logits = self.session.run(None, {self.input_name: feats})[0][0]
        
        other = float(logits[0])
        stop = float(logits[1])
        p_other, p_stop = self._softmax2(other, stop)
        
        raw_hit = (stop - other) >= self.logit_margin and p_stop >= self.prob_threshold
        
        if raw_hit:
            self._consecutive_hits += 1
        else:
            self._consecutive_hits = 0
        
        if self.debug:
            self.get_logger().debug(
                f'[STOP-KWS] logits other={other:.3f} stop={stop:.3f} '
                f'p_stop={p_stop:.3f} hits={self._consecutive_hits}/{self.hits_required}'
            )
        
        if self._consecutive_hits >= self.hits_required:
            self._consecutive_hits = 0
            return StopDetectionResult(probability=p_stop, logits=(other, stop))
        
        return None
    
    def _on_stop_detected(self, result: StopDetectionResult):
        """Action when stop keyword is detected."""
        self.get_logger().info(f'🛑 STOP detected! prob={result.probability:.2f}')
        
        # Stop TTS immediately
        stop_msg = Bool()
        stop_msg.data = True
        self.tts_stop_pub.publish(stop_msg)
        
        # Also send end_session
        self.end_session_pub.publish(stop_msg)
        
        # Reset
        self.reset()
    
    def reset(self):
        """Reset detector state."""
        self._stride = 0
        self._consecutive_hits = 0
        self._buf[:] = 0.0
        self._buf_filled = False
        self._filled = 0
    
    def _featurize(self, chunk: np.ndarray) -> np.ndarray:
        """Extract Mel Spectrogram features for ONNX."""
        t = torch.from_numpy(chunk[np.newaxis, :])
        mel = self._mel(t)
        mel_db = self._db(mel)
        mel_db = (mel_db - mel_db.mean()) / (mel_db.std() + 1e-9)
        mel_db = mel_db.unsqueeze(0)
        feats = mel_db.numpy().astype(np.float32)
        return feats
    
    @staticmethod
    def _softmax2(a: float, b: float) -> Tuple[float, float]:
        """Softmax for 2 values."""
        m = max(a, b)
        ea = math.exp(a - m)
        eb = math.exp(b - m)
        s = ea + eb
        if s == 0.0:
            return 0.5, 0.5
        return ea / s, eb / s


def main(args=None):
    rclpy.init(args=args)
    node = StopKeywordNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
    try:
        rclpy.shutdown()
    except Exception:
        pass


if __name__ == '__main__':
    main()
