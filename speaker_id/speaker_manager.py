#!/usr/bin/env python3
"""
Compatibility wrapper for SpeakerManager.
"""
import os
from pathlib import Path

from conversational_client.speaker_manager import SpeakerManager  # noqa: F401


try:
    from conversational_client.workspace_paths import find_workspace_root
except ImportError:  # standalone use, without the ROS package on PYTHONPATH
    def find_workspace_root() -> Path | None:
        """Fallback: identify the workspace by the packages it contains."""
        markers = (
            Path('conversational_client') / 'package.xml',
            Path('conversational_server') / 'package.xml',
        )
        override = os.environ.get('VOICE_ROS2_WS', '').strip()
        if override and Path(override).expanduser().is_dir():
            return Path(override).expanduser().resolve()
        for base in (Path(__file__).resolve(), Path.cwd().resolve()):
            for parent in [base] + list(base.parents):
                if all((parent / marker).is_file() for marker in markers):
                    return parent
        return None

if __name__ == '__main__':
    import sys

    workspace_root = find_workspace_root()
    enrollment_path = os.path.join(
        str(workspace_root) if workspace_root else os.getcwd(),
        'voices',
        'enrollment'
    )

    if not os.path.isdir(enrollment_path):
        print(f"❌ Enrollment folder does not exist: {enrollment_path}")
        print("   Run first: python3 enroll_speaker.py")
        sys.exit(1)

    wav_count = len([f for f in os.listdir(enrollment_path) if f.endswith('.wav')])
    if wav_count == 0:
        print("❌ No .wav files in enrollment. Run first: python3 enroll_speaker.py")
        sys.exit(1)

    print(f"\n📂 Checking SpeakerManager with {wav_count} enrolled voices...\n")
    print("ℹ️  This command validates the enrollment database only. It does not record a new voice.\n")
    manager = SpeakerManager(enrollment_path)
    print(f"\n✅ Loaded speakers: {manager.get_speakers()}")
    print("   SpeakerManager is working correctly.")
