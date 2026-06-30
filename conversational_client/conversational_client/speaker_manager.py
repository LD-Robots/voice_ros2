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
from .speaker_scoring import build_centroid, cosine, l2_normalize
from .speaker_embedding_store import (
    load_speaker_embeddings,
    save_speaker_embeddings,
)

# Identifies the embedding backend. Persisted voiceprints are only reused when
# this matches the stored model id, so changing the model safely invalidates
# stale vectors instead of mixing incompatible geometry.
EMBEDDING_MODEL_ID = 'ecapa-voxceleb'


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
        self.embedding_model_id = EMBEDDING_MODEL_ID
        # How many clips each stored centroid was averaged from (for logging /
        # incremental re-enrollment); kept in step with self.speaker_db.
        self._clip_counts = {}
        self.model_cache_dir = os.path.expanduser(
            "~/.cache/speechbrain/spkrec-ecapa-voxceleb"
        )

        os.makedirs(self.model_cache_dir, exist_ok=True)
        self._ensure_placeholder_custom_module()

        # ─────────────────────────────────────────────────────────
        # Load SpeechBrain ECAPA-TDNN model
        # ─────────────────────────────────────────────────────────
        print("🔄 Loading SpeechBrain ECAPA-TDNN model...")
        self.classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=self.model_cache_dir,
            run_opts={"device": "cpu"}
        )
        print("✅ ECAPA-TDNN model loaded!")

        # ─────────────────────────────────────────────────────────
        # Database: {"Vale": embedding_tensor, "Delia": embedding_tensor}
        # ─────────────────────────────────────────────────────────
        self.speaker_db = {}
        self._load_enrollment()

    # ═══════════════════════════════════════════════════════════════════
    # ENROLLMENT LOADING
    # ═══════════════════════════════════════════════════════════════════

    def _collect_enrollment_clips(self):
        """Group enrollment .wav files by speaker.

        Two layouts are supported (and may be mixed):
          * ``<dir>/<name>.wav``        -> speaker "name" enrolled from one clip
          * ``<dir>/<name>/*.wav``      -> speaker "name" enrolled from a centroid
                                           of every clip in that sub-folder
        Multiple clips per speaker produce a far more robust template than a
        single utterance, so prefer the sub-folder layout for new enrollments.
        """
        clips: dict[str, list[str]] = {}
        for entry in sorted(os.listdir(self.enrollment_dir)):
            full = os.path.join(self.enrollment_dir, entry)
            if os.path.isdir(full):
                wavs = [
                    os.path.join(full, f)
                    for f in sorted(os.listdir(full))
                    if f.endswith('.wav')
                ]
                if wavs:
                    clips.setdefault(entry, []).extend(wavs)
            elif entry.endswith('.wav'):
                clips.setdefault(os.path.splitext(entry)[0], []).append(full)
        return clips

    def _load_enrollment(self):
        """Build the speaker database, preferring saved voiceprints.

        Precedence per speaker:
          1. a persisted centroid in ``speaker_embeddings.json`` (no .wav, no
             ECAPA recompute needed) — this is what lets a freshly deployed
             robot recognize everyone from a single portable file;
          2. otherwise, compute a centroid from the speaker's .wav clip(s) and
             persist it so the next launch (or another robot) needs no audio.
        """

        if not os.path.isdir(self.enrollment_dir):
            print(f"⚠️ Enrollment folder does not exist: {self.enrollment_dir}")
            return

        clips = self._collect_enrollment_clips()
        stored, raw_stored = load_speaker_embeddings(
            self.enrollment_dir, self.embedding_model_id
        )
        if raw_stored and not stored:
            print(
                "⚠️ Saved voiceprints were built with a different model — "
                "rebuilding from .wav clips where available."
            )

        labels = sorted(set(stored) | set(clips))
        if not labels:
            print(f"⚠️ No enrolled speakers in: {self.enrollment_dir}")
            return

        print(f"🔄 Loading {len(labels)} voices from enrollment...")
        recomputed = bool(raw_stored) and not stored

        for label in labels:
            if label in stored:
                self.speaker_db[label] = l2_normalize(stored[label])
                self._clip_counts[label] = int(
                    (raw_stored.get(label) or {}).get('clips', 1)
                )
                print(f"  ✅ {label} — loaded saved voiceprint")
                continue

            embeddings = []
            for wav_path in clips.get(label, []):
                try:
                    embeddings.append(self._embedding_np_from_file(wav_path))
                except Exception as e:
                    print(f"  ❌ Error at {os.path.basename(wav_path)}: {e}")

            centroid = build_centroid(embeddings)
            if centroid.size == 0:
                print(f"  ❌ {label} — no usable enrollment clips")
                continue

            self.speaker_db[label] = centroid
            self._clip_counts[label] = len(embeddings)
            recomputed = True
            print(f"  ✅ {label} — voiceprint from {len(embeddings)} clip(s) (saved)")

        # Persist whenever anything was (re)computed so the on-disk store stays
        # the authoritative, audio-free record.
        if recomputed:
            self._persist_embeddings()

        print(f"📊 Database: {len(self.speaker_db)} voices "
              f"({', '.join(self.speaker_db.keys())})")

    def _persist_embeddings(self):
        """Write the current voiceprint database to the portable store."""
        try:
            save_speaker_embeddings(
                self.enrollment_dir,
                self.embedding_model_id,
                self.speaker_db,
                self._clip_counts,
            )
        except Exception as e:
            print(f"⚠️ Could not persist voiceprints: {e}")

    def enroll_speaker(self, label, wav_paths, combine_with_existing=False):
        """Compute (or refresh) a speaker's voiceprint from clips and persist it.

        ``combine_with_existing`` averages the new clip(s) with the speaker's
        current centroid (used when re-recognizing someone already enrolled), so
        the template strengthens over time instead of being replaced.
        """
        embeddings = []
        for wav_path in wav_paths:
            try:
                embeddings.append(self._embedding_np_from_file(wav_path))
            except Exception as e:
                print(f"  ❌ Error at {os.path.basename(wav_path)}: {e}")

        prior_clips = 0
        if combine_with_existing and label in self.speaker_db:
            embeddings.append(self.speaker_db[label])
            prior_clips = self._clip_counts.get(label, 0)

        centroid = build_centroid(embeddings)
        if centroid.size == 0:
            raise RuntimeError(f'no_usable_clips_for_{label}')

        self.speaker_db[label] = centroid
        self._clip_counts[label] = prior_clips + len(wav_paths)
        self._persist_embeddings()
        return centroid

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

    def _embedding_np_from_file(self, wav_path):
        """Embedding for an enrollment clip as a 1-D float32 numpy vector."""
        return self._to_np(self._compute_embedding_from_file(wav_path))

    @staticmethod
    def _to_np(embedding) -> np.ndarray:
        """Convert a torch embedding (or array) to a flat float32 numpy vector."""
        if isinstance(embedding, torch.Tensor):
            embedding = embedding.detach().cpu().numpy()
        return np.asarray(embedding, dtype=np.float32).reshape(-1)

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
        new_embedding = self._to_np(self._compute_embedding_from_array(audio_float))

        # ─────────────────────────────────────────────────────────
        # Compare with each speaker centroid (length-normalized cosine)
        # ─────────────────────────────────────────────────────────
        scores = {
            name: cosine(new_embedding, centroid)
            for name, centroid in self.speaker_db.items()
        }

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
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════════

    def get_speakers(self):
        """Return the list of speaker names in the database."""
        return list(self.speaker_db.keys())

    def reload(self):
        """Reload the database (useful after new enrollment)."""
        self.speaker_db.clear()
        self._clip_counts.clear()
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
        print(f"❌ Enrollment folder does not exist: {enrollment_path}")
        print("   Run first: python3 speaker_id/enroll_speaker.py")
        sys.exit(1)

    wav_count = len([f for f in os.listdir(enrollment_path) if f.endswith('.wav')])
    if wav_count == 0:
        print("❌ No .wav files in enrollment. Run first: python3 speaker_id/enroll_speaker.py")
        sys.exit(1)

    print(f"\n📂 Testing SpeakerManager with {wav_count} voices...\n")
    manager = SpeakerManager(enrollment_path)
    print(f"\n✅ Loaded speakers: {manager.get_speakers()}")


if __name__ == '__main__':
    main()
    print("   SpeakerManager is working correctly!")
