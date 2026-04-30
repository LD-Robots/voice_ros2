"""Helpers for OpenAI-backed web search in the Realtime pipeline."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import requests


WEB_SEARCH_FUNCTION_NAME = 'web_search'
DEFAULT_WEB_SEARCH_MODEL = 'gpt-4.1-mini'
DEFAULT_WEB_SEARCH_CONTEXT_SIZE = 'medium'
DEFAULT_WEB_SEARCH_MAX_OUTPUT_TOKENS = 400
DEFAULT_WEB_SEARCH_TIMEOUT_S = 15.0
DEFAULT_WEB_SEARCH_SOURCES_LIMIT = 5

_REALTIME_WEB_SEARCH_TOOL = {
    'type': 'function',
    'name': WEB_SEARCH_FUNCTION_NAME,
    'description': (
        'Search the public web for current or factual information that may have changed recently. '
        'Use this for news, weather, prices, sports, elections, company leadership, product '
        'releases, live facts, or when the user explicitly asks you to search online.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'query': {
                'type': 'string',
                'description': 'The exact web search query to run.',
            },
        },
        'required': ['query'],
        'additionalProperties': False,
    },
}


def build_realtime_web_search_tool() -> dict[str, Any]:
    """Return a fresh function-tool schema for Realtime sessions."""
    return deepcopy(_REALTIME_WEB_SEARCH_TOOL)


def call_openai_web_search(
    api_key: str,
    query: str,
    *,
    model: str = DEFAULT_WEB_SEARCH_MODEL,
    search_context_size: str = DEFAULT_WEB_SEARCH_CONTEXT_SIZE,
    timeout_s: float = DEFAULT_WEB_SEARCH_TIMEOUT_S,
    max_output_tokens: int = DEFAULT_WEB_SEARCH_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """Execute a web search through the OpenAI Responses API."""
    response = requests.post(
        'https://api.openai.com/v1/responses',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        json={
            'model': model,
            'input': query,
            'tools': [{
                'type': 'web_search_preview',
                'search_context_size': search_context_size,
            }],
            'max_output_tokens': max_output_tokens,
        },
        timeout=max(1.0, float(timeout_s)),
    )

    if response.ok:
        return response.json()

    try:
        payload = response.json()
    except ValueError:
        payload = response.text.strip()
    raise RuntimeError(f'HTTP {response.status_code}: {payload}')


def extract_response_text(payload: dict[str, Any]) -> str:
    """Extract the assistant text from a Responses API payload."""
    output_text = str(payload.get('output_text', '') or '').strip()
    if output_text:
        return output_text

    parts: list[str] = []
    for item in payload.get('output', []) or []:
        for content in item.get('content', []) or []:
            text = str(content.get('text', '') or '').strip()
            if text:
                parts.append(text)
    return '\n'.join(parts).strip()


def extract_response_sources(
    payload: dict[str, Any],
    *,
    max_sources: int = DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
) -> list[dict[str, str]]:
    """Collect distinct cited URLs from a Responses API payload."""
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for item in payload.get('output', []) or []:
        for content in item.get('content', []) or []:
            for annotation in content.get('annotations', []) or []:
                if str(annotation.get('type', '') or '') != 'url_citation':
                    continue
                url = str(annotation.get('url', '') or '').strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                sources.append({
                    'title': str(annotation.get('title', '') or '').strip() or url,
                    'url': url,
                })
                if len(sources) >= max(1, int(max_sources)):
                    return sources

    return sources


def build_web_search_tool_output(
    query: str,
    *,
    payload: dict[str, Any] | None = None,
    error: str = '',
    max_sources: int = DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
    provider: str = '',
) -> str:
    """Serialize search results into a tool output string for Realtime."""
    summary = ''
    sources: list[dict[str, str]] = []

    if payload:
        summary = extract_response_text(payload)
        sources = extract_response_sources(payload, max_sources=max_sources)

    if not summary and not error:
        error = 'No web-search summary text was returned.'

    tool_output = {
        'ok': not error,
        'query': query,
        'summary': summary,
        'sources': sources,
    }
    provider_name = str(provider or '').strip().lower()
    if provider_name:
        tool_output['provider'] = provider_name
    if error:
        tool_output['error'] = error

    return json.dumps(tool_output, ensure_ascii=False)
