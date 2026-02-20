#!/usr/bin/env python3
"""
Compatibility wrapper for SpeakerManager.
"""
from conversational_client.speaker_manager import SpeakerManager, main  # noqa: F401

if __name__ == '__main__':
    import sys

    # Use XDG standard path for user data: ~/.local/share/voice_ros2/enrollment
    enrollment_path = os.path.join(
        os.path.expanduser('~'),
        '.local', 'share', 'voice_ros2', 'enrollment'
    )

    if not os.path.isdir(enrollment_path):
        print(f"❌ Folderul de enrollment nu există: {enrollment_path}")
        print("   Rulează mai întâi: python3 enroll_speaker.py")
        sys.exit(1)

    wav_count = len([f for f in os.listdir(enrollment_path) if f.endswith('.wav')])
    if wav_count == 0:
        print("❌ Niciun fișier .wav în enrollment. Rulează mai întâi: python3 enroll_speaker.py")
        sys.exit(1)

    print(f"\n📂 Testare SpeakerManager cu {wav_count} voci...\n")
    manager = SpeakerManager(enrollment_path)
    print(f"\n✅ Vorbitori încărcați: {manager.get_speakers()}")
    print("   SpeakerManager funcționează corect!")
