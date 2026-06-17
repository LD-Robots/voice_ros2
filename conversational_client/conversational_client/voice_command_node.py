#!/usr/bin/env python3
"""
Voice Command Node - Extract robot action intents from speech transcription.

Subscribes to:
  - /attended_transcription (Transcription)
  - /speaker_id (String)

Publishes to:
  - /humanoid_command (RobotCommand)
  - /tts_command (String, optional acknowledgement)
"""
import json

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import RobotCommand, Transcription
from std_msgs.msg import String

from .robot_command_catalog import command_id_for, command_name_for
from .robot_command_utils import parse_robot_command


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
        self.declare_parameter('command_topic', '/humanoid_command')
        self.declare_parameter('require_direct_robot_address', True)
        self.declare_parameter('log_ignored_transcriptions', False)

        self.min_transcription_confidence = float(
            self.get_parameter('min_transcription_confidence').value
        )
        self.default_steps = int(self.get_parameter('default_steps').value)
        self.max_steps = int(self.get_parameter('max_steps').value)
        self.enable_tts_ack = bool(self.get_parameter('enable_tts_ack').value)
        self.tts_ack_en = str(self.get_parameter('tts_ack_en').value)
        self.tts_ack_ro = str(self.get_parameter('tts_ack_ro').value)
        transcription_topic = str(self.get_parameter('transcription_topic').value)
        self.command_topic = str(self.get_parameter('command_topic').value)
        self.require_direct_robot_address = bool(
            self.get_parameter('require_direct_robot_address').value
        )
        self.log_ignored_transcriptions = bool(
            self.get_parameter('log_ignored_transcriptions').value
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
            '/speaker_id',
            self._speaker_callback,
            10
        )

        self.command_pub = self.create_publisher(
            RobotCommand,
            self.command_topic,
            10
        )
        self.tts_cmd_pub = self.create_publisher(
            String,
            '/tts_command',
            10
        )

        self.get_logger().info(f'Voice Command Node started. Publishing commands to {self.command_topic}')

    def _speaker_callback(self, msg: String):
        speaker = msg.data.strip()
        self.current_speaker = speaker if speaker else 'Unknown'

    def _transcription_callback(self, msg: Transcription):
        text = (msg.text or '').strip()
        if not text:
            return

        if msg.confidence < self.min_transcription_confidence:
            if self.log_ignored_transcriptions:
                self.get_logger().info(
                    f'Ignored transcription below confidence threshold: '
                    f'{msg.confidence:.2f} < {self.min_transcription_confidence:.2f}; text="{text}"'
                )
            return

        parsed = parse_robot_command(
            text,
            default_steps=self.default_steps,
            max_steps=self.max_steps,
            require_direct_robot_address=self.require_direct_robot_address,
        )
        if not parsed:
            if self.log_ignored_transcriptions:
                self.get_logger().info(
                    f'No robot command parsed from attended text: "{text}" '
                    f'(require_direct_robot_address={self.require_direct_robot_address})'
                )
            return

        cmd = RobotCommand()
        cmd.header.stamp = self.get_clock().now().to_msg()
        cmd.intent = parsed['intent']
        cmd.direction = parsed['direction']
        cmd.command_id = command_id_for(cmd.intent, cmd.direction)
        cmd.command_name = command_name_for(cmd.intent, cmd.direction)
        cmd.steps = int(parsed['steps'])
        cmd.confidence = float(parsed['confidence'])
        cmd.source_text = text
        cmd.language = msg.language or ''
        cmd.speaker = self.current_speaker
        parameters = dict(parsed['parameters'])
        parameters['command_id'] = cmd.command_id
        parameters['command_name'] = cmd.command_name
        cmd.parameters_json = json.dumps(parameters, separators=(',', ':'))
        self.command_pub.publish(cmd)

        if self.enable_tts_ack:
            ack = String()
            ack.data = self.tts_ack_ro if (msg.language or '').lower().startswith('ro') else self.tts_ack_en
            self.tts_cmd_pub.publish(ack)

        self.get_logger().info(
            f'Command detected: id={cmd.command_id}, name={cmd.command_name}, '
            f'intent={cmd.intent}, direction={cmd.direction}, steps={cmd.steps}, '
            f'speaker={cmd.speaker}, topic={self.command_topic}'
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
