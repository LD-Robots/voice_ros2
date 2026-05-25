from conversational_client.conversation_utils import (
    StickySpeakerTracker,
    advance_attention_focus,
    decide_attention,
    can_accept_control_action,
    can_accept_reengagement,
    detect_control_action,
    is_reengagement_phrase,
    normalize_text,
)


def test_sticky_speaker_tracker_keeps_last_known_speaker_temporarily():
    tracker = StickySpeakerTracker(timeout_s=5.0)

    assert tracker.update('Vasile', now=10.0) == 'Vasile'
    assert tracker.update('Unknown', now=13.0) == 'Vasile'


def test_sticky_speaker_tracker_expires_after_timeout():
    tracker = StickySpeakerTracker(timeout_s=5.0)

    assert tracker.update('Vasile', now=10.0) == 'Vasile'
    assert tracker.update('Unknown', now=16.1) == 'Unknown'


def test_sticky_speaker_tracker_requires_confirmed_switch_between_known_speakers():
    tracker = StickySpeakerTracker(timeout_s=5.0, switch_hits_required=2)

    assert tracker.update('Vasile', now=10.0) == 'Vasile'
    assert tracker.update('Delia', now=11.0) == 'Vasile'
    assert tracker.update('Delia', now=12.0) == 'Delia'


def test_sticky_speaker_tracker_reset_clears_speaker_state():
    tracker = StickySpeakerTracker(timeout_s=5.0)

    assert tracker.update('Vasile', now=10.0) == 'Vasile'
    tracker.reset()
    assert tracker.update('Unknown', now=11.0) == 'Unknown'


def test_attention_focus_requires_real_recognition_to_delegate():
    focus, focus_time = advance_attention_focus(
        current_speaker='Vasile',
        focused_speaker='Unknown',
        last_focus_time=0.0,
        allow=True,
        recognized_speaker='Unknown',
        now=12.0,
    )
    assert focus == 'Unknown'
    assert focus_time == 12.0

    focus, focus_time = advance_attention_focus(
        current_speaker='Vasile',
        focused_speaker='Unknown',
        last_focus_time=0.0,
        allow=True,
        recognized_speaker='Vasile',
        now=13.0,
    )
    assert focus == 'Vasile'
    assert focus_time == 13.0


def test_control_and_reengagement_detection_cover_pause_resume_flow():
    assert detect_control_action(normalize_text('Just wait a second, I need to talk with someone.')) == 'hold_on'
    assert is_reengagement_phrase(normalize_text("OK robot, I'm back")) is True


def test_control_detection_handles_generic_pause_phrases():
    assert detect_control_action(normalize_text("Please pause for a moment, I'm on the phone.")) == 'hold_on'
    assert detect_control_action(normalize_text('Pick up where you left off.')) == 'continue'


def test_control_detection_avoids_false_pause_and_stop_triggers():
    assert detect_control_action(normalize_text("I can't wait to start the interview.")) is None
    assert detect_control_action(normalize_text('We should stop climate change.')) is None
    assert detect_control_action(normalize_text('To continue this plan we need more data.')) is None
    assert is_reengagement_phrase(normalize_text('Hello robot')) is False


def test_reengagement_requires_clear_return_signal():
    assert is_reengagement_phrase(normalize_text("Robot, I'm back and ready now.")) is True
    assert is_reengagement_phrase(normalize_text('I will be back tomorrow.')) is False


def test_resume_word_inside_normal_sentence_does_not_trigger_continue():
    assert detect_control_action(normalize_text('Now why did you resume?')) is None


def test_control_acceptance_requires_known_speaker_or_direct_address():
    assert can_accept_control_action(
        'hold_on',
        current_speaker='Vasile',
        session_active=True,
        conversation_paused=False,
        direct_address=False,
    ) is True
    assert can_accept_control_action(
        'hold_on',
        current_speaker='Unknown',
        session_active=True,
        conversation_paused=False,
        direct_address=False,
    ) is False
    assert can_accept_control_action(
        'hold_on',
        current_speaker='Unknown',
        session_active=True,
        conversation_paused=False,
        direct_address=True,
    ) is True
    assert can_accept_control_action(
        'continue',
        current_speaker='Unknown',
        session_active=True,
        conversation_paused=True,
        direct_address=False,
    ) is False
    assert can_accept_control_action(
        'hold_on',
        current_speaker='Unknown',
        focused_speaker='Vasile',
        session_active=True,
        conversation_paused=False,
        direct_address=False,
        normalized_text=normalize_text("Please pause for a moment, I'm on the phone."),
    ) is True


def test_reengagement_acceptance_requires_paused_state_and_identity_signal():
    assert can_accept_reengagement(
        current_speaker='Vasile',
        session_active=True,
        conversation_paused=True,
        direct_address=False,
    ) is True
    assert can_accept_reengagement(
        current_speaker='Unknown',
        session_active=True,
        conversation_paused=True,
        direct_address=False,
    ) is False
    assert can_accept_reengagement(
        current_speaker='Unknown',
        session_active=True,
        conversation_paused=True,
        direct_address=True,
    ) is True


def test_attention_decision_rejects_unknown_side_conversation_when_focus_exists():
    allow, reason, focused_speaker, _ = decide_attention(
        session_active=True,
        conversation_paused=False,
        current_speaker='Unknown',
        focused_speaker='Vasile',
        last_focus_time=10.0,
        focus_timeout_s=45.0,
        allow_known_speaker_switch_without_address=True,
        direct_address=False,
        reengagement=False,
        robot_directive=False,
        control_action=None,
        normalized_text=normalize_text('We should discuss the exam later.'),
        now=12.0,
    )
    assert allow is False
    assert reason == 'unknown_side_conversation'
    assert focused_speaker == 'Vasile'


def test_attention_decision_allows_known_speaker_switch_without_direct_address_when_enabled():
    allow, reason, focused_speaker, focus_time = decide_attention(
        session_active=True,
        conversation_paused=False,
        current_speaker='Delia',
        focused_speaker='Vasile',
        last_focus_time=10.0,
        focus_timeout_s=45.0,
        allow_known_speaker_switch_without_address=True,
        direct_address=False,
        reengagement=False,
        robot_directive=False,
        control_action=None,
        normalized_text=normalize_text('Tell me about Hamilton.'),
        now=12.0,
    )
    assert allow is True
    assert reason == 'speaker_switch_without_address'
    assert focused_speaker == 'Delia'
    assert focus_time == 12.0


def test_attention_decision_can_require_direct_address_for_initial_focus():
    allow, reason, focused_speaker, _ = decide_attention(
        session_active=True,
        conversation_paused=False,
        current_speaker='Unknown',
        focused_speaker='Unknown',
        last_focus_time=0.0,
        focus_timeout_s=45.0,
        require_direct_address_for_new_focus=True,
        initial_turn_grace=False,
        allow_known_speaker_switch_without_address=False,
        direct_address=False,
        reengagement=False,
        robot_directive=False,
        control_action=None,
        normalized_text=normalize_text('Did you finish the homework?'),
        now=12.0,
    )
    assert allow is False
    assert reason == 'no_focus_without_direct_address'
    assert focused_speaker == 'Unknown'

    allow, reason, focused_speaker, _ = decide_attention(
        session_active=True,
        conversation_paused=False,
        current_speaker='Unknown',
        focused_speaker='Unknown',
        last_focus_time=0.0,
        focus_timeout_s=45.0,
        require_direct_address_for_new_focus=True,
        initial_turn_grace=False,
        allow_known_speaker_switch_without_address=False,
        direct_address=True,
        reengagement=False,
        robot_directive=False,
        control_action=None,
        normalized_text=normalize_text('Robot, did you finish the homework?'),
        now=12.0,
    )
    assert allow is True
    assert reason == 'no_focus_directed'


def test_attention_decision_allows_first_turn_after_wake_grace():
    allow, reason, focused_speaker, _ = decide_attention(
        session_active=True,
        conversation_paused=False,
        current_speaker='Unknown',
        focused_speaker='Unknown',
        last_focus_time=0.0,
        focus_timeout_s=45.0,
        require_direct_address_for_new_focus=True,
        initial_turn_grace=True,
        allow_known_speaker_switch_without_address=False,
        direct_address=False,
        reengagement=False,
        robot_directive=False,
        control_action=None,
        normalized_text=normalize_text('Tell me a story.'),
        now=12.0,
    )
    assert allow is True
    assert reason == 'initial_turn_after_wake'
    assert focused_speaker == 'Unknown'


def test_attention_decision_can_keep_strict_speaker_switch_policy_when_disabled():
    allow, reason, focused_speaker, _ = decide_attention(
        session_active=True,
        conversation_paused=False,
        current_speaker='Delia',
        focused_speaker='Vasile',
        last_focus_time=10.0,
        focus_timeout_s=45.0,
        allow_known_speaker_switch_without_address=False,
        direct_address=False,
        reengagement=False,
        robot_directive=False,
        control_action=None,
        normalized_text=normalize_text('Tell me about Hamilton.'),
        now=12.0,
    )
    assert allow is False
    assert reason == 'different_speaker_without_address'
    assert focused_speaker == 'Vasile'
