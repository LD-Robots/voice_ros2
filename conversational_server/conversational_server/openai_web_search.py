"""Helpers for OpenAI-backed web search in the Realtime pipeline."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import requests


WEB_SEARCH_FUNCTION_NAME = 'web_search'
DEFAULT_WEB_SEARCH_MODEL = 'gpt-4.1-mini'
DEFAULT_WEB_SEARCH_PROVIDER = 'openai'
DEFAULT_WEB_SEARCH_CONTEXT_SIZE = 'medium'
DEFAULT_WEB_SEARCH_MAX_OUTPUT_TOKENS = 400
DEFAULT_WEB_SEARCH_TIMEOUT_S = 15.0
DEFAULT_WEB_SEARCH_SOURCES_LIMIT = 5
DEFAULT_BRAVE_SEARCH_COUNTRY = 'ALL'
DEFAULT_BRAVE_SEARCH_LANG = 'en'
DEFAULT_BRAVE_SEARCH_COUNT = 5
BRAVE_SUPPORTED_COUNTRIES = {
    'AR', 'AU', 'AT', 'BE', 'BR', 'CA', 'CL', 'DK', 'FI', 'FR', 'DE', 'GR',
    'HK', 'IN', 'ID', 'IT', 'JP', 'KR', 'MY', 'MX', 'NL', 'NZ', 'NO', 'CN',
    'PL', 'PT', 'PH', 'RU', 'SA', 'ZA', 'ES', 'SE', 'CH', 'TW', 'TR', 'GB',
    'US', 'ALL',
}
BRAVE_SUPPORTED_LANGS = {
    'ar', 'eu', 'bn', 'bg', 'ca', 'zh-hans', 'zh-hant', 'hr', 'cs', 'da',
    'nl', 'en', 'en-gb', 'et', 'fi', 'fr', 'gl', 'de', 'el', 'gu', 'he',
    'hi', 'hu', 'is', 'it', 'jp', 'kn', 'ko', 'lv', 'lt', 'ms', 'ml', 'mr',
    'nb', 'pl', 'pt-br', 'pt-pt', 'pa', 'ro', 'ru', 'sr', 'sk', 'sl', 'es',
    'sv', 'ta', 'te', 'th', 'tr', 'uk', 'vi',
}

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


def call_brave_web_search(
    api_key: str,
    query: str,
    *,
    country: str = DEFAULT_BRAVE_SEARCH_COUNTRY,
    search_lang: str = DEFAULT_BRAVE_SEARCH_LANG,
    count: int = DEFAULT_BRAVE_SEARCH_COUNT,
    timeout_s: float = DEFAULT_WEB_SEARCH_TIMEOUT_S,
) -> dict[str, Any]:
    """Execute a web search through the Brave Search API."""
    normalized_country = str(country or DEFAULT_BRAVE_SEARCH_COUNTRY).strip().upper()
    if normalized_country not in BRAVE_SUPPORTED_COUNTRIES:
        normalized_country = DEFAULT_BRAVE_SEARCH_COUNTRY
    normalized_lang = str(search_lang or DEFAULT_BRAVE_SEARCH_LANG).strip().lower()
    if normalized_lang not in BRAVE_SUPPORTED_LANGS:
        normalized_lang = DEFAULT_BRAVE_SEARCH_LANG

    response = requests.get(
        'https://api.search.brave.com/res/v1/web/search',
        headers={
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
            'X-Subscription-Token': api_key,
        },
        params={
            'q': query,
            'count': max(1, min(20, int(count))),
            'country': normalized_country,
            'search_lang': normalized_lang,
            'spellcheck': 1,
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


def extract_brave_results(
    payload: dict[str, Any],
    *,
    max_sources: int = DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
) -> list[dict[str, str]]:
    """Collect web results from a Brave Search API payload."""
    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    web_payload = payload.get('web', {}) or {}

    for item in web_payload.get('results', []) or []:
        url = str(item.get('url', '') or '').strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        title = str(item.get('title', '') or '').strip() or url
        description = str(item.get('description', '') or '').strip()
        age = str(item.get('age', '') or '').strip()
        result = {
            'title': title,
            'url': url,
        }
        if description:
            result['description'] = description
        if age:
            result['age'] = age
        results.append(result)
        if len(results) >= max(1, int(max_sources)):
            return results

    return results


def build_brave_web_search_tool_output(
    query: str,
    *,
    payload: dict[str, Any] | None = None,
    error: str = '',
    max_sources: int = DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
) -> str:
    """Serialize Brave results into a tool output string for Realtime."""
    sources: list[dict[str, str]] = []
    summary = ''

    if payload:
        sources = extract_brave_results(payload, max_sources=max_sources)
        if sources:
            lines = []
            for idx, source in enumerate(sources, start=1):
                line = f"{idx}. {source['title']} - {source['url']}"
                description = source.get('description', '')
                if description:
                    line += f"\n   {description}"
                age = source.get('age', '')
                if age:
                    line += f"\n   Published/updated: {age}"
                lines.append(line)
            summary = '\n'.join(lines)

    if not summary and not error:
        error = 'No Brave Search results were returned.'

    tool_output = {
        'ok': not error,
        'provider': 'brave',
        'query': query,
        'summary': summary,
        'sources': [
            {'title': source['title'], 'url': source['url']}
            for source in sources
        ],
    }
    if error:
        tool_output['error'] = error

    return json.dumps(tool_output, ensure_ascii=False)


def build_web_search_tool_output(
    query: str,
    *,
    payload: dict[str, Any] | None = None,
    error: str = '',
    max_sources: int = DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
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
    if error:
        tool_output['error'] = error

    return json.dumps(tool_output, ensure_ascii=False)
