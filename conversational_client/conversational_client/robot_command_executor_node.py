#!/usr/bin/env python3
"""
Robot Command Executor Node.

Consumes normalized voice commands and executes controller-side actions.
"""
import queue
import threading
import time

import rclpy
from rclpy.node import Node
from conversational_interfaces.msg import RobotCommand
from geometry_msgs.msg import Twist
from std_msgs.msg import String
from std_srvs.srv import Trigger


class RobotCommandExecutorNode(Node):
    def __init__(self):
        super().__init__('robot_command_executor_node')

        self.declare_parameter('execution_enabled', True)
        self.declare_parameter('min_command_confidence', 0.60)
        self.declare_parameter('max_pending_commands', 20)

        self.declare_parameter('move_mode', 'twist')  # twist | topic
        self.declare_parameter('cmd_vel_topic', '/cmd_vel')
        self.declare_parameter('move_topic', '/robot_move_command')
        self.declare_parameter('step_length_m', 0.25)
        self.declare_parameter('linear_speed_mps', 0.15)
        self.declare_parameter('control_rate_hz', 20.0)
        self.declare_parameter('max_move_duration_s', 20.0)

        self.declare_parameter('behavior_mode', 'topic')  # topic | service | both
        self.declare_parameter('behavior_topic', '/robot_behavior_command')
        self.declare_parameter('raise_hands_service', '/raise_hands')
        self.declare_parameter('dance_service', '/dance')
        self.declare_parameter('service_timeout_s', 3.0)

        self.execution_enabled = bool(self.get_parameter('execution_enabled').value)
        self.min_command_confidence = float(self.get_parameter('min_command_confidence').value)
        self.max_pending_commands = int(self.get_parameter('max_pending_commands').value)

        self.move_mode = str(self.get_parameter('move_mode').value)
        self.cmd_vel_topic = str(self.get_parameter('cmd_vel_topic').value)
        self.move_topic = str(self.get_parameter('move_topic').value)
        self.step_length_m = float(self.get_parameter('step_length_m').value)
        self.linear_speed_mps = float(self.get_parameter('linear_speed_mps').value)
        self.control_rate_hz = float(self.get_parameter('control_rate_hz').value)
        self.max_move_duration_s = float(self.get_parameter('max_move_duration_s').value)

        self.behavior_mode = str(self.get_parameter('behavior_mode').value)
        self.behavior_topic = str(self.get_parameter('behavior_topic').value)
        self.raise_hands_service = str(self.get_parameter('raise_hands_service').value)
        self.dance_service = str(self.get_parameter('dance_service').value)
        self.service_timeout_s = float(self.get_parameter('service_timeout_s').value)

        self.command_sub = self.create_subscription(
            RobotCommand,
            '/robot_command',
            self._command_callback,
            10
        )

        self.cmd_vel_pub = self.create_publisher(Twist, self.cmd_vel_topic, 10)
        self.move_topic_pub = self.create_publisher(String, self.move_topic, 10)
        self.behavior_pub = self.create_publisher(String, self.behavior_topic, 10)

        self.raise_hands_client = self.create_client(Trigger, self.raise_hands_service)
        self.dance_client = self.create_client(Trigger, self.dance_service)

        self._queue = queue.Queue(maxsize=self.max_pending_commands)
        self._running = True
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name='robot-command-worker')
        self._worker.start()

        self.get_logger().info(
            'Robot Command Executor started: '
            f'execution_enabled={self.execution_enabled}, move_mode={self.move_mode}, behavior_mode={self.behavior_mode}'
        )

    def destroy_node(self):
        self._running = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        return super().destroy_node()

    def _command_callback(self, msg: RobotCommand):
        if not self.execution_enabled:
            return

        if msg.confidence < self.min_command_confidence:
            self.get_logger().debug(
                f'Ignoring command below threshold ({msg.confidence:.2f} < {self.min_command_confidence:.2f})'
            )
            return

        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            self.get_logger().warn('Command queue is full. Dropping command.')

    def _worker_loop(self):
        while self._running:
            try:
                msg = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if msg is None:
                continue

            try:
                self._execute(msg)
            except Exception as exc:
                self.get_logger().error(f'Command execution failed: {exc}')

    def _execute(self, msg: RobotCommand):
        intent = msg.intent.strip().lower()
        if intent == 'move':
            self._execute_move(msg)
            return
        if intent in ('raise_hands', 'dance'):
            self._execute_behavior(intent)
            return
        self.get_logger().warn(f'Unsupported intent: {intent}')

    def _execute_move(self, msg: RobotCommand):
        direction = msg.direction.strip().lower()
        steps = max(1, int(msg.steps) if msg.steps > 0 else 1)

        sign = 0.0
        if direction == 'forward':
            sign = 1.0
        elif direction == 'backward':
            sign = -1.0
        else:
            self.get_logger().warn(f'Invalid move direction: {direction}')
            return

        if self.move_mode == 'topic':
            payload = String()
            payload.data = f'{direction}:{steps}'
            self.move_topic_pub.publish(payload)
            self.get_logger().info(f'Published move topic command: {payload.data}')
            return

        speed = abs(self.linear_speed_mps)
        if speed <= 0.0:
            self.get_logger().warn('linear_speed_mps must be > 0. Move ignored.')
            return

        distance = steps * max(0.01, self.step_length_m)
        duration = min(self.max_move_duration_s, distance / speed)
        if duration <= 0.0:
            self.get_logger().warn('Computed move duration is 0. Move ignored.')
            return

        rate_hz = max(1.0, self.control_rate_hz)
        period = 1.0 / rate_hz
        end_t = time.monotonic() + duration

        if self.cmd_vel_pub.get_subscription_count() == 0:
            self.get_logger().warn(f'No subscribers on {self.cmd_vel_topic}. Command may not move the robot.')

        cmd = Twist()
        cmd.linear.x = sign * speed

        while self._running and time.monotonic() < end_t:
            self.cmd_vel_pub.publish(cmd)
            time.sleep(period)

        self.cmd_vel_pub.publish(Twist())
        self.get_logger().info(f'Executed move: direction={direction}, steps={steps}, duration={duration:.2f}s')

    def _execute_behavior(self, intent: str):
        mode = self.behavior_mode.lower()

        if mode in ('topic', 'both'):
            msg = String()
            msg.data = intent
            self.behavior_pub.publish(msg)
            self.get_logger().info(f'Published behavior command: {intent}')

        if mode in ('service', 'both'):
            client = self.raise_hands_client if intent == 'raise_hands' else self.dance_client
            if not client.wait_for_service(timeout_sec=0.5):
                self.get_logger().warn(f'Service unavailable for {intent}.')
                return

            req = Trigger.Request()
            future = client.call_async(req)
            end_t = time.monotonic() + max(0.1, self.service_timeout_s)
            while self._running and (not future.done()) and time.monotonic() < end_t:
                time.sleep(0.05)

            if not future.done():
                self.get_logger().warn(f'Service call timeout for {intent}.')
                return

            result = future.result()
            if result is None:
                self.get_logger().warn(f'Service call returned no result for {intent}.')
                return

            if result.success:
                self.get_logger().info(f'Service execution succeeded for {intent}: {result.message}')
            else:
                self.get_logger().warn(f'Service execution failed for {intent}: {result.message}')


def main(args=None):
    rclpy.init(args=args)
    node = RobotCommandExecutorNode()
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
