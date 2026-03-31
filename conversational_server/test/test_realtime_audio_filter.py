import numpy as np

from conversational_server.realtime_audio_filter import (
    PlaybackInputFilter,
    PlaybackInputFilterConfig,
)


def _tone(amplitude: int, *, freq_hz: float = 440.0, sample_rate: int = 16000) -> np.ndarray:
    t = np.arange(320, dtype=np.float32) / float(sample_rate)
    pcm = amplitude * np.sin(2.0 * np.pi * freq_hz * t)
    return np.clip(np.round(pcm), -32768, 32767).astype(np.int16)


def test_playback_input_filter_blocks_weak_playback_leak():
    guard = PlaybackInputFilter()
    quiet = _tone(300)

    for idx in range(8):
        assert guard.should_forward(quiet, sample_rate=16000, now_ms=idx * 20) is False


def test_playback_input_filter_opens_after_consecutive_strong_speech():
    guard = PlaybackInputFilter(
        PlaybackInputFilterConfig(
            min_rms_dbfs=-24.0,
            hits_required=4,
            hold_ms=240,
        )
    )
    strong = _tone(7000)

    for idx in range(3):
        assert guard.should_forward(strong, sample_rate=16000, now_ms=idx * 20) is False

    assert guard.should_forward(strong, sample_rate=16000, now_ms=60) is True
    assert guard.gate_open is True


def test_playback_input_filter_holds_gate_briefly_after_speech():
    guard = PlaybackInputFilter(
        PlaybackInputFilterConfig(
            min_rms_dbfs=-24.0,
            hits_required=2,
            hold_ms=240,
        )
    )
    strong = _tone(7000)
    silence = np.zeros(320, dtype=np.int16)

    assert guard.should_forward(strong, sample_rate=16000, now_ms=0) is False
    assert guard.should_forward(strong, sample_rate=16000, now_ms=20) is True
    assert guard.should_forward(silence, sample_rate=16000, now_ms=140) is True
    assert guard.should_forward(silence, sample_rate=16000, now_ms=320) is False
