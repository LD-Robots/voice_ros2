import json

from conversational_server.brave_web_search import (
    DEFAULT_WEB_SEARCH_CONTEXT_SIZE,
    WEB_SEARCH_FUNCTION_NAME,
    build_realtime_web_search_tool,
    build_web_search_tool_output,
    extract_response_sources,
    extract_response_text,
    get_brave_context_profile,
)


def test_build_realtime_web_search_tool_has_query_schema():
    tool = build_realtime_web_search_tool()

    assert tool['type'] == 'function'
    assert tool['name'] == WEB_SEARCH_FUNCTION_NAME
    assert tool['parameters']['required'] == ['query']


def test_get_brave_context_profile_uses_default_for_unknown_size():
    profile = get_brave_context_profile('very-large')

    assert profile == get_brave_context_profile(DEFAULT_WEB_SEARCH_CONTEXT_SIZE)


def test_extract_response_text_prefers_output_text():
    payload = {
        'output_text': 'Fresh summary from the web.',
        'grounding': {
            'generic': [{
                'url': 'https://a.example',
                'title': 'Source A',
                'snippets': ['Older fallback text.'],
            }],
        },
    }

    assert extract_response_text(payload) == 'Fresh summary from the web.'


def test_extract_response_sources_returns_distinct_citations():
    payload = {
        'grounding': {
            'generic': [
                {
                    'url': 'https://a.example',
                    'title': 'Source A',
                    'snippets': ['Snippet A1', 'Snippet A2'],
                },
                {
                    'url': 'https://a.example',
                    'title': 'Source A duplicate',
                    'snippets': ['Duplicate should be ignored'],
                },
                {
                    'url': 'https://b.example',
                    'title': 'Source B',
                    'snippets': ['Snippet B1'],
                },
            ],
        },
        'sources': {
            'https://a.example': {'hostname': 'a.example', 'age': ['2026-04-15']},
            'https://b.example': {'hostname': 'b.example'},
        },
    }

    assert extract_response_sources(payload) == [
        {
            'title': 'Source A',
            'url': 'https://a.example',
            'hostname': 'a.example',
            'age': '2026-04-15',
        },
        {'title': 'Source B', 'url': 'https://b.example', 'hostname': 'b.example'},
    ]


def test_build_web_search_tool_output_serializes_summary_and_sources():
    payload = {
        'grounding': {
            'generic': [{
                'url': 'https://a.example',
                'title': 'Source A',
                'snippets': ['Latest result summary.'],
            }],
        },
        'sources': {
            'https://a.example': {'hostname': 'a.example'},
        },
    }

    output = json.loads(build_web_search_tool_output('latest weather', payload=payload))

    assert output == {
        'ok': True,
        'provider': 'brave',
        'query': 'latest weather',
        'summary': 'Source A | a.example\n- Latest result summary.',
        'sources': [{'title': 'Source A', 'url': 'https://a.example', 'hostname': 'a.example'}],
    }
