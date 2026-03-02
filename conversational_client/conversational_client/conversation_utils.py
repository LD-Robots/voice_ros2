import re
import unicodedata


DIRECT_ROBOT_PHRASES = (
    'robot',
    'hey robot',
    'hello robot',
    'listen robot',
    'for you robot',
    'tu robot',
    'hei robot',
)


def normalize_text(text: str) -> str:
    text = (text or '').lower().strip()
    text = unicodedata.normalize('NFD', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    text = text.replace('-', ' ')
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    return ' '.join(text.split())


def contains_phrase(normalized_text: str, phrases) -> bool:
    return any(phrase in normalized_text for phrase in phrases)


def has_direct_robot_address(normalized_text: str) -> bool:
    if not normalized_text:
        return False
    if contains_phrase(normalized_text, DIRECT_ROBOT_PHRASES):
        return True
    return normalized_text.startswith(('hey ', 'hei ', 'robot '))


def detect_control_action(normalized_text: str) -> str | None:
    if not normalized_text:
        return None

    if contains_phrase(normalized_text, (
        'wait a second',
        'wait a little',
        'wait a bit',
        'hold on',
        'one second',
        'just a second',
        'stai putin',
        'asteapta putin',
        'asteapta un pic',
        'asteapta o secunda',
        'un moment',
        'mai tarziu',
    )):
        return 'hold_on'

    if contains_phrase(normalized_text, (
        'continue',
        'resume',
        'continue please',
        'continue the answer',
        'continue your answer',
        'pick up where you left off',
        'continua',
        'continua te rog',
        'reia',
        'reia raspunsul',
        'reia de unde ai ramas',
        'continua raspunsul',
    )):
        return 'continue'

    if contains_phrase(normalized_text, (
        'repeat',
        'repeat that',
        'say that again',
        'repeat your answer',
        'repeta',
        'repeta te rog',
        'mai spune o data',
        'mai zice o data',
        'repeta raspunsul',
    )):
        return 'repeat'

    if contains_phrase(normalized_text, (
        'stop',
        'stop talking',
        'stop please',
        'be quiet',
        'quiet',
        'opreste',
        'opreste te',
        'taci',
        'gata',
    )):
        return 'stop'

    return None


def is_robot_directive(normalized_text: str) -> bool:
    return bool(re.search(
        r'\b(move|go|walk|step|turn|rotate|dance|wave|stop|halt|cancel|forward|backward|left|right|'
        r'mergi|inainte|inapoi|stanga|dreapta|ridica|coboara|danseaza|saluta|opreste|anuleaza)\b',
        normalized_text,
    ))
