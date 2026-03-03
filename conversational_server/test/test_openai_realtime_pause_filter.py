from conversational_server.realtime_text_utils import should_preserve_paused_transcript


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
