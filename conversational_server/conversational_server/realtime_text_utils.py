import re
import time
import unicodedata

from conversational_client.conversation_config import load_conversation_rules

_RULES = load_conversation_rules()
REENGAGEMENT_PHRASES = tuple(_RULES['reengagement_phrases'])
CONTINUE_PHRASES = tuple(_RULES['continue_phrases'])
CONTINUE_STEMS = tuple(_RULES['continue_stems'])
MULTIWORD_CONTINUE_PHRASES = tuple(phrase for phrase in CONTINUE_PHRASES if ' ' in phrase)

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


def extract_opening_signature(text: str, *, max_words: int = 4) -> str:
    normalized = normalize_realtime_text(text)
    if not normalized:
        return ''
    words = normalized.split()
    if not words:
        return ''
    return ' '.join(words[: max(1, int(max_words))])


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
