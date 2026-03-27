from conversational_server.language_utils import (
    ConversationLanguageTracker,
    detect_explicit_language_choice,
    detect_text_language,
)


def test_detect_text_language_uses_marker_bias():
    assert detect_text_language('what is your favorite dog today') == 'en'
    assert detect_text_language('ce nume ai si cum pot sa te ajut acum') == 'ro'


def test_detect_explicit_language_choice_handles_direct_switch_request():
    assert detect_explicit_language_choice('please answer in romanian') == 'ro'
    assert detect_explicit_language_choice('vorbeste in engleza te rog') == 'en'


def test_conversation_language_tracker_does_not_switch_on_single_mixed_turn():
    tracker = ConversationLanguageTracker(switch_hits_required=2)
    assert tracker.observe('ce nume ai si cum pot sa te ajut acum') == 'ro'
    assert tracker.observe('what is your favorite dog') == 'ro'
    assert tracker.observe('what is your favorite dog') == 'en'


def test_conversation_language_tracker_switches_immediately_on_explicit_request():
    tracker = ConversationLanguageTracker(switch_hits_required=2)
    assert tracker.observe('ce nume ai si cum pot sa te ajut acum') == 'ro'
    assert tracker.observe('please answer in english') == 'en'


def test_conversation_language_tracker_prefers_live_detection_over_stored_preference():
    tracker = ConversationLanguageTracker(switch_hits_required=2)
    assert tracker.observe(
        'what is your favorite dog today',
        preferred_language='ro',
    ) == 'en'
