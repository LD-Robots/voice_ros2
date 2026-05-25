import time

import pytest

pytest.importorskip('rclpy')

from conversational_client.attention_manager_node import (  # noqa: E402
    AttentionManagerNode,
    OnlineAttentionPolicy,
)
from conversational_client.conversation_utils import normalize_text  # noqa: E402


class DummyLogger:
    def info(self, *_args, **_kwargs):
        pass

    def warn(self, *_args, **_kwargs):
        pass


def make_attention_manager_stub(tmp_path):
    node = AttentionManagerNode.__new__(AttentionManagerNode)
    node._logger = DummyLogger()
    node.assistant_question_reply_window_s = 25.0
    node.initial_session_reply_grace_s = 30.0
    node.attention_learning_enabled = True
    node.attention_learning_route_enabled = True
    node.attention_learning_min_confidence = 0.18
    node.attention_policy = OnlineAttentionPolicy(
        tmp_path / 'attention_policy.json',
        learning_rate=0.08,
        threshold=0.58,
    )
    node.session_active = True
    node.session_started_at = time.monotonic()
    node.conversation_paused = False
    node.current_speaker = 'Unknown'
    node.focused_speaker = 'Unknown'
    node.latest_diarization = {}
    node.recent_address_events = []
    node.last_assistant_text = ''
    node.last_assistant_at = 0.0
    return node


def test_learning_policy_prefers_initial_single_speaker_question(tmp_path):
    node = make_attention_manager_stub(tmp_path)
    node.latest_diarization = {'speaker_word_counts': {'speaker_0': 4}}
    text = 'How are you today?'

    features = node._build_attention_learning_features(
        text=text,
        normalized_text=normalize_text(text),
        deterministic_allow=True,
        deterministic_reason='no_focus_yet',
        direct_address=False,
        robot_directive=False,
        control_action=None,
    )

    assert node._route_with_attention_learning(features, True, 'no_focus_yet')[0] is True


def test_learning_policy_leaves_multi_speaker_initial_question_to_router(tmp_path):
    node = make_attention_manager_stub(tmp_path)
    node.latest_diarization = {'speaker_word_counts': {'speaker_0': 4, 'speaker_1': 3}}
    text = 'How are you today?'

    features = node._build_attention_learning_features(
        text=text,
        normalized_text=normalize_text(text),
        deterministic_allow=True,
        deterministic_reason='no_focus_yet',
        direct_address=False,
        robot_directive=False,
        control_action=None,
    )

    assert node._route_with_attention_learning(features, True, 'no_focus_yet') is None


def test_learning_policy_does_not_auto_respond_for_focused_speaker(tmp_path):
    node = make_attention_manager_stub(tmp_path)
    node.current_speaker = 'speaker_001'
    node.focused_speaker = 'speaker_001'
    node.last_assistant_text = "Anything else you'd like help with?"
    node.last_assistant_at = time.monotonic()
    text = 'No, just checking if speaker diarization is working properly'

    features = node._build_attention_learning_features(
        text=text,
        normalized_text=normalize_text(text),
        deterministic_allow=True,
        deterministic_reason='focused_speaker',
        direct_address=False,
        robot_directive=False,
        control_action=None,
    )

    assert node._route_with_attention_learning(features, True, 'focused_speaker') is None


def test_learning_policy_can_auto_ignore_focused_speaker_when_confident(tmp_path):
    node = make_attention_manager_stub(tmp_path)
    node.current_speaker = 'speaker_001'
    node.focused_speaker = 'speaker_001'
    text = 'This is a very long side discussion about the implementation details that is not directed anywhere'

    features = node._build_attention_learning_features(
        text=text,
        normalized_text=normalize_text(text),
        deterministic_allow=True,
        deterministic_reason='focused_speaker',
        direct_address=False,
        robot_directive=False,
        control_action=None,
    )
    for _ in range(40):
        node.attention_policy.update(features, False, scale=1.0)

    routed = node._route_with_attention_learning(features, True, 'focused_speaker')
    assert routed[0] is False


def test_online_policy_updates_and_persists_feedback(tmp_path):
    path = tmp_path / 'attention_policy.json'
    policy = OnlineAttentionPolicy(path, learning_rate=0.5, threshold=0.58)
    features = {'bias': 1.0, 'recent_assistant_question_mark': 1.0}
    _, before = policy.predict(features)

    policy.update(features, True)
    _, after = policy.predict(features)
    reloaded = OnlineAttentionPolicy(path, learning_rate=0.5, threshold=0.58)
    _, persisted = reloaded.predict(features)

    assert after > before
    assert persisted == pytest.approx(after)


def test_attention_feedback_parser_accepts_reward_json():
    assert AttentionManagerNode._parse_attention_feedback('{"should_respond": true}') is True
    assert AttentionManagerNode._parse_attention_feedback('{"reward": -1}') is False
    assert AttentionManagerNode._parse_attention_feedback('not-json') is None


def test_router_failure_opens_first_fresh_single_speaker_turn(tmp_path):
    node = make_attention_manager_stub(tmp_path)
    node.latest_diarization = {'speaker_word_counts': {'speaker_0': 4}}
    text = 'How are you today?'
    features = node._build_attention_learning_features(
        text=text,
        normalized_text=normalize_text(text),
        deterministic_allow=True,
        deterministic_reason='no_focus_yet',
        direct_address=False,
        robot_directive=False,
        control_action=None,
    )

    allow, reason = node._router_failure_fallback(
        deterministic_allow=True,
        deterministic_reason='no_focus_yet',
        features=features,
        text=text,
        normalized_text=normalize_text(text),
    )

    assert allow is True
    assert reason.startswith('address_router_failed_open:')


def test_router_failure_does_not_open_later_turn(tmp_path):
    node = make_attention_manager_stub(tmp_path)
    node.latest_diarization = {'speaker_word_counts': {'speaker_0': 4}}
    node.recent_address_events = [{'allowed': False, 'reason': 'previous ignore'}]
    text = 'How are you today?'
    features = node._build_attention_learning_features(
        text=text,
        normalized_text=normalize_text(text),
        deterministic_allow=True,
        deterministic_reason='no_focus_yet',
        direct_address=False,
        robot_directive=False,
        control_action=None,
    )

    assert node._router_failure_fallback(
        deterministic_allow=True,
        deterministic_reason='no_focus_yet',
        features=features,
        text=text,
        normalized_text=normalize_text(text),
    ) is None


def test_router_failure_opens_short_request_from_focused_speaker(tmp_path):
    node = make_attention_manager_stub(tmp_path)
    node.current_speaker = 'speaker_001'
    node.focused_speaker = 'speaker_001'
    node.latest_diarization = {'speaker_word_counts': {'speaker_0': 5}}
    text = 'Fine. Could you tell me'
    features = node._build_attention_learning_features(
        text=text,
        normalized_text=normalize_text(text),
        deterministic_allow=True,
        deterministic_reason='focused_speaker',
        direct_address=False,
        robot_directive=False,
        control_action=None,
    )

    allow, reason = node._router_failure_fallback(
        deterministic_allow=True,
        deterministic_reason='focused_speaker',
        features=features,
        text=text,
        normalized_text=normalize_text(text),
    )

    assert allow is True
    assert reason.startswith('address_router_failed_open:request_opening')


def test_router_failure_does_not_open_focused_side_comment(tmp_path):
    node = make_attention_manager_stub(tmp_path)
    node.current_speaker = 'speaker_001'
    node.focused_speaker = 'speaker_001'
    node.latest_diarization = {'speaker_word_counts': {'speaker_0': 6}}
    text = 'Fine, that was just a comment'
    features = node._build_attention_learning_features(
        text=text,
        normalized_text=normalize_text(text),
        deterministic_allow=True,
        deterministic_reason='focused_speaker',
        direct_address=False,
        robot_directive=False,
        control_action=None,
    )

    assert node._router_failure_fallback(
        deterministic_allow=True,
        deterministic_reason='focused_speaker',
        features=features,
        text=text,
        normalized_text=normalize_text(text),
    ) is None


def test_extract_response_text_handles_empty_output():
    assert AttentionManagerNode._extract_response_text({'output': []}) == ''
