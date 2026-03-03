#!/usr/bin/env python3
"""
Compatibility wrapper for SpeakerManager.
"""
import os
from pathlib import Path

from conversational_client.speaker_manager import SpeakerManager, main  # noqa: F401


def _find_workspace_root() -> Path | None:
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None

if __name__ == '__main__':
    import sys

    workspace_root = _find_workspace_root()
    enrollment_path = os.path.join(
        str(workspace_root) if workspace_root else os.getcwd(),
        'voices',
        'enrollment'
    )

    if not os.path.isdir(enrollment_path):
        print(f"❌ Folderul de enrollment nu există: {enrollment_path}")
        print("   Rulează mai întâi: python3 enroll_speaker.py")
        sys.exit(1)

    wav_count = len([f for f in os.listdir(enrollment_path) if f.endswith('.wav')])
    if wav_count == 0:
        print("❌ Niciun fișier .wav în enrollment. Rulează mai întâi: python3 enroll_speaker.py")
        sys.exit(1)

    print(f"\n📂 Checking SpeakerManager with {wav_count} enrolled voices...\n")
    print("ℹ️  This command validates the enrollment database only. It does not record a new voice.\n")
    manager = SpeakerManager(enrollment_path)
    print(f"\n✅ Loaded speakers: {manager.get_speakers()}")
    print("   SpeakerManager is working correctly.")
