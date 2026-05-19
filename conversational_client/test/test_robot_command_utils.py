from conversational_client.robot_command_utils import looks_like_robot_command, parse_robot_command


def test_parse_move_and_turn_commands():
    move = parse_robot_command(
        'Robot, move back 3 steps',
        default_steps=1,
        max_steps=20,
        require_direct_robot_address=True,
    )
    turn = parse_robot_command(
        'Robot, turn left 90 degrees',
        default_steps=1,
        max_steps=20,
        require_direct_robot_address=True,
    )

    assert move is not None
    assert move['intent'] == 'move'
    assert move['command_id'] == 21
    assert move['direction'] == 'backward'
    assert move['steps'] == 3

    assert turn is not None
    assert turn['intent'] == 'turn'
    assert turn['command_id'] == 30
    assert turn['direction'] == 'left'
    assert turn['parameters']['angle_deg'] == 90


def test_parse_short_addressed_direction_command():
    parsed = parse_robot_command(
        'Robot forward',
        default_steps=1,
        max_steps=20,
        require_direct_robot_address=True,
    )

    assert parsed is not None
    assert parsed['intent'] == 'move'
    assert parsed['command_id'] == 20
    assert parsed['direction'] == 'forward'
    assert parsed['steps'] == 1


def test_parse_handshake_command_id():
    parsed = parse_robot_command(
        'Robot, give me a handshake',
        require_direct_robot_address=True,
    )

    assert parsed is not None
    assert parsed['intent'] == 'handshake'
    assert parsed['command_id'] == 1
    assert parsed['parameters']['style'] == 'handshake'


def test_do_not_misread_normal_sentences_as_robot_commands():
    assert looks_like_robot_command(
        'We should stop climate change.',
        require_direct_robot_address=True,
    ) is False
    assert looks_like_robot_command(
        "Robot, I'm right here.",
        require_direct_robot_address=True,
    ) is False
    assert looks_like_robot_command(
        'I left my phone right here.',
        require_direct_robot_address=True,
    ) is False
