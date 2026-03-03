def can_request_realtime_response(
    *,
    user_speaking: bool,
    conversation_paused: bool,
    waiting_for_robot_confirmation: bool,
    response_active: bool,
    item_id: str,
    last_response_request_item_id: str,
) -> tuple[bool, str]:
    if user_speaking:
        return False, 'user_speaking'
    if conversation_paused or waiting_for_robot_confirmation:
        return False, 'blocked'
    if response_active:
        return False, 'response_active'
    if item_id and item_id == last_response_request_item_id:
        return False, 'duplicate_item'
    return True, ''


def continued_turn_response_delay_ms(
    response_create_delay_ms: int,
    continued_turn_response_delay_ms: int,
) -> int:
    return max(
        0,
        max(int(response_create_delay_ms), int(continued_turn_response_delay_ms)),
    )
