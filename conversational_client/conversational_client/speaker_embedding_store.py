#!/usr/bin/env python3
"""
speaker_embedding_store.py

Durable, portable persistence for enrolled voice templates.

A speaker's voiceprint is a small (~192-float) centroid embedding. Persisting
the *embedding* — not the source .wav — makes enrollment:
  * portable     : copy one JSON to a new robot and it knows every speaker
  * fast         : no ECAPA recompute per speaker at every startup
  * privacy-safe : the vector is not reversible to audio

The store is model-versioned. Embeddings are reused only when the running
embedding model matches the one that produced them, so swapping the backend
(e.g. ECAPA -> CAM++) safely invalidates stale vectors instead of silently
mixing incompatible geometry — speakers are then transparently rebuilt from
their .wav clips (if present) or flagged for re-enrollment.

Pure numpy + json (no torch/ROS), so it is unit-testable on its own and is the
exact format a future central enrollment server would store and serve.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np

EMBEDDING_STORE_FILENAME = 'speaker_embeddings.json'
STORE_VERSION = 1


def store_path(enrollment_dir: str) -> str:
    return os.path.join(enrollment_dir, EMBEDDING_STORE_FILENAME)


def has_any_speakers(enrollment_dir: str) -> bool:
    """True if a persisted store exists with at least one speaker.

    Model-agnostic on purpose: a model-mismatched store still counts as
    "there is enrollment data" so the manager gets a chance to rebuild it
    (from .wav) rather than the node short-circuiting to Unknown mode.
    """
    path = store_path(enrollment_dir)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
    except Exception:
        return False
    return bool(payload.get('speakers'))


def load_speaker_embeddings(enrollment_dir: str, model_id: str):
    """Load persisted voiceprints produced by ``model_id``.

    Returns ``(embeddings, raw_speakers)`` where ``embeddings`` is
    ``{label: np.float32[dim]}`` and ``raw_speakers`` is the untouched
    per-speaker records (used for clip counts / logging). When the stored model
    differs from ``model_id`` the embeddings dict is empty (stale geometry) but
    ``raw_speakers`` is still returned so the caller can report/rebuild.
    """
    path = store_path(enrollment_dir)
    if not os.path.isfile(path):
        return {}, {}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
    except Exception:
        return {}, {}

    speakers = payload.get('speakers', {}) or {}
    if str(payload.get('model', '') or '') != model_id:
        return {}, speakers

    embeddings = {}
    for label, record in speakers.items():
        vec = np.asarray(
            (record or {}).get('embedding', []), dtype=np.float32
        ).reshape(-1)
        if vec.size:
            embeddings[label] = vec
    return embeddings, speakers


def save_speaker_embeddings(enrollment_dir, model_id, embeddings, clip_counts=None):
    """Atomically write the consolidated, model-versioned voiceprint store."""
    if not enrollment_dir:
        return
    os.makedirs(enrollment_dir, exist_ok=True)
    clip_counts = clip_counts or {}
    now = datetime.now(timezone.utc).isoformat()

    dim = 0
    speakers = {}
    for label, vec in embeddings.items():
        arr = np.asarray(vec, dtype=np.float32).reshape(-1)
        if not arr.size:
            continue
        dim = max(dim, int(arr.size))
        speakers[label] = {
            'embedding': [round(float(x), 7) for x in arr.tolist()],
            'clips': int(clip_counts.get(label, 1)),
            'updated_at': now,
        }

    payload = {
        'model': model_id,
        'dim': dim,
        'version': STORE_VERSION,
        'speakers': speakers,
    }
    path = store_path(enrollment_dir)
    tmp = f'{path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
