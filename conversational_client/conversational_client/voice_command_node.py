#!/usr/bin/env python3
"""
Voice Command Node - Extract robot action intents from speech transcription.

Recognizes one of the 5 supported commands and publishes it to the safety gate.
It does NOT talk to the motion team directly; the gate
(robot_command_gate_node) validates and forwards approved commands.

Topic names are relative; under the default `voice` namespace they resolve
to /voice/... (e.g. /voice/recognized_commands).

Subscribes to:
  - attended_transcription (Transcription)
  - speaker_id (String)

Publishes to:
  - recognized_commands (RobotCommand, raw recognizer output -> gate input)
  - tts_command (String, optional acknowledgement)
"""
import json

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import RobotCommand, Transcription
from std_msgs.msg import String

from .robot_command_utils import parse_robot_command


class VoiceCommandNode(Node):
    def __init__(self):
        super().__init__('voice_command_node')

        self.declare_parameter('min_transcription_confidence', 0.40)
        self.declare_parameter('default_steps', 1)
        self.declare_parameter('max_steps', 20)
        self.declare_parameter('enable_tts_ack', False)
        self.declare_parameter('tts_ack_en', 'ack_en')
        self.declare_parameter('tts_ack_ro', 'ack_ro')
        self.declare_parameter('transcription_topic', 'attended_transcription')
        self.declare_parameter('command_topic', 'recognized_commands')
        self.declare_parameter('require_direct_robot_address', True)

        self.min_transcription_confidence = float(
            self.get_parameter('min_transcription_confidence').value
        )
        self.default_steps = int(self.get_parameter('default_steps').value)
        self.max_steps = int(self.get_parameter('max_steps').value)
        self.enable_tts_ack = bool(self.get_parameter('enable_tts_ack').value)
        self.tts_ack_en = str(self.get_parameter('tts_ack_en').value)
        self.tts_ack_ro = str(self.get_parameter('tts_ack_ro').value)
        transcription_topic = str(self.get_parameter('transcription_topic').value)
        command_topic = str(self.get_parameter('command_topic').value)
        self.require_direct_robot_address = bool(
            self.get_parameter('require_direct_robot_address').value
        )

        self.current_speaker = 'Unknown'

        self.transcription_sub = self.create_subscription(
            Transcription,
            transcription_topic,
            self._transcription_callback,
            10
        )
        self.speaker_sub = self.create_subscription(
            String,
            'speaker_id',
            self._speaker_callback,
            10
        )

        self.command_pub = self.create_publisher(
            RobotCommand,
            command_topic,
            10
        )
        self.tts_cmd_pub = self.create_publisher(
            String,
            'tts_command',
            10
        )

        self.command_topic = command_topic
        self.get_logger().info(
            f'Voice Command Node started. Publishing recognized commands to {self.command_topic} '
            f'(safety gate forwards approved commands to /robot_commands).'
        )

    def _speaker_callback(self, msg: String):
        speaker = msg.data.strip()
        self.current_speaker = speaker if speaker else 'Unknown'

    def _transcription_callback(self, msg: Transcription):
        text = (msg.text or '').strip()
        if not text:
            return

        if msg.confidence < self.min_transcription_confidence:
            self.get_logger().debug(
                f'Ignoring low-confidence transcript from speaker={self.current_speaker}: '
                f'confidence={msg.confidence:.2f}, text="{text}"'
            )
            return

        self.get_logger().info(
            f'Speaker speaking: speaker={self.current_speaker}, text="{text}", '
            f'confidence={msg.confidence:.2f}'
        )

        parsed = parse_robot_command(
            text,
            default_steps=self.default_steps,
            max_steps=self.max_steps,
            require_direct_robot_address=self.require_direct_robot_address,
        )
        if not parsed:
            return

        cmd = RobotCommand()
        cmd.header.stamp = self.get_clock().now().to_msg()
        cmd.command_id = int(parsed['command_id'])
        cmd.command_name = parsed['command_name']
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
            f'Robot command recognized: id={cmd.command_id}, name={cmd.command_name}, '
            f'steps={cmd.steps}, speaker={cmd.speaker}, topic={self.command_topic}, '
            f'source="{cmd.source_text}"'
        )


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
