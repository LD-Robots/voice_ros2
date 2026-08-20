#!/usr/bin/env python3
"""
speaker_scoring.py

Backend-agnostic math for robust speaker recognition (Phase 1 + Phase 2):

  * length normalization (L2)            -> stable cosine geometry
  * centroid enrollment templates        -> average of multiple utterances
  * adaptive symmetric score norm (AS-norm) -> calibrated, room/mic robust scores
  * temporal smoothing / hysteresis      -> kills per-segment flip-flopping

These are pure functions + small stateful helpers with NO torch/ROS imports, so
they are unit-testable on their own and shared by every embedding backend
(ECAPA today, CAM++ tomorrow).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Vector helpers
# ─────────────────────────────────────────────────────────────────────────────

def l2_normalize(vec) -> np.ndarray:
    """Return ``vec`` scaled to unit L2 norm (float32). Zero vectors pass through."""
    arr = np.asarray(vec, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(arr))
    if norm <= 1e-12:
        return arr
    return (arr / norm).astype(np.float32)


def build_centroid(embeddings) -> np.ndarray:
    """L2-normalize each embedding, average them, and L2-normalize the result.

    This is the standard, most-effective way to combine multiple enrollment
    utterances into a single template (length-normalized mean).
    """
    mats = [l2_normalize(e) for e in embeddings if e is not None and np.size(e)]
    if not mats:
        return np.zeros(0, dtype=np.float32)
    stacked = np.vstack(mats).astype(np.float32)
    mean = stacked.mean(axis=0)
    return l2_normalize(mean)


def cosine(a, b) -> float:
    """Cosine similarity in [-1, 1]; robust to un-normalized inputs."""
    va = l2_normalize(a)
    vb = l2_normalize(b)
    if va.size == 0 or vb.size == 0 or va.size != vb.size:
        return 0.0
    return float(np.dot(va, vb))


# ─────────────────────────────────────────────────────────────────────────────
# AS-norm (adaptive symmetric score normalization)
# ─────────────────────────────────────────────────────────────────────────────

def _adaptive_stats(score_vs_cohort, top_k: int):
    """Mean/std of the top-K most similar cohort scores (adaptive s-norm)."""
    scores = np.asarray(list(score_vs_cohort), dtype=np.float32)
    if scores.size == 0:
        return 0.0, 1.0
    k = min(int(top_k), scores.size) if top_k and top_k > 0 else scores.size
    top = np.sort(scores)[::-1][:k]
    mu = float(top.mean())
    sigma = float(top.std())
    if sigma < 1e-6:
        sigma = 1.0
    return mu, sigma


def as_norm_score(raw_score: float,
                  enroll_vs_cohort,
                  test_vs_cohort,
                  top_k: int = 300) -> float:
    """Adaptive symmetric normalization of one enroll-vs-test cosine score.

    ``enroll_vs_cohort`` : cosines of the enrolled template against the cohort.
    ``test_vs_cohort``   : cosines of the test embedding against the cohort.
    Returns a calibrated (z-score-like) score; a fixed threshold on this value
    is far more stable across rooms/mics than a threshold on the raw cosine.
    """
    mu_e, sigma_e = _adaptive_stats(enroll_vs_cohort, top_k)
    mu_t, sigma_t = _adaptive_stats(test_vs_cohort, top_k)
    z_e = (raw_score - mu_e) / sigma_e
    z_t = (raw_score - mu_t) / sigma_t
    return 0.5 * (z_e + z_t)


# ─────────────────────────────────────────────────────────────────────────────
# Decision (threshold + margin) — kept compatible with select_speaker_match
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScoredDecision:
    speaker: str
    best_name: str
    best_score: float
    second_best_score: float
    reason: str


def decide(scores: dict, threshold: float, min_margin: float) -> ScoredDecision:
    """Pick the best speaker if it clears ``threshold`` and beats #2 by ``min_margin``."""
    if not scores:
        return ScoredDecision('Unknown', 'Unknown', -1.0, -1.0, 'no_candidates')

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_name, best_score = ordered[0]
    second_best_score = ordered[1][1] if len(ordered) > 1 else -1.0

    if best_score < threshold:
        return ScoredDecision('Unknown', best_name, best_score, second_best_score, 'below_threshold')
    if second_best_score > -1.0 and (best_score - second_best_score) < min_margin:
        return ScoredDecision('Unknown', best_name, best_score, second_best_score, 'margin_too_small')
    return ScoredDecision(best_name, best_name, best_score, second_best_score, 'matched')


# ─────────────────────────────────────────────────────────────────────────────
# Temporal smoothing / hysteresis
# ─────────────────────────────────────────────────────────────────────────────

class TemporalSpeakerSmoother:
    """Suppress single-segment flips on the /speaker_id stream.

    Rules:
      * To switch from one known speaker to a *different* known speaker, the new
        speaker must be seen ``switch_hits`` times in a row.
      * A single 'Unknown' does NOT drop a held known speaker; it takes
        ``unknown_hits`` consecutive Unknowns inside ``window_s`` to release.
    This gives the conversation a stable notion of "who is speaking" even though
    per-segment embeddings are noisy.
    """

    def __init__(self, switch_hits: int = 2, unknown_hits: int = 3, window_s: float = 8.0):
        self.switch_hits = max(1, int(switch_hits))
        self.unknown_hits = max(1, int(unknown_hits))
        self.window_s = float(window_s)
        self._held = 'Unknown'
        self._cand = None
        self._cand_hits = 0
        self._unknown_streak = 0
        self._last_ts = 0.0

    def reset(self):
        self._held = 'Unknown'
        self._cand = None
        self._cand_hits = 0
        self._unknown_streak = 0

    def update(self, observed: str, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        # Long silence between segments -> forget the candidate streak (not the held id).
        if self._last_ts and (now - self._last_ts) > self.window_s:
            self._cand = None
            self._cand_hits = 0
            self._unknown_streak = 0
        self._last_ts = now

        observed = observed or 'Unknown'

        if observed == 'Unknown':
            self._unknown_streak += 1
            self._cand = None
            self._cand_hits = 0
            if self._held != 'Unknown' and self._unknown_streak >= self.unknown_hits:
                self._held = 'Unknown'
            return self._held

        # A confident known observation.
        self._unknown_streak = 0
        if observed == self._held:
            self._cand = None
            self._cand_hits = 0
            return self._held

        # Different (or first) known speaker -> require consecutive confirmations.
        if observed == self._cand:
            self._cand_hits += 1
        else:
            self._cand = observed
            self._cand_hits = 1

        # First time we ever lock onto someone is immediate; switching needs hits.
        needed = 1 if self._held == 'Unknown' else self.switch_hits
        if self._cand_hits >= needed:
            self._held = observed
            self._cand = None
            self._cand_hits = 0
        return self._held


@dataclass
class IdentificationConfig:
    """Tunables shared by the manager and node (so calibration lives in one place)."""
    threshold: float = 0.35           # cosine floor (recalibrate per deployment)
    min_margin: float = 0.10          # best vs 2nd-best gap
    min_id_seconds: float = 1.5       # speech needed for a *confident* decision
    use_as_norm: bool = False         # Phase 2 score normalization
    as_norm_threshold: float = 1.5    # threshold on AS-norm (z-like) scores
    as_norm_top_k: int = 300
