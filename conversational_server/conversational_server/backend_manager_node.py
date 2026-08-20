#!/usr/bin/env python3
"""
Backend Manager Node.

Publishes the active conversation backend and automatically falls back to the
legacy text pipeline when the realtime speech-to-speech backend becomes
unavailable.
"""
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

# Name of the speech-to-speech backend this stack drives (OpenAI Realtime).
REALTIME_BACKEND = 'openai_realtime'


class BackendManagerNode(Node):
    def __init__(self):
        super().__init__('backend_manager_node')

        self.declare_parameter('preferred_backend', 'legacy')
        self.declare_parameter('fallback_backend', 'legacy')
        self.declare_parameter('offline_timeout_s', 6.0)
        self.declare_parameter('auto_return_to_preferred', True)

        self.preferred_backend = str(self.get_parameter('preferred_backend').value)
        self.fallback_backend = str(self.get_parameter('fallback_backend').value)
        self.offline_timeout_s = float(self.get_parameter('offline_timeout_s').value)
        self.auto_return_to_preferred = bool(
            self.get_parameter('auto_return_to_preferred').value
        )

        self.active_backend = self.preferred_backend
        self.realtime_status = 'unknown'
        self.last_realtime_status_time = (
            time.monotonic() if self.preferred_backend == REALTIME_BACKEND else 0.0
        )

        self.realtime_status_sub = self.create_subscription(
            String,
            'openai_realtime_status',
            self._realtime_status_callback,
            10,
        )
        self.backend_pub = self.create_publisher(String, 'conversation_backend', 10)
        self.status_pub = self.create_publisher(String, 'conversation_backend_status', 10)
        self.timer = self.create_timer(1.0, self._timer_callback)

        self._publish_backend(self.active_backend, 'startup')
        self.get_logger().info(
            f'Backend Manager started: preferred={self.preferred_backend}, fallback={self.fallback_backend}'
        )

    def _realtime_status_callback(self, msg: String):
        if self.preferred_backend != REALTIME_BACKEND:
            return
        self._handle_realtime_status(msg.data.strip() or 'unknown', REALTIME_BACKEND)

    def _handle_realtime_status(self, status: str, backend_name: str):
        self.realtime_status = status
        self.last_realtime_status_time = time.monotonic()

        if status == 'online':
            if self.auto_return_to_preferred and self.active_backend != backend_name:
                self._publish_backend(backend_name, 'realtime_recovered')
            return

        # 'reconnecting' = intentional context refresh by the node itself; stay on preferred backend.
        if status == 'reconnecting':
            return

        if status in ('offline', 'error', 'auth_error'):
            if self.active_backend != self.fallback_backend:
                self._publish_backend(self.fallback_backend, f'realtime_{status}')

    def _timer_callback(self):
        self._publish_backend_topic()

        if self.preferred_backend != REALTIME_BACKEND:
            if self.active_backend != self.preferred_backend:
                self._publish_backend(self.preferred_backend, 'preferred_backend')
            else:
                self._publish_status()
            return

        now = time.monotonic()
        if self.active_backend == REALTIME_BACKEND:
            stale = (
                self.last_realtime_status_time > 0.0
                and (now - self.last_realtime_status_time) > self.offline_timeout_s
            )
            if stale and self.realtime_status != 'online':
                self._publish_backend(self.fallback_backend, 'realtime_timeout')
                return

        self._publish_status()

    def _publish_backend(self, backend: str, reason: str):
        self.active_backend = backend
        self._publish_backend_topic()
        self._publish_status(reason)
        self.get_logger().warn(f'Active backend -> {backend} ({reason})')

    def _publish_backend_topic(self):
        msg = String()
        msg.data = self.active_backend
        self.backend_pub.publish(msg)

    def _publish_status(self, reason: str = ''):
        msg = String()
        reason_suffix = f'|{reason}' if reason else ''
        msg.data = (
            f'active={self.active_backend};preferred={self.preferred_backend};'
            f'realtime={self.realtime_status}{reason_suffix}'
        )
        self.status_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = BackendManagerNode()
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
