"""Escalation from the realtime voice model to a stronger text model.

The Realtime model is bound to the WebSocket URL at connect time, so it cannot
be swapped mid-conversation without dropping the socket (and the server-side
conversation state with it). Worse, the choice would have to be made *before*
hearing the turn — by the time a question is known to be hard, its audio has
already streamed to whichever model is connected.

So the tiers are split by role instead of by connection: the realtime model
stays on as the voice (turn-taking, prosody, emotion — most of the traffic and
where the per-minute cost lives), and delegates the occasional genuinely hard
question here, to a stronger text model reached over the Responses API. Text
tokens are far cheaper than realtime audio tokens, so the escalation costs
little and only on the turns that need it.

Same shape as ``openai_web_search``: schema, a blocking API call meant for a
worker thread, and a tool-output serializer. Kept out of the node so all three
stay unit-testable.
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import requests

from .openai_web_search import extract_response_text


DEEP_REASON_FUNCTION_NAME = 'deep_reason'
# A reasoning-tier model in its cheap size. Measured on the multi-step spatial
# question in the tests: gpt-4.1 answered the direction wrong, gpt-5.4-mini got
# it right, both in about 3s. Escalation is rare and text-only, so the reasoning
# tier costs little here; swap in a larger model for more depth.
DEFAULT_DEEP_REASON_MODEL = 'gpt-5.4-mini'
# Must default alongside the model above: a reasoning model called with no
# effort spends zero reasoning tokens and answers straight off the top of its
# head. Measured on the spatial question in the tests, that flips the answer
# from right to wrong. Set to '' only when pointing at a non-reasoning model.
DEFAULT_DEEP_REASON_EFFORT = 'medium'
DEFAULT_DEEP_REASON_TIMEOUT_S = 25.0
# Reasoning models spend hidden reasoning tokens out of this same budget, so it
# must clear the answer plus that. It is a ceiling, not a spend: unused tokens
# cost nothing, and the prompt already caps the spoken answer at ~80 words.
DEFAULT_DEEP_REASON_MAX_OUTPUT_TOKENS = 2000

# The answer is spoken aloud, so it must come back as speech, not as an essay.
_DEEP_REASON_SYSTEM_PROMPT = (
    'You are the reasoning back-end for a speaking robot. Another model handles the '
    'conversation and has escalated a hard question to you. Work the problem carefully, '
    'then reply with only the conclusion the robot should say out loud. '
    'Keep it under roughly 80 spoken words. Use plain sentences: no markdown, no bullet '
    'lists, no headings, no code blocks, no emoji, no step-by-step derivation. '
    'State the answer directly and commit to it. If the question is genuinely undecidable, '
    'say so in one sentence and give the single best available answer anyway. '
    'Answer in the same language the question is written in.'
)

_DEEP_REASON_TOOL = {
    'type': 'function',
    'name': DEEP_REASON_FUNCTION_NAME,
    'description': (
        'Escalate a genuinely hard question to a stronger reasoning model, then speak its '
        'answer. Use this for multi-step logic or maths, careful analysis or planning, '
        'debugging, comparing options against several criteria, or any question where a '
        'fast answer would likely be wrong. Do NOT use it for ordinary chat, opinions, '
        'greetings, robot movement commands, or anything about the current speaker — you '
        'answer those yourself. Do NOT use it for current events or live facts; that is '
        'what web_search is for. This takes a few seconds, so say a brief natural filler '
        'like "let me think about that for a second" before calling it.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'question': {
                'type': 'string',
                'description': (
                    'The question to reason about, written out in full. The reasoning model '
                    'cannot hear the conversation, so make it self-contained.'
                ),
            },
            'context': {
                'type': 'string',
                'description': (
                    'Optional relevant background from the conversation so far, if the '
                    'question depends on it.'
                ),
            },
        },
        'required': ['question'],
        'additionalProperties': False,
    },
}


def build_deep_reason_tool() -> dict[str, Any]:
    """Return a fresh ``deep_reason`` function-tool schema."""
    return deepcopy(_DEEP_REASON_TOOL)


def call_openai_deep_reason(
    api_key: str,
    question: str,
    *,
    context: str = '',
    model: str = DEFAULT_DEEP_REASON_MODEL,
    timeout_s: float = DEFAULT_DEEP_REASON_TIMEOUT_S,
    max_output_tokens: int = DEFAULT_DEEP_REASON_MAX_OUTPUT_TOKENS,
    effort: str = DEFAULT_DEEP_REASON_EFFORT,
) -> dict[str, Any]:
    """Ask a stronger text model to reason about ``question``.

    Blocking: call it from a worker thread so the audio stream keeps flowing.
    ``effort`` is only sent when non-empty, since non-reasoning models reject it.
    """
    user_input = question.strip()
    if context.strip():
        user_input = f'{user_input}\n\nConversation context:\n{context.strip()}'

    payload: dict[str, Any] = {
        'model': model,
        'instructions': _DEEP_REASON_SYSTEM_PROMPT,
        'input': user_input,
        'max_output_tokens': max(1, int(max_output_tokens)),
    }
    if effort.strip():
        payload['reasoning'] = {'effort': effort.strip()}

    response = requests.post(
        'https://api.openai.com/v1/responses',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        json=payload,
        timeout=max(1.0, float(timeout_s)),
    )

    if response.ok:
        return response.json()

    try:
        detail = response.json()
    except ValueError:
        detail = response.text.strip()
    raise RuntimeError(f'HTTP {response.status_code}: {detail}')


def build_deep_reason_tool_output(
    question: str,
    *,
    payload: dict[str, Any] | None = None,
    error: str = '',
) -> str:
    """Serialize a reasoning result into a Realtime tool-output string."""
    answer = extract_response_text(payload) if payload else ''

    if not answer and not error:
        error = 'No reasoning answer text was returned.'

    tool_output: dict[str, Any] = {
        'ok': not error,
        'question': question,
        'answer': answer,
    }
    if error:
        tool_output['error'] = error
        # The robot is mid-turn and must still say something useful.
        tool_output['fallback_instruction'] = (
            'The reasoning model did not answer. Tell the user briefly that you could not '
            'work it out just now, then answer as best you can yourself.'
        )

    return json.dumps(tool_output, ensure_ascii=False)
