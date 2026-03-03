import re
import unicodedata

from .conversation_utils import has_direct_robot_address


NUMBER_WORDS = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15,
    'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20,
    'un': 1, 'unu': 1, 'una': 1, 'o': 1,
    'doi': 2, 'doua': 2, 'trei': 3, 'patru': 4, 'cinci': 5,
    'sase': 6, 'sapte': 7, 'opt': 8, 'noua': 9, 'zece': 10,
    'unsprezece': 11, 'doisprezece': 12, 'treisprezece': 13,
    'paisprezece': 14, 'cincisprezece': 15, 'saisprezece': 16,
    'saptesprezece': 17, 'optsprezece': 18, 'nouasprezece': 19, 'douazeci': 20,
}

PREFIXES = (
    'hey robot',
    'hello robot',
    'robot',
    'please',
    'te rog',
    'can you',
    'could you',
    'can',
    'poti sa',
    'poti',
    'vreau sa',
    'vreau',
    'hai sa',
)

STOP_PHRASES = (
    'stop',
    'stop now',
    'halt',
    'freeze',
    'cancel',
    'opreste',
    'anuleaza',
)

MOVE_VERBS = (
    'move',
    'go',
    'walk',
    'step',
    'take',
    'head',
    'advance',
    'move forward',
    'move backward',
    'mergi',
    'du te',
    'du',
    'inainteaza',
    'retrage te',
)

TURN_VERBS = (
    'turn',
    'rotate',
    'spin',
    'intoarce',
    'roteste',
)

RAISE_HANDS_PATTERNS = (
    r'^(hands|arms) up\b',
    r'^(raise|lift|put)\b.*\b(hand|hands|arm|arms)\b',
    r'^ridica\b.*\b(mainile|mana|bratele|brat)\b',
    r'^mainile sus\b',
)

LOWER_HANDS_PATTERNS = (
    r'^(lower|drop|put)\b.*\b(hand|hands|arm|arms)\b',
    r'^(hands|arms) down\b',
    r'^coboara\b.*\b(mainile|mana|bratele|brat)\b',
    r'^mainile jos\b',
)

WAVE_PATTERNS = (
    r'^(wave|saluta)\b',
    r'^wave your hand\b',
    r'^fa cu mana\b',
)

DANCE_PATTERNS = (
    r'^(dance|danseaza)\b',
    r'^(do a|fa un) dans\b',
)

FORWARD_PATTERNS = ('forward', 'ahead', 'inainte', 'in fata')
BACKWARD_PATTERNS = ('backward', 'backwards', 'back', 'inapoi', 'spate')
LEFT_PATTERNS = ('left', 'stanga')
RIGHT_PATTERNS = ('right', 'dreapta')


def normalize_command_text(text: str) -> str:
    text = text.lower().strip()
    text = unicodedata.normalize('NFD', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    text = text.replace('-', ' ')
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    return ' '.join(text.split())


def parse_robot_command(
    text: str,
    *,
    default_steps: int = 1,
    max_steps: int = 20,
    require_direct_robot_address: bool = False,
):
    normalized = normalize_command_text(text)
    if not normalized:
        return None

    direct_address = has_direct_robot_address(normalized)
    if require_direct_robot_address and not direct_address:
        return None

    body = _strip_prefixes(normalized)
    if not body:
        return None

    parsed = (
        _parse_stop(body, direct_address)
        or _parse_behavior(body)
        or _parse_turn(body)
        or _parse_move(body, default_steps=default_steps, max_steps=max_steps, direct_address=direct_address)
    )
    return parsed


def looks_like_robot_command(text: str, *, require_direct_robot_address: bool = False) -> bool:
    return parse_robot_command(
        text,
        require_direct_robot_address=require_direct_robot_address,
    ) is not None


def _strip_prefixes(normalized: str) -> str:
    text = normalized
    changed = True
    while changed and text:
        changed = False
        for prefix in PREFIXES:
            if text == prefix:
                return ''
            if text.startswith(prefix + ' '):
                text = text[len(prefix):].strip()
                changed = True
                break
    return text


def _starts_with_phrase(text: str, phrases) -> bool:
    return any(text == phrase or text.startswith(phrase + ' ') for phrase in phrases)


def _contains_phrase(text: str, phrases) -> bool:
    return any(re.search(rf'\b{re.escape(phrase)}\b', text) for phrase in phrases)


def _extract_steps(text: str) -> int:
    step_patterns = (
        r'\b(\d+)\s*(steps?|pasi?|pasii)\b',
        r'\b(\d+)\s*(forward|ahead|backward|backwards|back|inainte|inapoi)\b',
    )
    for pattern in step_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return 0

    words = text.split()
    for idx, word in enumerate(words):
        value = NUMBER_WORDS.get(word)
        if value is None:
            continue
        next_word = words[idx + 1] if idx + 1 < len(words) else ''
        if next_word in (
            'step', 'steps', 'pas', 'pasi', 'pasii', 'forward',
            'ahead', 'backward', 'backwards', 'back', 'inainte', 'inapoi',
        ):
            return value
    return 0


def _extract_move_direction(text: str):
    has_forward = _contains_phrase(text, FORWARD_PATTERNS)
    has_backward = _contains_phrase(text, BACKWARD_PATTERNS)
    if has_forward and not has_backward:
        return 'forward'
    if has_backward and not has_forward:
        return 'backward'
    return None


def _extract_turn_direction(text: str):
    has_left = _contains_phrase(text, LEFT_PATTERNS)
    has_right = _contains_phrase(text, RIGHT_PATTERNS)
    if has_left and not has_right:
        return 'left'
    if has_right and not has_left:
        return 'right'
    return None


def _extract_turn_angle(text: str) -> int:
    match = re.search(r'\b(\d+)\s*(degrees?|deg|grade)\b', text)
    if match:
        try:
            return max(5, min(360, int(match.group(1))))
        except ValueError:
            return 90

    words = text.split()
    for idx, word in enumerate(words):
        value = NUMBER_WORDS.get(word)
        if value is None:
            continue
        next_word = words[idx + 1] if idx + 1 < len(words) else ''
        if next_word in ('degree', 'degrees', 'deg', 'grade'):
            return max(5, min(360, value))
    return 90


def _parse_stop(body: str, direct_address: bool):
    if not _starts_with_phrase(body, STOP_PHRASES):
        return None
    word_count = len(body.split())
    if word_count > 4 and not direct_address:
        return None
    return {
        'intent': 'stop',
        'direction': 'none',
        'steps': 0,
        'confidence': 0.98,
        'parameters': {'reason': 'voice_stop'},
    }


def _parse_behavior(body: str):
    for pattern in RAISE_HANDS_PATTERNS:
        if re.match(pattern, body):
            return {
                'intent': 'raise_hands',
                'direction': 'none',
                'steps': 0,
                'confidence': 0.95,
                'parameters': {'motion': 'upper_body', 'style': 'default'},
            }
    for pattern in LOWER_HANDS_PATTERNS:
        if re.match(pattern, body):
            return {
                'intent': 'lower_hands',
                'direction': 'none',
                'steps': 0,
                'confidence': 0.93,
                'parameters': {'motion': 'upper_body', 'style': 'default'},
            }
    for pattern in DANCE_PATTERNS:
        if re.match(pattern, body):
            return {
                'intent': 'dance',
                'direction': 'none',
                'steps': 0,
                'confidence': 0.90,
                'parameters': {'style': 'default', 'duration_s': 8.0},
            }
    for pattern in WAVE_PATTERNS:
        if re.match(pattern, body):
            return {
                'intent': 'wave',
                'direction': 'none',
                'steps': 0,
                'confidence': 0.91,
                'parameters': {'style': 'greeting'},
            }
    return None


def _parse_turn(body: str):
    direction = _extract_turn_direction(body)
    if direction is None:
        return None

    has_turn_verb = _starts_with_phrase(body, TURN_VERBS)
    short_directional_turn = len(body.split()) <= 4 and _starts_with_phrase(body, LEFT_PATTERNS + RIGHT_PATTERNS)
    if not has_turn_verb and not short_directional_turn:
        return None

    return {
        'intent': 'turn',
        'direction': direction,
        'steps': 0,
        'confidence': 0.90,
        'parameters': {
            'angle_deg': _extract_turn_angle(body),
            'speed_scale': 1.0,
        },
    }


def _parse_move(body: str, *, default_steps: int, max_steps: int, direct_address: bool):
    direction = _extract_move_direction(body)
    if direction is None:
        return None

    steps = _extract_steps(body)
    word_count = len(body.split())
    has_move_verb = _starts_with_phrase(body, MOVE_VERBS)
    short_directional_command = word_count <= 4 and (
        _starts_with_phrase(body, FORWARD_PATTERNS + BACKWARD_PATTERNS)
    )

    if not has_move_verb and not short_directional_command:
        return None
    if not direct_address and not has_move_verb and steps == 0:
        return None

    if steps <= 0:
        steps = default_steps
    steps = max(1, min(int(steps), max_steps))
    return {
        'intent': 'move',
        'direction': direction,
        'steps': steps,
        'confidence': 0.88,
        'parameters': {
            'step_mode': 'discrete',
            'speed_scale': 1.0,
        },
    }
