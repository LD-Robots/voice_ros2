#!/usr/bin/env python3
"""
audio_feedback_node.py
Provides subtle audio feedback earcons (chimes, pops, hums) for key system state transitions.

Earcon 1: Activation Signal (Session Active via Wake Word)
- Soft ascending 2-note chime (C5 -> E5, 523Hz -> 659Hz)
- Volume: ~25% (-12dB) below standard TTS playback
- Triggered ONLY on session activation (False -> True transition on /session_active or /wake_detected)
"""

import numpy as np
import rclpy
from conversational_interfaces.msg import Audio
from rclpy.node import Node
from std_msgs.msg import Bool


def generate_activation_chime(sample_rate: int = 16000, volume_scale: float = 0.25) -> np.ndarray:
    """Generate a soft, elegant 2-note ascending chime (C5 -> E5) with exponential decay envelope."""
    note_duration_s = 0.08  # 80ms per note (160ms total)
    samples_per_note = int(sample_rate * note_duration_s)
    
    t1 = np.linspace(0, note_duration_s, samples_per_note, False)
    t2 = np.linspace(0, note_duration_s, samples_per_note, False)

    # Note 1: C5 (523.25 Hz) with fast exponential decay
    env1 = np.exp(-t1 * 25.0)
    note1 = np.sin(2 * np.pi * 523.25 * t1) * env1

    # Note 2: E5 (659.25 Hz) with smooth decay
    env2 = np.exp(-t2 * 18.0)
    note2 = np.sin(2 * np.pi * 659.25 * t2) * env2

    audio = np.concatenate([note1, note2])
    # Normalize and scale volume (30% level)
    audio_int16 = (audio * volume_scale * 32767.0).astype(np.int16)
    return audio_int16


class AudioFeedbackNode(Node):
    """ROS2 Node that publishes earcons on system state changes."""

    def __init__(self):
        super().__init__('audio_feedback_node')

        self.declare_parameter('enabled', True)
        self.declare_parameter('activation_chime_volume', 0.25)
        self.declare_parameter('sample_rate', 16000)

        self.enabled = bool(self.get_parameter('enabled').value)
        self.activation_volume = float(self.get_parameter('activation_chime_volume').value)
        self.sample_rate = int(self.get_parameter('sample_rate').value)

        self._session_active = False

        # Pre-synthesize activation chime audio payload
        chime_pcm = generate_activation_chime(self.sample_rate, self.activation_volume)
        self.activation_audio_msg = Audio()
        self.activation_audio_msg.data = chime_pcm.tolist()
        self.activation_audio_msg.sample_rate = self.sample_rate
        self.activation_audio_msg.channels = 1

        # Subscribers
        self.session_sub = self.create_subscription(
            Bool, 'session_active', self._session_callback, 10
        )
        self.wake_sub = self.create_subscription(
            Bool, 'wake_detected', self._wake_callback, 10
        )

        # Publisher for audio output
        self.audio_pub = self.create_publisher(Audio, 'audio_out', 10)

        self.get_logger().info('🔔 Audio Feedback Node started (Earcons enabled)')

    def _session_callback(self, msg: Bool):
        is_active = bool(msg.data)
        # Trigger activation chime on False -> True transition (Session start via Wake Word)
        if is_active and not self._session_active:
            self._play_activation_chime()
        self._session_active = is_active

    def _wake_callback(self, msg: Bool):
        if bool(msg.data) and not self._session_active:
            self._play_activation_chime()

    def _play_activation_chime(self):
        if not self.enabled:
            return
        self.audio_pub.publish(self.activation_audio_msg)
        self.get_logger().info('🎵 Played activation chime (Session Started via Wake Word)')


def main(args=None):
    rclpy.init(args=args)
    node = AudioFeedbackNode()
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
