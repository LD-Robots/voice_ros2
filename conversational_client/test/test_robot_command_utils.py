from conversational_client.robot_command_utils import looks_like_robot_command, parse_robot_command


def test_parse_motion_team_commands():
    move = parse_robot_command(
        'Robot, move forward 3 steps',
        default_steps=1,
        max_steps=20,
        require_direct_robot_address=True,
    )
    raise_hand = parse_robot_command(
        'Robot, raise your hand',
        require_direct_robot_address=True,
    )
    turn_arround = parse_robot_command(
        'Robot, turn around',
        require_direct_robot_address=True,
    )
    clap = parse_robot_command(
        'Robot, clap',
        require_direct_robot_address=True,
    )
    say_hi = parse_robot_command(
        'Robot, say hi',
        default_steps=1,
        max_steps=20,
        require_direct_robot_address=True,
    )

    assert move is not None
    assert move['command_id'] == 1
    assert move['command_name'] == 'move_forward'
    assert move['steps'] == 3

    assert raise_hand is not None
    assert raise_hand['command_id'] == 2
    assert raise_hand['command_name'] == 'raise_hand'

    assert turn_arround is not None
    assert turn_arround['command_id'] == 3
    assert turn_arround['command_name'] == 'turn_arround'
    assert turn_arround['parameters']['angle_deg'] == 180

    assert clap is not None
    assert clap['command_id'] == 4
    assert clap['command_name'] == 'clap'

    assert say_hi is not None
    assert say_hi['command_id'] == 5
    assert say_hi['command_name'] == 'say_hi'


def test_parse_short_addressed_direction_command():
    parsed = parse_robot_command(
        'Robot forward',
        default_steps=1,
        max_steps=20,
        require_direct_robot_address=True,
    )

    assert parsed is not None
    assert parsed['command_name'] == 'move_forward'
    assert parsed['steps'] == 1


def test_parse_split_robot_address_from_gemini_transcript():
    move = parse_robot_command(
        'Ro bot move forward.',
        default_steps=1,
        max_steps=20,
        require_direct_robot_address=True,
    )
    raise_hand = parse_robot_command(
        'Ro bot raise hand.',
        require_direct_robot_address=True,
    )

    assert move is not None
    assert move['command_name'] == 'move_forward'
    assert move['steps'] == 1
    assert raise_hand is not None
    assert raise_hand['command_name'] == 'raise_hand'


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
