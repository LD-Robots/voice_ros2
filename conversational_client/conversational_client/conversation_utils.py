import re
import time
import unicodedata

from .conversation_config import load_conversation_rules

_RULES = load_conversation_rules()
DIRECT_ROBOT_PREFIXES = tuple(_RULES['direct_robot_prefixes'])
REENGAGEMENT_PHRASES = tuple(_RULES['reengagement_phrases'])
FIRST_PERSON_WORDS = tuple(_RULES['first_person_words'])
PAUSE_WORDS = tuple(_RULES['pause_words'])
SHORT_DELAY_WORDS = tuple(_RULES['short_delay_words'])
STRONG_PAUSE_PHRASES = tuple(_RULES['strong_pause_phrases'])
SIDE_CONVERSATION_PHRASES = tuple(_RULES['side_conversation_phrases'])
CONTINUE_PHRASES = tuple(_RULES['continue_phrases'])
CONTINUE_STEMS = tuple(_RULES['continue_stems'])
REPEAT_PHRASES = tuple(_RULES['repeat_phrases'])
REPEAT_STEMS = tuple(_RULES['repeat_stems'])
STOP_PHRASES = tuple(_RULES['stop_phrases'])
STOP_STEMS = tuple(_RULES['stop_stems'])
SHORT_REPEAT_WORDS = tuple(_RULES['short_repeat_words'])
SHORT_CONTINUE_WORDS = tuple(_RULES['short_continue_words'])
SHORT_PAUSE_WORDS = tuple(_RULES['short_pause_words'])
MULTIWORD_CONTINUE_PHRASES = tuple(phrase for phrase in CONTINUE_PHRASES if ' ' in phrase)
MULTIWORD_REPEAT_PHRASES = tuple(phrase for phrase in REPEAT_PHRASES if ' ' in phrase)
MULTIWORD_STOP_PHRASES = tuple(phrase for phrase in STOP_PHRASES if ' ' in phrase)


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


def normalize_text(text: str) -> str:
    text = (text or '').lower().strip()
    text = unicodedata.normalize('NFD', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    text = text.replace('-', ' ')
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    return ' '.join(text.split())


def _phrase_pattern(phrase: str) -> str:
    tokens = [re.escape(token) for token in phrase.split() if token]
    if not tokens:
        return ''
    return r'\b' + r'\s+'.join(tokens) + r'\b'


def contains_phrase(normalized_text: str, phrases) -> bool:
    for phrase in phrases:
        pattern = _phrase_pattern(phrase)
        if pattern and re.search(pattern, normalized_text):
            return True
    return False


def contains_standalone_word(normalized_text: str, word: str) -> bool:
    return bool(re.search(rf'\b{re.escape(word)}\b', normalized_text))


def contains_word_stem(normalized_text: str, stems) -> bool:
    for stem in stems:
        if re.search(rf'\b{re.escape(stem)}[a-z]*\b', normalized_text):
            return True
    return False


def contains_any_word(normalized_text: str, words) -> bool:
    return any(contains_standalone_word(normalized_text, word) for word in words)


def starts_with_word_stem(normalized_text: str, stems) -> bool:
    for stem in stems:
        if re.match(rf'^{re.escape(stem)}[a-z]*\b', normalized_text):
            return True
    return False


def strip_robot_prefixes(normalized_text: str) -> str:
    text = normalized_text.strip()
    changed = True
    while changed and text:
        changed = False
        for prefix in DIRECT_ROBOT_PREFIXES:
            if text.startswith(prefix + ' '):
                text = text[len(prefix):].strip()
                changed = True
                break
    return text


def has_direct_robot_address(normalized_text: str) -> bool:
    if not normalized_text:
        return False
    if normalized_text in DIRECT_ROBOT_PREFIXES:
        return True
    if normalized_text.startswith(tuple(f'{phrase} ' for phrase in DIRECT_ROBOT_PREFIXES)):
        return True
    if contains_standalone_word(normalized_text, 'robot'):
        return True
    return False


def is_reengagement_phrase(normalized_text: str) -> bool:
    if not normalized_text:
        return False
    if contains_phrase(normalized_text, REENGAGEMENT_PHRASES):
        return True
    if contains_phrase(normalized_text, ('be right back', 'right back', 'revin imediat')):
        return False
    has_return_signal = contains_word_stem(
        normalized_text,
        ('back', 'return', 'revin', 'reven', 'inapoi'),
    )
    if not has_return_signal:
        return False
    has_first_person = contains_any_word(normalized_text, FIRST_PERSON_WORDS)
    has_ready_signal = contains_word_stem(
        normalized_text,
        ('here', 'gata', 'acum', 'ready', 'again', 'continu'),
    )
    if has_first_person and (has_direct_robot_address(normalized_text) or has_ready_signal):
        return True
    return False


def detect_control_action(normalized_text: str) -> str | None:
    if not normalized_text:
        return None

    words = normalized_text.split()
    word_count = len(words)
    command_text = strip_robot_prefixes(normalized_text)

    if is_explicit_pause_request(normalized_text):
        return 'hold_on'

    if contains_phrase(normalized_text, MULTIWORD_CONTINUE_PHRASES):
        return 'continue'
    if starts_with_word_stem(command_text, CONTINUE_STEMS):
        if len(command_text.split()) <= 6:
            return 'continue'

    if contains_phrase(normalized_text, MULTIWORD_REPEAT_PHRASES):
        return 'repeat'
    if starts_with_word_stem(command_text, REPEAT_STEMS):
        if len(command_text.split()) <= 6:
            return 'repeat'

    if contains_phrase(normalized_text, MULTIWORD_STOP_PHRASES):
        return 'stop'
    if starts_with_word_stem(command_text, STOP_STEMS):
        if len(command_text.split()) <= 4:
            return 'stop'
    if contains_phrase(normalized_text, SHORT_REPEAT_WORDS) and word_count <= 2:
        return 'repeat'
    if contains_phrase(normalized_text, SHORT_CONTINUE_WORDS) and word_count <= 3:
        return 'continue'
    if contains_phrase(normalized_text, SHORT_PAUSE_WORDS) and word_count <= 2:
        return 'hold_on'

    return None


def is_explicit_pause_request(normalized_text: str) -> bool:
    if not normalized_text:
        return False

    if contains_phrase(normalized_text, STRONG_PAUSE_PHRASES):
        return True

    has_pause_word = contains_word_stem(normalized_text, PAUSE_WORDS)
    has_delay_hint = contains_word_stem(normalized_text, SHORT_DELAY_WORDS)
    has_side_context = (
        contains_phrase(normalized_text, SIDE_CONVERSATION_PHRASES)
        or (
            contains_word_stem(normalized_text, ('talk', 'speak', 'vorbesc'))
            and contains_word_stem(normalized_text, ('someone', 'somebody', 'person', 'cineva'))
        )
    )
    has_first_person = contains_any_word(normalized_text, FIRST_PERSON_WORDS)
    return has_pause_word and (has_side_context or (has_first_person and has_delay_hint))


def can_accept_control_action(
    action: str | None,
    *,
    current_speaker: str,
    focused_speaker: str = 'Unknown',
    session_active: bool,
    conversation_paused: bool,
    direct_address: bool,
    normalized_text: str = '',
) -> bool:
    if not action:
        return False

    has_known_speaker = (current_speaker or '').strip() not in ('', 'Unknown')
    has_focus = (focused_speaker or '').strip() not in ('', 'Unknown')
    if not session_active and not conversation_paused:
        return False

    if action in ('hold_on', 'stop'):
        if action == 'hold_on' and session_active and is_explicit_pause_request(normalized_text):
            return True
        return has_known_speaker or direct_address

    if action in ('continue', 'repeat'):
        if conversation_paused and has_focus:
            return True
        return has_known_speaker or direct_address

    return False


def can_accept_reengagement(
    *,
    current_speaker: str,
    focused_speaker: str = 'Unknown',
    session_active: bool,
    conversation_paused: bool,
    direct_address: bool,
) -> bool:
    if not conversation_paused:
        return False

    has_known_speaker = (current_speaker or '').strip() not in ('', 'Unknown')
    has_focus = (focused_speaker or '').strip() not in ('', 'Unknown')
    return (session_active or conversation_paused) and (has_known_speaker or direct_address or has_focus)


def normalize_focus_state(
    focused_speaker: str,
    last_focus_time: float,
    focus_timeout_s: float,
    *,
    now: float | None = None,
) -> tuple[str, float]:
    now_value = time.monotonic() if now is None else float(now)
    focus = (focused_speaker or '').strip() or 'Unknown'
    focus_time = float(last_focus_time or 0.0)
    if focus != 'Unknown' and (now_value - focus_time) > float(focus_timeout_s):
        return 'Unknown', focus_time
    return focus, focus_time


def decide_attention(
    *,
    session_active: bool,
    conversation_paused: bool,
    current_speaker: str,
    focused_speaker: str,
    last_focus_time: float,
    focus_timeout_s: float,
    allow_known_speaker_switch_without_address: bool = False,
    direct_address: bool,
    reengagement: bool,
    robot_directive: bool,
    control_action: str | None,
    normalized_text: str,
    now: float | None = None,
) -> tuple[bool, str, str, float]:
    now_value = time.monotonic() if now is None else float(now)
    focus, focus_time = normalize_focus_state(
        focused_speaker,
        last_focus_time,
        focus_timeout_s,
        now=now_value,
    )

    if not session_active:
        return False, 'session_inactive', focus, focus_time

    if conversation_paused:
        if control_action in ('continue', 'repeat', 'hold_on', 'stop') and can_accept_control_action(
            control_action,
            current_speaker=current_speaker,
            focused_speaker=focus,
            session_active=session_active,
            conversation_paused=conversation_paused,
            direct_address=direct_address,
            normalized_text=normalized_text,
        ):
            return True, f'paused_control_{control_action}', focus, focus_time
        if reengagement and can_accept_reengagement(
            current_speaker=current_speaker,
            focused_speaker=focus,
            session_active=session_active,
            conversation_paused=conversation_paused,
            direct_address=direct_address,
        ):
            return True, 'paused_reengagement', focus, focus_time
        return False, 'paused_side_conversation', focus, focus_time

    if focus == 'Unknown':
        return True, 'no_focus_yet', focus, focus_time

    if current_speaker == 'Unknown':
        if direct_address or robot_directive:
            return True, 'unknown_but_directed', focus, focus_time
        if control_action is not None and can_accept_control_action(
            control_action,
            current_speaker=current_speaker,
            focused_speaker=focus,
            session_active=session_active,
            conversation_paused=conversation_paused,
            direct_address=direct_address,
            normalized_text=normalized_text,
        ):
            return True, f'unknown_control_{control_action}', focus, focus_time
        return False, 'unknown_side_conversation', focus, focus_time

    if current_speaker == focus:
        return True, 'focused_speaker', focus, focus_time

    if direct_address or robot_directive:
        return True, 'speaker_switch_with_direct_address', focus, focus_time

    if allow_known_speaker_switch_without_address:
        return True, 'speaker_switch_without_address', current_speaker, now_value

    return False, 'different_speaker_without_address', focus, focus_time


def advance_attention_focus(
    *,
    current_speaker: str,
    focused_speaker: str,
    last_focus_time: float,
    allow: bool,
    recognized_speaker: str = 'Unknown',
    now: float | None = None,
) -> tuple[str, float]:
    now_value = time.monotonic() if now is None else float(now)
    if not allow:
        return focused_speaker, last_focus_time
    recognized = (recognized_speaker or '').strip() or 'Unknown'
    if recognized != 'Unknown':
        return recognized, now_value
    if current_speaker == focused_speaker and focused_speaker != 'Unknown':
        return focused_speaker, now_value
    if focused_speaker == 'Unknown':
        return 'Unknown', now_value
    return focused_speaker, last_focus_time


def is_robot_directive(normalized_text: str) -> bool:
    return bool(re.search(
        r'\b(move|go|walk|step|turn|rotate|dance|wave|handshake|stop|halt|cancel|'
        r'forward|backward|left|right|mergi|inainte|inapoi|stanga|dreapta|ridica|'
        r'coboara|danseaza|saluta|strange|mana|opreste|anuleaza)\b',
        normalized_text,
    ))
