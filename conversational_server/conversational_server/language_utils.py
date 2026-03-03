import re
import unicodedata

from conversational_client.conversation_config import load_conversation_rules
from conversational_client.person_profile_utils import extract_language_preference


_RULES = load_conversation_rules()
_LANGUAGE_MARKERS = {
    str(language): tuple(str(token) for token in tokens)
    for language, tokens in (_RULES.get('language_markers') or {}).items()
}


def normalize_language_text(text: str) -> str:
    normalized = unicodedata.normalize('NFKD', (text or '').lower().strip())
    normalized = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = re.sub(r'[^a-z0-9\s]', ' ', normalized)
    return ' '.join(normalized.split())


def detect_explicit_language_choice(text: str) -> str:
    normalized = normalize_language_text(text)
    explicit = extract_language_preference(normalized)
    if explicit:
        return explicit

    has_romanian = bool(re.search(r'\b(romanian|romana)\b', normalized))
    has_english = bool(re.search(r'\b(english|engleza)\b', normalized))
    has_switch_context = bool(
        re.search(r'\b(speak|answer|talk|prefer|switch|continue|vorbeste|raspunde|prefer|continua)\b', normalized)
    )
    if has_romanian and (has_switch_context or 'please' in normalized or 'sorry' in normalized):
        return 'ro'
    if has_english and (has_switch_context or 'please' in normalized or 'sorry' in normalized):
        return 'en'
    return ''


def detect_text_language(text: str) -> str:
    normalized = normalize_language_text(text)
    if not normalized:
        return ''

    explicit = detect_explicit_language_choice(normalized)
    if explicit:
        return explicit

    scores = {'en': 0, 'ro': 0}
    words = normalized.split()
    for language, markers in _LANGUAGE_MARKERS.items():
        marker_set = set(markers)
        scores[language] = sum(1 for word in words if word in marker_set)

    if scores['en'] == 0 and scores['ro'] == 0:
        return ''
    if abs(scores['en'] - scores['ro']) < 2:
        return ''
    return 'en' if scores['en'] > scores['ro'] else 'ro'


class ConversationLanguageTracker:
    def __init__(self, switch_hits_required: int = 2):
        self.switch_hits_required = max(1, int(switch_hits_required))
        self.current_language = ''
        self.pending_language = ''
        self.pending_hits = 0

    def reset(self):
        self.current_language = ''
        self.pending_language = ''
        self.pending_hits = 0

    def seed(self, preferred_language: str) -> str:
        preferred = self._normalize_language(preferred_language)
        if preferred and not self.current_language:
            self.current_language = preferred
        return self.current_language

    def observe(self, text: str, preferred_language: str = '') -> str:
        preferred = self._normalize_language(preferred_language)
        explicit = detect_explicit_language_choice(text)
        if explicit:
            self.current_language = explicit
            self.pending_language = ''
            self.pending_hits = 0
            return self.current_language

        detected = detect_text_language(text)
        if not self.current_language:
            self.current_language = preferred or detected
            return self.current_language

        if not detected:
            return self.current_language

        if detected == self.current_language:
            self.pending_language = ''
            self.pending_hits = 0
            return self.current_language

        if detected == self.pending_language:
            self.pending_hits += 1
        else:
            self.pending_language = detected
            self.pending_hits = 1

        if self.pending_hits >= self.switch_hits_required:
            self.current_language = detected
            self.pending_language = ''
            self.pending_hits = 0

        return self.current_language

    @staticmethod
    def _normalize_language(language: str) -> str:
        value = (language or '').strip().lower()
        if value.startswith('en'):
            return 'en'
        if value.startswith('ro'):
            return 'ro'
        return ''
