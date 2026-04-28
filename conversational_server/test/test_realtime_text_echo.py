from conversational_server.realtime_text_utils import is_probable_assistant_echo


def test_echo_detected_for_matching_long_phrase():
    assert is_probable_assistant_echo(
        "How are you today I'm glad you're here",
        "How are you today? I'm glad you're here. How can I help you today?",
    ) is True


def test_echo_not_detected_for_different_content():
    assert is_probable_assistant_echo(
        'What time is it in Bucharest?',
        'Please move forward two meters and then stop.',
    ) is False


def test_short_turn_not_marked_as_echo():
    assert is_probable_assistant_echo('yes', 'yes', min_length=8) is False
