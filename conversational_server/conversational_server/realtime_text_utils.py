from difflib import SequenceMatcher
import re
import time
import unicodedata

from conversational_client.conversation_config import load_conversation_rules

_RULES = load_conversation_rules()
REENGAGEMENT_PHRASES = tuple(_RULES['reengagement_phrases'])
CONTINUE_PHRASES = tuple(_RULES['continue_phrases'])
CONTINUE_STEMS = tuple(_RULES['continue_stems'])
MULTIWORD_CONTINUE_PHRASES = tuple(phrase for phrase in CONTINUE_PHRASES if ' ' in phrase)

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False


class StickySpeakerTracker:
    """Keep the last known speaker through brief Unknown detections."""

    def __init__(self, timeout_s: float = 60.0, switch_hits_required: int = 2):
        self.timeout_s = max(0.0, float(timeout_s))
        self.switch_hits_required = max(1, int(switch_hits_required))
        self.current_speaker = 'Unknown'
        self.last_known_at = 0.0
        self.pending_speaker = 'Unknown'
        self.pending_hits = 0

    def update(self, speaker: str, now: float | None = None) -> str:
        now = time.monotonic() if now is None else float(now)
        speaker = (speaker or '').strip() or 'Unknown'

        if speaker != 'Unknown':
            if speaker == self.current_speaker:
                self.last_known_at = now
                self.pending_speaker = 'Unknown'
                self.pending_hits = 0
                return self.current_speaker

            if self.current_speaker == 'Unknown':
                self.current_speaker = speaker
                self.last_known_at = now
                self.pending_speaker = 'Unknown'
                self.pending_hits = 0
                return self.current_speaker

            if speaker == self.pending_speaker:
                self.pending_hits += 1
            else:
                self.pending_speaker = speaker
                self.pending_hits = 1

            if self.pending_hits >= self.switch_hits_required:
                self.current_speaker = speaker
                self.last_known_at = now
                self.pending_speaker = 'Unknown'
                self.pending_hits = 0
                return self.current_speaker

            self.last_known_at = now
            return self.current_speaker

        if self.current_speaker != 'Unknown' and (now - self.last_known_at) <= self.timeout_s:
            return self.current_speaker

        self.current_speaker = 'Unknown'
        self.pending_speaker = 'Unknown'
        self.pending_hits = 0
        return self.current_speaker

    def reset(self):
        self.current_speaker = 'Unknown'
        self.last_known_at = 0.0
        self.pending_speaker = 'Unknown'
        self.pending_hits = 0


def normalize_realtime_text(text: str) -> str:
    normalized = unicodedata.normalize('NFKD', text.lower())
    normalized = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = re.sub(r'[^a-z0-9\\s]+', ' ', normalized)
    return ' '.join(normalized.split())


def is_resume_request(text: str) -> bool:
    normalized = normalize_realtime_text(text)
    if any(pattern in normalized for pattern in MULTIWORD_CONTINUE_PHRASES):
        return True
    return any(re.search(rf'\b{re.escape(stem)}[a-z]*\b', normalized) for stem in CONTINUE_STEMS)


def should_preserve_paused_transcript(text: str) -> bool:
    normalized = normalize_realtime_text(text)
    if not normalized:
        return False
    if is_resume_request(text):
        return True
    if any(pattern in normalized for pattern in REENGAGEMENT_PHRASES):
        return True

    has_robot_address = 'robot' in normalized
    has_return_signal = any(token in normalized for token in ('back', 'return', 'revin', 'reven', 'inapoi'))
    has_ready_signal = any(token in normalized for token in ('here', 'ready', 'again', 'gata', 'acum'))
    return has_robot_address and has_return_signal and has_ready_signal


def assistant_echo_similarity(user_text: str, assistant_text: str) -> float:
    user_norm = normalize_realtime_text(user_text)
    assistant_norm = normalize_realtime_text(assistant_text)
    if not user_norm or not assistant_norm:
        return 0.0
    if user_norm in assistant_norm or assistant_norm in user_norm:
        return 100.0
    if RAPIDFUZZ_AVAILABLE:
        return float(
            max(
                fuzz.partial_ratio(user_norm, assistant_norm),
                fuzz.token_set_ratio(user_norm, assistant_norm),
            )
        )
    return float(SequenceMatcher(None, user_norm, assistant_norm).ratio() * 100.0)


def is_probable_assistant_echo(
    user_text: str,
    assistant_text: str,
    *,
    threshold: float = 85.0,
    min_length: int = 8,
) -> bool:
    user_norm = normalize_realtime_text(user_text)
    assistant_norm = normalize_realtime_text(assistant_text)
    if len(user_norm) < max(1, int(min_length)) or len(assistant_norm) < max(1, int(min_length)):
        return False
    return assistant_echo_similarity(user_norm, assistant_norm) >= float(threshold)


def ignored_short_transcript_reason(
    text: str,
    *,
    waiting_for_robot_confirmation: bool = False,
    last_accepted_transcript_norm: str = '',
    last_accepted_transcript_at: float = 0.0,
    dedupe_window_s: float = 4.0,
    now: float | None = None,
) -> str:
    raw_text = ' '.join(str(text or '').split()).strip()
    if not raw_text:
        return 'empty'

    normalized = normalize_realtime_text(text)
    if not normalized:
        return 'unsupported_script_turn'

    control_patterns = (
        'stop',
        'stop talking',
        'be quiet',
        'quiet',
        'hold on',
        'wait',
        'wait a second',
        'wait a little',
        'wait a bit',
        'continue',
        'resume',
        'repeat',
        'say that again',
        'continua',
        'reia',
        'repeta',
        'stai putin',
        'asteapta putin',
        'opreste',
        'taci',
    )
    if any(
        pattern == normalized or f' {pattern} ' in f' {normalized} '
        for pattern in control_patterns
    ):
        return ''

    words = normalized.split()
    if not words:
        return 'unsupported_script_turn'

    yes_no_words = {'yes', 'no', 'da', 'nu'}
    if len(words) == 1 and words[0] in yes_no_words:
        if waiting_for_robot_confirmation:
            return ''
        return 'stray_yes_no'

    now = time.monotonic() if now is None else float(now)
    if (
        normalized
        and normalized == (last_accepted_transcript_norm or '')
        and (now - float(last_accepted_transcript_at or 0.0)) <= float(dedupe_window_s)
        and len(words) <= 4
    ):
        return 'duplicate_short_turn'

    question_words = {
        'who', 'what', 'when', 'where', 'why', 'how',
        'cine', 'ce', 'cand', 'unde', 'cum',
    }
    filler_words = {
        'ok', 'okay', 'and', 'so', 'well', 'sure', 'right',
        'hello', 'hi', 'hey', 'uh', 'um', 'hmm', 'huh',
    }
    if len(words) == 1 and '?' in raw_text and words[0] in question_words:
        return ''

    if len(words) == 1 and words[0] in filler_words:
        return 'single_filler_word'

    if len(words) == 1 and len(words[0]) <= 4:
        return 'single_short_word'

    if len(words) == 2 and all(word in filler_words or len(word) <= 2 for word in words):
        return 'very_short_fragment'

    return ''
