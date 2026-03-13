from conversational_server.realtime_turn_utils import (
    can_request_realtime_response,
    continued_turn_response_delay_ms,
)


def test_can_request_realtime_response_blocks_while_user_is_speaking():
    allowed, reason = can_request_realtime_response(
        user_speaking=True,
        conversation_paused=False,
        waiting_for_robot_confirmation=False,
        response_create_pending=False,
        response_active=False,
        item_id='item-1',
        last_response_request_item_id='',
    )

    assert allowed is False
    assert reason == 'user_speaking'


def test_can_request_realtime_response_rejects_duplicate_item():
    allowed, reason = can_request_realtime_response(
        user_speaking=False,
        conversation_paused=False,
        waiting_for_robot_confirmation=False,
        response_create_pending=False,
        response_active=False,
        item_id='item-1',
        last_response_request_item_id='item-1',
    )

    assert allowed is False
    assert reason == 'duplicate_item'


def test_can_request_realtime_response_blocks_while_response_is_pending():
    allowed, reason = can_request_realtime_response(
        user_speaking=False,
        conversation_paused=False,
        waiting_for_robot_confirmation=False,
        response_create_pending=True,
        response_active=False,
        item_id='item-2',
        last_response_request_item_id='',
    )

    assert allowed is False
    assert reason == 'response_pending'


def test_continued_turn_response_delay_uses_the_larger_delay():
    assert continued_turn_response_delay_ms(250, 650) == 650
    assert continued_turn_response_delay_ms(900, 650) == 900
