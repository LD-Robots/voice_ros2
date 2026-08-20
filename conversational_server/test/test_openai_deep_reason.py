import json

from conversational_server.openai_deep_reason import (
    DEEP_REASON_FUNCTION_NAME,
    build_deep_reason_tool,
    build_deep_reason_tool_output,
)


def test_tool_schema_shape():
    tool = build_deep_reason_tool()
    assert tool['type'] == 'function'
    assert tool['name'] == DEEP_REASON_FUNCTION_NAME
    params = tool['parameters']
    assert params['required'] == ['question']
    assert set(params['properties']) == {'question', 'context'}
    assert params['additionalProperties'] is False


def test_tool_schema_is_a_fresh_copy_each_call():
    """The node hands these to the API; a shared dict would leak mutations."""
    first = build_deep_reason_tool()
    first['parameters']['properties'].clear()
    assert set(build_deep_reason_tool()['parameters']['properties']) == {'question', 'context'}


def test_tool_output_carries_the_answer():
    payload = {'output_text': 'Seventeen.'}
    parsed = json.loads(build_deep_reason_tool_output('how many?', payload=payload))
    assert parsed['ok'] is True
    assert parsed['answer'] == 'Seventeen.'
    assert parsed['question'] == 'how many?'
    assert 'error' not in parsed


def test_tool_output_extracts_nested_content():
    payload = {'output': [{'content': [{'text': 'Because of entropy.'}]}]}
    parsed = json.loads(build_deep_reason_tool_output('why?', payload=payload))
    assert parsed['ok'] is True
    assert parsed['answer'] == 'Because of entropy.'


def test_tool_output_reports_errors_with_a_spoken_fallback():
    parsed = json.loads(build_deep_reason_tool_output('why?', error='HTTP 500: boom'))
    assert parsed['ok'] is False
    assert parsed['error'] == 'HTTP 500: boom'
    # The robot is mid-turn, so it must still be told what to say.
    assert parsed['fallback_instruction']


def test_empty_payload_is_treated_as_failure():
    """A 200 response with no text must not be spoken as an empty answer."""
    parsed = json.loads(build_deep_reason_tool_output('why?', payload={'output': []}))
    assert parsed['ok'] is False
    assert parsed['error']
    assert parsed['fallback_instruction']


def test_missing_payload_is_treated_as_failure():
    parsed = json.loads(build_deep_reason_tool_output('why?'))
    assert parsed['ok'] is False
    assert parsed['error']


class _FakeResponse:
    ok = True

    def __init__(self, captured):
        self._captured = captured

    def json(self):
        return {'output_text': 'ok'}


def _capture_request(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured['url'] = url
        captured['headers'] = headers
        captured['json'] = json
        captured['timeout'] = timeout
        return _FakeResponse(captured)

    monkeypatch.setattr('conversational_server.openai_deep_reason.requests.post', fake_post)
    return captured


def test_effort_is_omitted_for_non_reasoning_models(monkeypatch):
    """gpt-4.1 and friends reject a reasoning field, so it must not be sent."""
    from conversational_server.openai_deep_reason import call_openai_deep_reason

    captured = _capture_request(monkeypatch)
    call_openai_deep_reason('key', 'why?', model='gpt-4.1', effort='')
    assert 'reasoning' not in captured['json']


def test_effort_defaults_on_so_the_reasoning_model_actually_reasons(monkeypatch):
    """A reasoning model called with no effort answers off the top of its head."""
    from conversational_server.openai_deep_reason import (
        DEFAULT_DEEP_REASON_EFFORT,
        call_openai_deep_reason,
    )

    captured = _capture_request(monkeypatch)
    call_openai_deep_reason('key', 'why?')
    assert captured['json']['reasoning'] == {'effort': DEFAULT_DEEP_REASON_EFFORT}


def test_effort_is_sent_when_configured(monkeypatch):
    from conversational_server.openai_deep_reason import call_openai_deep_reason

    captured = _capture_request(monkeypatch)
    call_openai_deep_reason('key', 'why?', model='o3', effort='high')
    assert captured['json']['reasoning'] == {'effort': 'high'}


def test_blank_effort_is_treated_as_unset(monkeypatch):
    from conversational_server.openai_deep_reason import call_openai_deep_reason

    captured = _capture_request(monkeypatch)
    call_openai_deep_reason('key', 'why?', effort='   ')
    assert 'reasoning' not in captured['json']


def test_context_is_appended_to_the_question(monkeypatch):
    from conversational_server.openai_deep_reason import call_openai_deep_reason

    captured = _capture_request(monkeypatch)
    call_openai_deep_reason('key', 'which is faster?', context='we compared A and B')
    assert 'which is faster?' in captured['json']['input']
    assert 'we compared A and B' in captured['json']['input']


def test_blank_context_adds_no_noise(monkeypatch):
    from conversational_server.openai_deep_reason import call_openai_deep_reason

    captured = _capture_request(monkeypatch)
    call_openai_deep_reason('key', 'why?', context='   ')
    assert captured['json']['input'] == 'why?'


def test_api_key_is_sent_as_bearer(monkeypatch):
    from conversational_server.openai_deep_reason import call_openai_deep_reason

    captured = _capture_request(monkeypatch)
    call_openai_deep_reason('secret-key', 'why?')
    assert captured['headers']['Authorization'] == 'Bearer secret-key'


def test_http_error_is_raised_with_detail(monkeypatch):
    import pytest

    from conversational_server.openai_deep_reason import call_openai_deep_reason

    class _ErrorResponse:
        ok = False
        status_code = 400

        def json(self):
            return {'error': 'bad model'}

    monkeypatch.setattr(
        'conversational_server.openai_deep_reason.requests.post',
        lambda *a, **k: _ErrorResponse(),
    )
    with pytest.raises(RuntimeError, match='400'):
        call_openai_deep_reason('key', 'why?')
