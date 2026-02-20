from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conversational_server.robot_command_parser import parse_robot_command


@pytest.mark.parametrize(
    "text,expected_action,expected_target,expected_value,expected_unit",
    [
        ("raise hand", "raise_hand", "both_hands", 0.0, ""),
        ("please raise left hand", "raise_hand", "left_hand", 0.0, ""),
        ("go 10 meters", "move", "forward", 10.0, "m"),
        ("move backward 2.5 m", "move", "backward", 2.5, "m"),
        ("dance", "dance", "whole_body", 0.0, ""),
        ("ridica mana dreapta", "raise_hand", "right_hand", 0.0, ""),
        ("mergi 3 metri", "move", "forward", 3.0, "m"),
        ("opreste te", "stop", "all", 0.0, ""),
    ],
)
def test_parse_robot_commands(text, expected_action, expected_target, expected_value, expected_unit):
    parsed, _ = parse_robot_command(text)
    assert parsed is not None
    assert parsed.action == expected_action
    assert parsed.target == expected_target
    assert parsed.value == pytest.approx(expected_value)
    assert parsed.unit == expected_unit


def test_non_command_returns_none():
    parsed, normalized = parse_robot_command("I like dancing music a lot")
    assert parsed is None
    assert normalized == "i like dancing music a lot"

