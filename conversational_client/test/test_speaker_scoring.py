import numpy as np

from conversational_client.speaker_scoring import (
    l2_normalize,
    build_centroid,
    cosine,
    as_norm_score,
    decide,
    TemporalSpeakerSmoother,
)


def test_l2_normalize_unit_norm():
    v = l2_normalize([3.0, 4.0])
    assert abs(np.linalg.norm(v) - 1.0) < 1e-6
    assert np.allclose(v, [0.6, 0.8])


def test_l2_normalize_zero_vector_safe():
    v = l2_normalize([0.0, 0.0, 0.0])
    assert np.allclose(v, [0.0, 0.0, 0.0])


def test_centroid_of_identical_is_same_direction():
    e = [1.0, 0.0, 0.0]
    c = build_centroid([e, e, e])
    assert abs(np.linalg.norm(c) - 1.0) < 1e-6
    assert np.allclose(c, [1.0, 0.0, 0.0])


def test_centroid_averages_directions():
    c = build_centroid([[1.0, 0.0], [0.0, 1.0]])
    # mean of the two unit axes -> 45 degrees, normalized
    assert np.allclose(c, [0.70710677, 0.70710677], atol=1e-5)


def test_cosine_bounds_and_values():
    assert abs(cosine([1, 0], [1, 0]) - 1.0) < 1e-6
    assert abs(cosine([1, 0], [0, 1]) - 0.0) < 1e-6
    assert abs(cosine([1, 0], [-1, 0]) + 1.0) < 1e-6


def test_as_norm_boosts_true_speaker_separation():
    # raw same-speaker score modest (0.5); cohort (imposters) sit lower.
    cohort = [0.1, 0.05, 0.0, -0.1, 0.2]
    z = as_norm_score(0.5, cohort, cohort, top_k=5)
    assert z > 1.0  # well above the imposter cloud


def test_decide_threshold_and_margin():
    assert decide({'a': 0.6, 'b': 0.2}, 0.35, 0.10).speaker == 'a'
    assert decide({'a': 0.30, 'b': 0.1}, 0.35, 0.10).reason == 'below_threshold'
    assert decide({'a': 0.50, 'b': 0.45}, 0.35, 0.10).reason == 'margin_too_small'
    assert decide({}, 0.35, 0.10).speaker == 'Unknown'


def test_smoother_locks_first_then_requires_hits_to_switch():
    s = TemporalSpeakerSmoother(switch_hits=2, unknown_hits=3, window_s=100.0)
    t = 0.0
    assert s.update('alice', now=t) == 'alice'          # first lock immediate
    t += 1; assert s.update('bob', now=t) == 'alice'    # 1 hit, not enough
    t += 1; assert s.update('bob', now=t) == 'bob'      # 2 hits -> switch


def test_smoother_single_unknown_does_not_drop_held():
    s = TemporalSpeakerSmoother(switch_hits=2, unknown_hits=3, window_s=100.0)
    t = 0.0
    s.update('alice', now=t)
    t += 1; assert s.update('Unknown', now=t) == 'alice'
    t += 1; assert s.update('Unknown', now=t) == 'alice'
    t += 1; assert s.update('Unknown', now=t) == 'Unknown'  # 3rd -> release
