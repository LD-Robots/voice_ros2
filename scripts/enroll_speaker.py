#!/usr/bin/env python3
"""
enroll_speaker.py
Script for recording a user's voice (enrollment).

Modified for organization repo:
- Saves files in conversational_client/voices/enrollment/
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

SAMPLE_RATE = 16000           # Hz
DURATION = 5                  # recording seconds
CHANNELS = 1                  # mono


def _find_workspace_root() -> Path | None:
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


_workspace_root = _find_workspace_root()
# NEW PATH: inside conversational_client package
ENROLLMENT_DIR = os.path.join(
    str(_workspace_root) if _workspace_root else os.getcwd(),
    'conversational_client',
    'voices',
    'enrollment'
)


def ensure_enrollment_dir():
    os.makedirs(ENROLLMENT_DIR, exist_ok=True)
    print(f"📂 Folder enrollment: {ENROLLMENT_DIR}")


def get_speaker_name():
    print("\n" + "═" * 50)
    print("  🎤 ENROLLMENT — Înregistrare voce nouă")
    print("═" * 50)

    while True:
        name = input("\n👤 Introdu numele (ex: Vale, Delia): ").strip()

        if not name:
            print("   ⚠️ Numele nu poate fi gol!")
            continue

        filename = name.lower().replace(' ', '_')
        wav_path = os.path.join(ENROLLMENT_DIR, f"{filename}.wav")

        if os.path.exists(wav_path):
            overwrite = input(f"   ⚠️ '{name}' există deja. Suprascrii? (d/n): ").strip().lower()
            if overwrite != 'd':
                continue

        return name, filename, wav_path


def record_audio():
    print(f"\n🎙️  Pregătește-te să vorbești {DURATION} secunde...")
    print("   Vorbește clar și natural.")
    input("   Apasă ENTER când ești gata...")

    print(f"\n🔴 ÎNREGISTREZ...")
    audio = sd.rec(int(DURATION * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32')
    sd.wait()
    print("⏹️  Înregistrare completă!")
    return audio


def check_audio_quality(audio):
    rms = np.sqrt(np.mean(audio ** 2))
    if rms < 0.005:
        print("\n⚠️  ATENȚIE: Audio-ul pare foarte silențios!")
        retry = input("   Vrei să reînregistrezi? (d/n): ").strip().lower()
        return retry != 'd'
    return True


def save_audio(audio, wav_path, name):
    sf.write(wav_path, audio, SAMPLE_RATE)
    print(f"\n✅ Salvat: {wav_path}")
    print(f"   👤 Vorbitor: {name}")


def main():
    ensure_enrollment_dir()
    name, filename, wav_path = get_speaker_name()
    while True:
        audio = record_audio()
        if check_audio_quality(audio):
            break
    save_audio(audio, wav_path, name)
    print("\n" + "─" * 50)
    print("💡 După enrollment, speaker_id_node va folosi automat noua voce.")
    print("═" * 50)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️  Enrollment anulat.")
        sys.exit(0)
