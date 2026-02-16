#!/usr/bin/env python3
"""
enroll_speaker.py
Script pentru înregistrarea vocii unui utilizator (enrollment).

EXPLICAȚIE:
- Întreabă numele persoanei
- Înregistrează 5 secunde de audio de la microfon (16kHz, mono)
- Salvează fișierul în ~/voice_ros2/voices/enrollment/<nume>.wav

Folosire:
    python3 enroll_speaker.py
"""

import os
import sys
import sounddevice as sd
import soundfile as sf
import numpy as np

# ═══════════════════════════════════════════════════════════════════
# CONFIGURARE
# ═══════════════════════════════════════════════════════════════════

SAMPLE_RATE = 16000           # Hz — standard pentru speech processing
DURATION = 5                  # secunde de înregistrare
CHANNELS = 1                  # mono
# Use XDG standard path: ~/.local/share/voice_ros2/enrollment
ENROLLMENT_DIR = os.path.join(
    os.path.expanduser('~'),
    '.local', 'share', 'voice_ros2', 'enrollment'
)


# ═══════════════════════════════════════════════════════════════════
# FUNCȚII
# ═══════════════════════════════════════════════════════════════════

def ensure_enrollment_dir():
    """Creează folderul de enrollment dacă nu există."""
    os.makedirs(ENROLLMENT_DIR, exist_ok=True)
    print(f"📂 Folder enrollment: {ENROLLMENT_DIR}")


def get_speaker_name():
    """Cere numele vorbitorului de la utilizator."""
    print("\n" + "═" * 50)
    print("  🎤 ENROLLMENT — Înregistrare voce nouă")
    print("═" * 50)

    while True:
        name = input("\n👤 Introdu numele (ex: Vale, Delia): ").strip()

        if not name:
            print("   ⚠️ Numele nu poate fi gol!")
            continue

        # Normalizează: litere mici pentru fișier
        filename = name.lower().replace(' ', '_')
        wav_path = os.path.join(ENROLLMENT_DIR, f"{filename}.wav")

        # Verifică dacă există deja
        if os.path.exists(wav_path):
            overwrite = input(f"   ⚠️ '{name}' există deja. Suprascrii? (d/n): ").strip().lower()
            if overwrite != 'd':
                continue

        return name, filename, wav_path


def record_audio():
    """Înregistrează DURATION secunde de audio de la microfon."""
    print(f"\n🎙️  Pregătește-te să vorbești {DURATION} secunde...")
    print("   Vorbește clar și natural (poți spune orice).")
    input("   Apasă ENTER când ești gata...")

    print(f"\n🔴 ÎNREGISTREZ... ({DURATION} secunde)")

    # Înregistrează audio
    audio = sd.rec(
        int(DURATION * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype='float32'
    )
    sd.wait()  # Așteaptă finalizarea înregistrării

    print("⏹️  Înregistrare completă!")

    return audio


def check_audio_quality(audio):
    """Verificare simplă a calității audio (nu e tăcere)."""
    rms = np.sqrt(np.mean(audio ** 2))

    if rms < 0.005:
        print("\n⚠️  ATENȚIE: Audio-ul pare foarte silențios!")
        print("   Verifică dacă microfonul funcționează corect.")
        retry = input("   Vrei să reînregistrezi? (d/n): ").strip().lower()
        return retry != 'd'

    # Afișează nivelul audio
    db = 20 * np.log10(max(rms, 1e-10))
    print(f"   📊 Nivel audio: {db:.1f} dB RMS")
    return True


def save_audio(audio, wav_path, name):
    """Salvează audio-ul ca fișier .wav."""
    sf.write(wav_path, audio, SAMPLE_RATE)
    file_size = os.path.getsize(wav_path)
    print(f"\n✅ Salvat: {wav_path}")
    print(f"   📁 Dimensiune: {file_size / 1024:.1f} KB")
    print(f"   👤 Vorbitor: {name}")
    print(f"   ⏱️  Durată: {DURATION}s | 📻 Sample rate: {SAMPLE_RATE}Hz")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    ensure_enrollment_dir()

    # 1. Cere numele
    name, filename, wav_path = get_speaker_name()

    # 2. Înregistrează
    while True:
        audio = record_audio()

        # 3. Verifică calitatea
        if check_audio_quality(audio):
            break

    # 4. Salvează
    save_audio(audio, wav_path, name)

    # 5. Sugestii
    print("\n" + "─" * 50)
    print("💡 Pași următori:")
    print("   1. Pentru rezultate mai bune, poți înregistra din nou")
    print("      într-un mediu mai silențios.")
    print("   2. După ce ai enrollment pentru toți vorbitorii,")
    print("      pornește sistemul ROS2 și speaker_id_node va folosi")
    print("      automat baza de date.")

    # Enumeră vocile existente
    existing = [f.replace('.wav', '').capitalize()
                for f in os.listdir(ENROLLMENT_DIR) if f.endswith('.wav')]
    print(f"\n📋 Voci înregistrate: {', '.join(existing)}")
    print("═" * 50)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️  Enrollment anulat.")
        sys.exit(0)
