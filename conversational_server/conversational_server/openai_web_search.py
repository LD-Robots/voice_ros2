"""Helpers for Brave-backed web search in the Realtime pipeline."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import requests


WEB_SEARCH_FUNCTION_NAME = 'web_search'
DEFAULT_WEB_SEARCH_MODEL = 'brave-search'
DEFAULT_WEB_SEARCH_CONTEXT_SIZE = 'medium'
DEFAULT_WEB_SEARCH_MAX_OUTPUT_TOKENS = 400
DEFAULT_WEB_SEARCH_TIMEOUT_S = 15.0
DEFAULT_WEB_SEARCH_SOURCES_LIMIT = 5
_BRAVE_API_BASE = 'https://api.search.brave.com/res/v1'
_BRAVE_COUNTRY = 'US'
_BRAVE_SEARCH_LANG = 'en'
_BRAVE_UI_LANG = 'en-US'
_BRAVE_SAFESEARCH = 'moderate'
_CONTEXT_SIZE_TO_RESULT_COUNT = {
    'low': 4,
    'medium': 6,
    'high': 8,
}
_SUMMARY_RESULT_COUNT = 3
_MAX_RESULT_TEXT_CHARS = 240
_MAX_LEAF_LINES = 12

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


def _request_brave_json(
    api_key: str,
    path: str,
    *,
    params: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    response = requests.get(
        f'{_BRAVE_API_BASE}{path}',
        headers={
            'Accept': 'application/json',
            'X-Subscription-Token': api_key,
        },
        params=params,
        timeout=max(1.0, float(timeout_s)),
    )

    if response.ok:
        return response.json()

    try:
        payload = response.json()
    except ValueError:
        payload = response.text.strip()
    raise RuntimeError(f'HTTP {response.status_code}: {payload}')


def _context_size_result_count(search_context_size: str) -> int:
    normalized = str(search_context_size or '').strip().lower()
    return _CONTEXT_SIZE_TO_RESULT_COUNT.get(normalized, _CONTEXT_SIZE_TO_RESULT_COUNT['medium'])


def _trim_text(text: str, limit: int) -> str:
    cleaned = ' '.join(str(text or '').split()).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + '…'


def _coerce_result(item: dict[str, Any], *, source_type: str) -> dict[str, Any] | None:
    url = str(item.get('url', '') or '').strip()
    if not url:
        return None

    title = str(item.get('title', '') or '').strip() or url
    description = _trim_text(
        str(item.get('description', '') or item.get('snippet', '') or '').strip(),
        _MAX_RESULT_TEXT_CHARS,
    )
    extra_snippets = [
        _trim_text(str(snippet).strip(), _MAX_RESULT_TEXT_CHARS)
        for snippet in (item.get('extra_snippets') or [])
        if str(snippet or '').strip()
    ]
    age = str(item.get('age', '') or item.get('page_age', '') or '').strip()

    result = {
        'source_type': source_type,
        'title': title,
        'url': url,
    }
    if description:
        result['description'] = description
    if extra_snippets:
        result['extra_snippets'] = extra_snippets[:2]
    if age:
        result['age'] = age
    return result


def _extract_brave_results(payload: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for source_type in ('news', 'web'):
        section = payload.get(source_type, {}) or {}
        for item in section.get('results', []) or []:
            if len(results) >= limit:
                return results
            if not isinstance(item, dict):
                continue
            result = _coerce_result(item, source_type=source_type)
            if result is None:
                continue
            url = str(result.get('url', '') or '').strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(result)

    return results


def _extract_first_text(value: Any, preferred_keys: tuple[str, ...]) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in preferred_keys:
            candidate = _extract_first_text(value.get(key), preferred_keys)
            if candidate:
                return candidate
        for candidate_value in value.values():
            candidate = _extract_first_text(candidate_value, preferred_keys)
            if candidate:
                return candidate
    if isinstance(value, list):
        for item in value:
            candidate = _extract_first_text(item, preferred_keys)
            if candidate:
                return candidate
    return ''


def _extract_brave_summary(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ''
    if str(payload.get('status', '') or '').strip().lower() == 'failed':
        return ''
    return _extract_first_text(
        payload,
        preferred_keys=('summary', 'raw_text', 'text', 'description', 'content', 'answer'),
    )


def _collect_leaf_lines(value: Any, *, prefix: str = '', limit: int = _MAX_LEAF_LINES) -> list[str]:
    lines: list[str] = []

    def visit(node: Any, node_prefix: str):
        if len(lines) >= limit:
            return
        if isinstance(node, dict):
            for key, child in node.items():
                key_text = str(key or '').strip()
                if not key_text or key_text.endswith('_key'):
                    continue
                next_prefix = f'{node_prefix}{key_text}' if not node_prefix else f'{node_prefix}.{key_text}'
                visit(child, next_prefix)
            return
        if isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f'{node_prefix}[{index}]')
            return
        if isinstance(node, (str, int, float, bool)):
            text = _trim_text(str(node), _MAX_RESULT_TEXT_CHARS)
            if text:
                label = node_prefix.replace('_', ' ').strip()
                lines.append(f'{label}: {text}' if label else text)

    visit(value, prefix)
    return lines


def _extract_rich_summary(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ''
    lines = _collect_leaf_lines(payload)
    if not lines:
        return ''
    return 'Structured Brave result:\n' + '\n'.join(lines[:_MAX_LEAF_LINES])


def _build_result_summary(results: list[dict[str, Any]], *, max_output_tokens: int) -> str:
    if not results:
        return ''

    summary_lines: list[str] = []
    for index, result in enumerate(results[:_SUMMARY_RESULT_COUNT], start=1):
        title = str(result.get('title', '') or result.get('url', '') or '').strip()
        age = str(result.get('age', '') or '').strip()
        snippet_parts = [
            str(result.get('description', '') or '').strip(),
            *[str(item).strip() for item in (result.get('extra_snippets') or []) if str(item or '').strip()],
        ]
        snippet = _trim_text(' '.join(part for part in snippet_parts if part), _MAX_RESULT_TEXT_CHARS)
        label = f'{index}. {title}'
        if age:
            label += f' ({age})'
        summary_lines.append(f'{label}: {snippet}' if snippet else label)

    max_chars = max(320, int(max_output_tokens) * 6)
    return _trim_text('Brave web findings:\n' + '\n'.join(summary_lines), max_chars)


def _extract_top_level_sources(
    payload: dict[str, Any],
    *,
    max_sources: int = DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
) -> list[dict[str, str]]:
    raw_sources = payload.get('sources', []) or []
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        url = str(item.get('url', '') or '').strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        sources.append({
            'title': str(item.get('title', '') or '').strip() or url,
            'url': url,
        })
        if len(sources) >= max(1, int(max_sources)):
            return sources

    return sources


def _build_sources_from_results(
    results: list[dict[str, Any]],
    *,
    max_sources: int = DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for result in results:
        url = str(result.get('url', '') or '').strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        sources.append({
            'title': str(result.get('title', '') or '').strip() or url,
            'url': url,
        })
        if len(sources) >= max(1, int(max_sources)):
            return sources

    return sources


def call_brave_web_search(
    api_key: str,
    query: str,
    *,
    model: str = DEFAULT_WEB_SEARCH_MODEL,
    search_context_size: str = DEFAULT_WEB_SEARCH_CONTEXT_SIZE,
    timeout_s: float = DEFAULT_WEB_SEARCH_TIMEOUT_S,
    max_output_tokens: int = DEFAULT_WEB_SEARCH_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """Execute a Brave web search and return a normalized tool payload."""
    del model
    if not str(api_key or '').strip():
        raise RuntimeError('BRAVE_SEARCH_API_KEY not set')

    result_count = _context_size_result_count(search_context_size)
    search_payload = _request_brave_json(
        api_key,
        '/web/search',
        params={
            'q': query,
            'count': result_count,
            'summary': 1,
            'extra_snippets': 'true',
            'enable_rich_callback': 1,
            'country': _BRAVE_COUNTRY,
            'search_lang': _BRAVE_SEARCH_LANG,
            'ui_lang': _BRAVE_UI_LANG,
            'safesearch': _BRAVE_SAFESEARCH,
        },
        timeout_s=timeout_s,
    )

    warnings: list[str] = []
    summary_payload: dict[str, Any] | None = None
    rich_payload: dict[str, Any] | None = None

    summary_key = str((search_payload.get('summarizer', {}) or {}).get('key', '') or '').strip()
    if summary_key:
        try:
            summary_payload = _request_brave_json(
                api_key,
                '/summarizer/search',
                params={
                    'key': summary_key,
                    'inline_references': 'true',
                },
                timeout_s=timeout_s,
            )
        except Exception as exc:
            warnings.append(f'Brave summarizer unavailable: {exc}')

    callback_key = str(
        (((search_payload.get('rich', {}) or {}).get('hint', {}) or {}).get('callback_key', '') or '')
    ).strip()
    if callback_key:
        try:
            rich_payload = _request_brave_json(
                api_key,
                '/web/rich',
                params={'callback_key': callback_key},
                timeout_s=timeout_s,
            )
        except Exception as exc:
            warnings.append(f'Brave rich data unavailable: {exc}')

    results = _extract_brave_results(search_payload, limit=result_count)
    sources = _build_sources_from_results(results, max_sources=result_count)
    summary = _extract_brave_summary(summary_payload)
    if not summary:
        summary = _extract_rich_summary(rich_payload)
    if not summary:
        summary = _build_result_summary(results, max_output_tokens=max_output_tokens)
        if summary:
            warnings.append('Using Brave search result snippets because no Brave summary was returned.')

    payload = {
        'provider': 'brave_search',
        'query': query,
        'summary': summary,
        'sources': sources,
        'results': results,
    }
    if warnings:
        payload['warnings'] = warnings
    if rich_payload:
        payload['rich_data'] = rich_payload
    if summary_payload:
        payload['summary_data'] = summary_payload
    if search_payload:
        payload['search_data'] = search_payload
    return payload


def call_openai_web_search(
    api_key: str,
    query: str,
    *,
    model: str = DEFAULT_WEB_SEARCH_MODEL,
    search_context_size: str = DEFAULT_WEB_SEARCH_CONTEXT_SIZE,
    timeout_s: float = DEFAULT_WEB_SEARCH_TIMEOUT_S,
    max_output_tokens: int = DEFAULT_WEB_SEARCH_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """Backward-compatible alias for the older helper name."""
    return call_brave_web_search(
        api_key,
        query,
        model=model,
        search_context_size=search_context_size,
        timeout_s=timeout_s,
        max_output_tokens=max_output_tokens,
    )


def extract_response_text(payload: dict[str, Any]) -> str:
    """Extract summary text from a normalized web-search payload."""
    normalized_summary = str(payload.get('summary', '') or '').strip()
    if normalized_summary:
        return normalized_summary

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
    """Collect distinct cited URLs from a normalized web-search payload."""
    top_level_sources = _extract_top_level_sources(payload, max_sources=max_sources)
    if top_level_sources:
        return top_level_sources

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
) -> str:
    """Serialize search results into a tool output string for Realtime."""
    summary = ''
    sources: list[dict[str, str]] = []
    provider = ''
    results: list[dict[str, Any]] = []
    rich_data: dict[str, Any] | None = None
    warnings: list[str] = []

    if payload:
        provider = str(payload.get('provider', '') or '').strip()
        summary = extract_response_text(payload)
        sources = extract_response_sources(payload, max_sources=max_sources)
        raw_results = payload.get('results', []) or []
        if isinstance(raw_results, list):
            results = [item for item in raw_results if isinstance(item, dict)]
        raw_warnings = payload.get('warnings', []) or []
        if isinstance(raw_warnings, list):
            warnings = [str(item).strip() for item in raw_warnings if str(item or '').strip()]
        candidate_rich = payload.get('rich_data')
        if isinstance(candidate_rich, dict) and candidate_rich:
            rich_data = candidate_rich

    if not summary and not error:
        error = 'No web-search summary text was returned.'

    tool_output = {
        'ok': not error,
        'query': query,
        'summary': summary,
        'sources': sources,
    }
    if provider:
        tool_output['provider'] = provider
    if results:
        tool_output['results'] = results
    if rich_data:
        tool_output['rich_data'] = rich_data
    if warnings:
        tool_output['warnings'] = warnings
    if error:
        tool_output['error'] = error

    return json.dumps(tool_output, ensure_ascii=False)
