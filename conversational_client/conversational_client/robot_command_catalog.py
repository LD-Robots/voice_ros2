COMMAND_IDS = {
    'stop': 0,
    'move_forward': 10,
    'move_backward': 11,
    'turn_left': 20,
    'turn_right': 21,
    'raise_hands': 30,
    'lower_hands': 31,
    'wave': 32,
    'dance': 33,
    'sit': 34,
    'stand': 35,
}

UNKNOWN_COMMAND_ID = -1


def command_name_for(intent: str, direction: str = '') -> str:
    intent = (intent or '').strip().lower()
    direction = (direction or '').strip().lower()

    if intent == 'move' and direction in ('forward', 'backward'):
        return f'move_{direction}'
    if intent == 'turn' and direction in ('left', 'right'):
        return f'turn_{direction}'
    return intent


def command_id_for(intent: str, direction: str = '') -> int:
    return COMMAND_IDS.get(command_name_for(intent, direction), UNKNOWN_COMMAND_ID)
