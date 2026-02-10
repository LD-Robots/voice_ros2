#!/usr/bin/env python3
"""
Test script pentru stop_keyword.onnx (PyTorch-based detector)
Arată scorurile în timp real pentru detectarea cuvântului "stop"
"""
import pyaudio
import numpy as np
import sys
import os

# Adaugă calea la stop_keyword_detector
sys.path.insert(0, '/home/delia/voice_ros2/conversational_client/conversational_client')
from stop_keyword_detector import StopKeywordDetector

# Configurație
SAMPLE_RATE = 16000
CHUNK_SIZE = 1600  # 100ms
MODEL_PATH = '/home/delia/voice_ros2/conversational_client/models/stop_keyword.onnx'

class SimpleLogger:
    def info(self, msg): print(f"[INFO] {msg}")
    def debug(self, msg): pass
    def warning(self, msg): print(f"[WARN] {msg}")

def main():
    print("🎤 Test Stop Keyword Detector (PyTorch)")
    print("=" * 60)
    
    # Verifică modelul
    if not os.path.exists(MODEL_PATH):
        print(f"❌ Model nu există: {MODEL_PATH}")
        return
    
    data_path = MODEL_PATH + ".data"
    if not os.path.exists(data_path):
        print(f"❌ Data file nu există: {data_path}")
        return
    
    print(f"✓ Model: {MODEL_PATH}")
    print(f"✓ Data: {data_path}")
    
    # Inițializează detectorul
    logger = SimpleLogger()
    cfg = {
        'model_path': MODEL_PATH,
        'prob_threshold': 0.7,  # Scăzut pentru testing
        'logit_margin': 0.3,
        'hits_required': 1,  # 1 pentru a vedea fiecare detectare
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
            # Citește audio
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            pcm = np.frombuffer(data, dtype=np.int16)
            
            # Procesează prin detector
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
