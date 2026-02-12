#!/usr/bin/env python3
"""
Test script for stop_keyword.onnx (PyTorch-based detector)
Shows real-time scores for detecting the word "stop"
"""
import pyaudio
import numpy as np
import os
from ament_index_python.packages import get_package_share_directory

from conversational_client.stop_keyword_detector import StopKeywordDetector

# Configuration
SAMPLE_RATE = 16000
CHUNK_SIZE = 1600  # 100ms
CLIENT_SHARE = get_package_share_directory('conversational_client')
MODEL_PATH = os.path.join(CLIENT_SHARE, 'voices', 'stop_keyword.onnx')

class SimpleLogger:
    def info(self, msg): print(f"[INFO] {msg}")
    def debug(self, msg): pass
    def warning(self, msg): print(f"[WARN] {msg}")

def main():
    print("🎤 Test Stop Keyword Detector (PyTorch)")
    print("=" * 60)
    
    # Check model
    if not os.path.exists(MODEL_PATH):
        print(f"❌ Model nu există: {MODEL_PATH}")
        return
    
    data_path = MODEL_PATH + ".data"
    if not os.path.exists(data_path):
        print(f"❌ Data file nu există: {data_path}")
        return
    
    print(f"✓ Model: {MODEL_PATH}")
    print(f"✓ Data: {data_path}")
    
    # Initialize detector
    logger = SimpleLogger()
    cfg = {
        'model_path': MODEL_PATH,
        'prob_threshold': 0.7,  # Lowered for testing
        'logit_margin': 0.3,
        'hits_required': 1,  # 1 to see every detection
        'debug': True,
    }
    
    try:
        detector = StopKeywordDetector(cfg, SAMPLE_RATE, logger)
    except Exception as e:
        print(f"❌ Eroare la inițializare: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print("\n" + "=" * 60)
    print("🗣️  Zii 'STOP' și urmărește scorurile!")
    print("    Threshold: prob >= 0.7, logit_margin >= 0.3")
    print("    Apasă Ctrl+C pentru a opri")
    print("=" * 60 + "\n")
    
    # PyAudio setup
    pa = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE
    )
    
    try:
        while True:
            # Read audio
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            pcm = np.frombuffer(data, dtype=np.int16)
            
            # Process through detector
            result = detector.process_block(pcm)
            
            if result:
                print(f"\n🛑 STOP DETECTED! probability={result.probability:.2f}, logits={result.logits}")
                print()
                
    except KeyboardInterrupt:
        print("\n\n🛑 Oprit.")
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

if __name__ == '__main__':
    main()
