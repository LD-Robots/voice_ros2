import pytest

pytest.importorskip('rclpy')

from conversational_server.llm_node import LLMNode  # noqa: E402


def test_search_router_sanity_blocks_contradictory_search_reason():
    should_search, reason, query = LLMNode._sanitize_search_router_decision(
        True,
        'user asks for clarification about behavior - no need for fresh online info',
        'this query should not run',
        'original text',
    )

    assert should_search is False
    assert 'no need for fresh online info' in reason
    assert query == ''


def test_search_router_sanity_truncates_long_query():
    long_query = ' '.join(f'word{i}' for i in range(60))

    should_search, reason, query = LLMNode._sanitize_search_router_decision(
        True,
        'current information request',
        long_query,
        'original text',
    )

    assert should_search is True
    assert len(query.split()) == 45
    assert 'truncated' in reason
