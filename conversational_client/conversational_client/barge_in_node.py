#!/usr/bin/env python3
"""
barge_in_node.py
Detectează cuvântul "stop" și oprește TTS-ul.

EXPLICAȚIE:
- Ascultă audio pe /audio_raw (de la audio_capture_node)
- Folosește modelul ONNX stop_keyword.onnx pentru a detecta "stop"
- Când detectează "stop", publică pe /stop_playback pentru a opri audio_playback_node

Barge-in funcționează DOAR când user-ul zice "stop", nu când vorbește pur și simplu.
"""

# ═══════════════════════════════════════════════════════════════════
# IMPORTURI
# ═══════════════════════════════════════════════════════════════════

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import Audio
from std_msgs.msg import Bool
import numpy as np
import os
import math
from dataclasses import dataclass
from typing import Optional, Tuple

# Pentru a găsi path-ul pachetului ROS2
from ament_index_python.packages import get_package_share_directory

# ONNX Runtime pentru inferență
try:
    import onnxruntime as ort
    ONNX_AVAILABLE = True
except ImportError:
    ONNX_AVAILABLE = False
    ort = None
    print("⚠️ onnxruntime not installed. Run: pip install onnxruntime")

# Torch/Torchaudio pentru Mel Spectrogram
try:
    import torch
    import torchaudio
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    torch = None
    torchaudio = None
    print("⚠️ torch/torchaudio not installed. Run: pip install torch torchaudio")


# ═══════════════════════════════════════════════════════════════════
# DATACLASS PENTRU REZULTAT
# ═══════════════════════════════════════════════════════════════════

@dataclass
class StopDetectionResult:
    """Rezultatul detectării cuvântului stop."""
    probability: float
    logits: Tuple[float, float]  # (other, stop)


# ═══════════════════════════════════════════════════════════════════
# CLASA NODULUI
# ═══════════════════════════════════════════════════════════════════

class BargeInNode(Node):
    """
    Nod ROS2 pentru stop keyword detection.
    
    Funcționare:
    1. Primește audio pe /audio_raw (de la audio_capture_node)
    2. Procesează cu modelul ONNX stop_keyword.onnx (Mel Spectrogram)
    3. Când detectează "stop", publică True pe /stop_playback
    """
    
    def __init__(self):
        super().__init__('barge_in_node')
        
        # ─────────────────────────────────────────────────────────
        # PATH CĂTRE MODEL - din pachetul ROS2
        # ─────────────────────────────────────────────────────────
        pkg_share = get_package_share_directory('conversational_client')
        default_model_path = os.path.join(pkg_share, 'models', 'stop_robot.onnx')
        
        # ─────────────────────────────────────────────────────────
        # PARAMETRI
        # ─────────────────────────────────────────────────────────
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('model_path', default_model_path)
        self.declare_parameter('logit_margin', 2.5)      # Diferență minimă între logits
        self.declare_parameter('prob_threshold', 0.96)   # Probabilitate minimă pentru "stop"
        self.declare_parameter('hits_required', 2)       # Detecții consecutive necesare
        self.declare_parameter('debug', False)
        
        self.sample_rate = self.get_parameter('sample_rate').value
        self.model_path = self.get_parameter('model_path').value
        self.logit_margin = self.get_parameter('logit_margin').value
        self.prob_threshold = self.get_parameter('prob_threshold').value
        self.hits_required = self.get_parameter('hits_required').value
        self.debug = self.get_parameter('debug').value
        
        # Modelul așteaptă input [1, 16, 96]
        # Cu hop_length=160 și 96 frames: 96 * 160 = 15360 samples (~0.96s la 16kHz)
        self.n_mels = 16
        self.hop_length = 160
        self.n_frames = 96
        self.frame = self.hop_length * self.n_frames  # 15360 samples
        # Hop = jumătate din frame pentru overlap
        self.hop = self.frame // 2
        
        # ─────────────────────────────────────────────────────────
        # BUFFER AUDIO
        # ─────────────────────────────────────────────────────────
        self._buf = np.zeros(self.frame, dtype=np.float32)
        self._buf_filled = False
        self._filled = 0
        self._stride = 0
        self._consecutive_hits = 0
        
        # ─────────────────────────────────────────────────────────
        # SUBSCRIBER - primim audio de la microfon
        # ─────────────────────────────────────────────────────────
        self.audio_sub = self.create_subscription(
            Audio,
            '/audio_raw',
            self.audio_callback,
            10
        )
        
        # ─────────────────────────────────────────────────────────
        # PUBLISHER - trimitem comandă de stop
        # ─────────────────────────────────────────────────────────
        self.stop_pub = self.create_publisher(Bool, '/stop_playback', 10)
        
        # ─────────────────────────────────────────────────────────
        # INIȚIALIZARE MODEL ONNX
        # ─────────────────────────────────────────────────────────
        self.session = None
        self._mel = None
        self._db = None
        
        if ONNX_AVAILABLE and TORCH_AVAILABLE:
            try:
                if not os.path.exists(self.model_path):
                    self.get_logger().error(f'❌ Model not found: {self.model_path}')
                else:
                    # Configurare ONNX Runtime
                    so = ort.SessionOptions()
                    so.intra_op_num_threads = 1
                    self.session = ort.InferenceSession(self.model_path, so)
                    self.input_name = self.session.get_inputs()[0].name
                    
                    # Transformări audio pentru Mel Spectrogram
                    # Modelul așteaptă [1, 16, 96] - 16 mel bins, 96 time frames
                    self._mel = torchaudio.transforms.MelSpectrogram(
                        sample_rate=self.sample_rate,
                        n_mels=self.n_mels,
                        hop_length=self.hop_length,
                        n_fft=400,  # 25ms window
                        win_length=400
                    )
                    self._db = torchaudio.transforms.AmplitudeToDB()
                    
                    self.get_logger().info(
                        f'🛑 Barge-in Node started - Stop Keyword Detection active\n'
                        f'   Model: {os.path.basename(self.model_path)}\n'
                        f'   Threshold: {self.prob_threshold}, Hits required: {self.hits_required}'
                    )
            except Exception as e:
                self.get_logger().error(f'❌ Failed to load model: {e}')
                self.session = None
        else:
            self.get_logger().warn('⚠️ ONNX/Torch not available - running in dummy mode')
    
    # ═══════════════════════════════════════════════════════════════════
    # CALLBACK AUDIO
    # ═══════════════════════════════════════════════════════════════════
    def audio_callback(self, msg: Audio):
        """Procesează fiecare chunk de audio pentru detectare stop keyword."""
        
        if self.session is None:
            return
        
        # Convertește la numpy array și normalizează la float32
        pcm_i16 = np.array(msg.data, dtype=np.int16)
        chunk = pcm_i16.astype(np.float32) / 32768.0
        
        # Adaugă chunk-ul în buffer
        need = len(chunk)
        if need >= self.frame:
            self._buf[:] = chunk[-self.frame:]
            self._filled = self.frame
            self._buf_filled = True
        else:
            self._buf = np.roll(self._buf, -need)
            self._buf[-need:] = chunk
            self._filled = min(self.frame, self._filled + need)
            self._buf_filled = self._filled >= self.frame
        
        # Procesează la fiecare hop
        self._stride += len(chunk)
        while self._stride >= self.hop:
            self._stride -= self.hop
            if not self._buf_filled:
                continue
            
            result = self._run_detector(self._buf)
            if result:
                self._trigger_stop(result)
                break
    
    # ═══════════════════════════════════════════════════════════════════
    # DETECTARE STOP KEYWORD
    # ═══════════════════════════════════════════════════════════════════
    def _run_detector(self, chunk: np.ndarray) -> Optional[StopDetectionResult]:
        """Rulează modelul ONNX pe chunk-ul audio."""
        
        # Extrage features (Mel Spectrogram)
        feats = self._featurize(chunk)
        
        # Inferență ONNX - modelul returnează un scalar [1, 1]
        output = self.session.run(None, {self.input_name: feats})[0]
        
        # Modelul returnează probabilitatea pentru "stop" direct
        p_stop = float(output[0][0])
        
        # Verifică dacă e hit bazat pe threshold
        raw_hit = p_stop >= self.prob_threshold
        
        if raw_hit:
            self._consecutive_hits += 1
        else:
            self._consecutive_hits = 0
        
        if self.debug:
            self.get_logger().info(
                f'[STOP-KWS] p_stop={p_stop:.3f} hits={self._consecutive_hits}/{self.hits_required}'
            )
        
        # Returnează rezultat doar dacă avem suficiente hit-uri consecutive
        if self._consecutive_hits >= self.hits_required:
            self._consecutive_hits = 0
            return StopDetectionResult(probability=p_stop, logits=(1-p_stop, p_stop))
        
        return None
    
    def _featurize(self, chunk: np.ndarray) -> np.ndarray:
        """Convertește audio la Mel Spectrogram normalizat [1, 16, 96]."""
        # Input: chunk cu self.frame samples
        t = torch.from_numpy(chunk[np.newaxis, :]).float()
        mel = self._mel(t)  # Output: [1, n_mels, n_frames]
        mel_db = self._db(mel)
        
        # Normalizare
        mel_db = (mel_db - mel_db.mean()) / (mel_db.std() + 1e-9)
        
        # Asigură-te că avem exact 96 frames (padding sau truncare)
        n_frames = mel_db.shape[-1]
        if n_frames < self.n_frames:
            # Padding cu zerouri
            pad = torch.zeros(1, self.n_mels, self.n_frames - n_frames)
            mel_db = torch.cat([mel_db, pad], dim=-1)
        elif n_frames > self.n_frames:
            # Truncare
            mel_db = mel_db[:, :, :self.n_frames]
        
        # Output: [1, 16, 96] - exact ce așteaptă modelul
        feats = mel_db.numpy().astype(np.float32)
        return feats
    
    @staticmethod
    def _softmax2(a: float, b: float) -> Tuple[float, float]:
        """Softmax pentru 2 valori."""
        m = max(a, b)
        ea = math.exp(a - m)
        eb = math.exp(b - m)
        s = ea + eb
        if s == 0.0:
            return 0.5, 0.5
        return ea / s, eb / s
    
    # ═══════════════════════════════════════════════════════════════════
    # TRIGGER STOP
    # ═══════════════════════════════════════════════════════════════════
    def _trigger_stop(self, result: StopDetectionResult):
        """Declanșează stop când detectăm keyword-ul."""
        
        self.get_logger().info(
            f'🛑 STOP detected! Probability: {result.probability:.2f} - Stopping playback'
        )
        
        # Publică pe /stop_playback
        msg = Bool()
        msg.data = True
        self.stop_pub.publish(msg)
        
        # Reset buffer
        self._buf[:] = 0.0
        self._buf_filled = False
        self._filled = 0


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main(args=None):
    rclpy.init(args=args)
    node = BargeInNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
