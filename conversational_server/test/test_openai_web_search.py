import json

from conversational_server.openai_web_search import (
    WEB_SEARCH_FUNCTION_NAME,
    build_realtime_web_search_tool,
    build_web_search_tool_output,
    extract_response_sources,
    extract_response_text,
)


def test_build_realtime_web_search_tool_has_query_schema():
    tool = build_realtime_web_search_tool()

    assert tool['type'] == 'function'
    assert tool['name'] == WEB_SEARCH_FUNCTION_NAME
    assert tool['parameters']['required'] == ['query']


def test_extract_response_text_prefers_normalized_summary():
    payload = {
        'provider': 'brave_search',
        'summary': 'Fresh summary from Brave.',
        'output_text': 'Older fallback text.',
    }

    assert extract_response_text(payload) == 'Fresh summary from Brave.'


def test_extract_response_text_falls_back_to_output_text():
    payload = {
        'output_text': 'Fresh summary from the web.',
        'output': [{
            'content': [{
                'text': 'Older fallback text.',
            }],
        }],
    }

    assert extract_response_text(payload) == 'Fresh summary from the web.'


def test_extract_response_sources_prefers_top_level_sources():
    payload = {
        'provider': 'brave_search',
        'sources': [
            {'title': 'Source A', 'url': 'https://a.example'},
            {'title': 'Source A duplicate', 'url': 'https://a.example'},
            {'title': 'Source B', 'url': 'https://b.example'},
        ],
    }

    assert extract_response_sources(payload) == [
        {'title': 'Source A', 'url': 'https://a.example'},
        {'title': 'Source B', 'url': 'https://b.example'},
    ]


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
        'provider': 'brave_search',
        'summary': 'Latest result summary.',
        'sources': [
            {'title': 'Source A', 'url': 'https://a.example'},
        ],
        'results': [{
            'source_type': 'web',
            'title': 'Source A',
            'url': 'https://a.example',
            'description': 'Snippet',
        }],
    }

    output = json.loads(build_web_search_tool_output('latest weather', payload=payload))

    assert output == {
        'ok': True,
        'query': 'latest weather',
        'summary': 'Latest result summary.',
        'sources': [{'title': 'Source A', 'url': 'https://a.example'}],
        'provider': 'brave_search',
        'results': [{
            'source_type': 'web',
            'title': 'Source A',
            'url': 'https://a.example',
            'description': 'Snippet',
        }],
    }
