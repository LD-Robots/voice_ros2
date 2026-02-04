#!/usr/bin/env python3
"""
Test rapid pentru scorurile modelului stop_robot_oww.
Rulează și zii "stop robot" pentru a vedea scorurile în timp real.
"""
import pyaudio
import numpy as np
from openwakeword.model import Model as OWWModel
import os

# Calea către modele
MODELS_DIR = '/home/delia/voice_ros2/conversational_client/models'

def main():
    print("🎤 Încărcare modele...")
    
    # Încarcă toate modelele
    model_paths = [
        os.path.join(MODELS_DIR, 'hello_robot.onnx'),
        os.path.join(MODELS_DIR, 'stop_robot_oww.onnx'),
        os.path.join(MODELS_DIR, 'goodbye_robot.onnx'),
    ]
    
    # Verifică ce modele există
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
    
    # Încarcă modelul OWW
    model = OWWModel(wakeword_models=existing)
    print(f"\n✅ Modele încărcate: {list(model.models.keys())}")
    
    # Inițializează PyAudio
    audio = pyaudio.PyAudio()
    
    # Găsește ReSpeaker sau default
    device_index = None
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if 'ReSpeaker' in info['name'] and info['maxInputChannels'] > 0:
            device_index = i
            print(f"🎙️ Folosesc: {info['name']}")
            break
    
    if device_index is None:
        print("🎙️ Folosesc microfonul default")
    
    # Deschide stream
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
            # Citește audio
            data = stream.read(1280, exception_on_overflow=False)
            audio_chunk = np.frombuffer(data, dtype=np.int16)
            
            # Predicție
            prediction = model.predict(audio_chunk)
            
            # Afișează scoruri
            scores = []
            triggered = []
            for label, score_obj in prediction.items():
                if isinstance(score_obj, dict):
                    score = max(score_obj.values()) if score_obj else 0.0
                else:
                    score = float(score_obj)
                
                scores.append(f"{label}: {score:.3f}")
                
                # Verifică trigger
                if 'stop' in label.lower() and score >= 0.25:
                    triggered.append(f"🛑 {label}={score:.2f}")
                elif 'hello' in label.lower() and score >= 0.30:
                    triggered.append(f"👋 {label}={score:.2f}")
                elif 'goodbye' in label.lower() and score >= 0.50:
                    triggered.append(f"👋 {label}={score:.2f}")
            
            # Print pe o singură linie (overwrite)
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
