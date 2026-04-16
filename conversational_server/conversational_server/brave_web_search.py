"""Helpers for Brave-backed web search in the Realtime pipeline."""
from __future__ import annotations

import json
import time
from copy import deepcopy
from typing import Any

import requests


WEB_SEARCH_FUNCTION_NAME = 'web_search'
DEFAULT_WEB_SEARCH_CONTEXT_SIZE = 'medium'
DEFAULT_WEB_SEARCH_TIMEOUT_S = 6.0
DEFAULT_WEB_SEARCH_SOURCES_LIMIT = 5
DEFAULT_WEB_SEARCH_COUNTRY = 'us'
DEFAULT_WEB_SEARCH_LANGUAGE = 'en'
DEFAULT_WEB_SEARCH_MAX_SNIPPETS_PER_SOURCE = 2
DEFAULT_WEB_SEARCH_MAX_SNIPPET_CHARS = 240

_BRAVE_CONTEXT_PROFILES = {
    'low': {
        'count': 4,
        'maximum_number_of_urls': 4,
        'maximum_number_of_tokens': 1536,
        'maximum_number_of_snippets': 8,
        'maximum_number_of_tokens_per_url': 512,
        'maximum_number_of_snippets_per_url': 2,
        'context_threshold_mode': 'strict',
    },
    'medium': {
        'count': 6,
        'maximum_number_of_urls': 6,
        'maximum_number_of_tokens': 3072,
        'maximum_number_of_snippets': 12,
        'maximum_number_of_tokens_per_url': 768,
        'maximum_number_of_snippets_per_url': 2,
        'context_threshold_mode': 'balanced',
    },
    'high': {
        'count': 8,
        'maximum_number_of_urls': 8,
        'maximum_number_of_tokens': 4096,
        'maximum_number_of_snippets': 16,
        'maximum_number_of_tokens_per_url': 1024,
        'maximum_number_of_snippets_per_url': 3,
        'context_threshold_mode': 'lenient',
    },
}

_REALTIME_WEB_SEARCH_TOOL = {
    'type': 'function',
    'name': WEB_SEARCH_FUNCTION_NAME,
    'description': (
        'Search the public web for current or factual information that may have changed recently. '
        'Use this only for time-sensitive, live, recent, online, or explicitly requested web lookups. '
        'Do not use it for timeless general knowledge.'
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


def get_brave_context_profile(context_size: str) -> dict[str, Any]:
    """Return the Brave request profile for the requested context size."""
    normalized = str(context_size or '').strip().lower()
    if normalized not in _BRAVE_CONTEXT_PROFILES:
        normalized = DEFAULT_WEB_SEARCH_CONTEXT_SIZE
    return deepcopy(_BRAVE_CONTEXT_PROFILES[normalized])


def call_brave_web_search(
    api_key: str,
    query: str,
    *,
    context_size: str = DEFAULT_WEB_SEARCH_CONTEXT_SIZE,
    timeout_s: float = DEFAULT_WEB_SEARCH_TIMEOUT_S,
    country: str = DEFAULT_WEB_SEARCH_COUNTRY,
    search_language: str = DEFAULT_WEB_SEARCH_LANGUAGE,
) -> dict[str, Any]:
    """Execute a low-latency Brave Search LLM Context request."""
    api_key = str(api_key or '').strip()
    query = str(query or '').strip()
    if not api_key:
        raise ValueError('BRAVE_SEARCH_API_KEY is not configured.')
    if not query:
        raise ValueError('Search query cannot be empty.')

    payload = {
        'q': query,
        'country': str(country or DEFAULT_WEB_SEARCH_COUNTRY).strip() or DEFAULT_WEB_SEARCH_COUNTRY,
        'search_lang': (
            str(search_language or DEFAULT_WEB_SEARCH_LANGUAGE).strip()
            or DEFAULT_WEB_SEARCH_LANGUAGE
        ),
        **get_brave_context_profile(context_size),
    }
    request_timeout = max(1.0, float(timeout_s))
    headers = {
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip',
        'Content-Type': 'application/json',
        'X-Subscription-Token': api_key,
    }

    last_error: RuntimeError | None = None

    for attempt in range(2):
        try:
            response = requests.post(
                'https://api.search.brave.com/res/v1/llm/context',
                headers=headers,
                json=payload,
                timeout=request_timeout,
            )
            if response.ok:
                return response.json()

            try:
                error_payload = response.json()
            except ValueError:
                error_payload = response.text.strip()

            last_error = RuntimeError(
                f'HTTP {response.status_code} from Brave Search: {error_payload}'
            )
            if response.status_code < 500 and response.status_code != 429:
                raise last_error
        except requests.RequestException as exc:
            last_error = RuntimeError(f'Brave Search request failed: {exc}')

        if attempt == 0:
            time.sleep(0.15)

    raise last_error or RuntimeError('Brave Search request failed.')


def _coerce_age_text(source_meta: dict[str, Any]) -> str:
    age_values = source_meta.get('age')
    if isinstance(age_values, list):
        for value in age_values:
            text = str(value or '').strip()
            if text:
                return text
    return ''


def _clean_snippet(text: str, *, max_chars: int = DEFAULT_WEB_SEARCH_MAX_SNIPPET_CHARS) -> str:
    cleaned = ' '.join(str(text or '').split()).strip()
    if not cleaned:
        return ''
    if len(cleaned) <= max_chars:
        return cleaned
    truncated = cleaned[: max(1, int(max_chars) - 3)].rstrip()
    return f'{truncated}...'


def _iter_grounding_entries(payload: dict[str, Any]):
    grounding = payload.get('grounding', {}) or {}

    generic_entries = grounding.get('generic', []) or []
    for entry in generic_entries:
        if isinstance(entry, dict):
            yield entry

    poi_entry = grounding.get('poi')
    if isinstance(poi_entry, dict) and poi_entry:
        yield poi_entry

    map_entries = grounding.get('map', []) or []
    for entry in map_entries:
        if isinstance(entry, dict):
            yield entry


def extract_response_sources(
    payload: dict[str, Any],
    *,
    max_sources: int = DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
) -> list[dict[str, str]]:
    """Collect distinct cited URLs from a Brave LLM Context payload."""
    sources: list[dict[str, str]] = []
    source_meta_map = payload.get('sources', {}) or {}
    seen_urls: set[str] = set()

    for entry in _iter_grounding_entries(payload):
        url = str(entry.get('url', '') or '').strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        source_meta = source_meta_map.get(url, {}) if isinstance(source_meta_map, dict) else {}
        if not isinstance(source_meta, dict):
            source_meta = {}

        source = {
            'title': (
                str(entry.get('title', '') or '').strip()
                or str(source_meta.get('title', '') or '').strip()
                or url
            ),
            'url': url,
        }
        hostname = str(source_meta.get('hostname', '') or '').strip()
        age_text = _coerce_age_text(source_meta)
        if hostname:
            source['hostname'] = hostname
        if age_text:
            source['age'] = age_text
        sources.append(source)

        if len(sources) >= max(1, int(max_sources)):
            return sources

    return sources


def extract_response_text(
    payload: dict[str, Any],
    *,
    max_sources: int = DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
    max_snippets_per_source: int = DEFAULT_WEB_SEARCH_MAX_SNIPPETS_PER_SOURCE,
    max_chars_per_snippet: int = DEFAULT_WEB_SEARCH_MAX_SNIPPET_CHARS,
) -> str:
    """Build a concise text grounding block from a Brave LLM Context payload."""
    output_text = str(payload.get('output_text', '') or '').strip()
    if output_text:
        return output_text

    source_meta_map = payload.get('sources', {}) or {}
    blocks: list[str] = []
    seen_urls: set[str] = set()

    for entry in _iter_grounding_entries(payload):
        url = str(entry.get('url', '') or '').strip()
        dedupe_key = url or json.dumps(entry, sort_keys=True, ensure_ascii=False)
        if dedupe_key in seen_urls:
            continue

        snippets = [
            cleaned
            for cleaned in (
                _clean_snippet(snippet, max_chars=max_chars_per_snippet)
                for snippet in (entry.get('snippets', []) or [])[: max(1, int(max_snippets_per_source))]
            )
            if cleaned
        ]
        if not snippets:
            continue

        seen_urls.add(dedupe_key)
        source_meta = source_meta_map.get(url, {}) if isinstance(source_meta_map, dict) else {}
        if not isinstance(source_meta, dict):
            source_meta = {}

        title = (
            str(entry.get('title', '') or '').strip()
            or str(source_meta.get('title', '') or '').strip()
            or url
            or 'Untitled source'
        )
        extras: list[str] = []
        hostname = str(source_meta.get('hostname', '') or '').strip()
        age_text = _coerce_age_text(source_meta)
        if hostname:
            extras.append(hostname)
        if age_text:
            extras.append(age_text)

        heading = title
        if extras:
            heading = f'{heading} | {" | ".join(extras)}'
        block = [heading]
        block.extend(f'- {snippet}' for snippet in snippets)
        blocks.append('\n'.join(block))

        if len(blocks) >= max(1, int(max_sources)):
            break

    return '\n\n'.join(blocks).strip()


def build_web_search_tool_output(
    query: str,
    *,
    payload: dict[str, Any] | None = None,
    error: str = '',
    max_sources: int = DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
) -> str:
    """Serialize Brave Search results into a tool output string for Realtime."""
    summary = ''
    sources: list[dict[str, str]] = []

    if payload:
        summary = extract_response_text(payload, max_sources=max_sources)
        sources = extract_response_sources(payload, max_sources=max_sources)

    if not summary and not error:
        error = 'No Brave Search grounding was returned.'

    tool_output: dict[str, Any] = {
        'ok': not error,
        'provider': 'brave',
        'query': query,
        'summary': summary,
        'sources': sources,
    }
    if error:
        tool_output['error'] = error

    return json.dumps(tool_output, ensure_ascii=False)
