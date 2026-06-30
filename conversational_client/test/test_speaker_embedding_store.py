import numpy as np

from conversational_client.speaker_embedding_store import (
    load_speaker_embeddings,
    save_speaker_embeddings,
    store_path,
)

MODEL = 'ecapa-voxceleb'


def test_round_trip_preserves_vectors(tmp_path):
    d = str(tmp_path)
    embeddings = {
        'speaker_001': np.array([0.1, 0.2, 0.3], dtype=np.float32),
        'speaker_002': np.array([-0.4, 0.5, 0.6], dtype=np.float32),
    }
    save_speaker_embeddings(d, MODEL, embeddings, clip_counts={'speaker_001': 3})

    loaded, raw = load_speaker_embeddings(d, MODEL)
    assert set(loaded) == {'speaker_001', 'speaker_002'}
    assert np.allclose(loaded['speaker_001'], [0.1, 0.2, 0.3], atol=1e-6)
    assert raw['speaker_001']['clips'] == 3


def test_missing_file_returns_empty(tmp_path):
    loaded, raw = load_speaker_embeddings(str(tmp_path), MODEL)
    assert loaded == {} and raw == {}


def test_model_mismatch_invalidates_vectors(tmp_path):
    d = str(tmp_path)
    save_speaker_embeddings(d, 'old-model', {'a': np.array([1.0, 0.0], dtype=np.float32)})
    # A different running model must not reuse stale geometry...
    loaded, raw = load_speaker_embeddings(d, 'new-model')
    assert loaded == {}
    # ...but the raw records survive so the caller can rebuild/report.
    assert 'a' in raw


def test_save_is_atomic_no_tmp_left(tmp_path):
    d = str(tmp_path)
    save_speaker_embeddings(d, MODEL, {'a': np.array([1.0, 2.0], dtype=np.float32)})
    assert (tmp_path / 'speaker_embeddings.json').exists()
    assert not (tmp_path / 'speaker_embeddings.json.tmp').exists()
    assert store_path(d).endswith('speaker_embeddings.json')
