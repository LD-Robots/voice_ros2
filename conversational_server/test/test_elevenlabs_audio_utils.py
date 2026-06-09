import io
import numpy as np
import pytest

pytest.importorskip('rclpy')

from conversational_server import asr_node as asr_module  # noqa: E402
from conversational_server import tts_node as tts_module  # noqa: E402
from conversational_server.asr_node import ASRNode  # noqa: E402
from conversational_server.tts_node import TTSNode  # noqa: E402


def make_asr_stub():
    node = ASRNode.__new__(ASRNode)
    node.eleven_model_id = 'scribe_v2'
    node.eleven_diarize = True
    node.eleven_tag_audio_events = False
    node.eleven_language_code = ''
    node.language = 'ro_en'
    node.eleven_num_speakers = 0
    node.eleven_diarization_threshold = 0.18
    node.eleven_timeout_s = 30.0
    node.eleven_api_key = 'test-key'
    node.eleven_prefer_raw_pcm = True
    node.eleven_min_diarized_words = 2
    node.sample_rate = 16000
    node.channels = 1
    return node


def make_tts_stub():
    node = TTSNode.__new__(TTSNode)
    node.eleven_api_key = 'test-key'
    node.eleven_model_id = 'eleven_v3'
    node.eleven_output_format = 'pcm_16000'
    node.eleven_timeout_s = 30.0
    node.eleven_latency_optimization = 2
    node.eleven_stream_chunk_ms = 120
    node.eleven_stability = 0.42
    node.eleven_similarity_boost = 0.78
    node.eleven_style = 0.38
    node.eleven_use_speaker_boost = True
    node.stop_requested = False
    return node


def test_elevenlabs_stt_uses_raw_pcm_for_mono_16k(monkeypatch):
    node = make_asr_stub()
    audio = np.arange(160, dtype=np.int16)
    captured = {}

    class Response:
        ok = True

        @staticmethod
        def json():
            return {'text': 'hello', 'language_code': 'en', 'language_probability': 0.9}

    def fake_post(url, headers, data, files, timeout):
        captured['url'] = url
        captured['data'] = data
        captured['files'] = files
        captured['timeout'] = timeout
        return Response()

    monkeypatch.setattr(asr_module.requests, 'post', fake_post)

    result = node._run_elevenlabs(io.BytesIO(b'wav bytes'), audio)

    assert result['text'] == 'hello'
    assert captured['data']['file_format'] == 'pcm_s16le_16'
    filename, file_bytes, content_type = captured['files']['file']
    assert filename == 'speech.pcm'
    assert file_bytes == audio.tobytes()
    assert content_type == 'application/octet-stream'


def test_diarization_payload_filters_tiny_speaker_noise_and_exports_segments():
    node = make_asr_stub()
    payload = {
        'language_code': 'en',
        'language_probability': 0.95,
        'words': [
            {'type': 'word', 'speaker_id': 'speaker_0', 'text': 'hello', 'start': 0.0, 'end': 0.2},
            {'type': 'word', 'speaker_id': 'speaker_0', 'text': 'there', 'start': 0.2, 'end': 0.4},
            {'type': 'word', 'speaker_id': 'speaker_1', 'text': 'yeah', 'start': 0.5, 'end': 0.7},
        ],
    }

    diarization = node._build_diarization_payload(payload)

    assert diarization['speaker_word_counts'] == {'speaker_0': 2}
    assert diarization['raw_speaker_word_counts'] == {'speaker_0': 2, 'speaker_1': 1}
    assert diarization['speaker_count'] == 1
    assert diarization['multi_speaker'] is False
    assert diarization['segments'] == diarization['spans']


def test_elevenlabs_tts_stream_yields_pcm_chunks(monkeypatch):
    node = make_tts_stub()
    samples = np.arange(4000, dtype=np.int16)
    pcm = samples.tobytes()
    captured = {}

    class Response:
        ok = True
        text = ''

        @staticmethod
        def iter_content(chunk_size):
            midpoint = len(pcm) // 2
            yield pcm[:midpoint]
            yield pcm[midpoint:]

    def fake_post(url, headers, params, json, timeout, stream):
        captured['params'] = dict(params)
        captured['json'] = dict(json)
        captured['stream'] = stream
        return Response()

    monkeypatch.setattr(tts_module.requests, 'post', fake_post)

    chunks = list(node._stream_elevenlabs_pcm('hello', 'voice-id', 'en', 16000))

    assert captured['params']['output_format'] == 'pcm_16000'
    assert captured['params']['optimize_streaming_latency'] == '2'
    assert captured['json']['model_id'] == 'eleven_v3'
    assert captured['stream'] is True
    assert np.concatenate(chunks).tolist() == samples.tolist()
