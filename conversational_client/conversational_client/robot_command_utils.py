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

COMMAND_IDS = {
    'move_forward': 1,
    'raise_hand': 2,
    'turn_arround': 3,
    'clap': 4,
    'say_hi': 5,
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

MOVE_FORWARD_PATTERNS = (
    r'^(move|go|walk|step|take|head|advance)\b.*\b(forward|ahead)\b',
    r'^(forward|ahead)\b',
    r'^(mergi|du te|du|inainteaza)\b.*\b(inainte|in fata)\b',
    r'^(inainte|in fata)\b',
)

RAISE_HAND_PATTERNS = (
    r'^(hand|hands|arm|arms) up\b',
    r'^(raise|lift|put)\b.*\b(hand|hands|arm|arms)\b',
    r'^ridica\b.*\b(mainile|mana|bratul|bratele|brat)\b',
    r'^mainile sus\b',
)

TURN_ARROUND_PATTERNS = (
    r'^(turn|rotate|spin)\b.*\b(around|arround|back)\b',
    r'^(turn|rotate|spin)\b.*\b(180|one hundred eighty)\b',
    r'^(turn around|turn arround|spin around|spin arround)\b',
    r'^(intoarce|roteste)\b.*\b(inapoi|180|o suta optzeci)\b',
)

CLAP_PATTERNS = (
    r'^clap\b',
    r'^clap\b.*\b(hand|hands)\b',
    r'^bate\b.*\b(palma|palmele|din palme)\b',
    r'^aplauda\b',
)

SAY_HI_PATTERNS = (
    r'^(say|tell)\b.*\b(hi|hello|hello there)\b',
    r'^(say hi|say hello|hello|hi)\b',
    r'^(saluta|spune salut|zi salut)\b',
)

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
        _parse_move_forward(body, default_steps=default_steps, max_steps=max_steps)
        or _parse_raise_hand(body)
        or _parse_turn_arround(body)
        or _parse_clap(body)
        or _parse_say_hi(body)
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


def _make_command(command_name: str, *, steps=0, confidence=0.90, parameters=None):
    return {
        'command_id': COMMAND_IDS[command_name],
        'command_name': command_name,
        'steps': int(steps),
        'confidence': float(confidence),
        'parameters': parameters or {},
    }


def _matches_any(text: str, patterns) -> bool:
    return any(re.match(pattern, text) for pattern in patterns)


def _parse_move_forward(body: str, *, default_steps: int, max_steps: int):
    if not _matches_any(body, MOVE_FORWARD_PATTERNS):
        return None

    steps = _extract_steps(body)
    if steps <= 0:
        steps = default_steps
    steps = max(1, min(int(steps), max_steps))
    return _make_command(
        'move_forward',
        steps=steps,
        confidence=0.94,
        parameters={
            'step_mode': 'discrete',
            'speed_scale': 1.0,
        },
    )


def _parse_raise_hand(body: str):
    if not _matches_any(body, RAISE_HAND_PATTERNS):
        return None
    return _make_command(
        'raise_hand',
        confidence=0.95,
        parameters={'motion': 'upper_body', 'style': 'default'},
    )


def _parse_turn_arround(body: str):
    if not _matches_any(body, TURN_ARROUND_PATTERNS):
        return None
    return _make_command(
        'turn_arround',
        confidence=0.93,
        parameters={'angle_deg': 180, 'speed_scale': 1.0},
    )


def _parse_clap(body: str):
    if not _matches_any(body, CLAP_PATTERNS):
        return None
    return _make_command(
        'clap',
        confidence=0.95,
        parameters={'motion': 'upper_body', 'style': 'default'},
    )


def _parse_say_hi(body: str):
    if not _matches_any(body, SAY_HI_PATTERNS):
        return None
    return _make_command(
        'say_hi',
        confidence=0.95,
        parameters={'motion': 'greeting', 'style': 'default'},
    )
