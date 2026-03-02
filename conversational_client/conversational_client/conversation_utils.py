import re
import unicodedata


DIRECT_ROBOT_PREFIXES = (
    'robot',
    'hey robot',
    'hello robot',
    'listen robot',
    'for you robot',
    'tu robot',
    'hei robot',
)

REENGAGEMENT_PHRASES = (
    'i am back',
    'i m back',
    'im back',
    'back now',
    'i am here',
    'i m here',
    'im here',
    'ok robot',
    'okay robot',
    'am revenit',
    'sunt inapoi',
    'gata am revenit',
    'acum am revenit',
    'ok am revenit',
    'bun am revenit',
)


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
    if contains_standalone_word(normalized_text, 'robot') and contains_phrase(
        normalized_text,
        ('i am back', 'i m back', 'im back', 'am revenit', 'sunt inapoi', 'acum am revenit'),
    ):
        return True
    return False


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
        'wait',
        'just wait',
        'just wait a bit',
        'pause a little',
        'pause for a second',
        'speak later',
        'talk later',
        'vorbesc cu cineva',
        'vorbesc cu cineva acum',
        'vorbesc acum cu cineva',
        'stai putin ca vorbesc',
        'stai putin ca vorbesc cu cineva',
        'asteapta ca vorbesc cu cineva',
        'am de vorbit cu cineva',
        'i am talking with someone',
        'i m talking with someone',
        'i need to talk with someone',
        'let me talk with someone',
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
