#!/usr/bin/env python3
"""
Quick test for stop_robot_oww model scores.
Run and say "stop robot" to see scores in real time.
"""
import pyaudio
import numpy as np
from openwakeword.model import Model as OWWModel
import os
from ament_index_python.packages import get_package_share_directory

# Path to models
CLIENT_SHARE = get_package_share_directory('conversational_client')
MODELS_DIR = os.path.join(CLIENT_SHARE, 'models')

def main():
    print("🎤 Încărcare modele...")
    
    # Load all models
    model_paths = [
        os.path.join(MODELS_DIR, 'hello_robot.onnx'),
        os.path.join(MODELS_DIR, 'stop_robot.onnx'),
        os.path.join(MODELS_DIR, 'goodbye_robot.onnx'),
    ]
    
    # Check which models exist
    existing = []
    for p in model_paths:
        if os.path.exists(p):
            print(f"  ✓ {os.path.basename(p)}")
            existing.append(p)
        else:
            print(f"  ✗ {os.path.basename(p)} - NU EXISTĂ!")
    
    if not existing:
        print("❌ Niciun model găsit!")
        return
    
    # Load the OWW model
    model = OWWModel(wakeword_models=existing)
    print(f"\n✅ Modele încărcate: {list(model.models.keys())}")
    
    # Initialize PyAudio
    audio = pyaudio.PyAudio()
    
    # Find ReSpeaker or fallback to default
    device_index = None
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if 'ReSpeaker' in info['name'] and info['maxInputChannels'] > 0:
            device_index = i
            print(f"🎙️ Folosesc: {info['name']}")
            break
    
    if device_index is None:
        print("🎙️ Folosesc microfonul default")
    
    # Open stream
    stream = audio.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=16000,
        input=True,
        input_device_index=device_index,
        frames_per_buffer=1280  # 80ms chunks
    )
    
    print("\n" + "="*60)
    print("🗣️  Zii 'STOP ROBOT' și urmărește scorurile!")
    print("    Threshold pentru barge-in: 0.25")
    print("    Apasă Ctrl+C pentru a opri")
    print("="*60 + "\n")
    
    try:
        while True:
            # Read audio
            data = stream.read(1280, exception_on_overflow=False)
            audio_chunk = np.frombuffer(data, dtype=np.int16)
            
            # Prediction
            prediction = model.predict(audio_chunk)
            
            # Display scores
            scores = []
            triggered = []
            for label, score_obj in prediction.items():
                if isinstance(score_obj, dict):
                    score = max(score_obj.values()) if score_obj else 0.0
                else:
                    score = float(score_obj)
                
                scores.append(f"{label}: {score:.3f}")
                
                # Check trigger
                if 'stop' in label.lower() and score >= 0.25:
                    triggered.append(f"🛑 {label}={score:.2f}")
                elif 'hello' in label.lower() and score >= 0.30:
                    triggered.append(f"👋 {label}={score:.2f}")
                elif 'goodbye' in label.lower() and score >= 0.50:
                    triggered.append(f"👋 {label}={score:.2f}")
            
            # Print on a single line (overwrite)
            line = " | ".join(scores)
            if triggered:
                line += f"  <<<  TRIGGERED: {', '.join(triggered)}"
            print(f"\r{line}                    ", end='', flush=True)
            
    except KeyboardInterrupt:
        print("\n\n🛑 Oprit.")
    finally:
        stream.stop_stream()
        stream.close()
        audio.terminate()

if __name__ == '__main__':
    main()
