from conversational_client.speaker_match_utils import select_speaker_match


def test_select_speaker_match_rejects_below_threshold():
    result = select_speaker_match({'speaker_001': 0.44, 'speaker_002': 0.20}, 0.45, 0.12)
    assert result.speaker_name == 'Unknown'
    assert result.best_name == 'speaker_001'
    assert result.reason == 'below_threshold'


def test_select_speaker_match_rejects_small_margin():
    result = select_speaker_match({'speaker_001': 0.60, 'speaker_002': 0.51}, 0.45, 0.12)
    assert result.speaker_name == 'Unknown'
    assert result.best_name == 'speaker_001'
    assert result.reason == 'margin_too_small'


def test_select_speaker_match_accepts_clear_match():
    result = select_speaker_match({'speaker_001': 0.63, 'speaker_002': 0.41}, 0.45, 0.12)
    assert result.speaker_name == 'speaker_001'
    assert result.best_name == 'speaker_001'
    assert result.reason == 'matched'
