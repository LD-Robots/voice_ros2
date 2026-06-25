#!/usr/bin/env python3
"""
speaker_manager.py
Manages the voice fingerprint database.

EXPLANATION:
- Loads .wav files from the enrollment folder
- Computes embeddings using SpeechBrain (ECAPA-TDNN)
- Identifies the speaker via cosine similarity

Used by speaker_id_node.py (Developer B — Delia).
"""

import os
from pathlib import Path
import inspect
import numpy as np
import soundfile as sf
import torch
import torchaudio
# ─────────────────────────────────────────────────────────────────
# FIX: torchaudio 2.6+ removed list_audio_backends(),
#      but speechbrain 1.0.x still calls it at import.
# ─────────────────────────────────────────────────────────────────
if not hasattr(torchaudio, 'list_audio_backends'):
    torchaudio.list_audio_backends = lambda: ['ffmpeg']

# ─────────────────────────────────────────────────────────────────
# FIX: huggingface_hub >= 1.0 removed `use_auth_token` and some
# download kwargs, but speechbrain 1.0.x still forwards them.
# Normalize those kwargs before SpeechBrain imports/calls the hub.
# ─────────────────────────────────────────────────────────────────
try:
    import huggingface_hub

    _hf_hub_download = huggingface_hub.hf_hub_download
    _hf_hub_download_signature = inspect.signature(_hf_hub_download)

    if 'use_auth_token' not in _hf_hub_download_signature.parameters:
        def _compat_hf_hub_download(*args, **kwargs):
            if 'use_auth_token' in kwargs and 'token' not in kwargs:
                kwargs['token'] = kwargs.pop('use_auth_token')
            else:
                kwargs.pop('use_auth_token', None)

            for deprecated_kwarg in (
                'resume_download',
                'force_filename',
                'local_dir_use_symlinks',
            ):
                kwargs.pop(deprecated_kwarg, None)

            return _hf_hub_download(*args, **kwargs)

        huggingface_hub.hf_hub_download = _compat_hf_hub_download
except Exception:
    pass

from speechbrain.inference.speaker import EncoderClassifier

from .speaker_match_utils import SpeakerMatchResult, select_speaker_match


class SpeakerManager:
    """
    Voice fingerprint manager.

    Usage:
        manager = SpeakerManager('/path/to/enrollment/')
        speaker = manager.identify(audio_float32_array)
    """

    def __init__(self, enrollment_dir, threshold=0.25, min_margin=0.08):
        """
        Initialize SpeakerManager.

        Args:
            enrollment_dir: Path to the folder with enrollment .wav files
                           (e.g., voice_ros2/voices/enrollment/)
                           Each file must be named after the person: vale.wav, delia.wav
            threshold: Minimum cosine similarity for identification (default: 0.25)
        """
        self.enrollment_dir = enrollment_dir
        self.threshold = threshold
        self.min_margin = max(0.0, float(min_margin))
        self.model_cache_dir = os.path.expanduser(
            "~/.cache/speechbrain/spkrec-ecapa-voxceleb"
        )

        os.makedirs(self.model_cache_dir, exist_ok=True)
        self._ensure_placeholder_custom_module()

        # ─────────────────────────────────────────────────────────
        # Limit PyTorch threads to avoid CPU starvation/contention with other ROS2 nodes
        # ─────────────────────────────────────────────────────────
        try:
            torch.set_num_threads(2)
            if hasattr(torch, 'set_num_interop_threads'):
                torch.set_num_interop_threads(2)
            print("🧵 PyTorch thread limit set to 2")
        except Exception as e:
            print(f"⚠️ Failed to set PyTorch thread limit: {e}")

        # ─────────────────────────────────────────────────────────
        # Load SpeechBrain ECAPA-TDNN model
        # ─────────────────────────────────────────────────────────
        print("🔄 Se încarcă modelul SpeechBrain ECAPA-TDNN...")
        self.classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=self.model_cache_dir,
            run_opts={"device": "cpu"}
        )
        print("✅ Model ECAPA-TDNN încărcat!")

        # ─────────────────────────────────────────────────────────
        # Database: {"Vale": embedding_tensor, "Delia": embedding_tensor}
        # ─────────────────────────────────────────────────────────
        self.speaker_db = {}
        self._load_enrollment()

    # ═══════════════════════════════════════════════════════════════════
    # ENROLLMENT LOADING
    # ═══════════════════════════════════════════════════════════════════

    def _load_enrollment(self):
        """Load all .wav files from enrollment_dir and compute embeddings."""

        if not os.path.isdir(self.enrollment_dir):
            print(f"⚠️ Folderul de enrollment nu există: {self.enrollment_dir}")
            return

        wav_files = [f for f in os.listdir(self.enrollment_dir) if f.endswith('.wav')]

        if not wav_files:
            print(f"⚠️ Niciun fișier .wav în: {self.enrollment_dir}")
            return

        print(f"🔄 Se încarcă {len(wav_files)} voci din enrollment...")

        for wav_file in wav_files:
            # Person name = file name without extension
            speaker_name = os.path.splitext(wav_file)[0]
            wav_path = os.path.join(self.enrollment_dir, wav_file)

            try:
                embedding = self._compute_embedding_from_file(wav_path)
                self.speaker_db[speaker_name] = embedding
                print(f"  ✅ {speaker_name} — embedding calculat ({wav_file})")
            except Exception as e:
                print(f"  ❌ Eroare la {wav_file}: {e}")

        print(f"📊 Baza de date: {len(self.speaker_db)} voci "
              f"({', '.join(self.speaker_db.keys())})")

    def _ensure_placeholder_custom_module(self):
        """SpeechBrain tries to fetch an optional custom.py by default."""
        custom_module_path = os.path.join(self.model_cache_dir, 'custom.py')
        if os.path.exists(custom_module_path):
            return

        with open(custom_module_path, 'w', encoding='utf-8') as handle:
            handle.write(
                '# Placeholder module for SpeechBrain pretrained loading.\n'
            )

    # ═══════════════════════════════════════════════════════════════════
    # EMBEDDING COMPUTATION
    # ═══════════════════════════════════════════════════════════════════

    def _compute_embedding_from_file(self, wav_path):
        """
        Compute embedding from a .wav file.

        Args:
            wav_path: Path to the .wav file

        Returns:
            torch.Tensor: Voice embedding (vector)
        """
        signal, sr = self._load_audio_file(wav_path)

        # Resample to 16kHz if needed
        if sr != 16000:
            resampler = torchaudio.transforms.Resample(orig_freq=sr, new_freq=16000)
            signal = resampler(signal)

        # Convert to mono if stereo
        if signal.shape[0] > 1:
            signal = torch.mean(signal, dim=0, keepdim=True)

        # Compute embedding
        embedding = self.classifier.encode_batch(signal)
        return embedding.squeeze()

    @staticmethod
    def _load_audio_file(wav_path):
        """
        Load enrollment audio without depending on TorchCodec.

        soundfile handles the common PCM .wav files used for enrollment and avoids
        the torchaudio backend path that started requiring torchcodec.
        """
        try:
            signal, sr = sf.read(wav_path, dtype='float32', always_2d=True)
            signal = torch.from_numpy(signal.T.copy())
            return signal, int(sr)
        except Exception:
            signal, sr = torchaudio.load(wav_path)
            return signal, int(sr)

    def _compute_embedding_from_array(self, audio_float):
        """
        Compute embedding from a float32 numpy array.

        Args:
            audio_float: float32 numpy array, normalized [-1, 1], mono, 16kHz

        Returns:
            torch.Tensor: Voice embedding (vector)
        """
        # Convert numpy → torch tensor
        if isinstance(audio_float, np.ndarray):
            signal = torch.from_numpy(audio_float).float()
        else:
            signal = torch.tensor(audio_float, dtype=torch.float32)

        # Ensure 2D: [1, num_samples]
        if signal.dim() == 1:
            signal = signal.unsqueeze(0)

        # Compute embedding
        embedding = self.classifier.encode_batch(signal)
        return embedding.squeeze()

    # ═══════════════════════════════════════════════════════════════════
    # SPEAKER IDENTIFICATION
    # ═══════════════════════════════════════════════════════════════════

    def identify(self, audio_float, threshold=None, min_margin=None):
        """
        Identify the speaker based on an audio segment.

        Args:
            audio_float: float32 numpy array, normalized [-1, 1], mono, 16kHz
            threshold: Optional threshold (uses self.threshold if not provided)

        Returns:
            str: Speaker name (e.g., "Vale") or "Unknown"
        """
        return self.identify_with_details(
            audio_float,
            threshold=threshold,
            min_margin=min_margin,
        ).speaker_name

    def identify_with_details(self, audio_float, threshold=None, min_margin=None) -> SpeakerMatchResult:
        """Identify the speaker and return decision details for logging."""
        if min_margin is None:
            min_margin = self.min_margin
        else:
            min_margin = max(0.0, float(min_margin))
        if not self.speaker_db:
            return select_speaker_match({}, threshold or self.threshold, min_margin)

        if threshold is None:
            threshold = self.threshold

        # Compute embedding for the incoming audio
        new_embedding = self._compute_embedding_from_array(audio_float)

        # ─────────────────────────────────────────────────────────
        # Compare with all speakers in the database (cosine similarity)
        # ─────────────────────────────────────────────────────────
        scores = {}

        for name, stored_embedding in self.speaker_db.items():
            scores[name] = self._cosine_similarity(new_embedding, stored_embedding)

        return select_speaker_match(scores, threshold, min_margin)

    def identify_file(self, wav_path, threshold=None, min_margin=None):
        signal, sr = self._load_audio_file(wav_path)
        if sr != 16000:
            resampler = torchaudio.transforms.Resample(orig_freq=sr, new_freq=16000)
            signal = resampler(signal)
        if signal.shape[0] > 1:
            signal = torch.mean(signal, dim=0, keepdim=True)
        audio_float = signal.squeeze(0).detach().cpu().numpy().astype(np.float32)
        return self.identify(audio_float, threshold=threshold, min_margin=min_margin)

    # ═══════════════════════════════════════════════════════════════════
    # COSINE SIMILARITY
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def _cosine_similarity(vec_a, vec_b):
        """
        Compute cosine similarity between two vectors.

        Returns:
            float: Score between -1 and 1 (1 = identical, 0 = unrelated)
        """
        # Ensure 1D
        vec_a = vec_a.flatten()
        vec_b = vec_b.flatten()

        dot_product = torch.dot(vec_a, vec_b)
        norm_a = torch.norm(vec_a)
        norm_b = torch.norm(vec_b)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        similarity = dot_product / (norm_a * norm_b)
        return similarity.item()

    # ═══════════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════════

    def get_speakers(self):
        """Return the list of speaker names in the database."""
        return list(self.speaker_db.keys())

    def reload(self):
        """Reload the database (useful after new enrollment)."""
        self.speaker_db.clear()
        self._load_enrollment()


# ═══════════════════════════════════════════════════════════════════
# QUICK TEST (optional)
# ═══════════════════════════════════════════════════════════════════

def _find_workspace_root() -> Path | None:
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [base] + list(base.parents):
            if parent.name == 'voice_ros2':
                return parent
    return None


def main():
    import sys

    workspace_root = _find_workspace_root()
    voices_dir = os.path.join(
        str(workspace_root) if workspace_root else os.getcwd(),
        'voices'
    )
    enrollment_path = os.path.join(voices_dir, 'enrollment')

    if not os.path.isdir(enrollment_path):
        print(f"❌ Folderul de enrollment nu există: {enrollment_path}")
        print("   Rulează mai întâi: python3 speaker_id/enroll_speaker.py")
        sys.exit(1)

    wav_count = len([f for f in os.listdir(enrollment_path) if f.endswith('.wav')])
    if wav_count == 0:
        print("❌ Niciun fișier .wav în enrollment. Rulează mai întâi: python3 speaker_id/enroll_speaker.py")
        sys.exit(1)

    print(f"\n📂 Testare SpeakerManager cu {wav_count} voci...\n")
    manager = SpeakerManager(enrollment_path)
    print(f"\n✅ Vorbitori încărcați: {manager.get_speakers()}")


if __name__ == '__main__':
    main()
    print("   SpeakerManager funcționează corect!")
