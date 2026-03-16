#!/usr/bin/env python3
import os
import numpy as np
import sounddevice as sd
from openwakeword.model import Model
import time

# Configuration
MODEL_PATH = "/home/delia/voice_ros2/conversational_client/models/be_quiet.onnx"
RATE = 16000
CHUNK = 1280  # 80ms at 16kHz (required by openWakeWord)

def main():
    if not os.path.exists(MODEL_PATH):
        print(f"Error: Model file not found at {MODEL_PATH}")
        return

    print(f"Loading model: {MODEL_PATH}")
    oww_model = Model(wakeword_models=[MODEL_PATH], inference_framework="onnx")
    
    # Get model key
    model_key = list(oww_model.models.keys())[0]
    print(f"Model loaded successfully. Detecting for: {model_key}")

    print("\n--- Listening! Speak 'Be quiet' to see scores ---")
    print("Press Ctrl+C to stop.\n")

    def audio_callback(indata, frames, time, status):
        if status:
            print(f"Status: {status}")
        
        # indata is (frames, channels)
        audio_data = (indata[:, 0] * 32767).astype(np.int16)
        
        # Process
        prediction = oww_model.predict(audio_data)
        score = prediction[model_key]
        
        # Visual feedback
        bar_len = int(score * 50)
        bar = "█" * bar_len + "-" * (50 - bar_len)
        print(f"\r[{bar}] {score:.4f}", end="", flush=True)

    try:
        with sd.InputStream(samplerate=RATE, channels=1, callback=audio_callback, blocksize=CHUNK):
            while True:
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\nStopping...")

if __name__ == "__main__":
    main()
