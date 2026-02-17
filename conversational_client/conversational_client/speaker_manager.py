#!/usr/bin/env python3
"""
speaker_manager.py
Gestionează baza de date de amprente vocale (voice fingerprints).

EXPLICAȚIE:
- Încarcă fișierele .wav din folderul de enrollment
- Calculează embedding-uri folosind SpeechBrain (ECAPA-TDNN)
- Identifică vorbitorul prin Cosine Similarity

Folosit de speaker_id_node.py (Developer B — Delia).
"""

import os
import numpy as np
import torch
import torchaudio

# ─────────────────────────────────────────────────────────────────
# FIX: torchaudio 2.6+ a scos list_audio_backends(),
#      dar speechbrain 1.0.x încă o apelează la import.
# ─────────────────────────────────────────────────────────────────
if not hasattr(torchaudio, 'list_audio_backends'):
    torchaudio.list_audio_backends = lambda: ['ffmpeg']

from speechbrain.inference.speaker import EncoderClassifier


class SpeakerManager:
    """
    Manager pentru amprente vocale.

    Utilizare:
        manager = SpeakerManager('/path/to/enrollment/')
        speaker = manager.identify(audio_float32_array)
    """

    def __init__(self, enrollment_dir, threshold=0.25):
        """
        Inițializează SpeakerManager.

        Args:
            enrollment_dir: Calea către folderul cu fișiere .wav de enrollment
                           (ex: ~/voice_ros2/voices/enrollment/)
                           Fiecare fișier trebuie numit cu numele persoanei: vale.wav, delia.wav
            threshold: Pragul minim de similaritate cosinus pentru identificare (default: 0.25)
        """
        self.enrollment_dir = enrollment_dir
        self.threshold = threshold

        # ─────────────────────────────────────────────────────────
        # Încarcă modelul SpeechBrain ECAPA-TDNN
        # ─────────────────────────────────────────────────────────
        print("🔄 Se încarcă modelul SpeechBrain ECAPA-TDNN...")
        self.classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=os.path.expanduser("~/.cache/speechbrain/spkrec-ecapa-voxceleb"),
            run_opts={"device": "cpu"}
        )
        print("✅ Model ECAPA-TDNN încărcat!")

        # ─────────────────────────────────────────────────────────
        # Baza de date: {"Vale": embedding_tensor, "Delia": embedding_tensor}
        # ─────────────────────────────────────────────────────────
        self.speaker_db = {}
        self._load_enrollment()

    # ═══════════════════════════════════════════════════════════════════
    # ÎNCĂRCARE ENROLLMENT
    # ═══════════════════════════════════════════════════════════════════

    def _load_enrollment(self):
        """Încarcă toate fișierele .wav din enrollment_dir și calculează embedding-urile."""

        if not os.path.isdir(self.enrollment_dir):
            print(f"⚠️ Folderul de enrollment nu există: {self.enrollment_dir}")
            return

        wav_files = [f for f in os.listdir(self.enrollment_dir) if f.endswith('.wav')]

        if not wav_files:
            print(f"⚠️ Niciun fișier .wav în: {self.enrollment_dir}")
            return

        print(f"🔄 Se încarcă {len(wav_files)} voci din enrollment...")

        for wav_file in wav_files:
            # Numele persoanei = numele fișierului fără extensie
            # ex: vale.wav → "Vale" (cu prima literă mare)
            speaker_name = os.path.splitext(wav_file)[0].capitalize()
            wav_path = os.path.join(self.enrollment_dir, wav_file)

            try:
                embedding = self._compute_embedding_from_file(wav_path)
                self.speaker_db[speaker_name] = embedding
                print(f"  ✅ {speaker_name} — embedding calculat ({wav_file})")
            except Exception as e:
                print(f"  ❌ Eroare la {wav_file}: {e}")

        print(f"📊 Baza de date: {len(self.speaker_db)} voci "
              f"({', '.join(self.speaker_db.keys())})")

    # ═══════════════════════════════════════════════════════════════════
    # CALCUL EMBEDDING
    # ═══════════════════════════════════════════════════════════════════

    def _compute_embedding_from_file(self, wav_path):
        """
        Calculează embedding-ul dintr-un fișier .wav.

        Args:
            wav_path: Calea către fișierul .wav

        Returns:
            torch.Tensor: Embedding-ul vocii (vector)
        """
        signal, sr = torchaudio.load(wav_path)

        # Resample la 16kHz dacă e necesar
        if sr != 16000:
            resampler = torchaudio.transforms.Resample(orig_freq=sr, new_freq=16000)
            signal = resampler(signal)

        # Convertește la mono dacă e stereo
        if signal.shape[0] > 1:
            signal = torch.mean(signal, dim=0, keepdim=True)

        # Calculează embedding-ul
        embedding = self.classifier.encode_batch(signal)
        return embedding.squeeze()

    def _compute_embedding_from_array(self, audio_float):
        """
        Calculează embedding-ul dintr-un numpy array float32.

        Args:
            audio_float: numpy array float32, normalizat [-1, 1], mono, 16kHz

        Returns:
            torch.Tensor: Embedding-ul vocii (vector)
        """
        # Convertește numpy → torch tensor
        if isinstance(audio_float, np.ndarray):
            signal = torch.from_numpy(audio_float).float()
        else:
            signal = torch.tensor(audio_float, dtype=torch.float32)

        # Asigură-te că e 2D: [1, num_samples]
        if signal.dim() == 1:
            signal = signal.unsqueeze(0)

        # Calculează embedding-ul
        embedding = self.classifier.encode_batch(signal)
        return embedding.squeeze()

    # ═══════════════════════════════════════════════════════════════════
    # IDENTIFICARE VORBITOR
    # ═══════════════════════════════════════════════════════════════════

    def identify(self, audio_float, threshold=None):
        """
        Identifică vorbitorul pe baza unui segment audio.

        Args:
            audio_float: numpy array float32, normalizat [-1, 1], mono, 16kHz
            threshold: Prag opțional (folosește self.threshold dacă nu e specificat)

        Returns:
            str: Numele vorbitorului (ex: "Vale") sau "Unknown"
        """
        if not self.speaker_db:
            return "Unknown"

        if threshold is None:
            threshold = self.threshold

        # Calculează embedding-ul audio-ului primit
        new_embedding = self._compute_embedding_from_array(audio_float)

        # ─────────────────────────────────────────────────────────
        # Compară cu toți vorbitorii din baza de date (Cosine Similarity)
        # ─────────────────────────────────────────────────────────
        best_name = "Unknown"
        best_score = -1.0

        for name, stored_embedding in self.speaker_db.items():
            score = self._cosine_similarity(new_embedding, stored_embedding)

            if score > best_score:
                best_score = score
                best_name = name

        # Verifică dacă scorul depășește pragul
        if best_score >= threshold:
            return best_name
        else:
            return "Unknown"

    # ═══════════════════════════════════════════════════════════════════
    # COSINE SIMILARITY
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def _cosine_similarity(vec_a, vec_b):
        """
        Calculează similaritatea cosinus între doi vectori.

        Returns:
            float: Scor între -1 și 1 (1 = identic, 0 = nepotrivit)
        """
        # Asigură-te că sunt 1D
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
    # UTILITĂȚI
    # ═══════════════════════════════════════════════════════════════════

    def get_speakers(self):
        """Returnează lista numelor vorbitorilor din baza de date."""
        return list(self.speaker_db.keys())

    def reload(self):
        """Reîncarcă baza de date (util după enrollment nou)."""
        self.speaker_db.clear()
        self._load_enrollment()


# ═══════════════════════════════════════════════════════════════════
# TEST RAPID (opțional)
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import sys

    enrollment_path = os.path.expanduser('~/voice_ros2/voices/enrollment/')

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
