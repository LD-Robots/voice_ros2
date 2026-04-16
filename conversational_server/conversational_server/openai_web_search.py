"""Backward-compatible wrappers around the Brave Search web-search helper."""

from .brave_web_search import (  # noqa: F401
    DEFAULT_WEB_SEARCH_CONTEXT_SIZE,
    DEFAULT_WEB_SEARCH_COUNTRY,
    DEFAULT_WEB_SEARCH_LANGUAGE,
    DEFAULT_WEB_SEARCH_MAX_SNIPPET_CHARS,
    DEFAULT_WEB_SEARCH_MAX_SNIPPETS_PER_SOURCE,
    DEFAULT_WEB_SEARCH_SOURCES_LIMIT,
    DEFAULT_WEB_SEARCH_TIMEOUT_S,
    WEB_SEARCH_FUNCTION_NAME,
    build_realtime_web_search_tool,
    build_web_search_tool_output,
    call_brave_web_search,
    extract_response_sources,
    extract_response_text,
    get_brave_context_profile,
)


DEFAULT_WEB_SEARCH_MODEL = 'gpt-4.1-mini'
DEFAULT_WEB_SEARCH_MAX_OUTPUT_TOKENS = 400


def call_openai_web_search(
    api_key: str,
    query: str,
    *,
    model: str = DEFAULT_WEB_SEARCH_MODEL,
    search_context_size: str = DEFAULT_WEB_SEARCH_CONTEXT_SIZE,
    timeout_s: float = DEFAULT_WEB_SEARCH_TIMEOUT_S,
    max_output_tokens: int = DEFAULT_WEB_SEARCH_MAX_OUTPUT_TOKENS,
):
    """Compatibility wrapper that now uses Brave Search grounding."""
    del model
    del max_output_tokens
    return call_brave_web_search(
        api_key,
        query,
        context_size=search_context_size,
        timeout_s=timeout_s,
    )
