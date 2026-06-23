#!/usr/bin/env python3
"""
enroll_speaker.py
Script for recording a user's voice (enrollment).

EXPLANATION:
- Asks for the person's name
- Records 5 seconds of audio from the microphone (16kHz, mono)
- Saves the file in voice_ros2/voices/enrollment/<name>.wav

Usage:
    python3 enroll_speaker.py
"""

import os
from pathlib import Path
import sys
import sounddevice as sd
import soundfile as sf
import numpy as np

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

SAMPLE_RATE = 16000           # Hz — standard for speech processing
DURATION = 5                  # recording seconds
CHANNELS = 1                  # mono


def _find_workspace_root() -> Path | None:
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


# Canonical enrollment path in this project: <workspace>/voices/enrollment
_workspace_root = _find_workspace_root()
ENROLLMENT_DIR = os.path.join(
    str(_workspace_root) if _workspace_root else os.getcwd(),
    'voices',
    'enrollment'
)


# ═══════════════════════════════════════════════════════════════════
# FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def ensure_enrollment_dir():
    """Create the enrollment folder if it doesn't exist."""
    os.makedirs(ENROLLMENT_DIR, exist_ok=True)
    print(f"📂 Enrollment folder: {ENROLLMENT_DIR}")


def get_speaker_name():
    """Ask the user for the speaker name."""
    print("\n" + "═" * 50)
    print("  🎤 ENROLLMENT — New voice registration")
    print("═" * 50)

    while True:
        name = input("\n👤 Enter name (e.g., Vale, Delia): ").strip()

        if not name:
            print("   ⚠️ Name cannot be empty!")
            continue

        # Normalize: lowercase for filename
        filename = name.lower().replace(' ', '_')
        wav_path = os.path.join(ENROLLMENT_DIR, f"{filename}.wav")

        # Check if it already exists
        if os.path.exists(wav_path):
            overwrite = input(f"   ⚠️ '{name}' already exists. Overwrite? (y/n): ").strip().lower()
            if overwrite != 'y':
                continue

        return name, filename, wav_path


def record_audio():
    """Record DURATION seconds of audio from the microphone."""
    print(f"\n🎙️  Get ready to speak for {DURATION} seconds...")
    print("   Speak clearly and naturally (you can say anything).")
    input("   Press ENTER when ready...")

    print(f"\n🔴 RECORDING... ({DURATION} seconds)")

    # Record audio
    audio = sd.rec(
        int(DURATION * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype='float32'
    )
    sd.wait()  # Wait for recording to finish

    print("⏹️  Recording complete!")

    return audio


def check_audio_quality(audio):
    """Simple audio quality check (not silence)."""
    rms = np.sqrt(np.mean(audio ** 2))

    if rms < 0.005:
        print("\n⚠️  WARNING: Audio seems very quiet!")
        print("   Check if the microphone is working correctly.")
        retry = input("   Do you want to re-record? (y/n): ").strip().lower()
        return retry != 'y'

    # Show audio level
    db = 20 * np.log10(max(rms, 1e-10))
    print(f"   📊 Audio level: {db:.1f} dB RMS")
    return True


def save_audio(audio, wav_path, name):
    """Save audio as a .wav file."""
    sf.write(wav_path, audio, SAMPLE_RATE)
    file_size = os.path.getsize(wav_path)
    print(f"\n✅ Saved: {wav_path}")
    print(f"   📁 Size: {file_size / 1024:.1f} KB")
    print(f"   👤 Speaker: {name}")
    print(f"   ⏱️  Duration: {DURATION}s | 📻 Sample rate: {SAMPLE_RATE}Hz")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    ensure_enrollment_dir()

    # 1. Ask for the name
    name, filename, wav_path = get_speaker_name()

    # 2. Record
    while True:
        audio = record_audio()

        # 3. Check quality
        if check_audio_quality(audio):
            break

    # 4. Save
    save_audio(audio, wav_path, name)

    # 5. Tips
    print("\n" + "─" * 50)
    print("💡 Next steps:")
    print("   1. For more stable recognition, you can re-record")
    print("      in a quieter environment.")
    print("   2. This is the correct script for manual enrollment.")
    print("      `speaker_manager.py` only verifies the database.")
    print("   3. Once you have enrollment for all speakers,")
    print("      start the ROS2 system and speaker_id_node will automatically")
    print("      use the database.")

    # List existing voices
    existing = [f.replace('.wav', '').capitalize()
                for f in os.listdir(ENROLLMENT_DIR) if f.endswith('.wav')]
    print(f"\n📋 Enrolled voices: {', '.join(existing)}")
    print("═" * 50)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️  Enrollment cancelled.")
        sys.exit(0)
