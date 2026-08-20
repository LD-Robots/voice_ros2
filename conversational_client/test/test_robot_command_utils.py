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
    assert move['direction'] == 'backward'
    assert move['steps'] == 3

    assert turn is not None
    assert turn['intent'] == 'turn'
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
    assert parsed['direction'] == 'forward'
    assert parsed['steps'] == 1


def test_parse_humanoid_posture_commands():
    sit = parse_robot_command(
        'Robot, sit down',
        default_steps=1,
        max_steps=20,
        require_direct_robot_address=True,
    )
    stand = parse_robot_command(
        'Robot, stand up',
        default_steps=1,
        max_steps=20,
        require_direct_robot_address=True,
    )

    assert sit is not None
    assert sit['intent'] == 'sit'
    assert sit['parameters']['posture'] == 'sit'

    assert stand is not None
    assert stand['intent'] == 'stand'
    assert stand['parameters']['posture'] == 'stand'


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


def test_command_parses_when_robot_address_is_mid_sentence():
    """Words before "robot" used to block the anchored command patterns."""
    for text, expected in (
        ('Now robot move forward.', 'move'),
        ("Let's see robot turn around.", 'turn'),
        ('Ok so now robot raise your hands', 'raise_hands'),
        ('And then robot move forward two steps', 'move'),
        ('Asculta robot mergi inainte', 'move'),
        ('Hey there robot wave', 'wave'),
        ('So robot sit down', 'sit'),
    ):
        parsed = parse_robot_command(text, require_direct_robot_address=True)
        assert parsed is not None, text
        assert parsed['intent'] == expected, text


def test_mid_sentence_address_does_not_trigger_on_conversation():
    for text in (
        'robot what do you think about moving forward',
        'robot tell me a story',
        'the robot is nice',
        'tell me about turning around',
    ):
        assert parse_robot_command(text, require_direct_robot_address=True) is None, text
