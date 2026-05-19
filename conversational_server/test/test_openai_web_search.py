import json

from conversational_server.openai_web_search import (
    WEB_SEARCH_FUNCTION_NAME,
    build_brave_llm_context_tool_output,
    build_brave_web_search_tool_output,
    build_realtime_web_search_tool,
    build_web_search_tool_output,
    call_brave_llm_context,
    call_brave_web_search,
    extract_brave_results,
    extract_response_sources,
    extract_response_text,
    should_use_web_search,
)


def test_build_realtime_web_search_tool_has_query_schema():
    tool = build_realtime_web_search_tool()

    assert tool['type'] == 'function'
    assert tool['name'] == WEB_SEARCH_FUNCTION_NAME
    assert tool['parameters']['required'] == ['query']


def test_extract_response_text_prefers_output_text():
    payload = {
        'output_text': 'Fresh summary from the web.',
        'output': [{
            'content': [{
                'text': 'Older fallback text.',
            }],
        }],
    }

    assert extract_response_text(payload) == 'Fresh summary from the web.'


def test_extract_response_sources_returns_distinct_citations():
    payload = {
        'output': [{
            'content': [{
                'text': 'Summary',
                'annotations': [
                    {'type': 'url_citation', 'title': 'Source A', 'url': 'https://a.example'},
                    {'type': 'url_citation', 'title': 'Source A duplicate', 'url': 'https://a.example'},
                    {'type': 'url_citation', 'title': 'Source B', 'url': 'https://b.example'},
                ],
            }],
        }],
    }

    assert extract_response_sources(payload) == [
        {'title': 'Source A', 'url': 'https://a.example'},
        {'title': 'Source B', 'url': 'https://b.example'},
    ]


def test_build_web_search_tool_output_serializes_summary_and_sources():
    payload = {
        'output_text': 'Latest result summary.',
        'output': [{
            'content': [{
                'annotations': [
                    {'type': 'url_citation', 'title': 'Source A', 'url': 'https://a.example'},
                ],
            }],
        }],
    }

    output = json.loads(build_web_search_tool_output('latest weather', payload=payload))

    assert output == {
        'ok': True,
        'query': 'latest weather',
        'summary': 'Latest result summary.',
        'sources': [{'title': 'Source A', 'url': 'https://a.example'}],
    }


def test_extract_brave_results_returns_distinct_web_results():
    payload = {
        'web': {
            'results': [
                {
                    'title': 'Result A',
                    'url': 'https://a.example',
                    'description': 'First result.',
                },
                {
                    'title': 'Result A duplicate',
                    'url': 'https://a.example',
                    'description': 'Duplicate.',
                },
                {
                    'title': 'Result B',
                    'url': 'https://b.example',
                    'description': 'Second result.',
                },
            ],
        },
    }

    assert extract_brave_results(payload) == [
        {'title': 'Result A', 'url': 'https://a.example', 'description': 'First result.'},
        {'title': 'Result B', 'url': 'https://b.example', 'description': 'Second result.'},
    ]


def test_build_brave_web_search_tool_output_serializes_results():
    payload = {
        'web': {
            'results': [
                {
                    'title': 'Weather source',
                    'url': 'https://weather.example',
                    'description': 'Tomorrow forecast.',
                },
            ],
        },
    }

    output = json.loads(build_brave_web_search_tool_output('weather tomorrow', payload=payload))

    assert output['ok'] is True
    assert output['provider'] == 'brave'
    assert output['query'] == 'weather tomorrow'
    assert 'Tomorrow forecast.' in output['summary']
    assert output['sources'] == [
        {'title': 'Weather source', 'url': 'https://weather.example'},
    ]


def test_build_brave_llm_context_tool_output_serializes_grounding():
    payload = {
        'grounding': {
            'generic': [
                {
                    'title': 'Romanian SuperLiga table',
                    'url': 'https://standings.example',
                    'snippets': ['Team A 43 points', 'Team B 41 points'],
                },
            ],
        },
        'sources': {},
    }

    output = json.loads(build_brave_llm_context_tool_output('superliga table', payload=payload))

    assert output['ok'] is True
    assert output['provider'] == 'brave_llm_context'
    assert 'Team A 43 points' in output['summary']
    assert output['sources'] == [
        {'title': 'Romanian SuperLiga table', 'url': 'https://standings.example'},
    ]


def test_call_brave_web_search_normalizes_invalid_country_and_uppercase_lang(monkeypatch):
    captured = {}

    class FakeResponse:
        ok = True

        @staticmethod
        def json():
            return {'web': {'results': []}}

    def fake_get(url, *, headers, params, timeout):
        captured['params'] = params
        return FakeResponse()

    monkeypatch.setattr('conversational_server.openai_web_search.requests.get', fake_get)

    call_brave_web_search('key', 'weather', country='RO', search_lang='EN')

    assert captured['params']['country'] == 'ALL'
    assert captured['params']['search_lang'] == 'en'


def test_call_brave_llm_context_uses_context_params(monkeypatch):
    captured = {}

    class FakeResponse:
        ok = True

        @staticmethod
        def json():
            return {'grounding': {'generic': []}}

    def fake_get(url, *, headers, params, timeout):
        captured['params'] = params
        return FakeResponse()

    monkeypatch.setattr('conversational_server.openai_web_search.requests.get', fake_get)

    call_brave_llm_context('key', 'standings', country='ALL', search_lang='EN', count=60)

    assert captured['params']['country'] == 'US'
    assert captured['params']['search_lang'] == 'en'
    assert captured['params']['count'] == 50
    assert captured['params']['context_threshold_mode'] == 'lenient'


def test_should_use_web_search_for_live_or_current_requests():
    assert should_use_web_search('How will the weather be tomorrow?')[0] is True
    assert should_use_web_search('Some football matches from tomorrow in Romania')[0] is True
    assert should_use_web_search('Cauta online ultimele stiri din Romania')[0] is True
    assert should_use_web_search('Who is the CEO of OpenAI?')[0] is True
    assert should_use_web_search('But about Ukraine war')[0] is True


def test_should_not_use_web_search_for_stable_local_requests():
    assert should_use_web_search('Explain what a neural network is')[0] is False
    assert should_use_web_search('Robot, give me a handshake')[0] is False
    assert should_use_web_search('How are you today?')[0] is False
    assert should_use_web_search('Cum ești azi?')[0] is False
