from conversational_server.realtime_text_utils import ignored_short_transcript_reason


def test_non_latin_transcript_is_not_labeled_empty():
    assert ignored_short_transcript_reason('지구요') == 'unsupported_script_turn'


def test_content_single_word_is_not_rejected_as_short_turn():
    assert ignored_short_transcript_reason('Nature') == ''


def test_question_word_single_turn_is_allowed():
    assert ignored_short_transcript_reason('Who?') == ''


def test_filler_acknowledgement_is_still_ignored():
    assert ignored_short_transcript_reason('OK.') == 'single_filler_word'
