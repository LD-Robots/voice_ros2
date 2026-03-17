#!/usr/bin/env python3
import os
import numpy as np
import sounddevice as sd
from openwakeword.model import Model
import time

# ─────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────
GAIN = 3.0 
CHANNEL_TO_TEST = 5  
CAPTURE_CHANNELS = 6 

MODELS = {
    "be_quiet": "/home/delia/voice_ros2/conversational_client/models/be_quiet.onnx",
    "stop_robot": "/home/delia/voice_ros2/conversational_client/models/stop_robot.onnx"
}
RATE = 16000
CHUNK = 1280 

def main():
    existing_models = []
    for name, path in MODELS.items():
        if os.path.exists(path):
            existing_models.append(path)
    
    if not existing_models:
        print("Error: No valid models found!")
        return

    print("--- Diagnostic Detection Test (Compact View) ---")
    oww_model = Model(wakeword_models=existing_models, inference_framework="onnx")
    
    device_index = None
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if ('ReSpeaker' in d['name'] or 'ArrayUAC10' in d['name']) and d['max_input_channels'] >= 6:
            device_index = i
            break

    print(f"Device: {devices[device_index]['name'] if device_index is not None else 'Default'}")
    print(f"CH: {CHANNEL_TO_TEST} | GAIN: {GAIN}x\n")

    def audio_callback(indata, frames, time_info, status):
        ch_data = indata[:, CHANNEL_TO_TEST]
        audio_f32 = ch_data * GAIN
        audio_i16 = (audio_f32 * 32767).clip(-32768, 32767).astype(np.int16)
        
        prediction = oww_model.predict(audio_i16)
        
        rms = np.sqrt(np.mean(audio_i16.astype(np.float32)**2))
        max_val = np.abs(audio_i16).max()
        clipping = "!" if max_val >= 32760 else " "
        
        # Compact display to avoid truncation
        # Format: [RMS] [B][Score] [S][Score]
        out = f"RMS:{int(rms):5d} {clipping} | "
        for model_key, score in prediction.items():
            short_key = "QUIET" if "quiet" in model_key else "STOP"
            out += f"{short_key}:{score:.3f}  "
        
        print(f"\r{out}", end="", flush=True)

    try:
        with sd.InputStream(samplerate=RATE, channels=CAPTURE_CHANNELS, device=device_index, 
                            callback=audio_callback, blocksize=CHUNK):
            while True:
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\nStopping...")

if __name__ == "__main__":
    main()
