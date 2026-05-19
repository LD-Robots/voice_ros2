"""Helpers for OpenAI/Brave web search decisions and tool output."""
from __future__ import annotations

import json
import re
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
DEFAULT_BRAVE_LLM_CONTEXT_COUNT = 10
DEFAULT_BRAVE_LLM_CONTEXT_TOKENS = 8192
DEFAULT_BRAVE_LLM_CONTEXT_SNIPPETS = 120
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

WEB_SEARCH_EXPLICIT_PATTERNS = (
    r'\b(search|look up|google|browse|check online|on the internet|online)\b',
    r'\b(cauta|caută|verifica online|pe internet)\b',
)
WEB_SEARCH_SOCIAL_PATTERNS = (
    r'\b(how are you|how are things|how is it going|how\'s it going|how do you feel)\b',
    r'\b(ce faci|cum esti|cum ești|cum te simti|cum te simți)\b',
)
WEB_SEARCH_CURRENT_PATTERNS = (
    r'\b(today|tomorrow|yesterday|tonight|this week|this month|latest|current|recent|now|live|breaking)\b',
    r'\b(azi|maine|mâine|ieri|diseara|saptamana asta|săptămâna asta|ultima|ultimele|curent|recent|acum)\b',
)
WEB_SEARCH_VOLATILE_TOPICS = (
    r'\b(weather|forecast|temperature|rain|snow|traffic|flight|flights)\b',
    r'\b(news|price|prices|stock|stocks|crypto|bitcoin|exchange rate|currency)\b',
    r'\b(score|scores|result|results|match|matches|fixture|fixtures|schedule|standings|league|football|soccer)\b',
    r'\b(election|elections|vote|candidate|president|prime minister|minister|mayor|ceo of)\b',
    r'\b(politics|political|government|parliament|war|conflict|ukraine|russia|nato|defense|defence)\b',
    r'\b(vreme|prognoza|prognoză|temperatura|temperatură|ploaie|ninsoare|trafic|zbor|zboruri)\b',
    r'\b(stiri|știri|pret|preț|preturi|prețuri|actiuni|acțiuni|curs valutar|moneda|monedă)\b',
    r'\b(scor|rezultat|rezultate|meci|meciuri|program|clasament|liga|fotbal)\b',
    r'\b(alegeri|vot|candidat|presedinte|președinte|prim ministru|ministru|primar)\b',
    r'\b(politica|politică|guvern|parlament|razboi|război|ucraina|rusia|nato|aparare|apărare)\b',
)
WEB_SEARCH_CHANGEABLE_QUESTION_PATTERNS = (
    r'\b(who is|who are|who won|what happened|when is|where is)\b',
    r'\b(cine este|cine sunt|cine a castigat|cine a câștigat|ce s a intamplat|ce s-a întâmplat|cand este|când este)\b',
)

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


def should_use_web_search(text: str) -> tuple[bool, str]:
    """Return whether a user request should be answered with fresh web data."""
    normalized = str(text or '').strip().lower()
    if not normalized:
        return False, ''

    if _matches_any(normalized, WEB_SEARCH_EXPLICIT_PATTERNS):
        return True, 'the user explicitly asked to search online'

    if _matches_any(normalized, WEB_SEARCH_SOCIAL_PATTERNS):
        return False, 'social conversation does not need web search'

    has_current_signal = _matches_any(normalized, WEB_SEARCH_CURRENT_PATTERNS)
    has_volatile_topic = _matches_any(normalized, WEB_SEARCH_VOLATILE_TOPICS)
    if has_volatile_topic:
        return True, 'the topic changes over time'
    if has_current_signal:
        return True, 'the request is time-sensitive'

    if _matches_any(normalized, WEB_SEARCH_CHANGEABLE_QUESTION_PATTERNS) and _looks_like_question(normalized):
        return True, 'the factual answer may have changed recently'

    return False, ''


def build_web_search_reasoning_hint(text: str) -> str:
    """Build a short per-turn instruction for Realtime search decisions."""
    needed, reason = should_use_web_search(text)
    if not needed:
        return ''
    return (
        'The current user request needs fresh web information because '
        f'{reason}. Call the web_search tool before answering. '
        'After the tool result arrives, answer from that result and mention uncertainty briefly if results are weak.'
    )


def _matches_any(text: str, patterns) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _looks_like_question(text: str) -> bool:
    if '?' in text:
        return True
    return text.startswith((
        'who ', 'what ', 'when ', 'where ', 'which ', 'how ',
        'cine ', 'ce ', 'cand ', 'când ', 'unde ', 'care ', 'cum ',
    ))


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
            'extra_snippets': 'true',
            'enable_rich_callback': 1,
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


def call_brave_llm_context(
    api_key: str,
    query: str,
    *,
    country: str = DEFAULT_BRAVE_SEARCH_COUNTRY,
    search_lang: str = DEFAULT_BRAVE_SEARCH_LANG,
    count: int = DEFAULT_BRAVE_LLM_CONTEXT_COUNT,
    max_tokens: int = DEFAULT_BRAVE_LLM_CONTEXT_TOKENS,
    max_snippets: int = DEFAULT_BRAVE_LLM_CONTEXT_SNIPPETS,
    timeout_s: float = DEFAULT_WEB_SEARCH_TIMEOUT_S,
) -> dict[str, Any]:
    """Execute Brave LLM Context search for agent-ready grounding."""
    normalized_country = str(country or DEFAULT_BRAVE_SEARCH_COUNTRY).strip().upper()
    if normalized_country == 'ALL':
        normalized_country = 'US'
    if normalized_country not in BRAVE_SUPPORTED_COUNTRIES:
        normalized_country = 'US'
    normalized_lang = str(search_lang or DEFAULT_BRAVE_SEARCH_LANG).strip().lower()
    if normalized_lang not in BRAVE_SUPPORTED_LANGS:
        normalized_lang = DEFAULT_BRAVE_SEARCH_LANG

    response = requests.get(
        'https://api.search.brave.com/res/v1/llm/context',
        headers={
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
            'X-Subscription-Token': api_key,
        },
        params={
            'q': query,
            'count': max(1, min(50, int(count))),
            'country': normalized_country,
            'search_lang': normalized_lang,
            'spellcheck': 'true',
            'enable_source_metadata': 'true',
            'maximum_number_of_tokens': max(1024, min(32768, int(max_tokens))),
            'maximum_number_of_snippets': max(1, min(256, int(max_snippets))),
            'context_threshold_mode': 'lenient',
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
        extra_snippets = item.get('extra_snippets', []) or []
        if extra_snippets:
            result['extra_snippets'] = [
                str(snippet).strip()
                for snippet in extra_snippets
                if str(snippet).strip()
            ][:5]
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
                for snippet in source.get('extra_snippets', []) or []:
                    line += f"\n   {snippet}"
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


def build_brave_llm_context_tool_output(
    query: str,
    *,
    payload: dict[str, Any] | None = None,
    error: str = '',
    max_sources: int = DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
) -> str:
    """Serialize Brave LLM Context grounding into a compact tool output."""
    sources: list[dict[str, str]] = []
    summary = ''

    if payload:
        grounding = payload.get('grounding', {}) or {}
        generic_items = list(grounding.get('generic', []) or [])
        source_meta = payload.get('sources', {}) or {}
        lines = []
        seen_urls: set[str] = set()
        for idx, item in enumerate(generic_items[:max(1, int(max_sources))], start=1):
            url = str(item.get('url', '') or '').strip()
            title = str(item.get('title', '') or '').strip()
            if not title and url in source_meta:
                title = str(source_meta[url].get('title', '') or '').strip()
            if not title:
                title = url or f'Source {idx}'
            snippets = [
                str(snippet).strip()
                for snippet in (
                    item.get('snippets', [])
                    or item.get('text', [])
                    or item.get('chunks', [])
                    or []
                )
                if str(snippet).strip()
            ][:10]
            if not snippets:
                content = str(
                    item.get('content', '')
                    or item.get('description', '')
                    or item.get('markdown', '')
                    or ''
                ).strip()
                if content:
                    snippets = [content]
            source_line = f"{idx}. {title}"
            if url:
                source_line += f" - {url}"
            if snippets:
                source_line += '\n   ' + '\n   '.join(snippets)
            lines.append(source_line)
            if url and url not in seen_urls:
                seen_urls.add(url)
                sources.append({'title': title, 'url': url})
        summary = '\n'.join(lines)

    if not summary and not error:
        error = 'No Brave LLM Context grounding was returned.'

    tool_output = {
        'ok': not error,
        'provider': 'brave_llm_context',
        'query': query,
        'summary': summary,
        'sources': sources,
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
