from conversational_server.realtime_text_utils import (
    assistant_echo_similarity,
    extract_opening_signature,
    is_probable_assistant_echo,
    should_preserve_paused_transcript,
)


def test_preserve_paused_transcript_for_resume_request():
    assert should_preserve_paused_transcript('Continue please') is True


def test_preserve_paused_transcript_for_direct_robot_reengagement():
    assert should_preserve_paused_transcript("OK robot, I'm back") is True


def test_drop_paused_transcript_for_side_conversation():
    assert should_preserve_paused_transcript(
        'I will have an interview within three months. Could you help me?'
    ) is False


def test_drop_paused_transcript_for_direct_address_without_resume_intent():
    assert should_preserve_paused_transcript('Hello robot') is False


def test_extract_opening_signature_normalizes_first_words():
    assert extract_opening_signature('Sure, I can help with that.') == 'sure i can help'
    assert extract_opening_signature('') == ''


def test_assistant_echo_detection_matches_partial_reply():
    reply = 'That is the current weather in Bucharest today.'
    transcript = 'the current weather in bucharest today'
    assert assistant_echo_similarity(transcript, reply) >= 85.0
    assert is_probable_assistant_echo(transcript, reply) is True


def test_assistant_echo_detection_ignores_new_request():
    reply = 'That is the current weather in Bucharest today.'
    transcript = 'Could you tell me again, please?'
    assert is_probable_assistant_echo(transcript, reply) is False
