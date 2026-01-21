# 🎤 Wake Word Detection Fix - Summary

## Problem
The "Hello Robot" wake word was not being detected despite the microphone capturing audio with good RMS levels.

## Root Causes Identified

### 1. Audio Backend Issue (PyAudio → sounddevice)
- **Problem**: PyAudio was producing very low audio levels (RMS ~30-55)
- **Solution**: Migrated `audio_capture_node.py` to use `sounddevice` library
- **File**: `conversational_client/audio_capture_node.py`

### 2. Wrong Audio Device
- **Problem**: The default ALSA device caused distorted "robot voice" audio
- **Solution**: Auto-select PulseAudio device (index 3) instead of PipeWire or default
- **File**: `conversational_client/audio_capture_node.py`
- **Code**: Added logic to search for 'pulse' device first, then 'pipewire'

### 3. Audio Format Mismatch (CRITICAL FIX)
- **Problem**: OpenWakeWord was receiving `float32` audio but expects `int16`
- **Solution**: Pass `int16` audio directly to `model.predict()` without conversion
- **File**: `conversational_client/wake_word_node.py`
- **Before**: `audio_float = audio_chunk.astype(np.float32) / 32768.0`
- **After**: `prediction = self.oww_model.predict(audio_chunk)` (int16 directly)

### 4. Minor: Message Field Error
- **Problem**: `WakeWord` message used `header.stamp`, not `timestamp`
- **Solution**: Changed `wake_event.timestamp` to `wake_event.header.stamp`
- **File**: `conversational_client/wake_word_node.py`

## Result
- Detection score went from **0.0008** to **0.67** ✅
- "Hello Robot" wake word now triggers reliably

## Files Modified
1. `conversational_client/audio_capture_node.py` - sounddevice + PulseAudio selection
2. `conversational_client/wake_word_node.py` - int16 format + header.stamp fix
