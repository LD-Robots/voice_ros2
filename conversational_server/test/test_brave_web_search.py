import json

from conversational_server.brave_web_search import (
    WEB_SEARCH_FUNCTION_NAME,
    build_realtime_web_search_tool,
    build_web_search_tool_output,
    extract_response_sources,
    extract_response_text,
    normalize_brave_country,
)


def test_build_realtime_web_search_tool_has_query_schema():
    tool = build_realtime_web_search_tool()

    assert tool['type'] == 'function'
    assert tool['name'] == WEB_SEARCH_FUNCTION_NAME
    assert tool['parameters']['required'] == ['query']


def test_extract_response_text_uses_grounding_snippets():
    payload = {
        'grounding': {
            'generic': [{
                'title': 'Source One',
                'url': 'https://one.example',
                'snippets': [
                    'First detail from source one.',
                    'Second detail from source one.',
                ],
            }],
        },
        'sources': {
            'https://one.example': {
                'hostname': 'one.example',
                'age': ['2 hours ago'],
            },
        },
    }

    text = extract_response_text(payload)
    assert 'Source One' in text
    assert 'First detail from source one.' in text
    assert 'Second detail from source one.' in text


def test_extract_response_sources_dedupes_urls():
    payload = {
        'grounding': {
            'generic': [
                {'title': 'A', 'url': 'https://a.example', 'snippets': ['a']},
                {'title': 'A duplicate', 'url': 'https://a.example', 'snippets': ['a2']},
                {'title': 'B', 'url': 'https://b.example', 'snippets': ['b']},
            ],
        },
        'sources': {},
    }

    assert extract_response_sources(payload) == [
        {'title': 'A', 'url': 'https://a.example'},
        {'title': 'B', 'url': 'https://b.example'},
    ]


def test_build_web_search_tool_output_includes_provider():
    payload = {
        'grounding': {
            'generic': [{
                'title': 'A',
                'url': 'https://a.example',
                'snippets': ['summary snippet'],
            }],
        },
        'sources': {},
    }
    output = json.loads(build_web_search_tool_output('latest weather', payload=payload))
    assert output['ok'] is True
    assert output['provider'] == 'brave'
    assert output['query'] == 'latest weather'


def test_normalize_brave_country_maps_unsupported_to_all():
    assert normalize_brave_country('ro') == 'ALL'
    assert normalize_brave_country('US') == 'US'
