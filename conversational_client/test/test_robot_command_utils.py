from conversational_client.robot_command_catalog import command_id_for, command_name_for
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


def test_command_catalog_ids_are_stable():
    assert command_name_for('move', 'forward') == 'move_forward'
    assert command_id_for('move', 'forward') == 10
    assert command_id_for('move', 'backward') == 11
    assert command_id_for('turn', 'left') == 20
    assert command_id_for('turn', 'right') == 21
    assert command_id_for('dance', 'none') == 33
    assert command_id_for('sit', 'none') == 34
    assert command_id_for('stand', 'none') == 35


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
