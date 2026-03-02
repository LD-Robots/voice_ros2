#!/usr/bin/env python3
"""
Voice Command Node - Extract robot action intents from speech transcription.

Subscribes to:
  - /transcription (Transcription)
  - /speaker_id (String)

Publishes to:
  - /robot_command (RobotCommand)
  - /tts_command (String, optional acknowledgement)
"""
import json
import re
import unicodedata

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import RobotCommand, Transcription
from std_msgs.msg import String


NUMBER_WORDS = {
    # English
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15,
    'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20,
    # Romanian (normalized without diacritics)
    'zero': 0, 'un': 1, 'unu': 1, 'una': 1, 'o': 1,
    'doi': 2, 'doua': 2, 'trei': 3, 'patru': 4, 'cinci': 5,
    'sase': 6, 'sapte': 7, 'opt': 8, 'noua': 9, 'zece': 10,
    'unsprezece': 11, 'doisprezece': 12, 'treisprezece': 13,
    'paisprezece': 14, 'cincisprezece': 15, 'saisprezece': 16,
    'saptesprezece': 17, 'optsprezece': 18, 'nouasprezece': 19, 'douazeci': 20,
}


class VoiceCommandNode(Node):
    def __init__(self):
        super().__init__('voice_command_node')

        self.declare_parameter('min_transcription_confidence', 0.40)
        self.declare_parameter('default_steps', 1)
        self.declare_parameter('max_steps', 20)
        self.declare_parameter('enable_tts_ack', True)
        self.declare_parameter('tts_ack_en', 'ack_en')
        self.declare_parameter('tts_ack_ro', 'ack_ro')
        self.declare_parameter('transcription_topic', '/attended_transcription')

        self.min_transcription_confidence = float(
            self.get_parameter('min_transcription_confidence').value
        )
        self.default_steps = int(self.get_parameter('default_steps').value)
        self.max_steps = int(self.get_parameter('max_steps').value)
        self.enable_tts_ack = bool(self.get_parameter('enable_tts_ack').value)
        self.tts_ack_en = str(self.get_parameter('tts_ack_en').value)
        self.tts_ack_ro = str(self.get_parameter('tts_ack_ro').value)
        transcription_topic = str(self.get_parameter('transcription_topic').value)

        self.current_speaker = 'Unknown'

        self.transcription_sub = self.create_subscription(
            Transcription,
            transcription_topic,
            self._transcription_callback,
            10
        )
        self.speaker_sub = self.create_subscription(
            String,
            '/speaker_id',
            self._speaker_callback,
            10
        )

        self.command_pub = self.create_publisher(
            RobotCommand,
            '/robot_command',
            10
        )
        self.tts_cmd_pub = self.create_publisher(
            String,
            '/tts_command',
            10
        )

        self.get_logger().info('Voice Command Node started. Publishing intents to /robot_command')

    def _speaker_callback(self, msg: String):
        speaker = msg.data.strip()
        self.current_speaker = speaker if speaker else 'Unknown'

    def _transcription_callback(self, msg: Transcription):
        text = (msg.text or '').strip()
        if not text:
            return

        if msg.confidence < self.min_transcription_confidence:
            return

        parsed = self._parse_command(text)
        if not parsed:
            return

        cmd = RobotCommand()
        cmd.header.stamp = self.get_clock().now().to_msg()
        cmd.intent = parsed['intent']
        cmd.direction = parsed['direction']
        cmd.steps = int(parsed['steps'])
        cmd.confidence = float(parsed['confidence'])
        cmd.source_text = text
        cmd.language = msg.language or ''
        cmd.speaker = self.current_speaker
        cmd.parameters_json = json.dumps(parsed['parameters'], separators=(',', ':'))
        self.command_pub.publish(cmd)

        if self.enable_tts_ack:
            ack = String()
            ack.data = self.tts_ack_ro if (msg.language or '').lower().startswith('ro') else self.tts_ack_en
            self.tts_cmd_pub.publish(ack)

        self.get_logger().info(
            f'Command detected: intent={cmd.intent}, direction={cmd.direction}, steps={cmd.steps}, speaker={cmd.speaker}'
        )

    def _parse_command(self, text: str):
        normalized = self._normalize_text(text)

        prefix = r'^(?:(?:robot|hey robot|please|te rog|can you|can|poti sa|poti|vreau sa|vreau|hai sa|fa|baga)\s+)*'

        stop_patterns = [
            prefix + r'(stop|halt|freeze|cancel)\b',
            prefix + r'(opreste|anuleaza|stop)\b',
        ]
        for pattern in stop_patterns:
            if re.match(pattern, normalized):
                return {
                    'intent': 'stop',
                    'direction': 'none',
                    'steps': 0,
                    'confidence': 0.98,
                    'parameters': {'reason': 'voice_stop'},
                }

        raise_hands_patterns = [
            prefix + r'(hands|arms) up\b',
            prefix + r'(raise|lift|put)\b.*\b(hand|hands|arm|arms)\b',
            prefix + r'ridica\b.*\b(mainile|mana|bratele|brat)\b',
            prefix + r'mainile sus\b',
        ]
        for pattern in raise_hands_patterns:
            if re.match(pattern, normalized):
                return {
                    'intent': 'raise_hands',
                    'direction': 'none',
                    'steps': 0,
                    'confidence': 0.95,
                    'parameters': {'motion': 'upper_body', 'style': 'default'},
                }

        lower_hands_patterns = [
            prefix + r'(lower|drop|put)\b.*\b(hand|hands|arm|arms)\b',
            prefix + r'(hands|arms) down\b',
            prefix + r'coboara\b.*\b(mainile|mana|bratele|brat)\b',
            prefix + r'mainile jos\b',
        ]
        for pattern in lower_hands_patterns:
            if re.match(pattern, normalized):
                return {
                    'intent': 'lower_hands',
                    'direction': 'none',
                    'steps': 0,
                    'confidence': 0.93,
                    'parameters': {'motion': 'upper_body', 'style': 'default'},
                }

        dance_patterns = [
            prefix + r'(dance|danseaza)\b',
            prefix + r'(do a|fa un) dans\b',
        ]
        for pattern in dance_patterns:
            if re.match(pattern, normalized):
                return {
                    'intent': 'dance',
                    'direction': 'none',
                    'steps': 0,
                    'confidence': 0.90,
                    'parameters': {'style': 'default', 'duration_s': 8.0},
                }

        wave_patterns = [
            prefix + r'(wave|saluta)\b',
            prefix + r'wave your hand\b',
            prefix + r'fa cu mana\b',
        ]
        for pattern in wave_patterns:
            if re.match(pattern, normalized):
                return {
                    'intent': 'wave',
                    'direction': 'none',
                    'steps': 0,
                    'confidence': 0.91,
                    'parameters': {'style': 'greeting'},
                }

        turn = self._parse_turn(normalized)
        if turn:
            return turn

        move = self._parse_move(normalized)
        if move:
            return move

        return None

    def _parse_turn(self, normalized: str):
        prefix = r'^(?:(?:robot|hey robot|please|te rog|can you|can|poti sa|poti|vreau sa|vreau|hai sa|fa|baga)\s+)*'
        has_turn_verb = re.match(
            prefix + r'(turn|rotate|spin|intoarce|roteste)\b',
            normalized
        ) is not None
        direction = self._extract_turn_direction(normalized)
        angle = self._extract_turn_angle(normalized)

        if not has_turn_verb or direction is None:
            return None

        return {
            'intent': 'turn',
            'direction': direction,
            'steps': 0,
            'confidence': 0.90,
            'parameters': {
                'angle_deg': angle,
                'speed_scale': 1.0,
            },
        }

    def _parse_move(self, normalized: str):
        direction = self._extract_direction(normalized)
        steps = self._extract_steps(normalized)
        prefix = r'^(?:(?:robot|hey robot|please|te rog|can you|can|poti sa|poti|vreau sa|vreau|hai sa|fa|baga)\s+)*'
        has_move_verb = re.match(
            prefix + r'(move|go|walk|step|take|mergi|du te|inainteaza|retrage te|fa)\b',
            normalized
        ) is not None

        if direction and (has_move_verb or steps > 0):
            if steps <= 0:
                steps = self.default_steps
            steps = max(1, min(steps, self.max_steps))
            return {
                'intent': 'move',
                'direction': direction,
                'steps': steps,
                'confidence': 0.88,
                'parameters': {
                    'step_mode': 'discrete',
                    'speed_scale': 1.0,
                },
            }

        return None

    def _extract_direction(self, normalized: str):
        forward_tokens = ('forward', 'ahead', 'front', 'inainte', 'in fata')
        backward_tokens = ('backward', 'backwards', 'back', 'inapoi', 'spate')

        has_forward = any(token in normalized for token in forward_tokens)
        has_backward = any(token in normalized for token in backward_tokens)

        if has_forward and not has_backward:
            return 'forward'
        if has_backward and not has_forward:
            return 'backward'
        return None

    def _extract_turn_direction(self, normalized: str):
        left_tokens = ('left', 'stanga')
        right_tokens = ('right', 'dreapta')

        has_left = any(token in normalized for token in left_tokens)
        has_right = any(token in normalized for token in right_tokens)

        if has_left and not has_right:
            return 'left'
        if has_right and not has_left:
            return 'right'
        return None

    def _extract_turn_angle(self, normalized: str):
        angle_patterns = [
            r'\b(\d+)\s*(degrees?|deg|grade)\b',
        ]
        for pattern in angle_patterns:
            match = re.search(pattern, normalized)
            if match:
                try:
                    value = int(match.group(1))
                    return max(5, min(360, value))
                except ValueError:
                    return 90

        words = normalized.split()
        for i, word in enumerate(words):
            value = NUMBER_WORDS.get(word)
            if value is None:
                continue
            next_word = words[i + 1] if i + 1 < len(words) else ''
            if next_word in ('degree', 'degrees', 'deg', 'grade'):
                return max(5, min(360, value))

        return 90

    def _extract_steps(self, normalized: str):
        step_patterns = [
            r'\b(\d+)\s*(steps?|pasi?|pasii)\b',
            r'\b(\d+)\s*(forward|ahead|backward|backwards|back|inainte|inapoi)\b',
        ]
        for pattern in step_patterns:
            match = re.search(pattern, normalized)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    return 0

        words = normalized.split()
        for i, word in enumerate(words):
            value = NUMBER_WORDS.get(word)
            if value is None:
                continue
            next_word = words[i + 1] if i + 1 < len(words) else ''
            if next_word in (
                'step', 'steps', 'pas', 'pasi', 'pasii', 'forward',
                'ahead', 'backward', 'backwards', 'back', 'inainte', 'inapoi'
            ):
                return value

        return 0

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.lower().strip()
        text = unicodedata.normalize('NFD', text)
        text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
        text = text.replace('-', ' ')
        text = re.sub(r'[^a-z0-9\s]', ' ', text)
        return ' '.join(text.split())


def main(args=None):
    rclpy.init(args=args)
    node = VoiceCommandNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()
