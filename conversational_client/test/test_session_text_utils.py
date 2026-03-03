from conversational_client.session_text_utils import (
    detect_goodbye_keyword,
    goodbye_tts_command,
)


def test_detect_goodbye_keyword_matches_english_goodbye():
    assert detect_goodbye_keyword('Ok, goodbye for now.') == 'goodbye'


def test_detect_goodbye_keyword_matches_romanian_goodbye():
    assert detect_goodbye_keyword('Bine, la revedere.') == 'la revedere'


def test_detect_goodbye_keyword_matches_goodbye_clause_after_other_text():
    assert detect_goodbye_keyword("No, it's fine. Goodbye.") == 'goodbye'


def test_detect_goodbye_keyword_does_not_match_when_goodbye_is_only_mentioned():
    assert detect_goodbye_keyword('Can you explain what goodbye means?') == ''


def test_detect_goodbye_keyword_does_not_match_when_phrase_is_part_of_an_example():
    assert detect_goodbye_keyword('Write a short dialogue ending with see you later.') == ''


def test_detect_goodbye_keyword_does_not_match_when_goodbye_starts_a_question():
    assert detect_goodbye_keyword('Goodbye, what does that word mean?') == ''


def test_detect_goodbye_keyword_does_not_match_when_farewell_phrase_is_discussed():
    assert detect_goodbye_keyword('If someone says goodbye, should the robot close the session?') == ''


def test_detect_goodbye_keyword_does_not_match_when_phrase_is_quoted_for_translation():
    assert detect_goodbye_keyword('How do you translate "see you later" into Romanian?') == ''


def test_goodbye_tts_command_uses_romanian_when_language_is_ro():
    assert goodbye_tts_command('ro') == 'goodbye_ro'
    assert goodbye_tts_command('ro-RO') == 'goodbye_ro'


def test_goodbye_tts_command_defaults_to_english():
    assert goodbye_tts_command('en') == 'goodbye_en'
    assert goodbye_tts_command('') == 'goodbye_en'
