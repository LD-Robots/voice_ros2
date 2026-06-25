import re

from .conversation_utils import normalize_text, strip_robot_prefixes

GOODBYE_KEYWORDS = (
    'see you later',
    'bye bye',
    'la revedere',
    'goodbye',
    'good bye',
    'good by',
    'goodbuy',
    'good pie',
    'good boy',
    'good pair',
    'see you',
    'ne vedem',
    'pa pa',
    'bye',
)

GOODBYE_CONTEXT_WORDS = {
    'ok',
    'okay',
    'oke',
    'robot',
    'robert',
    'rowboat',
    'robotul',
    'robotule',
    'roboter',
    'of',
    'both',
    'pair',
    'pie',
    'boy',
    'by',
    'buy',
    'for',
    'now',
    'thanks',
    'thank',
    'you',
    'please',
    'well',
    'then',
    'so',
    'alright',
    'all',
    'right',
    'bye',
    'goodbye',
    'good',
    'bine',
    'pa',
    'robotule',
    'multumesc',
    'merci',
    'te',
    'rog',
    'gata',
    'acum',
    'deocamdata',
}


def _phrase_pattern(phrase: str) -> str:
    tokens = [re.escape(token) for token in phrase.split() if token]
    return r'\b' + r'\s+'.join(tokens) + r'\b'


def _split_candidate_clauses(text: str) -> list[str]:
    # Only split on sentence endings. Splitting on commas/semicolons can turn
    # quoted or discussed farewell phrases into a standalone "goodbye" clause.
    return [part.strip() for part in re.split(r'[.!?]+', text or '') if part.strip()]


def _is_explicit_goodbye_clause(normalized_clause: str) -> str:
    text = strip_robot_prefixes(normalized_clause)
    if not text:
        return ''

    for keyword in GOODBYE_KEYWORDS:
        match = re.search(_phrase_pattern(keyword), text)
        if not match:
            continue
        remainder = ' '.join((text[:match.start()] + ' ' + text[match.end():]).split())
        if not remainder:
            return keyword
        if all(token in GOODBYE_CONTEXT_WORDS for token in remainder.split()):
            return keyword
    return ''


def detect_goodbye_keyword(text: str) -> str:
    for clause in _split_candidate_clauses(text):
        keyword = _is_explicit_goodbye_clause(normalize_text(clause))
        if keyword:
            return keyword
    return ''


def goodbye_tts_command(language: str, keyword: str = '') -> str:
    # First priority: check if the matched keyword is explicitly English or Romanian
    keyword_norm = (keyword or '').strip().lower()
    if keyword_norm in ('la revedere', 'ne vedem', 'pa pa', 'pa', 'robotule', 'robotul'):
        return 'goodbye_ro'
    elif keyword_norm in ('goodbye', 'good bye', 'good by', 'goodbuy', 'good pie', 'good boy', 'good pair', 'see you later', 'see you', 'bye bye', 'bye'):
        return 'goodbye_en'

    # Fallback to the ASR detected language
    normalized_lang = (language or '').strip().lower()
    if normalized_lang.startswith('ro'):
        return 'goodbye_ro'
    return 'goodbye_en'
