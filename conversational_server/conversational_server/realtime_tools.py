"""Function-tool schemas for the OpenAI Realtime robot session.

These are the tools that carry robot context in and out of the speech-to-speech
model. Web search and the silent-turn no-op live in ``openai_web_search``; this
module owns the conversational/robot-state tools:

* ``report_user_emotion``  -> emotion awareness and prosody mirroring
* ``get_speaker_info``     -> personalization without a session rebuild
* ``remember_person``      -> name capture that drives voiceprint enrollment
* ``set_conversation_pause`` -> pause/resume resolved by intent, not by regex

Keeping them here (rather than in the node) makes the schemas unit-testable and
keeps ``openai_realtime_node`` focused on session/state handling.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


REPORT_EMOTION_FUNCTION_NAME = 'report_user_emotion'
GET_SPEAKER_INFO_FUNCTION_NAME = 'get_speaker_info'
REMEMBER_PERSON_FUNCTION_NAME = 'remember_person'
SET_PAUSE_FUNCTION_NAME = 'set_conversation_pause'

# The emotion vocabulary the prompt and the tool agree on. Downstream consumers
# (/user_emotion) can rely on the value being one of these.
EMOTION_LABELS = (
    'happy',
    'enthusiastic',
    'playful',
    'neutral',
    'curious',
    'serious',
    'sad',
    'tired',
    'anxious',
    'frustrated',
    'confused',
)


_REPORT_EMOTION_TOOL = {
    'type': 'function',
    'name': REPORT_EMOTION_FUNCTION_NAME,
    'description': (
        "Report the emotional state you perceive in the user's voice. Call this whenever you "
        'detect a clear affect, pitch, pacing, or tone shift, so the robot can mirror it. '
        'This does not produce speech by itself — keep answering normally.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'emotion': {
                'type': 'string',
                'description': 'The detected emotional state.',
                'enum': list(EMOTION_LABELS),
            },
            'reason': {
                'type': 'string',
                'description': 'Brief explanation of the vocal cues or context behind this.',
            },
        },
        'required': ['emotion'],
        'additionalProperties': False,
    },
}


_GET_SPEAKER_INFO_TOOL = {
    'type': 'function',
    'name': GET_SPEAKER_INFO_FUNCTION_NAME,
    'description': (
        "Retrieve the current speaker's identity context: their preferred spoken name, "
        'preferred language, and any known background facts. The speaker can change '
        'mid-conversation, so call this whenever you need to address someone by name or '
        'recall what you know about them.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {},
        'required': [],
        'additionalProperties': False,
    },
}


_REMEMBER_PERSON_TOOL = {
    'type': 'function',
    'name': REMEMBER_PERSON_FUNCTION_NAME,
    'description': (
        'Call this when the user introduces themselves by name, in any language or phrasing '
        '("my name is X", "sunt X", "ma numesc X", "I\'m X"). The robot enrolls their '
        'voiceprint so it recognizes them next time.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'name': {
                'type': 'string',
                'description': 'The spoken name the user gave for themselves.',
            },
            'language': {
                'type': 'string',
                'description': 'Language code of the introduction, e.g. "en" or "ro".',
            },
        },
        'required': ['name'],
        'additionalProperties': False,
    },
}


_SET_PAUSE_TOOL = {
    'type': 'function',
    'name': SET_PAUSE_FUNCTION_NAME,
    'description': (
        'Pause or resume the conversation. Call with paused=true when the user asks you to '
        'wait, hold on, give them a moment, or pause — they may talk to other people '
        'meanwhile and you must stay silent. Call with paused=false only when the same user '
        'says they are back, ready, or to continue.'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'paused': {
                'type': 'boolean',
                'description': 'true to pause the conversation, false to resume it.',
            },
        },
        'required': ['paused'],
        'additionalProperties': False,
    },
}


def build_report_emotion_tool() -> dict[str, Any]:
    """Return a fresh ``report_user_emotion`` function-tool schema."""
    return deepcopy(_REPORT_EMOTION_TOOL)


def build_get_speaker_info_tool() -> dict[str, Any]:
    """Return a fresh ``get_speaker_info`` function-tool schema."""
    return deepcopy(_GET_SPEAKER_INFO_TOOL)


def build_remember_person_tool() -> dict[str, Any]:
    """Return a fresh ``remember_person`` function-tool schema."""
    return deepcopy(_REMEMBER_PERSON_TOOL)


def build_set_conversation_pause_tool() -> dict[str, Any]:
    """Return a fresh ``set_conversation_pause`` function-tool schema."""
    return deepcopy(_SET_PAUSE_TOOL)


def normalize_emotion(value: str) -> str:
    """Clamp a model-provided emotion to the agreed vocabulary."""
    candidate = (value or '').strip().lower()
    return candidate if candidate in EMOTION_LABELS else 'neutral'
