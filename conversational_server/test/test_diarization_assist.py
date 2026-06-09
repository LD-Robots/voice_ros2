import base64
import io
import wave

from conversational_server.diarization_assist_node import (
    dominant_speaker_from_diarized_payload,
    load_known_speaker_references,
    pcm16_to_wav_bytes,
)


def _write_silence_wav(path, *, seconds=2.0, sample_rate=16000):
    frames = int(seconds * sample_rate)
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b'\x00\x00' * frames)


def test_dominant_speaker_uses_segment_duration_share():
    speaker, confidence, segments = dominant_speaker_from_diarized_payload({
        'segments': [
            {'speaker': 'speaker_001', 'start': 0.0, 'end': 0.8, 'text': 'hello'},
            {'speaker': 'speaker_002', 'start': 0.8, 'end': 3.0, 'text': 'side'},
            {'speaker': 'speaker_001', 'start': 3.0, 'end': 3.2, 'text': 'yes'},
        ],
    })

    assert speaker == 'speaker_002'
    assert round(confidence, 2) == 0.69
    assert len(segments) == 3


def test_pcm16_to_wav_bytes_round_trips_wave_header():
    data = pcm16_to_wav_bytes([0, 100, -100, 50], sample_rate=16000, channels=1)

    with wave.open(io.BytesIO(data), 'rb') as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == 4


def test_load_known_speaker_references_returns_data_urls(tmp_path):
    enrollment = tmp_path / 'enrollment'
    enrollment.mkdir()
    _write_silence_wav(enrollment / 'speaker_001.wav', seconds=2.0)
    _write_silence_wav(enrollment / 'too_short.wav', seconds=0.2)

    refs = load_known_speaker_references(
        str(enrollment),
        max_speakers=4,
        min_seconds=1.0,
        max_seconds=1.5,
    )

    assert [name for name, _ in refs] == ['speaker_001']
    data_url = refs[0][1]
    assert data_url.startswith('data:audio/wav;base64,')
    decoded = base64.b64decode(data_url.split(',', 1)[1])
    with wave.open(io.BytesIO(decoded), 'rb') as wav:
        assert wav.getnframes() == int(16000 * 1.5)
