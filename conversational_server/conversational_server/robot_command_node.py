#!/usr/bin/env python3
"""
Robot command node.

Subscribes to ASR transcription and publishes structured robot commands.
"""
from __future__ import annotations

import uuid

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

from conversational_interfaces.msg import RobotCommand, Transcription

from .robot_command_parser import parse_robot_command


class RobotCommandNode(Node):
    def __init__(self) -> None:
        super().__init__("robot_command_node")

        self.declare_parameter("enabled", True)
        self.declare_parameter("min_asr_confidence", 0.0)
        self.declare_parameter("require_session_active", False)
        self.declare_parameter("stop_ends_session", True)

        self.enabled = bool(self.get_parameter("enabled").value)
        self.min_asr_confidence = float(self.get_parameter("min_asr_confidence").value)
        self.require_session_active = bool(self.get_parameter("require_session_active").value)
        self.stop_ends_session = bool(self.get_parameter("stop_ends_session").value)

        self.session_active = False
        self.session_state_seen = False

        self.transcription_sub = self.create_subscription(
            Transcription,
            "/transcription",
            self.transcription_callback,
            10,
        )
        self.session_sub = self.create_subscription(
            Bool,
            "/session_active",
            self.session_callback,
            10,
        )
        self.command_pub = self.create_publisher(RobotCommand, "/robot_command", 10)
        self.end_session_pub = self.create_publisher(Bool, "/end_session_external", 10)

        self.get_logger().info(
            f"Robot command node started: enabled={self.enabled} "
            f"require_session_active={self.require_session_active}"
        )

    def session_callback(self, msg: Bool) -> None:
        self.session_active = bool(msg.data)
        self.session_state_seen = True

    def _session_gate_open(self) -> bool:
        if not self.require_session_active:
            return True
        if not self.session_state_seen:
            return False
        return self.session_active

    def transcription_callback(self, msg: Transcription) -> None:
        if not self.enabled:
            return
        if not self._session_gate_open():
            return
        if float(msg.confidence) < self.min_asr_confidence:
            return

        parsed, normalized = parse_robot_command(msg.text)
        if parsed is None:
            return

        cmd = RobotCommand()
        cmd.header.stamp = self.get_clock().now().to_msg()
        cmd.command_id = str(uuid.uuid4())
        cmd.action = parsed.action
        cmd.target = parsed.target
        cmd.value = float(parsed.value)
        cmd.unit = parsed.unit
        cmd.tags = list(parsed.tags)
        cmd.raw_text = msg.text
        cmd.normalized_text = normalized
        cmd.language = msg.language
        cmd.confidence = float(parsed.confidence)
        cmd.requires_confirmation = bool(parsed.requires_confirmation)
        self.command_pub.publish(cmd)

        if self.stop_ends_session and parsed.action == "stop":
            end_msg = Bool()
            end_msg.data = True
            self.end_session_pub.publish(end_msg)

        self.get_logger().info(
            f'Robot command: action="{cmd.action}" target="{cmd.target}" '
            f'value={cmd.value:.2f} {cmd.unit} (from "{msg.text}")'
        )


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = RobotCommandNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
