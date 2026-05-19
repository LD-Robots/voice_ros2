import time

import pytest

pytest.importorskip('rclpy')

from conversational_client.attention_manager_node import AttentionManagerNode
from conversational_client.conversation_utils import normalize_text


def make_attention_manager_stub():
    node = AttentionManagerNode.__new__(AttentionManagerNode)
    node.assistant_question_reply_window_s = 25.0
    node.last_assistant_text = ''
    node.last_assistant_at = 0.0
    return node


def test_first_person_reply_to_recent_assistant_question_is_addressed():
    node = make_attention_manager_stub()
    node.last_assistant_text = "I'm doing well, thanks. How are you today?"
    node.last_assistant_at = time.monotonic()

    assert node._answers_recent_robot_question(
        normalize_text("I'm doing fine. I just doing some rounds")
    )


def test_reply_window_expires_for_assistant_question():
    node = make_attention_manager_stub()
    node.last_assistant_text = 'Do you want me to continue?'
    node.last_assistant_at = time.monotonic() - 30.0

    assert not node._answers_recent_robot_question(normalize_text('yes'))
